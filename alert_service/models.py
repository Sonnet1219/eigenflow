"""Pydantic models and data structures for alert service."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from alert_service.config import AlertSeverity


class AlertStatus(str, Enum):
    """Lifecycle stages for an alert."""

    ACTIVE = "active"  # triggered and awaiting processing
    AWAITING_HITL = "awaiting_hitl"
    IGNORED = "ignored"
    RESOLVED = "resolved"
    AUTO_CLEARED = "auto_cleared"


class HumanAction(str, Enum):
    """Supported human interactions on alert cards."""

    RECHECK = "recheck"
    IGNORE = "ignore"
    COMMENT = "comment"


class HumanActionRequest(BaseModel):
    """Payload from Chat UI indicating user action."""

    traceId: str = Field(..., description="Alert trace identifier")
    threadId: Optional[str] = Field(None, description="LangGraph thread identifier")
    action: HumanAction = Field(..., description="Type of action performed")
    message: Optional[str] = Field(None, description="Optional user note")
    ignoreMinutes: Optional[int] = Field(
        default=None,
        description="Minutes to suppress further notifications when ignoring",
        ge=1,
    )

    def ignore_until(self, now: datetime) -> Optional[datetime]:
        if self.action != HumanAction.IGNORE or not self.ignoreMinutes:
            return None
        return now + timedelta(minutes=self.ignoreMinutes)


class AlertRecord(BaseModel):
    """Internal state record for an active alert."""

    lp: str
    severity: AlertSeverity
    status: AlertStatus = AlertStatus.ACTIVE
    marginLevel: float = 0.0
    traceId: str
    threadId: Optional[str] = None
    firstTriggeredAt: datetime
    lastNotifiedAt: Optional[datetime] = None
    notificationCount: int = 0
    ignoreUntil: Optional[datetime] = None
    lastUpdatedAt: datetime
    latestReport: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True

    def can_notify(self, now: datetime, intervals: list[int]) -> bool:
        if self.ignoreUntil and now < self.ignoreUntil:
            return False
        if not self.lastNotifiedAt:
            return True
        if self.notificationCount < len(intervals):
            required_gap = intervals[self.notificationCount]
        else:
            required_gap = intervals[-1]
        return (now - self.lastNotifiedAt).total_seconds() >= required_gap

    def mark_notified(self, now: datetime) -> None:
        self.lastNotifiedAt = now
        self.notificationCount += 1

    def mark_ignored(self, until: datetime) -> None:
        self.status = AlertStatus.IGNORED
        self.ignoreUntil = until

    def mark_resolved(self, *, auto: bool = False) -> None:
        self.status = AlertStatus.AUTO_CLEARED if auto else AlertStatus.RESOLVED
        self.ignoreUntil = None

    def mark_hitl(self) -> None:
        self.status = AlertStatus.AWAITING_HITL

    def reset_ignore_if_expired(self, now: datetime) -> None:
        if self.ignoreUntil and now >= self.ignoreUntil:
            self.ignoreUntil = None
            if self.status == AlertStatus.IGNORED:
                self.status = AlertStatus.ACTIVE
