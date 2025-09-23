"""Alert Service API endpoints implementing PRD-compliant monitoring."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, AsyncIterator

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse

from alert_service.config import AlertSeverity, MonitoringConfig
from alert_service.models import (
    AlertRecord,
    AlertStatus,
    HumanAction,
    HumanActionRequest,
)
from alert_service.orchestrator_client import OrchestratorClient
from alert_service.state_manager import AlertStateManager
from src.agent.data_gateway import EigenFlowAPI

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alert", tags=["alert"])


config = MonitoringConfig()
state_manager = AlertStateManager(config)
orchestrator_client = OrchestratorClient(
    config.orchestrator_base_url,
    timeout_seconds=config.orchestrator_timeout_seconds,
)


def _normalize_report_content(content: Any) -> Optional[str]:
    """Convert structured report payloads to a string representation."""

    if content is None:
        return None
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):  # Fallback to plain string conversion
        return str(content)


def _find_report_candidate(payload: Any) -> Optional[Any]:
    """Heuristically locate the most relevant report-like content in a payload."""
    
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in (
            "report",
            "card",
            "content",
            "body",
            "details",
            "message",
            "data",
        ):
            if key in payload:
                candidate = _find_report_candidate(payload[key])
                if candidate:
                    return candidate
        # Fallback to first string value
        for value in payload.values():
            candidate = _find_report_candidate(value)
            if candidate:
                return candidate
    if isinstance(payload, (list, tuple)):
        for item in payload:
            candidate = _find_report_candidate(item)
            if candidate:
                return candidate
    return None


def _extract_report_text(
    response: Dict[str, Any],
    *,
    exclude: Optional[set[str]] = None,
) -> Optional[str]:
    """Derive the latest report text from orchestrator responses."""

    response_type = response.get("type")
    suggestion_text: Any = None

    if response_type == "complete":
        suggestion_text = response.get("content")
    elif response_type == "interrupt":
        interrupt_payload = response.get("interrupt_data")
        suggestion_text = _find_report_candidate(interrupt_payload)

    if suggestion_text is None:
        suggestion_text = _find_report_candidate(response)

    normalized = _normalize_report_content(suggestion_text)
    if not normalized:
        return None

    comparison_pool = {text.strip() for text in (exclude or set()) if text}
    if normalized.strip() in comparison_pool:
        return None
    return normalized


def _wants_stream(request: Request) -> bool:
    """Check whether the caller expects server-sent events."""

    stream_param = request.query_params.get("stream")
    if stream_param and stream_param.lower() in {"1", "true", "yes"}:
        return True

    accept = request.headers.get("accept", "")
    return "text/event-stream" in accept.lower()


def _format_sse(payload: Dict[str, Any], event: str | None = None) -> str:
    """Serialize payload for SSE response."""

    data = json.dumps(payload, ensure_ascii=False)
    if event:
        return f"event: {event}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


def _build_recheck_message(user_message: Optional[str]) -> str:
    """Construct instruction for recheck invocations."""

    instruction = (
        """[Recheck Request] Please immediately regenerate a complete risk analysis 
        and operational recommendations based on the latest margin and position data, 
        and explicitly label the report at the beginning as 'Recheck' result."""
    )
    if user_message:
        trimmed = user_message.strip()
        if trimmed:
            return f"{trimmed}\n{instruction}"
    return instruction


RECHECK_PROMPT_PATTERN = re.compile(
    r"\{[^{}]*\"action\"\s*:\s*\"recheck\"[^{}]*\}\s*",
    re.IGNORECASE,
)


def _strip_recheck_prompt(text: Optional[str]) -> Optional[str]:
    """Remove prompt scaffolding and HITL metadata from stored reports."""

    if not text:
        return text

    cleaned = RECHECK_PROMPT_PATTERN.sub("", text)
    cleaned = cleaned.replace(
         """[Recheck Request] Please immediately regenerate a complete risk analysis 
        and operational recommendations based on the latest margin and position data, 
        and explicitly label the report at the beginning as 'Recheck' result.""",
        "",
    )
    # Collapse duplicated blank lines introduced by removals
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _annotate_recheck_report(report: Optional[str], *, occurred_at: datetime) -> Optional[str]:
    """Ensure recheck reports carry explicit annotation."""

    if not report:
        return None

    header = f"[RECHECK][{occurred_at.isoformat()}]"
    if header in report:
        return report
    if report.startswith("[RECHECK]"):
        return report
    return f"{header}\n{report}"


def _apply_recheck_outcome(
    record: AlertRecord,
    response: Dict[str, Any],
    *,
    now: datetime,
    note_recheck: bool = False,
) -> None:
    """Update alert record based on orchestrator response."""

    response_type = response.get("type")
    thread_id = response.get("thread_id")
    if thread_id:
        record.threadId = thread_id

    content = response.get("content")
    if note_recheck:
        content = _annotate_recheck_report(content, occurred_at=now) or content
    content = _strip_recheck_prompt(content)
    if content:
        record.latestReport = content

    if response_type == "complete":
        summary = (content or "").upper()
        if summary and ("CRITICAL" in summary or "高风险" in summary or "危机" in summary):
            record.mark_recheck_pending()
        elif summary:
            record.mark_resolved(auto=False)
        else:
            record.mark_recheck_pending()
    elif response_type == "interrupt":
        record.mark_recheck_pending()

    record.lastUpdatedAt = now


async def _fetch_report_from_history(thread_id: str, *, exclude: Optional[set[str]] = None) -> Optional[str]:
    """Fetch the latest assistant message from orchestrator history as a fallback report."""

    try:
        history = await orchestrator_client.fetch_history(thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to fetch history for report enrichment: %s", exc)
        return None

    execution = history.get("execution_history") or []
    for step in reversed(execution):
        messages = step.get("values", {}).get("messages", [])
        for message in reversed(messages):
            candidate: Optional[str] = None
            if isinstance(message, dict):
                msg_type = message.get("type") or message.get("role")
                if msg_type in {"ai", "assistant", "tool"}:
                    parsed = message.get("parsed_content")
                    candidate = _normalize_report_content(parsed) if parsed else _normalize_report_content(message.get("content"))
            elif isinstance(message, str):
                candidate = message

            if not candidate:
                continue

            comparison_pool = {text.strip() for text in (exclude or set()) if text}
            if candidate.strip() in comparison_pool:
                continue
            return candidate

    return None


class MonitoringService:
    """LP margin monitoring service with hysteresis and HITL integration."""

    def __init__(self) -> None:
        self.is_running = False
        self.api_client = EigenFlowAPI()
        self._lock = asyncio.Lock()
        self._last_blowup: Dict[str, datetime] = {}

    async def monitor_margins(self) -> None:
        """Monitor LP margins and trigger orchestrator flows."""
        async with self._lock:
            if self.is_running:
                logger.info("Monitoring service already running")
                return
            self.is_running = True

        logger.info(
            "Starting LP margin monitoring service",
            extra={"interval": config.monitoring_interval},
        )
        try:
            while self.is_running:
                await self._tick()
                await asyncio.sleep(config.monitoring_interval)
        except asyncio.CancelledError:
            logger.info("Monitoring task cancelled")
        finally:
            self.is_running = False
            logger.info("LP margin monitoring service stopped")

    async def _tick(self) -> None:
        accounts = await self.fetch_lp_data()
        if not accounts:
            logger.warning("No LP account data retrieved during monitoring tick")
            return

        for account in accounts:
            try:
                await self._process_account(account)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Failed to process account", extra={"account": account, "error": str(exc)}
                )

        # cleanup resolved alerts periodically
        state_manager.drop_resolved()

    async def _process_account(self, account: Dict[str, Any]) -> None:
        lp_name = account.get("LP", "Unknown")
        margin_level = float(account.get("Margin Utilization %", 0.0))
        now = datetime.now(timezone.utc)

        if self._detect_blowup(account, margin_level):
            if self._should_emit_blowup(lp_name, now):
                trace_id = f"blowup_{lp_name.replace(' ', '_')}_{int(now.timestamp())}"
                await orchestrator_client.trigger_margin_check(
                    lp=lp_name,
                    margin_level=margin_level,
                    severity=AlertSeverity.EMERGENCY,
                    trace_id=trace_id,
                    occurred_at=now,
                    payload={"accountSnapshot": account, "blowup": True},
                    event_type="ACCOUNT_BLOWUP",
                    message=f"URGENT: {lp_name} margin account is in liquidation risk.",
                )
                logger.critical(
                    "Blowup detected for LP",
                    extra={"lp": lp_name, "trace_id": trace_id, "margin": margin_level},
                )
            return

        severity = config.determine_severity(margin_level)
        existing_record = state_manager.get(lp_name)

        if severity is None:
            if existing_record and state_manager.hysteresis_cleared(existing_record, margin_level):
                await self._handle_auto_clear(existing_record, margin_level, account, now)
            elif existing_record:
                existing_record.marginLevel = margin_level
                existing_record.lastUpdatedAt = now
            return

        trace_id = (
            existing_record.traceId
            if existing_record and existing_record.severity == severity
            else self._build_trace_id(lp_name, severity, now)
        )

        record = state_manager.upsert(
            lp=lp_name,
            severity=severity,
            margin_level=margin_level,
            trace_id=trace_id,
            now=now,
        )

        if not state_manager.should_issue_alert(record, now):
            logger.debug(
                "Alert suppressed by pacing/ignore",
                extra={"lp": lp_name, "trace_id": trace_id},
            )
            return

        logger.warning(
            "[ALERT] %s margin %.2f%% (trace=%s, severity=%s) triggering orchestrator",
            lp_name,
            margin_level,
            trace_id,
            severity.value,
        )

        default_prompt = (
            f"Monitoring alert: {lp_name} margin level {margin_level:.2f}% "
            f"meets {severity.value} threshold."
        )

        response = await orchestrator_client.trigger_margin_check(
            lp=lp_name,
            margin_level=margin_level,
            severity=severity,
            trace_id=trace_id,
            occurred_at=now,
            payload={
                "accountSnapshot": account,
                "threshold": config.thresholds[severity].trigger / 100,
            },
            message=default_prompt,
        )

        record.mark_notified(now)
        record.marginLevel = margin_level
        record.lastUpdatedAt = now

        response_type = response.get("type")
        thread_id = response.get("thread_id")

        # Log AI suggestion or card content for visibility
        exclude_prompts = {default_prompt}
        if report_text := _extract_report_text(response, exclude=exclude_prompts):
            record.latestReport = report_text
        elif thread_id:
            history_report = await _fetch_report_from_history(thread_id, exclude=exclude_prompts)
            if history_report:
                record.latestReport = history_report

        if response_type == "interrupt":
            record.threadId = thread_id
            if record.status is not AlertStatus.RECHECK_PENDING:
                record.mark_hitl()
                status_tag = "[ALERT][HITL]"
            else:
                status_tag = "[ALERT][RECHECK]"
            logger.warning(
                "%s Awaiting user approval for %s (trace=%s, thread=%s, severity=%s)",
                status_tag,
                lp_name,
                trace_id,
                thread_id,
                record.severity.value,
            )
        elif response_type == "complete":
            state_manager.resolve(lp_name)
            logger.info(
                "[ALERT][RESOLVED] %s resolved automatically (trace=%s)",
                lp_name,
                trace_id,
            )
        else:
            logger.info(
                "[ALERT] %s processed with response=%s (trace=%s)",
                lp_name,
                response_type,
                trace_id,
            )

    async def _handle_auto_clear(
        self,
        record: AlertRecord,
        margin_level: float,
        account: Dict[str, Any],
        now: datetime,
    ) -> None:
        logger.info(
            "[ALERT][AUTO-CLEAR] %s margin %.2f%% (was %.2f%%) trace=%s",
            record.lp,
            margin_level,
            record.marginLevel,
            record.traceId,
        )
        await orchestrator_client.trigger_margin_check(
            lp=record.lp,
            margin_level=margin_level,
            severity=record.severity,
            trace_id=record.traceId,
            occurred_at=now,
            payload={
                "accountSnapshot": account,
                "autoCleared": True,
            },
            message=f"Monitoring update: {record.lp} margin level back to {margin_level:.2f}%.",
        )
        state_manager.resolve(record.lp, auto=True)

    async def fetch_lp_data(self) -> List[Dict[str, Any]]:
        """Fetch LP account data from Data Gateway."""
        try:
            if not self.api_client.access_token:
                auth_result = self.api_client.authenticate()
                if not auth_result.get("success"):
                    logger.error("Authentication failed: %s", auth_result.get("error"))
                    return []

            accounts_result = self.api_client.get_lp_accounts()
            if not isinstance(accounts_result, list):
                return []
            return accounts_result
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error fetching LP data: %s", exc)
            return []

    def stop(self) -> None:
        self.is_running = False

    def _build_trace_id(
        self, lp_name: str, severity: AlertSeverity, now: datetime
    ) -> str:
        epoch = int(now.timestamp())
        sanitized_lp = lp_name.replace(" ", "_")
        return f"monitor_{sanitized_lp}_{severity.value}_{epoch}"

    def _detect_blowup(self, account: Dict[str, Any], margin_level: float) -> bool:
        equity = float(account.get("Equity", 0.0))
        margin_used = float(account.get("Margin", 0.0))
        forced_liquidation = account.get("Forced Liquidation", 0)
        if forced_liquidation:
            return True
        if margin_used <= 0:
            return False
        if equity <= margin_used:
            return True
        return margin_level <= config.blowup_margin_level

    def _should_emit_blowup(self, lp: str, now: datetime) -> bool:
        last = self._last_blowup.get(lp)
        if last and (now - last).total_seconds() < config.blowup_suppression_seconds:
            return False
        self._last_blowup[lp] = now
        return True


monitoring_service = MonitoringService()


@router.post("/start-monitoring")
async def start_monitoring(background_tasks: BackgroundTasks) -> Dict[str, str]:
    """Start the LP margin monitoring service."""
    if monitoring_service.is_running:
        return {"status": "info", "message": "Monitoring service already running"}
    background_tasks.add_task(monitoring_service.monitor_margins)
    return {"status": "success", "message": "Monitoring service started"}


@router.post("/stop-monitoring")
async def stop_monitoring() -> Dict[str, str]:
    """Stop the monitoring service."""
    monitoring_service.stop()
    return {"status": "success", "message": "Monitoring service stopping"}


@router.get("/monitoring-status")
async def get_monitoring_status() -> Dict[str, Any]:
    """Get current monitoring service status and active alerts."""
    return {
        "status": "running" if monitoring_service.is_running else "stopped",
        "thresholds": {
            severity.value: {
                "trigger": config.thresholds[severity].trigger,
                "clear": config.thresholds[severity].clear,
            }
            for severity in config.thresholds
        },
        "interval": config.monitoring_interval,
        "blowupMarginLevel": config.blowup_margin_level,
        "alerts": state_manager.serialize(),
    }


@router.post("/human-action")
async def human_action(request: Request, payload: HumanActionRequest):
    """Receive human-in-the-loop actions from Chat UI cards."""
    record = state_manager.get_by_trace(payload.traceId)
    now = datetime.now(timezone.utc)

    if not record:
        raise HTTPException(status_code=404, detail="Alert trace not found")

    record.reset_ignore_if_expired(now)

    if payload.action is HumanAction.IGNORE:
        ignore_until = payload.ignore_until(now)
        if not ignore_until:
            raise HTTPException(status_code=400, detail="ignoreMinutes is required")
        record.mark_ignored(ignore_until)
        record.lastUpdatedAt = now
        logger.warning(
            "[ALERT][IGNORED] %s suppressed until %s (trace=%s)",
            record.lp,
            ignore_until.isoformat(),
            record.traceId,
        )
        return {
            "status": "ignored",
            "ignoreUntil": ignore_until.isoformat(),
        }

    if payload.action is HumanAction.COMMENT:
        logger.info(
            "[ALERT][COMMENT] %s trace=%s note=%s",
            record.lp,
            record.traceId,
            payload.message,
        )
        record.lastUpdatedAt = now
        return {"status": "noted"}

    # RECHECK
    thread_id = payload.threadId or record.threadId
    if not thread_id:
        raise HTTPException(status_code=400, detail="threadId is required for recheck")

    recheck_message = _build_recheck_message(payload.message)
    wants_stream = _wants_stream(request)

    async def _refresh_record_metrics(record: AlertRecord) -> None:
        accounts = await monitoring_service.fetch_lp_data()
        for account in accounts:
            if account.get("LP") == record.lp:
                margin_level = float(account.get("Margin Utilization %", 0.0))
                record.marginLevel = margin_level
                severity = config.determine_severity(margin_level)
                if severity:
                    record.severity = severity
                return

    if wants_stream:
        async def event_stream() -> AsyncIterator[str]:
            try:
                yield _format_sse(
                    {
                        "status": "recheck_started",
                        "traceId": record.traceId,
                        "threadId": thread_id,
                    },
                    event="status",
                )

                async for event in orchestrator_client.stream_resume_margin_check(
                    thread_id=thread_id,
                    user_input=recheck_message,
                ):
                    name = event.get("event", "message")
                    data = event.get("data") or {}

                    if name == "final" and isinstance(data, dict):
                        content = data.get("content")
                        thread_ref = data.get("thread_id") or thread_id
                        if not content and thread_ref:
                            history_report = await _fetch_report_from_history(
                                thread_ref,
                                exclude={recheck_message},
                            )
                            if history_report:
                                data = {**data, "content": history_report}

                        annotated = _annotate_recheck_report(
                            data.get("content"), occurred_at=now
                        )
                        if annotated:
                            data = {**data, "content": annotated}
                        stripped = _strip_recheck_prompt(data.get("content"))
                        if stripped is not None:
                            data = {**data, "content": stripped}
                    if name == "final":
                        payload_dict = data if isinstance(data, dict) else {}
                        _apply_recheck_outcome(
                            record,
                            payload_dict,
                            now=now,
                            note_recheck=True,
                        )
                        await _refresh_record_metrics(record)
                    yield _format_sse(data if isinstance(data, dict) else {"data": data}, event=name)

            except Exception as exc:  # noqa: BLE001
                logger.exception("Streaming recheck failed: %s", exc)
                error_payload = {
                    "status": "error",
                    "message": str(exc),
                    "traceId": record.traceId,
                }
                yield _format_sse(error_payload, event="error")

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    response = await orchestrator_client.resume_margin_check(
        thread_id=thread_id,
        user_input=recheck_message,
    )

    exclude_prompts = {recheck_message}
    report_text = _extract_report_text(response, exclude=exclude_prompts)
    if report_text:
        response = {**response, "content": _strip_recheck_prompt(report_text)}
    elif thread_id := response.get("thread_id"):
        history_report = await _fetch_report_from_history(thread_id, exclude=exclude_prompts)
        if history_report:
            response = {**response, "content": _strip_recheck_prompt(history_report)}

    _apply_recheck_outcome(record, response, now=now, note_recheck=True)
    await _refresh_record_metrics(record)

    response_type = response.get("type")
    if response_type == "complete":
        logger.info(
            "[ALERT][RESOLVED] %s cleared after recheck (trace=%s, thread=%s)",
            record.lp,
            record.traceId,
            record.threadId,
        )
    elif response_type == "interrupt":
        logger.warning(
            "[ALERT][RECHECK] Still pending after recheck for %s (trace=%s, thread=%s)",
            record.lp,
            record.traceId,
            record.threadId,
        )
    else:
        logger.info(
            "[ALERT] Recheck response=%s for %s (trace=%s)",
            response_type,
            record.lp,
            record.traceId,
        )

    return response


@router.get("/history/{trace_id}")
async def alert_history(trace_id: str) -> Dict[str, Any]:
    """Expose orchestrator execution history for a given alert trace."""

    record = state_manager.get_by_trace(trace_id)
    if not record:
        raise HTTPException(status_code=404, detail="Alert trace not found")

    if not record.threadId:
        raise HTTPException(status_code=404, detail="History unavailable for this alert")

    history = await orchestrator_client.fetch_history(record.threadId)
    return {
        "traceId": trace_id,
        "threadId": record.threadId,
        "history": history,
    }


@router.on_event("shutdown")
async def shutdown_event() -> None:
    await orchestrator_client.close()
