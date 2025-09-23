"""Configuration models for the alert monitoring service."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


class AlertSeverity(str, Enum):
    """Severity levels for margin alerts."""

    WARN = "warn"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass(slots=True)
class Threshold:
    """Trigger and release thresholds for a given severity level."""

    trigger: float
    clear: float


@dataclass(slots=True)
class NotificationPolicy:
    """Defines notification pacing to prevent alert storms."""

    initial_intervals: List[int] = field(default_factory=lambda: [60, 60, 60, 300])
    steady_state_interval: int = 900  # 15 minutes


@dataclass(slots=True)
class MonitoringConfig:
    """Top level configuration for the monitoring service."""

    orchestrator_base_url: str = field(
        default_factory=lambda: os.getenv("ORCHESTRATOR_BASE_URL", "http://localhost:8001")
    )
    orchestrator_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("ORCHESTRATOR_TIMEOUT", "150"))
    )
    monitoring_interval: int = field(
        default_factory=lambda: int(os.getenv("ALERT_MONITOR_INTERVAL", "60"))
    )
    thresholds: Dict[AlertSeverity, Threshold] = field(
        default_factory=lambda: {
            AlertSeverity.WARN: Threshold(
                trigger=float(os.getenv("ALERT_WARN_TRIGGER", "15")),
                clear=float(os.getenv("ALERT_WARN_CLEAR", "10")),
            ),
            AlertSeverity.CRITICAL: Threshold(
                trigger=float(os.getenv("ALERT_CRITICAL_TRIGGER", "25")),
                clear=float(os.getenv("ALERT_CRITICAL_CLEAR", "20")),
            ),
            AlertSeverity.EMERGENCY: Threshold(
                trigger=float(os.getenv("ALERT_EMERGENCY_TRIGGER", "35")),
                clear=float(os.getenv("ALERT_EMERGENCY_CLEAR", "30")),
            ),
        }
    )
    notification_policy: NotificationPolicy = field(default_factory=NotificationPolicy)
    blowup_margin_level: float = field(
        default_factory=lambda: float(os.getenv("ALERT_BLOWUP_MARGIN_LEVEL", "5"))
    )
    blowup_suppression_seconds: int = field(
        default_factory=lambda: int(os.getenv("ALERT_BLOWUP_SUPPRESS", "300"))
    )
    max_concurrent_accounts: int = field(
        default_factory=lambda: int(os.getenv("ALERT_MAX_CONCURRENT_ACCOUNTS", "3"))
    )

    def determine_severity(self, margin_level: float) -> AlertSeverity | None:
        """Return highest severity whose trigger is satisfied (higher margin levels imply higher risk)."""

        for severity in (AlertSeverity.EMERGENCY, AlertSeverity.CRITICAL, AlertSeverity.WARN):
            threshold = self.thresholds[severity]
            if margin_level >= threshold.trigger:
                return severity
        return None

    def should_auto_clear(self, severity: AlertSeverity, margin_level: float) -> bool:
        """Check if margin level has fallen back below the clear threshold."""

        threshold = self.thresholds[severity]
        return margin_level <= threshold.clear
