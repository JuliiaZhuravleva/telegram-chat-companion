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

import pytest

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


def _prior(issue: HealthIssue | None, *, age: float) -> dict[str, object]:
    return {
        "ts": time.time() - age,
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
    def test_all_clear_after_the_confirmation_window(self, checker):
        prior = _prior(_embedding_issue(), age=3600)
        healthy = _result()

        decisions = [checker._decide_alert(healthy, CONFIG, prior)[0] for _ in range(3)]

        # Not on the first two clean checks -- an intermittent trickle of
        # failures would otherwise alternate all-clear and alert every cycle.
        assert decisions == [None, None, "recovered"]

    def test_all_clear_names_what_cleared(self, checker):
        prior = _prior(_embedding_issue(47), age=3600)
        healthy = _result()

        for _ in range(2):
            checker._decide_alert(healthy, CONFIG, prior)
        decision, cleared = checker._decide_alert(healthy, CONFIG, prior)

        assert decision == "recovered"
        assert cleared == ["AI calls failed outright in last 15 min: embeddings x47"]

    def test_no_all_clear_when_nothing_was_alerted(self, checker):
        """Control: a bot that has been healthy all along says nothing.

        Without this, "recovered" could fire on a fresh install, which is the
        kind of message that teaches the reader to ignore the channel.
        """
        prior = _prior(None, age=3600)

        decisions = [checker._decide_alert(_result(), CONFIG, prior)[0] for _ in range(5)]

        assert decisions == [None] * 5

    def test_clean_streak_resets_when_the_problem_returns(self, checker):
        prior = _prior(_embedding_issue(), age=3600)

        checker._decide_alert(_result(), CONFIG, prior)
        checker._decide_alert(_result(), CONFIG, prior)
        checker._decide_alert(_result(_embedding_issue()), CONFIG, prior)  # back
        decision, _ = checker._decide_alert(_result(), CONFIG, prior)

        # The two clean checks before the relapse must not count towards the
        # window, or the all-clear arrives one check after a live outage.
        assert decision is None


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

        issue = next(i for i in result.issues if i.key == "ai_failure:embeddings")
        assert "holding since" in issue.message
        assert "22h 5m" in issue.message
        assert issue.detail is not None and "ai.studio" in issue.detail
        assert issue.hint is not None and "пополнить" in issue.hint


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
