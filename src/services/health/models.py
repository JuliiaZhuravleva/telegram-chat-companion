"""Data models for health monitoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class HealthStatus(StrEnum):
    """Health check status levels."""

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    SKIPPED = "skipped"


@dataclass
class HealthIssue:
    """A single issue detected during a health check."""

    severity: HealthStatus  # WARNING or CRITICAL
    message: str


@dataclass
class HealthCheckResult:
    """Complete result of a health check cycle."""

    status: HealthStatus = HealthStatus.HEALTHY
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    db_ok: bool = True
    messages_30m: int = 0
    fallbacks_15m: int = 0
    ai_provider: str | None = None
    issues: list[HealthIssue] = field(default_factory=list)
    alert_sent: bool = False
