"""Async client helpers for interacting with the orchestrator (LangGraph) service."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from alert_service.config import AlertSeverity

logger = logging.getLogger(__name__)


class OrchestratorClient:
    """Wrapper around httpx.AsyncClient to call margin-check endpoints."""

    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        timeout = httpx.Timeout(timeout_seconds)
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def trigger_margin_check(
        self,
        *,
        lp: str,
        margin_level: float,
        severity: AlertSeverity,
        trace_id: str,
        occurred_at: datetime,
        payload: Optional[Dict[str, Any]] = None,
        event_type: str = "MARGIN_ALERT",
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Trigger the margin-check orchestrator with monitor event payload."""

        json_payload: Dict[str, Any] = {
            "triggerType": "monitor",
            "traceId": trace_id,
            "slots": {"lp": lp, "severity": severity.value},
            "occurredAt": occurred_at.isoformat(),
            "eventType": event_type,
            "payload": {
                "lp": lp,
                "marginLevel": margin_level / 100.0,  # orchestrator expects ratio
                "severity": severity.value,
                **(payload or {}),
            },
            "messages": [
                {
                    "type": "human",
                    "content": message
                    or (
                        f"Monitoring alert: {lp} margin level {margin_level:.2f}% "
                        f"meets {severity.value} threshold."
                    ),
                }
            ],
        }
        logger.info(
            "[ALERT->ORCH] event=%s lp=%s severity=%s trace=%s",
            event_type,
            lp,
            severity.value,
            trace_id,
        )
        response = await self._client.post("/agent/margin-check", json=json_payload)
        response.raise_for_status()
        return response.json()

    async def resume_margin_check(
        self,
        *,
        thread_id: str,
        user_input: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resume a paused margin check session after human action."""

        payload = {
            "thread_id": thread_id,
        }
        if user_input:
            payload["messages"] = [{"type": "human", "content": user_input}]
        response = await self._client.post("/agent/margin-check/recheck", json=payload)
        response.raise_for_status()
        return response.json()

    async def fetch_history(self, thread_id: str) -> Dict[str, Any]:
        """Retrieve execution history for diagnostics."""

        response = await self._client.post(
            "/agent/margin-check/history", json={"thread_id": thread_id}
        )
        response.raise_for_status()
        return response.json()
