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
    # Stable identity of the CONDITION, deliberately free of counts, times and
    # provider text. It is what alert de-duplication compares, and the reason
    # it cannot be the message: "failed outright ... embeddings x47" changes
    # every single cycle while nothing about the situation has changed. A
    # fingerprint built from messages would therefore re-alert every five
    # minutes -- worse than the 30-minute repetition it is meant to replace.
    key: str = ""
    # The provider's own words, carried to the admin so the notification says
    # what happened rather than only that something did. May contain a URL;
    # never wrap it in <code>, which makes Telegram drop the link.
    detail: str | None = None
    # Our reading of `detail` -- what to do about it. Optional by design: an
    # unrecognised error shows its raw text and no advice, rather than advice
    # invented to fill the slot.
    hint: str | None = None

    def fingerprint(self) -> str:
        """Identity used for de-duplication; falls back to the message."""
        return f"{self.severity.value}:{self.key or self.message}"


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
    # True when a sub-check could not run at all. Every sub-check swallows its
    # own exception and appends no issue, which renders "the query is broken"
    # identically to "nothing is wrong" -- harmless while silence was the only
    # consequence, actively false once an all-clear message exists. Whoever
    # adds a check must set this in its `except`, or a broken check will vote
    # for health.
    checks_degraded: bool = False
