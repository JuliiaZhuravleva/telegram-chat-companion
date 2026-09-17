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

# Longest provider error text the alert will carry, per issue.
_MAX_DETAIL_CHARS = 400


def _clip(text: str | None, limit: int = _MAX_DETAIL_CHARS) -> str | None:
    """Bound external text before it becomes a Telegram message."""
    if not text:
        return None
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "\u2026"


def _positive_int(config: dict[str, Any], key: str, default: int, *, minimum: int = 0) -> int:
    """Read an int out of `bot_config`, tolerating whatever is actually stored.

    These values come from a key-value table a human edits. An unparseable one
    used to raise inside the alert decision, and the exception landed in
    `_run_loop`'s catch-all -- which skips the healthcheck file write, so after
    ten minutes Docker restarts the container into the same broken config.
    A typo in a tuning knob should not be able to do that, and it certainly
    should not be able to switch alerting off silently.
    """
    raw = config.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring unusable health config value", key=key, fallback=default)
        return default
    return max(value, minimum)


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
        # Consecutive checks with no issues, and when that run started.
        # In-process on purpose: a restart resetting them only postpones an
        # all-clear, whereas persisting them would need a column and buys
        # nothing. The timestamp is what makes the window mean elapsed time
        # rather than a number of calls.
        self._clean_checks = 0
        self._clean_since: float | None = None
        # Last alert this process delivered, used only when the database read
        # fails -- which is precisely the case where the database itself is
        # the thing being reported.
        self._last_alert_fallback: dict[str, Any] | None = None

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
            await self._persist_result(result, config, manual=True)
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

                # Same lock as the refresh button: without it a press landing
                # mid-cycle decides against the same previous alert and both
                # send. The button already serialises against itself.
                async with self._manual_lock:
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
            result.checks_degraded = True
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
            result.checks_degraded = True
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
                hint = hint_for(detail)
                body = f"{provider}: {detail}" if provider and detail else detail
                result.issues.append(
                    HealthIssue(
                        severity=HealthStatus.WARNING,
                        message=(
                            f"AI calls failed outright in last 15 min: "
                            f"{task} x{failure['count']}"
                            + (f" ({provider})" if provider else "")
                            + (f", holding since {_format_since(since)}" if since else "")
                        ),
                        # The CAUSE is part of the identity, not just the task.
                        # With the task alone, credits running out and then the
                        # key being revoked six hours later are "the same
                        # problem": no new alert, and the notification still
                        # advises topping up an account that is now fine. The
                        # two components chosen are stable per cycle -- unlike
                        # the count, which is why the message cannot be used.
                        key=":".join(
                            part
                            for part in ("ai_failure", task, failure.get("error_type"), hint)
                            if part
                        ),
                        # Capped here as well as in the Gemini provider: this
                        # is where the value becomes a Telegram message, and
                        # the provider's own cap protects only its own errors.
                        # A 60 KB `error_message` from anywhere else would
                        # become sixteen sequential sends and meet flood
                        # control halfway through an alert.
                        detail=_clip(body),
                        hint=hint,
                    )
                )
        except Exception as exc:
            result.checks_degraded = True
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
        *,
        manual: bool = False,
    ) -> None:
        """Save result to DB and send an alert, a reminder or an all-clear.

        `manual` marks the admin refresh button, which shares this method with
        the timed loop but must not contribute evidence to it -- see
        `_decide_alert`.
        """
        health_repo = HealthRepository(self._pool)

        # Reading the previous alert must not be able to stop the current one.
        # The one CRITICAL this checker knows how to detect is "the database is
        # unreachable", and this read is against that same database -- so the
        # issue would be built and then thrown away by the failure it describes.
        # Falling back to the in-process copy keeps de-duplication working; a
        # cold process falls back to None, which errs towards sending.
        try:
            last_alert = await health_repo.get_last_alert()
        except Exception as exc:
            logger.warning(
                "Could not read the previous alert; using in-process state",
                error_type=type(exc).__name__,
            )
            last_alert = self._last_alert_fallback

        decision, cleared = self._decide_alert(result, config, last_alert, manual=manual)

        # Sent BEFORE the bookkeeping, and `alert_sent` reflects what actually
        # went out. The other order records intent: a swallowed send failure
        # (flood control, network) would tell the de-duplicator the admin had
        # been informed, and a live CRITICAL would then wait for the 6-hour
        # reminder. An unrecorded send is the safe direction -- it can repeat;
        # an unsent record cannot be recovered from.
        delivered = False
        if decision == "recovered":
            delivered = await self._send_alert(result, config, text=self._format_recovery(cleared))
        elif decision is not None:
            delivered = await self._send_alert(
                result,
                config,
                text=self._format_alert(result, reminder=decision == "reminder"),
            )

        result.alert_sent = delivered
        if delivered:
            self._last_alert_fallback = {
                "ts": time.time(),
                "status": result.status.value,
                "issues": [_issue_row(i) for i in result.issues],
            }

        log_id = None
        try:
            log_id = await health_repo.insert_log(
                status=result.status.value,
                db_ok=result.db_ok,
                messages_30m=result.messages_30m,
                fallbacks_15m=result.fallbacks_15m,
                ai_provider=result.ai_provider,
                issues=[_issue_row(i) for i in result.issues],
                alert_sent=delivered,
            )
        except Exception as exc:
            # The row is bookkeeping; the message was the point and is already
            # out. Swallowed so a write failure cannot also skip the healthcheck
            # file write in `_run_loop`, which would restart the container.
            logger.warning(
                "Failed to record the health check",
                error_type=type(exc).__name__,
            )

        logger.info(
            "Health check completed",
            log_id=log_id,
            status=result.status.value,
            issues=len(result.issues),
            alert_sent=delivered,
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
        *,
        manual: bool = False,
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
        cooldown = _positive_int(config, "health_alert_cooldown_seconds", _DEFAULT_COOLDOWN)
        reminder = _positive_int(config, "health_alert_reminder_seconds", _DEFAULT_REMINDER)
        confirm = _positive_int(
            config, "health_recovery_confirm_checks", _DEFAULT_RECOVERY_CHECKS, minimum=1
        )
        interval = _positive_int(config, "health_check_interval_seconds", _DEFAULT_INTERVAL)

        previous_rows: list[dict[str, Any]] = last_alert["issues"] if last_alert else []
        previous = _fingerprint_of_rows(previous_rows)
        current = {i.fingerprint() for i in result.issues}
        now = time.time()
        age = None if last_alert is None else now - last_alert["ts"]

        if not result.issues:
            if result.checks_degraded:
                # A check that COULD NOT RUN is not a check that found nothing.
                # Every sub-check swallows its own exception and appends no
                # issue, so a broken query renders as perfect health -- and
                # with an all-clear message in the picture that silence became
                # an active false claim: three failed queries in a row would
                # announce "Recovered" in the middle of a live outage.
                logger.warning("Health check degraded; not counting it as clean")
                return None, []
            if manual:
                # The admin refresh button shares this method with the timed
                # loop. Counting its presses as evidence let three taps a few
                # seconds apart satisfy a window that is supposed to mean
                # fifteen minutes of quiet.
                return None, []
            if self._clean_since is None:
                self._clean_since = now
            self._clean_checks += 1
            # Recovery needs the quiet to have LASTED, measured on the clock
            # and not in calls: the condition being watched is "any failure in
            # the last 15 minutes", so a few quick calls can all see the same
            # empty window.
            held_for = now - self._clean_since
            if previous and self._clean_checks >= confirm and held_for >= confirm * interval:
                cleared = [
                    str(row.get("message", ""))
                    for row in previous_rows
                    if isinstance(row, dict)  # a legacy or hand-written row may not be
                ]
                return "recovered", [c for c in cleared if c]
            return None, []

        self._clean_checks = 0
        self._clean_since = None

        changed = current != previous
        is_critical = any(i.severity == HealthStatus.CRITICAL for i in result.issues)
        new_critical = is_critical and not current <= previous

        if new_critical:
            # A CRITICAL condition that was not in the last alert bypasses the
            # floor. The floor exists to bound flapping, and making the
            # database-is-down message wait up to half an hour to avoid noise
            # is the wrong side of that trade.
            return "new", []

        # The floor applies to REPETITION, so it does not gag the first word
        # about a fresh incident. Without this carve-out an all-clear followed
        # by a relapse stayed silent for half an hour, and a false all-clear
        # (see `checks_degraded` above) would suppress the real re-alert on top
        # of having lied. Flapping is still bounded, because getting back to an
        # all-clear now costs `confirm * interval` of measured quiet.
        if previous and age is not None and age < cooldown:
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
    ) -> bool:
        """Send alert to first admin via Telegram. True when it went out.

        The return value is what `alert_sent` is recorded from: this channel
        can fail (flood control, a network blip) exactly when it is most
        needed, and a de-duplicator told "delivered" by a failed send goes
        quiet for hours about a live problem.
        """
        admin_ids = parse_admin_ids(config.get("admin_ids", ""))
        if not admin_ids:
            # At error level, not silence: no admin configured means every
            # alert this process ever raises goes nowhere, and the only
            # symptom is the absence of messages.
            logger.error("Health alert not sent: no admin_ids configured")
            return False

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
            return False
        return True

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
