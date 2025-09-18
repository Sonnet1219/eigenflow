"""Alert Service API endpoints implementing PRD-compliant monitoring."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, HTTPException

from alert_service.config import AlertSeverity, MonitoringConfig
from alert_service.models import AlertRecord, HumanAction, HumanActionRequest
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

        response = await orchestrator_client.trigger_margin_check(
            lp=lp_name,
            margin_level=margin_level,
            severity=severity,
            trace_id=trace_id,
            occurred_at=now,
            payload={"accountSnapshot": account},
        )

        record.mark_notified(now)
        record.marginLevel = margin_level
        record.lastUpdatedAt = now

        response_type = response.get("type")
        thread_id = response.get("thread_id")

        # Log AI suggestion or card content for visibility
        suggestion_text = None
        if response_type == "complete":
            suggestion_text = response.get("content")
        elif response_type == "interrupt":
            interrupt_payload = response.get("interrupt_data")
            if isinstance(interrupt_payload, dict):
                # Prefer structured report/card fields if available
                for key in ("report", "card", "content"):
                    if interrupt_payload.get(key):
                        suggestion_text = interrupt_payload.get(key)
                        break
            if suggestion_text is None and interrupt_payload is not None:
                suggestion_text = interrupt_payload

        if suggestion_text:
            try:
                if not isinstance(suggestion_text, str):
                    suggestion_text = json.dumps(
                        suggestion_text, ensure_ascii=False
                    )
                preview_len = 800
                trimmed = (
                    suggestion_text
                    if len(suggestion_text) <= preview_len
                    else suggestion_text[:preview_len] + "..."
                )
                logger.info(
                    "[ALERT][AI] Suggestions for %s (trace=%s): %s",
                    lp_name,
                    trace_id,
                    trimmed,
                )
            except Exception as log_exc:  # noqa: BLE001
                logger.debug(
                    "Failed to serialize AI suggestion for logging: %s", log_exc
                )

        if response_type == "interrupt":
            record.threadId = thread_id
            record.mark_hitl()
            logger.warning(
                "[ALERT][HITL] Awaiting user approval for %s (trace=%s, thread=%s, severity=%s)",
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
async def human_action(payload: HumanActionRequest) -> Dict[str, Any]:
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

    user_message = payload.message or "用户已处理，请复查保证金风险"

    response = await orchestrator_client.resume_margin_check(
        thread_id=thread_id,
        user_input=user_message,
    )

    response_type = response.get("type")
    if response_type == "complete":
        record.mark_resolved(auto=False)
        record.lastUpdatedAt = now
        logger.info(
            "[ALERT][RESOLVED] %s cleared after recheck (trace=%s, thread=%s)",
            record.lp,
            record.traceId,
            thread_id,
        )
    elif response_type == "interrupt":
        record.mark_hitl()
        record.threadId = response.get("thread_id", thread_id)
        record.lastUpdatedAt = now
        logger.warning(
            "[ALERT][HITL] Still pending after recheck for %s (trace=%s, thread=%s)",
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
        record.lastUpdatedAt = now

    return response


@router.on_event("shutdown")
async def shutdown_event() -> None:
    await orchestrator_client.close()
