"""In-memory state manager for monitoring alerts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Iterable, Optional

from alert_service.config import AlertSeverity, MonitoringConfig
from alert_service.models import AlertRecord, AlertStatus

logger = logging.getLogger(__name__)


class AlertStateManager:
    """Track active alerts, hysteresis logic, and notification pacing."""

    def __init__(self, config: MonitoringConfig) -> None:
        self._config = config
        self._alerts: Dict[str, AlertRecord] = {}
        self._by_trace: Dict[str, str] = {}

    def get(self, lp: str) -> Optional[AlertRecord]:
        return self._alerts.get(lp)

    def get_by_trace(self, trace_id: str) -> Optional[AlertRecord]:
        lp = self._by_trace.get(trace_id)
        if lp:
            return self._alerts.get(lp)
        return None

    def upsert(
        self,
        *,
        lp: str,
        severity: AlertSeverity,
        margin_level: float,
        trace_id: str,
        now: datetime,
    ) -> AlertRecord:
        record = self._alerts.get(lp)
        if record:
            if record.severity != severity:
                record.notificationCount = 0
                record.lastNotifiedAt = None
            record.severity = severity
            record.marginLevel = margin_level
            record.lastUpdatedAt = now
            if record.traceId != trace_id:
                self._by_trace.pop(record.traceId, None)
                record.traceId = trace_id
        else:
            record = AlertRecord(
                lp=lp,
                severity=severity,
                marginLevel=margin_level,
                traceId=trace_id,
                firstTriggeredAt=now,
                lastUpdatedAt=now,
            )
            self._alerts[lp] = record
        self._by_trace[trace_id] = lp
        return record

    def resolve(self, lp: str, *, auto: bool = False) -> Optional[AlertRecord]:
        record = self._alerts.get(lp)
        if record:
            record.mark_resolved(auto=auto)
            record.lastUpdatedAt = datetime.now(timezone.utc)
            self._by_trace.pop(record.traceId, None)
            logger.info(
                "Alert resolved",
                extra={"lp": lp, "trace_id": record.traceId, "auto": auto},
            )
        return record

    def drop_resolved(self) -> None:
        to_remove = [lp for lp, rec in self._alerts.items() if rec.status in {AlertStatus.RESOLVED, AlertStatus.AUTO_CLEARED}]
        for lp in to_remove:
            record = self._alerts.pop(lp, None)
            if record:
                self._by_trace.pop(record.traceId, None)

    def active_alerts(self) -> Iterable[AlertRecord]:
        return list(self._alerts.values())

    def should_issue_alert(
        self,
        record: AlertRecord,
        now: datetime,
    ) -> bool:
        record.reset_ignore_if_expired(now)
        if record.status == AlertStatus.IGNORED:
            return False
        intervals = (
            self._config.notification_policy.initial_intervals
            + [self._config.notification_policy.steady_state_interval]
        )
        return record.can_notify(now, intervals)

    def hysteresis_cleared(
        self,
        record: AlertRecord,
        margin_level: float,
    ) -> bool:
        threshold = self._config.thresholds.get(record.severity)
        if not threshold:
            return False
        return margin_level <= threshold.clear

    def serialize(self) -> Dict[str, Dict[str, str]]:
        """Return simplified view for status endpoint."""
        return {
            lp: {
                "severity": rec.severity.value,
                "status": rec.status.value,
                "marginLevel": f"{rec.marginLevel:.2f}",
                "traceId": rec.traceId,
                "threadId": rec.threadId or "",
                "firstTriggeredAt": rec.firstTriggeredAt.isoformat(),
                "lastUpdatedAt": rec.lastUpdatedAt.isoformat(),
                "ignoreUntil": rec.ignoreUntil.isoformat() if rec.ignoreUntil else "",
                "latestReport": rec.latestReport or "",
            }
            for lp, rec in self._alerts.items()
        }
