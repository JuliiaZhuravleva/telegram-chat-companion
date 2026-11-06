"""Periodic health checker — background task that monitors bot health."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import asyncpg
import structlog
from aiogram import Bot

from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.health import HealthRepository
from src.services.health.diagnosis import hint_for
from src.services.health.models import HealthCheckResult, HealthIssue, HealthStatus
from src.utils import parse_admin_ids
from src.utils.telegram_text import split_html

if TYPE_CHECKING:
    from src.bot.screens.i18n import Lang
    from src.bot.screens.model import Screen

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


def _issue_text(issue: HealthIssue, lang: Lang, *, at: datetime) -> str:
    """The problem line for the card, from the issue's facts; its message otherwise."""
    from src.bot.screens import cards

    if isinstance(issue, DbIssue):
        return cards.db_down_line(lang, issue.cause)
    if isinstance(issue, FallbackIssue):
        return cards.fallback_line(lang, issue.count)
    if isinstance(issue, AIFailureIssue):
        return cards.failure_line(
            lang,
            task=issue.task,
            count=issue.count,
            provider=issue.provider,
            since=issue.since,
            at=at,
        )
    # A plain HealthIssue carries no facts to say again, only its text.
    return issue.message


def _admin_lang(config: dict[str, Any]) -> Lang:
    """The admins' panel language from the loaded ``admin_settings``; Russian if unreadable."""
    from src.bot.screens.cards import admin_lang

    return admin_lang(config.get("admin_settings"))


def _format_since(since: datetime) -> str:
    """Render how long a condition has been holding, compactly."""
    delta = datetime.now(UTC) - since
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes = remainder // 60
    elapsed = f"{hours}h {minutes}m" if hours else f"{minutes}m"
    return f"{since.strftime('%d.%m %H:%M UTC')} ({elapsed})"


# ---------------------------------------------------------------------------
# The issues `_run_check` raises
# ---------------------------------------------------------------------------
#
# Each carries the FACTS behind its message, so the card can say it in the
# admin's language. The English `message` is unchanged and is still what
# `health_log` stores and what a keyless fingerprint would fall back to; every
# issue below has a key, so its fingerprint never reads the message at all.

_DB_MESSAGES: Mapping[str, str] = {
    "connection": "Database connectivity failed: connection unavailable",
    "query": "Database connectivity failed: query error",
    "unexpected": "Database connectivity failed: unexpected error",
}


@dataclass
class DbIssue(HealthIssue):
    """The database did not answer ``SELECT 1``."""

    cause: str = "unexpected"  # connection / query / unexpected


@dataclass
class FallbackIssue(HealthIssue):
    """The fallback provider answered in the window."""

    count: int = 0


@dataclass
class AIFailureIssue(HealthIssue):
    """Every provider for one task failed in the window."""

    task: str = ""
    count: int = 0
    provider: str | None = None
    since: datetime | None = None


def db_issue(cause: str) -> DbIssue:
    """The CRITICAL database issue for ``cause`` (connection / query / unexpected)."""
    return DbIssue(
        severity=HealthStatus.CRITICAL,
        message=_DB_MESSAGES.get(cause, _DB_MESSAGES["unexpected"]),
        key="db",
        cause=cause,
    )


def fallback_issue(count: int) -> FallbackIssue:
    """The fallback provider answered ``count`` times in the last 15 minutes."""
    return FallbackIssue(
        severity=HealthStatus.WARNING,
        message=f"AI fallback activated {count} time(s) in last 15 min",
        key="ai_fallback",
        count=count,
    )


def ai_failure_issue(failure: Mapping[str, Any]) -> AIFailureIssue:
    """One row of `HealthRepository.get_ai_failure_details` as an issue."""
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
    return AIFailureIssue(
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
            part for part in ("ai_failure", task, failure.get("error_type"), hint) if part
        ),
        # Capped here as well as in the Gemini provider: this
        # is where the value becomes a Telegram message, and
        # the provider's own cap protects only its own errors.
        # A 60 KB `error_message` from anywhere else would
        # become sixteen sequential sends and meet flood
        # control halfway through an alert.
        detail=_clip(body),
        hint=hint,
        task=task,
        count=int(failure["count"]),
        provider=provider,
        since=since,
    )


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
            "admin_settings",
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
            result.issues.append(db_issue("connection"))
        except asyncpg.PostgresError:
            result.db_ok = False
            logger.error("Health check DB failed: PostgreSQL error", exc_info=True)
            result.issues.append(db_issue("query"))
        except Exception:
            result.db_ok = False
            logger.error("Health check DB failed: unexpected error", exc_info=True)
            result.issues.append(db_issue("unexpected"))

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
                result.issues.append(fallback_issue(result.fallbacks_15m))
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
            result.issues.extend(ai_failure_issue(failure) for failure in failures)
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
        lang = _admin_lang(config)
        if decision == "recovered":
            delivered = await self._send_alert(
                result, config, card=self._recovery_card(cleared, lang=lang)
            )
        elif decision is not None:
            delivered = await self._send_alert(
                result,
                config,
                card=self._alert_card(result, lang=lang, reminder=decision == "reminder"),
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
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Decide whether to say anything. Returns (reason, cleared rows).

        The cleared rows are the previous alert's stored issues, so the
        all-clear can name each one by its key in the admin's language.

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
                    row
                    for row in previous_rows
                    # a legacy or hand-written row may not be a dict
                    if isinstance(row, dict) and (row.get("message") or row.get("key"))
                ]
                return "recovered", cleared
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
        card: Screen | None = None,
    ) -> bool:
        """Send the card to every bot admin. True when at least one admin got all of it.

        The return value is what `alert_sent` is recorded from: this channel
        can fail (flood control, a network blip) exactly when it is most
        needed, and a de-duplicator told "delivered" by a failed send goes
        quiet for hours about a live problem. One admin is enough to count as
        told; an admin who blocked the bot must not hold the others hostage.
        """
        admin_ids = parse_admin_ids(config.get("admin_ids", ""))
        if not admin_ids:
            # At error level, not silence: no admin configured means every
            # alert this process ever raises goes nowhere, and the only
            # symptom is the absence of messages.
            logger.error("Health alert not sent: no admin_ids configured")
            return False

        if card is None:
            card = self._alert_card(result, lang=_admin_lang(config))

        # Imported here: the screen package imports render, and render
        # imports the reply path, which imports services.
        from src.bot.screens.render import render_text, to_markup

        # Through `split_html`, because this message carries a provider's
        # error text, which is external and unbounded. A body over the limit
        # is not a truncated alert -- it is NO alert, and the one time that
        # matters is while something is already broken. The button goes on
        # the last piece, under the text it refers to.
        pieces = split_html(render_text(card))
        markup = to_markup(card)
        delivered = False
        for admin_id in admin_ids:
            try:
                for index, piece in enumerate(pieces):
                    last = index == len(pieces) - 1
                    await self._bot.send_message(
                        admin_id,
                        piece,
                        parse_mode="HTML",
                        reply_markup=markup if last else None,
                    )
            except Exception as exc:
                # The likeliest cause (rate limit under load) is exactly when
                # the alert is most needed; the next admin is still tried.
                logger.warning(
                    "Failed to send health alert",
                    admin_id=admin_id,
                    error_type=type(exc).__name__,
                )
                continue
            delivered = True
        return delivered

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------
    #
    # The whole card is in the admin's language: the frame, and each problem
    # line said from its facts (`cards.*_line`). What is stored and compared
    # is untouched -- `health_log` keeps the English message, and every issue
    # `_run_check` raises has a key, so the fingerprint never reads the text.
    # The provider's own error (`detail`) is shown as it came.

    @staticmethod
    def _recovery_card(
        cleared: Sequence[Mapping[str, Any]], *, lang: Lang = "ru", at: datetime | None = None
    ) -> Screen:
        """The all-clear, naming what stopped failing."""
        from src.bot.screens import cards

        lines = [
            cards.cleared_line(
                lang, key=str(row.get("key") or ""), message=str(row.get("message") or "")
            )
            for row in cleared
        ]
        # Two cleared issues of one kind (the db issue has one key for three
        # causes) say the same line once.
        unique = list(dict.fromkeys(line for line in lines if line))
        return cards.recovered_card(lang, at=at or datetime.now(UTC), cleared=unique)

    @staticmethod
    def _alert_card(
        result: HealthCheckResult, *, lang: Lang = "ru", reminder: bool = False
    ) -> Screen:
        """The health card: what is wrong, since when, and the 💡 line."""
        from src.bot.screens import cards

        issues = [
            cards.IssueLine(
                critical=issue.severity == HealthStatus.CRITICAL,
                message=_issue_text(issue, lang, at=result.checked_at),
                detail=issue.detail,
                hint=cards.hint_line(lang, issue.hint),
            )
            for issue in result.issues
        ]
        return cards.health_card(
            lang,
            at=result.checked_at,
            critical=result.status == HealthStatus.CRITICAL,
            issues=issues,
            messages_30m=result.messages_30m,
            fallbacks_15m=result.fallbacks_15m,
            reminder=reminder,
        )

    @classmethod
    def _format_recovery(cls, cleared: Sequence[Mapping[str, Any]], *, lang: Lang = "ru") -> str:
        """The all-clear as the HTML text that is sent."""
        from src.bot.screens.render import render_text

        return render_text(cls._recovery_card(cleared, lang=lang))

    @classmethod
    def _format_alert(
        cls, result: HealthCheckResult, *, reminder: bool = False, lang: Lang = "ru"
    ) -> str:
        """The health card as the HTML text that is sent."""
        from src.bot.screens.render import render_text

        return render_text(cls._alert_card(result, lang=lang, reminder=reminder))

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
