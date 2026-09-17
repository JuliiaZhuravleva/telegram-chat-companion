"""Periodic health checker — background task that monitors bot health."""

from __future__ import annotations

import asyncio
import contextlib
import html as html_lib
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import structlog
from aiogram import Bot

from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.health import HealthRepository
from src.services.health.diagnosis import hint_for
from src.services.health.models import HealthCheckResult, HealthIssue, HealthStatus
from src.utils import parse_admin_ids
from src.utils.telegram_text import split_html

logger = structlog.get_logger(__name__)


def _issue_row(issue: HealthIssue) -> dict[str, str]:
    """One issue as it is stored in `health_log.issues`.

    `key` is persisted because de-duplication reads the previous alert back out
    of this column: without it, a restart compares fingerprints built from
    messages and re-alerts once. Empty values are omitted rather than stored as
    nulls, so old rows and new ones read the same way.
    """
    row = {"severity": issue.severity.value, "message": issue.message}
    if issue.key:
        row["key"] = issue.key
    if issue.detail:
        row["detail"] = issue.detail
    if issue.hint:
        row["hint"] = issue.hint
    return row


def _fingerprint_of_rows(rows: list[dict[str, Any]]) -> set[str]:
    """Fingerprints of the issues a stored alert carried.

    Mirrors `HealthIssue.fingerprint`, including its fallback to the message:
    rows written before `key` existed still compare, they just compare less
    stably -- which costs at most one extra alert after the deploy.
    """
    fingerprints = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        severity = str(row.get("severity", ""))
        identity = str(row.get("key") or row.get("message", ""))
        fingerprints.add(f"{severity}:{identity}")
    return fingerprints


def _format_since(since: datetime) -> str:
    """Render how long a condition has been holding, compactly."""
    delta = datetime.now(UTC) - since
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes = remainder // 60
    elapsed = f"{hours}h {minutes}m" if hours else f"{minutes}m"
    return f"{since.strftime('%d.%m %H:%M UTC')} ({elapsed})"


_HEALTHCHECK_FILE = Path("/tmp/healthcheck")
_DEFAULT_INTERVAL = 300  # 5 minutes
# Floor between any two alerts. No longer the whole alerting policy: it used to
# be, and that is how one unfixed condition produced 37 identical messages in
# 18 hours (measured 2026-09-17). It now only bounds flapping -- see
# `_decide_alert` for the policy it became part of.
_DEFAULT_COOLDOWN = 1800  # 30 minutes
# Reminder for a condition that has not changed. Four a day instead of 48, so
# the channel stays worth reading: the real cost of repetition is not the noise
# but that the NEXT, different alert arrives in a channel already tuned out.
_DEFAULT_REMINDER = 21600  # 6 hours
# Consecutive clean checks before "recovered" is announced. Hysteresis, because
# the condition being watched is "any failure in the last 15 minutes" and a
# trickle of intermittent failures would otherwise alternate between alert and
# all-clear every cycle -- two messages where zero were warranted.
_DEFAULT_RECOVERY_CHECKS = 3
_INITIAL_DELAY = 10  # seconds before first check


class HealthChecker:
    """Background task that runs periodic health checks.

    Not managed by Dishka — lives for the entire bot process.
    Receives pool and bot directly from main().
    """

    def __init__(self, pool: asyncpg.Pool, bot: Bot) -> None:
        self._pool = pool
        self._bot = bot
        self._task: asyncio.Task[None] | None = None
        self._manual_lock = asyncio.Lock()
        # Consecutive checks with no issues. In-process on purpose: a restart
        # resetting it only postpones an all-clear by a few minutes, whereas
        # persisting it would need a column and buys nothing.
        self._clean_checks = 0

    async def start(self) -> None:
        """Start the health check background loop."""
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Health checker started")

    async def stop(self) -> None:
        """Stop the health check background loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Health checker stopped")

    async def run_check_now(self) -> None:
        """Trigger an immediate health check (admin refresh button)."""
        async with self._manual_lock:
            config = await self._load_config()
            result = await self._run_check()
            await self._persist_result(result, config)
            self._write_healthcheck_file()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Check health every N seconds."""
        await asyncio.sleep(_INITIAL_DELAY)

        while True:
            interval = _DEFAULT_INTERVAL
            try:
                config = await self._load_config()
                interval = int(config.get("health_check_interval_seconds", _DEFAULT_INTERVAL))

                if not config.get("health_check_enabled", True):
                    logger.debug("Health check disabled, skipping")
                    await asyncio.sleep(interval)
                    continue

                result = await self._run_check()
                await self._persist_result(result, config)
                self._write_healthcheck_file()

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Health check cycle failed")

            await asyncio.sleep(interval)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def _load_config(self) -> dict[str, Any]:
        """Load health-related config from bot_config table."""
        repo = BotConfigRepository(self._pool)
        config: dict[str, Any] = {}
        for key in (
            "health_check_enabled",
            "health_check_interval_seconds",
            "health_alert_cooldown_seconds",
            "health_alert_reminder_seconds",
            "health_recovery_confirm_checks",
            "admin_ids",
        ):
            val = await repo.get(key)
            if val is not None:
                config[key] = val
        return config

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    async def _run_check(self) -> HealthCheckResult:
        """Execute all health checks and build a result."""
        result = HealthCheckResult()
        health_repo = HealthRepository(self._pool)

        # 1. DB connectivity (implicit — if this fails, it's critical)
        try:
            await self._pool.fetchval("SELECT 1")
            result.db_ok = True
        except asyncpg.InterfaceError:
            result.db_ok = False
            logger.error("Health check DB failed: connection interface error", exc_info=True)
            result.issues.append(
                HealthIssue(
                    severity=HealthStatus.CRITICAL,
                    message="Database connectivity failed: connection unavailable",
                    key="db",
                )
            )
        except asyncpg.PostgresError:
            result.db_ok = False
            logger.error("Health check DB failed: PostgreSQL error", exc_info=True)
            result.issues.append(
                HealthIssue(
                    severity=HealthStatus.CRITICAL,
                    message="Database connectivity failed: query error",
                    key="db",
                )
            )
        except Exception:
            result.db_ok = False
            logger.error("Health check DB failed: unexpected error", exc_info=True)
            result.issues.append(
                HealthIssue(
                    severity=HealthStatus.CRITICAL,
                    message="Database connectivity failed: unexpected error",
                    key="db",
                )
            )

        # 2. Message activity (metric only — no alert on inactivity)
        try:
            result.messages_30m = await health_repo.get_message_count_30m()
        except Exception as exc:
            logger.warning("Failed to get message count", error_type=type(exc).__name__)

        # 3. AI fallback frequency
        try:
            result.fallbacks_15m = await health_repo.get_fallback_count(
                timedelta(minutes=15),
            )
            if result.fallbacks_15m > 0:
                result.issues.append(
                    HealthIssue(
                        severity=HealthStatus.WARNING,
                        message=(
                            f"AI fallback activated {result.fallbacks_15m} time(s) in last 15 min"
                        ),
                        key="ai_fallback",
                    )
                )
        except Exception as exc:
            logger.warning("Failed to get fallback count", error_type=type(exc).__name__)

        # 4. AI calls that failed outright (migration 031)
        #
        # This is the check the five-day video-note outage needed and did not
        # have: `_log_usage` writes only on success, so failures never reach
        # `response_log`, and check 3 above reads that table -- it can only
        # ever see a fallback that already worked. A terminal failure means
        # every provider for the task was exhausted and the user got nothing.
        try:
            failures = await health_repo.get_ai_failure_details(timedelta(minutes=15))
            for failure in failures:
                task = failure["task_type"]
                detail = failure.get("error_message")
                provider = failure.get("provider")
                since = failure.get("since")
                # One issue per task, not one line listing every task: the
                # provider's error and the advice differ per task, and a single
                # merged line has nowhere to put either. It also gives each
                # task its own de-duplication key, so transcription breaking
                # while embeddings are already broken is a NEW alert rather
                # than a silent change inside an existing one.
                result.issues.append(
                    HealthIssue(
                        severity=HealthStatus.WARNING,
                        message=(
                            f"AI calls failed outright in last 15 min: "
                            f"{task} x{failure['count']}"
                            + (f" ({provider})" if provider else "")
                            + (f", holding since {_format_since(since)}" if since else "")
                        ),
                        key=f"ai_failure:{task}",
                        detail=f"{provider}: {detail}" if provider and detail else detail,
                        hint=hint_for(detail),
                    )
                )
        except Exception as exc:
            logger.warning("Failed to get AI failure counts", error_type=type(exc).__name__)

        # Determine overall status
        if any(i.severity == HealthStatus.CRITICAL for i in result.issues):
            result.status = HealthStatus.CRITICAL
        elif any(i.severity == HealthStatus.WARNING for i in result.issues):
            result.status = HealthStatus.WARNING
        else:
            result.status = HealthStatus.HEALTHY

        return result

    # ------------------------------------------------------------------
    # Persist & alert
    # ------------------------------------------------------------------

    async def _persist_result(
        self,
        result: HealthCheckResult,
        config: dict[str, Any],
    ) -> None:
        """Save result to DB and send an alert, a reminder or an all-clear."""
        health_repo = HealthRepository(self._pool)

        last_alert = await health_repo.get_last_alert()
        decision, cleared = self._decide_alert(result, config, last_alert)
        should_alert = decision is not None

        result.alert_sent = should_alert

        log_id = await health_repo.insert_log(
            status=result.status.value,
            db_ok=result.db_ok,
            messages_30m=result.messages_30m,
            fallbacks_15m=result.fallbacks_15m,
            ai_provider=result.ai_provider,
            issues=[_issue_row(i) for i in result.issues],
            alert_sent=should_alert,
        )

        if decision == "recovered":
            await self._send_alert(result, config, text=self._format_recovery(cleared))
        elif decision is not None:
            await self._send_alert(
                result,
                config,
                text=self._format_alert(result, reminder=decision == "reminder"),
            )

        logger.info(
            "Health check completed",
            log_id=log_id,
            status=result.status.value,
            issues=len(result.issues),
            alert_sent=should_alert,
            alert_reason=decision,
            clean_checks=self._clean_checks,
            messages_30m=result.messages_30m,
            fallbacks_15m=result.fallbacks_15m,
        )

        # Periodic cleanup (lightweight — runs every cycle)
        try:
            deleted = await health_repo.cleanup_old_logs(keep_days=30)
            if deleted:
                logger.debug("Cleaned up old health logs", deleted=deleted)
        except Exception as exc:
            logger.warning("Failed to clean up health logs", error_type=type(exc).__name__)

    def _decide_alert(
        self,
        result: HealthCheckResult,
        config: dict[str, Any],
        last_alert: dict[str, Any] | None,
    ) -> tuple[str | None, list[str]]:
        """Decide whether to say anything. Returns (reason, cleared-messages).

        Reason is "new" (the set of conditions changed), "reminder" (unchanged
        but old), "recovered" (all clear) or None (stay quiet).

        The rule this replaces was "anything non-healthy, at most every 30
        minutes", which cannot tell a week-old unfixed condition from a fresh
        one and therefore repeated both at the same rate -- 37 identical
        messages in 18 hours, measured. What matters for an alert is a CHANGE,
        so the comparison is against the conditions the last alert carried.
        """
        cooldown = int(config.get("health_alert_cooldown_seconds", _DEFAULT_COOLDOWN))
        reminder = int(config.get("health_alert_reminder_seconds", _DEFAULT_REMINDER))
        confirm = int(config.get("health_recovery_confirm_checks", _DEFAULT_RECOVERY_CHECKS))

        previous_rows: list[dict[str, Any]] = last_alert["issues"] if last_alert else []
        previous = _fingerprint_of_rows(previous_rows)
        current = {i.fingerprint() for i in result.issues}
        age = None if last_alert is None else time.time() - last_alert["ts"]

        if not result.issues:
            self._clean_checks += 1
            # Announce recovery only if the last thing the admin heard was a
            # problem, and only after `confirm` consecutive clean checks.
            if previous and self._clean_checks >= confirm:
                cleared = [str(row.get("message", "")) for row in previous_rows]
                return "recovered", [c for c in cleared if c]
            return None, []

        self._clean_checks = 0

        changed = current != previous
        is_critical = any(i.severity == HealthStatus.CRITICAL for i in result.issues)
        new_critical = is_critical and not current <= previous

        if new_critical:
            # A CRITICAL condition that was not in the last alert bypasses the
            # floor. The floor exists to bound flapping, and making the
            # database-is-down message wait up to half an hour to avoid noise
            # is the wrong side of that trade.
            return "new", []

        if age is not None and age < cooldown:
            return None, []

        if changed:
            return "new", []
        if age is None or age >= reminder:
            return "reminder", []
        return None, []

    async def _send_alert(
        self,
        result: HealthCheckResult,
        config: dict[str, Any],
        *,
        text: str | None = None,
    ) -> None:
        """Send alert to first admin via Telegram."""
        admin_ids = parse_admin_ids(config.get("admin_ids", ""))
        if not admin_ids:
            return

        first_admin = admin_ids[0]
        body = text if text is not None else self._format_alert(result)

        try:
            # Through `split_html`, because this message now carries a
            # provider's error text, which is external and unbounded. A body
            # over the limit is not a truncated alert -- it is NO alert, and
            # the one time that matters is while something is already broken.
            for piece in split_html(body):
                await self._bot.send_message(
                    first_admin,
                    piece,
                    parse_mode="HTML",
                )
        except Exception as exc:
            # The one that matters most: this is the channel that reports the bot
            # is unhealthy, and the likeliest cause of it failing (rate limit
            # under load) is exactly when the alert is most needed.
            logger.warning(
                "Failed to send health alert",
                admin_id=first_admin,
                error_type=type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_recovery(cleared: list[str]) -> str:
        """Format the all-clear, naming what stopped failing."""
        lines = [
            "\u2705 <b>Bot Health Recovered</b>",
            f"<b>Time:</b> {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        if cleared:
            lines.append("")
            lines.append("<b>Cleared:</b>")
            lines.extend(f"  \u2714\ufe0f {html_lib.escape(item)}" for item in cleared)
        return "\n".join(lines)

    @staticmethod
    def _format_alert(result: HealthCheckResult, *, reminder: bool = False) -> str:
        """Format HTML alert message for Telegram."""
        status_emoji = {
            HealthStatus.CRITICAL: "\U0001f6a8",
            HealthStatus.WARNING: "\u26a0\ufe0f",
            HealthStatus.HEALTHY: "\u2705",
        }
        emoji = status_emoji.get(result.status, "\u2753")
        title = "Bot Health Alert" + (
            " \u2014 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435"
            if reminder
            else ""
        )

        lines = [
            f"{emoji} <b>{title}</b>",
            f"<b>Status:</b> {result.status.value.upper()}",
            f"<b>Time:</b> {result.checked_at.strftime('%Y-%m-%d %H:%M UTC')}",
        ]

        if result.issues:
            lines.append("")
            lines.append("<b>Issues:</b>")
            for issue in result.issues:
                icon = "\U0001f525" if issue.severity == HealthStatus.CRITICAL else "\u26a0\ufe0f"
                lines.append(f"  {icon} {html_lib.escape(issue.message)}")
                if issue.detail:
                    # Escaped but NOT wrapped in <code>: Telegram drops link
                    # entities inside code spans, and a billing URL that is not
                    # clickable is the detail this whole change exists to
                    # deliver. A bare URL in plain text is auto-linked.
                    lines.append(f"     {html_lib.escape(issue.detail)}")
                if issue.hint:
                    lines.append(f"     \U0001f4a1 {html_lib.escape(issue.hint)}")

        lines.append("")
        lines.append(
            f"<i>Messages (30m): {result.messages_30m}"
            f" | Fallbacks (15m): {result.fallbacks_15m}</i>",
        )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Docker healthcheck file
    # ------------------------------------------------------------------

    @staticmethod
    def _write_healthcheck_file() -> None:
        """Write timestamp to healthcheck file for Docker."""
        try:
            _HEALTHCHECK_FILE.write_text(str(time.time()))
        except OSError:
            logger.warning("Failed to write healthcheck file")
