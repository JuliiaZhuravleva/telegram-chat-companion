"""Alerting policy: say it once, say it usefully, say when it stops.

Written from a measured incident (2026-09-17). One unfixed condition -- a
Gemini project whose prepaid credits ran out -- produced **37 identical
Telegram messages in 18.5 hours**, because the old rule was "not healthy and
30 minutes since the last one". Every message was true and none was new.

Each test here names the production behaviour it forbids, because the failure
mode of an alerting change is silence, and a test that only asserts "no
message was sent" passes just as happily when nothing works at all.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import asyncpg
import pytest

from src.services.health import checker as checker_module
from src.services.health.checker import HealthChecker
from src.services.health.models import HealthCheckResult, HealthIssue, HealthStatus


@pytest.fixture
def pool():
    return AsyncMock()


@pytest.fixture
def bot():
    b = AsyncMock()
    b.send_message = AsyncMock()
    return b


@pytest.fixture
def checker(pool, bot):
    return HealthChecker(pool=pool, bot=bot)


class _Clock:
    """Fake wall clock, anchored near a real epoch so ages stay sane."""

    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    """Patch the clock the checker reads.

    Recovery is gated on ELAPSED TIME, not on a number of calls, so a test
    that loops instantly must move the clock or it is testing nothing.
    """
    c = _Clock()
    monkeypatch.setattr(checker_module.time, "time", c)
    return c


CONFIG = {
    "admin_ids": "12345",
    "health_alert_cooldown_seconds": 1800,
    "health_alert_reminder_seconds": 21600,
    "health_recovery_confirm_checks": 3,
}


def _embedding_issue(count: int = 47, *, detail: str | None = None) -> HealthIssue:
    """The issue from the real incident, whose count moves every cycle."""
    return HealthIssue(
        severity=HealthStatus.WARNING,
        message=f"AI calls failed outright in last 15 min: embeddings x{count}",
        key="ai_failure:embeddings",
        detail=detail,
    )


def _stored(issue: HealthIssue) -> dict[str, str]:
    row = {"severity": issue.severity.value, "message": issue.message}
    if issue.key:
        row["key"] = issue.key
    return row


def _result(*issues: HealthIssue) -> HealthCheckResult:
    status = HealthStatus.HEALTHY
    if any(i.severity == HealthStatus.CRITICAL for i in issues):
        status = HealthStatus.CRITICAL
    elif issues:
        status = HealthStatus.WARNING
    return HealthCheckResult(status=status, issues=list(issues))


def _prior(issue: HealthIssue | None, *, age: float, now: float | None = None) -> dict[str, object]:
    return {
        "ts": (now if now is not None else time.time()) - age,
        "status": "warning" if issue else "healthy",
        "issues": [_stored(issue)] if issue else [],
    }


class TestUnchangedCondition:
    def test_same_condition_is_not_re_alerted_after_the_old_cooldown(self, checker):
        """The exact production defect: 30 minutes passed, nothing changed.

        Under the old rule this returned "alert" and did so 37 times.
        """
        prior = _prior(_embedding_issue(47), age=1801)

        decision, _ = checker._decide_alert(_result(_embedding_issue(51)), CONFIG, prior)

        assert decision is None

    def test_count_changing_is_not_a_change(self, checker):
        """Counts must stay out of the fingerprint.

        If they were in it, the fingerprint would differ on every cycle -- the
        policy would alert every five minutes instead of every thirty, turning
        a fix into a regression.
        """
        prior = _prior(_embedding_issue(1), age=99999)
        result = _result(_embedding_issue(4123))

        decision, _ = checker._decide_alert(result, CONFIG, prior)

        # Only because the reminder is due, never because 1 became 4123.
        assert decision == "reminder"

    def test_reminder_fires_once_the_reminder_interval_passes(self, checker):
        prior = _prior(_embedding_issue(), age=21601)

        decision, _ = checker._decide_alert(_result(_embedding_issue()), CONFIG, prior)

        assert decision == "reminder"

    def test_reminder_does_not_fire_early(self, checker):
        """Control: the reminder must be the reason, not a side effect.

        Five hours in, with the condition unchanged, silence is correct. This
        is the case that would break if `changed` were computed wrongly -- and
        without it, `test_reminder_fires...` passes for a version that alerts
        on every cycle.
        """
        prior = _prior(_embedding_issue(), age=18000)

        decision, _ = checker._decide_alert(_result(_embedding_issue()), CONFIG, prior)

        assert decision is None


class TestChangedCondition:
    def test_a_different_task_failing_is_a_new_alert(self, checker):
        """Transcription breaking during an embedding outage must be heard.

        This is what per-task keys buy. With one merged issue for all tasks,
        this change would be invisible: the fingerprint would not move.
        """
        prior = _prior(_embedding_issue(), age=1801)
        transcription = HealthIssue(
            severity=HealthStatus.WARNING,
            message="AI calls failed outright in last 15 min: transcription x3",
            key="ai_failure:transcription",
        )

        decision, _ = checker._decide_alert(
            _result(_embedding_issue(), transcription), CONFIG, prior
        )

        assert decision == "new"

    def test_new_critical_bypasses_the_floor(self, checker):
        """A dead database does not wait half an hour to be mentioned."""
        prior = _prior(_embedding_issue(), age=30)  # well inside the floor
        db_down = HealthIssue(
            severity=HealthStatus.CRITICAL,
            message="Database connectivity failed: connection unavailable",
            key="db",
        )

        decision, _ = checker._decide_alert(_result(_embedding_issue(), db_down), CONFIG, prior)

        assert decision == "new"

    def test_known_critical_does_not_bypass_the_floor(self, checker):
        """Control for the carve-out above: it must apply to NEW criticals only.

        Otherwise "critical" becomes a licence to alert every five minutes,
        which is the original defect wearing a different severity.
        """
        db_down = HealthIssue(
            severity=HealthStatus.CRITICAL,
            message="Database connectivity failed: connection unavailable",
            key="db",
        )
        prior = _prior(db_down, age=30)

        decision, _ = checker._decide_alert(_result(db_down), CONFIG, prior)

        assert decision is None

    def test_first_ever_alert_is_sent(self, checker):
        decision, _ = checker._decide_alert(_result(_embedding_issue()), CONFIG, None)

        assert decision == "new"


class TestRecovery:
    def test_all_clear_after_the_window_has_actually_ELAPSED(self, checker, clock):
        """Three checks AND the quiet must have lasted confirm * interval.

        Counting calls alone is satisfiable in three seconds -- see the admin
        refresh test below, which is how it would happen in practice.
        """
        prior = _prior(_embedding_issue(), age=3600, now=clock.now)
        healthy = _result()

        instant = [checker._decide_alert(healthy, CONFIG, prior)[0] for _ in range(3)]
        clock.advance(3 * 300)
        after = checker._decide_alert(healthy, CONFIG, prior)[0]

        assert instant == [None, None, None]
        assert after == "recovered"

    def test_all_clear_names_what_cleared(self, checker, clock):
        prior = _prior(_embedding_issue(47), age=3600, now=clock.now)
        healthy = _result()

        checker._decide_alert(healthy, CONFIG, prior)
        clock.advance(3 * 300)
        for _ in range(2):
            checker._decide_alert(healthy, CONFIG, prior)
        decision, cleared = checker._decide_alert(healthy, CONFIG, prior)

        assert decision == "recovered"
        assert cleared == ["AI calls failed outright in last 15 min: embeddings x47"]

    def test_a_broken_check_is_not_a_clean_check(self, checker, clock):
        """The worst regression this change could introduce: a false all-clear.

        Every sub-check swallows its own exception and appends no issue, so a
        broken query renders exactly like perfect health. While the only
        consequence was silence that was survivable; with an all-clear message
        it becomes an active false claim in the middle of a live outage.
        """
        prior = _prior(_embedding_issue(), age=3600, now=clock.now)
        degraded = _result()
        degraded.checks_degraded = True

        decisions = []
        for _ in range(4):
            decisions.append(checker._decide_alert(degraded, CONFIG, prior)[0])
            clock.advance(300)

        assert decisions == [None, None, None, None]

    def test_the_admin_refresh_button_cannot_manufacture_an_all_clear(self, checker, clock):
        """`run_check_now` shares this method -- and its counter -- with the loop.

        Three taps a few seconds apart used to satisfy a window that is meant
        to mean fifteen minutes of evidence.
        """
        prior = _prior(_embedding_issue(), age=3600, now=clock.now)
        healthy = _result()

        taps = [checker._decide_alert(healthy, CONFIG, prior, manual=True)[0] for _ in range(5)]

        assert taps == [None] * 5
        # Control: the button must not poison the loop's evidence either --
        # the loop still needs its own full window afterwards.
        clock.advance(3 * 300)
        assert checker._decide_alert(healthy, CONFIG, prior)[0] is None

    def test_no_all_clear_when_nothing_was_alerted(self, checker, clock):
        """Control: a bot that has been healthy all along says nothing.

        Without this, "recovered" could fire on a fresh install, which is the
        kind of message that teaches the reader to ignore the channel.
        """
        prior = _prior(None, age=3600, now=clock.now)

        decisions = []
        for _ in range(5):
            decisions.append(checker._decide_alert(_result(), CONFIG, prior)[0])
            clock.advance(300)

        assert decisions == [None] * 5

    def test_clean_streak_resets_when_the_problem_returns(self, checker, clock):
        prior = _prior(_embedding_issue(), age=3600, now=clock.now)

        checker._decide_alert(_result(), CONFIG, prior)
        clock.advance(600)
        checker._decide_alert(_result(), CONFIG, prior)
        checker._decide_alert(_result(_embedding_issue()), CONFIG, prior)  # back
        clock.advance(600)
        decision, _ = checker._decide_alert(_result(), CONFIG, prior)

        # The quiet before the relapse must not count towards the window, or
        # the all-clear arrives one check after a live outage.
        assert decision is None

    def test_a_relapse_after_an_all_clear_is_not_gagged_by_the_floor(self, checker, clock):
        """The floor is for repetition, not for the first word about a relapse.

        A false all-clear followed by 30 minutes of enforced silence would be
        the worst of both worlds: wrong, then quiet about being wrong.
        """
        recovery_row = _prior(None, age=60, now=clock.now)  # an all-clear, one minute ago

        decision, _ = checker._decide_alert(_result(_embedding_issue()), CONFIG, recovery_row)

        assert decision == "new"


class TestTheCauseIsPartOfTheIdentity:
    """Same task, different root cause, different fix -- that is a change.

    Measured by a review: credits depleted at 12:00, topped up by 18:00, and
    the key revoked in the meantime. With a task-only key `changed` was False,
    so no new alert went out and the admin's newest message still advised
    topping up an account that was already fine.
    """

    def test_a_different_error_type_under_the_same_task_alerts(self, checker, clock):
        credits = HealthIssue(
            severity=HealthStatus.WARNING,
            message="AI calls failed outright in last 15 min: embeddings x47",
            key="ai_failure:embeddings:RateLimitError:top up",
        )
        revoked = HealthIssue(
            severity=HealthStatus.WARNING,
            message="AI calls failed outright in last 15 min: embeddings x12",
            key="ai_failure:embeddings:AIProviderError:reissue the key",
        )
        prior = _prior(credits, age=1801, now=clock.now)

        decision, _ = checker._decide_alert(_result(revoked), CONFIG, prior)

        assert decision == "new"

    @pytest.mark.asyncio
    async def test_the_key_is_BUILT_from_the_cause(self, checker, pool):
        """Asserted where the key is constructed, not on a hand-written one.

        The first version of the test above supplied its own keys, so dropping
        the cause from `_run_check`'s key expression left it green -- the
        fixture was a mirror of the behaviour instead of a check on it. Found
        by mutating that expression.
        """

        async def _key_for(error_type: str, message: str, count: int) -> str:
            pool.fetchval.side_effect = [1, 5, 0]
            pool.fetch.return_value = [
                {
                    "task_type": "embeddings",
                    "n": count,
                    "provider": "gemini",
                    "error_type": error_type,
                    "error_message": message,
                    "since": None,
                }
            ]
            result = await checker._run_check()
            return next(i.key for i in result.issues if i.key.startswith("ai_failure:embeddings"))

        depleted = await _key_for("RateLimitError", "Your prepayment credits are depleted.", 47)
        revoked = await _key_for("AIProviderError", "API key not valid.", 12)
        louder = await _key_for("RateLimitError", "Your prepayment credits are depleted.", 4123)

        assert depleted != revoked  # different cause, different fix, new alert
        assert depleted == louder  # same cause, louder count, still the same problem

    def test_the_same_cause_still_does_not_re_alert(self, checker, clock):
        """Control: the cause must not be a proxy for the count.

        If anything per-cycle leaked into the key, this would return "new"
        every five minutes -- the original defect, amplified.
        """
        issue = HealthIssue(
            severity=HealthStatus.WARNING,
            message="AI calls failed outright in last 15 min: embeddings x47",
            key="ai_failure:embeddings:RateLimitError:top up",
        )
        louder = HealthIssue(
            severity=HealthStatus.WARNING,
            message="AI calls failed outright in last 15 min: embeddings x4123",
            key="ai_failure:embeddings:RateLimitError:top up",
        )
        prior = _prior(issue, age=1801, now=clock.now)

        decision, _ = checker._decide_alert(_result(louder), CONFIG, prior)

        assert decision is None


class TestConfigIsNotTrusted:
    def test_a_garbage_interval_does_not_kill_the_decision(self, checker, clock):
        """These values come from a table a human edits by hand.

        An unparseable one used to raise inside the decision; the exception
        landed in the loop's catch-all, which also skips the healthcheck file
        write, so Docker restarted the container into the same broken config.
        """
        bad = dict(CONFIG, health_alert_cooldown_seconds="30m", health_alert_reminder_seconds=None)

        decision, _ = checker._decide_alert(_result(_embedding_issue()), bad, None)

        assert decision == "new"

    def test_zero_confirm_checks_cannot_disable_the_hysteresis(self, checker, clock):
        prior = _prior(_embedding_issue(), age=3600, now=clock.now)
        cfg = dict(CONFIG, health_recovery_confirm_checks=0)

        first = checker._decide_alert(_result(), cfg, prior)[0]

        assert first is None


class TestAlertContents:
    def test_provider_error_and_hint_reach_the_message(self):
        """What Julia asked for: the link, in the notification itself."""
        detail = (
            "gemini: Your prepayment credits are depleted. Please go to AI Studio "
            "at https://ai.studio/projects to manage your project and billing."
        )
        issue = _embedding_issue(detail=detail)
        issue.hint = "Кредиты провайдера кончились — нужно пополнить"

        text = HealthChecker._format_alert(_result(issue))

        assert "https://ai.studio/projects" in text
        assert "Кредиты провайдера кончились" in text
        # Never inside <code>: Telegram drops link entities in code spans, and
        # an unclickable URL is exactly what this change exists to avoid.
        assert "<code>" not in text

    def test_html_in_provider_text_is_escaped(self):
        issue = _embedding_issue(detail="<b>boom</b> & <script>alert(1)</script>")

        text = HealthChecker._format_alert(_result(issue))

        assert "<script>" not in text
        assert "&lt;script&gt;" in text
        # Control: the escaping must not also eat our own markup.
        assert "<b>Status:</b>" in text

    def test_reminder_is_marked_as_one(self):
        plain = HealthChecker._format_alert(_result(_embedding_issue()))
        reminder = HealthChecker._format_alert(_result(_embedding_issue()), reminder=True)

        assert "напоминание" not in plain
        assert "напоминание" in reminder

    def test_recovery_message_lists_what_cleared(self):
        text = HealthChecker._format_recovery(["embeddings were failing"])

        assert "Recovered" in text
        assert "embeddings were failing" in text


class TestLongProviderErrors:
    @pytest.mark.asyncio
    async def test_an_oversized_alert_is_split_not_dropped(self, checker, bot):
        """A rejected send is not a degraded alert -- it is no alert at all.

        Telegram's 4096 limit counts UTF-16 units of the parsed text, and this
        message now carries external provider text. The same trap silently ate
        four voice-note transcriptions in production.
        """
        issue = _embedding_issue(detail="я" * 6000)

        await checker._send_alert(_result(issue), CONFIG)

        assert bot.send_message.await_count >= 2
        for call in bot.send_message.await_args_list:
            assert len(call.args[1]) <= 4096

    @pytest.mark.asyncio
    async def test_ordinary_alert_is_one_message(self, checker, bot):
        """Control: splitting must not perturb the common path."""
        await checker._send_alert(_result(_embedding_issue()), CONFIG)

        assert bot.send_message.await_count == 1


class TestSinceFormatting:
    @pytest.mark.asyncio
    async def test_since_and_hint_are_built_from_the_row(self, checker, pool):
        pool.fetchval.side_effect = [1, 5, 0]
        pool.fetch.return_value = [
            {
                "task_type": "embeddings",
                "n": 47,
                "provider": "gemini",
                "error_type": "RateLimitError",
                "error_message": (
                    "Your prepayment credits are depleted. Please go to https://ai.studio/projects"
                ),
                "since": datetime.now(UTC) - timedelta(hours=22, minutes=5),
            }
        ]

        result = await checker._run_check()

        issue = next(i for i in result.issues if i.key.startswith("ai_failure:embeddings"))
        assert "holding since" in issue.message
        assert "22h 5m" in issue.message
        assert issue.detail is not None and "ai.studio" in issue.detail
        assert issue.hint is not None and "пополнить" in issue.hint


class TestDetailBounds:
    @pytest.mark.asyncio
    async def test_detail_is_bounded_even_from_a_chatty_provider(self, checker, pool):
        """The Gemini provider caps its own errors; nothing capped the rest.

        A 60 KB `error_message` (another provider, a proxy's HTML page) became
        sixteen sequential sends — meeting flood control halfway through an
        alert, which is how an alert becomes a partial alert.
        """
        pool.fetchval.side_effect = [1, 5, 0]
        pool.fetch.return_value = [
            {
                "task_type": "embeddings",
                "n": 3,
                "provider": "someprovider",
                "error_type": "RateLimitError",
                "error_message": "x" * 60000,
                "since": None,
            }
        ]

        result = await checker._run_check()

        issue = next(i for i in result.issues if i.key.startswith("ai_failure:embeddings"))
        assert issue.detail is not None
        assert len(issue.detail) < 500
        # Control: the text must still be there, not emptied by the cap.
        assert issue.detail.startswith("someprovider: xxx")


class TestAdminPanelAgrees:
    """The panel and the alert must tell the same story.

    The panel is what someone opens when they are not sure the alert they got
    an hour ago still describes reality; showing less there than the alert
    carried sends them back to the logs.
    """

    def test_panel_shows_the_provider_error_and_the_hint(self):
        from src.bot.handlers.admin import _format_health_status

        row = {
            "status": "warning",
            "checked_at": datetime(2026, 9, 17, 15, 46, tzinfo=UTC),
            "db_ok": True,
            "messages_30m": 17,
            "fallbacks_15m": 0,
            "issues": [
                {
                    "severity": "warning",
                    "message": "AI calls failed outright in last 15 min: embeddings x47",
                    "key": "ai_failure:embeddings",
                    "detail": "gemini: Your prepayment credits are depleted. https://ai.studio/projects",
                    "hint": "Кредиты провайдера кончились — нужно пополнить",
                }
            ],
        }

        text = _format_health_status(row, "ru")

        assert "https://ai.studio/projects" in text
        assert "Кредиты провайдера кончились" in text

    def test_panel_survives_rows_without_the_new_fields(self):
        """Control: rows written before this change must still render.

        `health_log` keeps 30 days, so old rows outlive the deploy.
        """
        from src.bot.handlers.admin import _format_health_status

        row = {
            "status": "warning",
            "checked_at": datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
            "db_ok": True,
            "messages_30m": 3,
            "fallbacks_15m": 1,
            "issues": [{"severity": "warning", "message": "old style issue"}],
        }

        text = _format_health_status(row, "ru")

        assert "old style issue" in text


class TestTheDecisionActuallyReachesTelegram:
    """`_decide_alert` and `_send_alert` were each tested alone.

    Nothing connected them, so deleting both `await self._send_alert(...)`
    calls from `_persist_result` left every test in this file green while the
    bot went permanently silent. That is the call-site class of defect: the
    helper is perfect and nobody calls it.
    """

    @pytest.mark.asyncio
    async def test_a_new_problem_is_sent_as_an_alert(self, checker, pool, bot, clock):
        pool.fetchrow.side_effect = [None, {"id": 1}]
        pool.fetchval.return_value = 0

        await checker._persist_result(_result(_embedding_issue()), CONFIG)

        bot.send_message.assert_awaited_once()
        assert "Bot Health Alert" in bot.send_message.await_args.args[1]

    @pytest.mark.asyncio
    async def test_an_all_clear_is_sent_with_the_recovery_wording(self, checker, pool, bot, clock):
        """And with the RECOVERY formatter — swapping the two must fail."""
        prior = _prior(_embedding_issue(), age=3600, now=clock.now)
        pool.fetchrow.side_effect = [prior, {"id": 1}] * 3
        pool.fetchval.return_value = 0

        # Three checks AND the clock: the all-clear needs both.
        for _ in range(2):
            await checker._persist_result(_result(), CONFIG)
            clock.advance(300)
        clock.advance(600)
        await checker._persist_result(_result(), CONFIG)

        bot.send_message.assert_awaited_once()
        sent = bot.send_message.await_args.args[1]
        assert "Recovered" in sent
        assert "Bot Health Alert" not in sent

    @pytest.mark.asyncio
    async def test_what_is_stored_is_what_the_fingerprint_reads(self, checker, pool, bot, clock):
        """The WRITE side of de-duplication had no test at all.

        Rename or drop `key` in `_issue_row` and production silently falls back
        to message-based fingerprints — which carry the per-cycle count, so
        `changed` is true every cycle and the alert fires every five minutes.
        That is the original defect amplified sixfold, with every other test
        still green.
        """
        pool.fetchrow.side_effect = [None, {"id": 1}]
        pool.fetchval.return_value = 0

        await checker._persist_result(_result(_embedding_issue()), CONFIG)

        stored = pool.fetchrow.await_args_list[1].args[6]
        assert '"key": "ai_failure:embeddings"' in stored
        # And the identity must survive the round trip through storage.
        import json

        assert checker_module._fingerprint_of_rows(json.loads(stored)) == {
            "warning:ai_failure:embeddings"
        }

    @pytest.mark.asyncio
    async def test_alert_sent_records_delivery_not_intention(self, checker, pool, bot, clock):
        """A swallowed send failure must not be recorded as "the admin knows".

        Recorded as sent, the de-duplicator waits for the 6-hour reminder while
        a live CRITICAL goes unmentioned — the channel reported its own failure
        to the logs only.
        """
        pool.fetchrow.side_effect = [None, {"id": 1}]
        pool.fetchval.return_value = 0
        bot.send_message.side_effect = RuntimeError("flood control")

        result = _result(_embedding_issue())
        await checker._persist_result(result, CONFIG)

        assert result.alert_sent is False
        assert pool.fetchrow.await_args_list[1].args[7] is False

    @pytest.mark.asyncio
    async def test_a_database_outage_still_gets_its_alert_out(self, checker, pool, bot, clock):
        """The one CRITICAL this checker detects is a dead database.

        Reading the previous alert hits that same database, so the issue was
        built and then discarded by the very failure it describes.
        """
        pool.fetchval.side_effect = asyncpg.PostgresError("down")
        pool.fetchrow.side_effect = asyncpg.PostgresError("down")

        result = await checker._run_check()
        await checker._persist_result(result, CONFIG)

        assert result.status == HealthStatus.CRITICAL
        bot.send_message.assert_awaited_once()
        assert "Database connectivity failed" in bot.send_message.await_args.args[1]


class TestStoredRowsFromOlderVersions:
    """`health_log` keeps 30 days, so rows written by the previous code
    outlive the deploy and are read back by the de-duplicator."""

    def test_a_row_without_a_key_still_compares(self, checker, clock):
        legacy = {
            "ts": clock.now - 21601,
            "status": "warning",
            "issues": [{"severity": "warning", "message": "AI calls failed outright: x1"}],
        }

        decision, _ = checker._decide_alert(_result(_embedding_issue()), CONFIG, legacy)

        # Different message -> treated as a change. One extra alert after the
        # deploy is the intended cost; a crash or silence would not be.
        assert decision == "new"

    def test_a_non_dict_row_does_not_crash_the_all_clear(self, checker, clock):
        """A mixed array (hand-inserted row, partial migration) used to raise
        `AttributeError` in the recovery branch — on every cycle, for ever,
        because the row that poisons it is also the row that stays "last"."""
        poisoned = {
            "ts": clock.now - 3600,
            "status": "warning",
            "issues": ["just a string", {"severity": "warning", "message": "real one"}],
        }

        for _ in range(2):
            checker._decide_alert(_result(), CONFIG, poisoned)
        clock.advance(3 * 300)
        decision, cleared = checker._decide_alert(_result(), CONFIG, poisoned)

        assert decision == "recovered"
        assert cleared == ["real one"]
