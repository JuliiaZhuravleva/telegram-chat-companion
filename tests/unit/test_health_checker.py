"""Tests for HealthChecker service."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.bot.screens import cards
from src.services.health import diagnosis
from src.services.health.checker import (
    HealthChecker,
    ai_failure_issue,
    db_issue,
    fallback_issue,
)
from src.services.health.models import HealthCheckResult, HealthIssue, HealthStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pool():
    """Mocked asyncpg pool."""
    return AsyncMock()


@pytest.fixture
def bot():
    """Mocked aiogram Bot."""
    b = AsyncMock()
    b.send_message = AsyncMock()
    return b


@pytest.fixture
def checker(pool, bot):
    """HealthChecker instance with mocked deps."""
    return HealthChecker(pool=pool, bot=bot)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _failure_row(task: str, count: int, *, error: str | None = None, provider="gemini"):
    """A row as `get_ai_failure_details` returns it."""
    return {
        "task_type": task,
        "n": count,
        "provider": provider,
        "error_type": "RateLimitError",
        "error_message": error,
        "since": datetime.now(UTC) - timedelta(hours=2),
    }


def _last_alert(*, age: float, issues=None):
    """A row as `get_last_alert` returns it."""
    return {
        "ts": time.time() - age,
        "status": "warning",
        "issues": issues if issues is not None else [],
    }


# ---------------------------------------------------------------------------
# _run_check tests
# ---------------------------------------------------------------------------


class TestRunCheck:
    @pytest.mark.asyncio
    async def test_healthy_when_all_checks_pass(self, checker, pool):
        pool.fetchval.side_effect = [
            1,  # SELECT 1 (db check)
            0,  # message count 30m
            0,  # fallback count 15m
        ]

        result = await checker._run_check()

        assert result.status == HealthStatus.HEALTHY
        assert result.db_ok is True
        assert result.messages_30m == 0
        assert result.fallbacks_15m == 0
        assert len(result.issues) == 0

    @pytest.mark.asyncio
    async def test_critical_when_db_fails(self, checker, pool):
        pool.fetchval.side_effect = ConnectionError("connection refused")

        result = await checker._run_check()

        assert result.status == HealthStatus.CRITICAL
        assert result.db_ok is False
        assert any(
            i.severity == HealthStatus.CRITICAL and "Database" in i.message for i in result.issues
        )

    @pytest.mark.asyncio
    async def test_warning_when_fallbacks_detected(self, checker, pool):
        pool.fetchval.side_effect = [
            1,  # SELECT 1
            5,  # message count
            3,  # fallback count > 0
        ]

        result = await checker._run_check()

        assert result.status == HealthStatus.WARNING
        assert result.fallbacks_15m == 3
        assert any(
            i.severity == HealthStatus.WARNING and "fallback" in i.message.lower()
            for i in result.issues
        )


# ---------------------------------------------------------------------------
# _format_alert tests
# ---------------------------------------------------------------------------


class TestFormatAlert:
    def test_critical_alert_format(self):
        result = HealthCheckResult(
            status=HealthStatus.CRITICAL,
            checked_at=datetime(2026, 2, 10, 15, 30, tzinfo=UTC),
            db_ok=False,
            messages_30m=12,
            fallbacks_15m=3,
            issues=[db_issue("connection"), fallback_issue(3)],
        )

        text = HealthChecker._format_alert(result)

        assert text.startswith("🔥 <b>Сбой бота</b> · 15:30 UTC")
        assert "🔥 База данных недоступна: нет соединения" in text
        assert "⚠️ Запасной ИИ включался 3 раза за 15 минут" in text
        assert "За 30 минут сообщений: 12, запасной ИИ за 15 минут: 3" in text

    def test_the_whole_card_is_in_the_admin_language(self):
        result = HealthCheckResult(
            status=HealthStatus.CRITICAL,
            checked_at=datetime(2026, 2, 10, 15, 30, tzinfo=UTC),
            messages_30m=12,
            issues=[db_issue("query"), fallback_issue(1)],
        )

        text = HealthChecker._format_alert(result, lang="en")

        assert text.startswith("🔥 <b>The bot is failing</b>")
        assert "Messages in 30 min: 12" in text
        assert "The database is unreachable: queries fail" in text
        assert "The fallback AI stepped in 1 time in 15 min" in text

    def test_a_plain_issue_without_facts_shows_its_message(self):
        """Only `_run_check`'s own issues carry facts; anything else keeps its text."""
        result = HealthCheckResult(
            status=HealthStatus.CRITICAL,
            issues=[HealthIssue(severity=HealthStatus.CRITICAL, message="Database down")],
        )

        assert "Database down" in HealthChecker._format_alert(result, lang="en")

    def test_warning_alert_format(self):
        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            checked_at=datetime(2026, 2, 10, 15, 30, tzinfo=UTC),
            fallbacks_15m=1,
            issues=[
                HealthIssue(
                    severity=HealthStatus.WARNING,
                    message="AI fallback activated 1 time(s) in last 15 min",
                ),
            ],
        )

        text = HealthChecker._format_alert(result)

        assert text.startswith("⚠️ <b>Перебои в работе бота</b>")


# ---------------------------------------------------------------------------
# Alert cooldown tests
# ---------------------------------------------------------------------------


class TestPersistResult:
    @pytest.mark.asyncio
    async def test_alert_sent_after_cooldown(self, checker, pool, bot):
        """Alert should be sent if cooldown has expired."""
        # Mock health repo methods
        pool.fetchrow.side_effect = [
            _last_alert(age=3600),  # last alert 1h ago, about something else
            {"id": 1},  # insert_log
        ]
        pool.fetchval.side_effect = [
            0,  # cleanup_old_logs
        ]

        config = {"admin_ids": "12345", "health_alert_cooldown_seconds": 1800}
        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[
                HealthIssue(severity=HealthStatus.WARNING, message="test issue"),
            ],
        )

        await checker._persist_result(result, config)

        assert result.alert_sent is True
        bot.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_alert_not_sent_during_cooldown(self, checker, pool, bot):
        """Alert should NOT be sent if within cooldown period."""
        pool.fetchrow.side_effect = [
            # About the SAME issue, one minute ago. The floor deliberately
            # applies only to repetition, so a prior alert about something
            # else (or an all-clear) would not suppress this one.
            _last_alert(age=60, issues=[{"severity": "warning", "message": "test issue"}]),
            {"id": 2},  # insert_log
        ]
        pool.fetchval.side_effect = [
            0,  # cleanup_old_logs
        ]

        config = {"admin_ids": "12345", "health_alert_cooldown_seconds": 1800}
        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[
                HealthIssue(severity=HealthStatus.WARNING, message="test issue"),
            ],
        )

        await checker._persist_result(result, config)

        assert result.alert_sent is False
        bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_alert_when_healthy(self, checker, pool, bot):
        """No alert for healthy status (no issues)."""
        pool.fetchrow.side_effect = [None, {"id": 3}]  # no prior alert, insert_log
        pool.fetchval.return_value = 0

        config = {"admin_ids": "12345"}
        result = HealthCheckResult(status=HealthStatus.HEALTHY, issues=[])

        await checker._persist_result(result, config)

        assert result.alert_sent is False
        bot.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Alert target tests
# ---------------------------------------------------------------------------


class TestSendAlert:
    @pytest.mark.asyncio
    async def test_sends_to_every_admin(self, checker, bot):
        """Every bot admin gets the card, with the status button under it."""
        config = {"admin_ids": "111,222,333", "admin_settings": {"lang": "en"}}
        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[
                HealthIssue(severity=HealthStatus.WARNING, message="test"),
            ],
        )

        assert await checker._send_alert(result, config) is True

        assert [c.args[0] for c in bot.send_message.await_args_list] == [111, 222, 333]
        markup = bot.send_message.await_args.kwargs["reply_markup"]
        assert [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row] == [
            ("📊 Status", "adm_health:en")
        ]

    @pytest.mark.asyncio
    async def test_one_admin_failing_does_not_silence_the_others(self, checker, bot):
        config = {"admin_ids": "111,222"}
        bot.send_message.side_effect = [RuntimeError("blocked"), None]
        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[HealthIssue(severity=HealthStatus.WARNING, message="test")],
        )

        assert await checker._send_alert(result, config) is True
        assert [c.args[0] for c in bot.send_message.await_args_list] == [111, 222]

    @pytest.mark.asyncio
    async def test_nobody_reached_is_not_delivered(self, checker, bot):
        config = {"admin_ids": "111,222"}
        bot.send_message.side_effect = RuntimeError("flood control")
        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[HealthIssue(severity=HealthStatus.WARNING, message="test")],
        )

        assert await checker._send_alert(result, config) is False

    @pytest.mark.asyncio
    async def test_handles_empty_admin_ids(self, checker, bot):
        """No crash when admin_ids is empty."""
        config = {"admin_ids": ""}

        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[HealthIssue(severity=HealthStatus.WARNING, message="test")],
        )

        await checker._send_alert(result, config)

        bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_admin_ids_as_list(self, checker, bot):
        """admin_ids can be a JSON list from bot_config."""
        config = {"admin_ids": [111, 222]}

        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[HealthIssue(severity=HealthStatus.WARNING, message="test")],
        )

        await checker._send_alert(result, config)

        assert [c.args[0] for c in bot.send_message.await_args_list] == [111, 222]


# ---------------------------------------------------------------------------
# Healthcheck file
# ---------------------------------------------------------------------------


class TestHealthcheckFile:
    def test_writes_timestamp(self, checker, tmp_path):
        """Healthcheck file should contain a recent timestamp."""
        hc_file = tmp_path / "healthcheck"

        with patch(
            "src.services.health.checker._HEALTHCHECK_FILE",
            hc_file,
        ):
            checker._write_healthcheck_file()

        content = hc_file.read_text()
        ts = float(content)
        assert abs(time.time() - ts) < 5


# ---------------------------------------------------------------------------
# Health check disabled
# ---------------------------------------------------------------------------


class TestDisabledCheck:
    @pytest.mark.asyncio
    async def test_load_config_reads_keys(self, checker, pool):
        """Config loading should read expected keys."""
        pool.fetchrow.return_value = None  # no config found

        config = await checker._load_config()

        assert isinstance(config, dict)


class TestAIFailureCheck:
    """The check the five-day video-note outage needed and did not have.

    `_log_usage` writes only on success, so a failed AI call never reached
    `response_log` -- and the fallback check above reads that table, so it can
    only ever see a fallback that already worked. Nothing was watching the
    case where every provider was exhausted and the user got nothing.
    """

    @pytest.mark.asyncio
    async def test_warning_names_the_task_that_failed(self, checker, pool):
        pool.fetchval.side_effect = [1, 5, 0]  # db ok, messages, no fallbacks
        pool.fetch.return_value = [
            _failure_row("transcription", 5),
            _failure_row("vision", 1),
        ]

        result = await checker._run_check()

        assert result.status == HealthStatus.WARNING
        failure_issues = [i for i in result.issues if "failed outright" in i.message]
        # One issue per task, not one merged line: each task carries its own
        # provider error, its own advice and its own de-duplication key.
        assert len(failure_issues) == 2
        # Naming the task is the point: "transcription is down" and "the
        # provider is down" are different incidents with different responses.
        messages = " | ".join(i.message for i in failure_issues)
        assert "transcription x5" in messages
        assert "vision x1" in messages
        # The key carries task AND cause (see `_run_check`), so assert the
        # task prefix rather than pinning the whole composite.
        keys = sorted(i.key for i in failure_issues)
        assert keys[0].startswith("ai_failure:transcription")
        assert keys[1].startswith("ai_failure:vision")

    @pytest.mark.asyncio
    async def test_no_failures_raises_no_issue(self, checker, pool):
        pool.fetchval.side_effect = [1, 5, 0]
        pool.fetch.return_value = []

        result = await checker._run_check()

        assert result.status == HealthStatus.HEALTHY
        assert not any("failed outright" in i.message for i in result.issues)

    @pytest.mark.asyncio
    async def test_query_failure_does_not_break_the_whole_check(self, checker, pool):
        """A broken sub-check must not cost the checks around it."""
        pool.fetchval.side_effect = [1, 5, 0]
        pool.fetch.side_effect = RuntimeError("relation does not exist")

        result = await checker._run_check()

        assert result.db_ok is True
        assert result.status == HealthStatus.HEALTHY


class TestTheCardFromTheRealCheck:
    """The card an admin reads is built from what `_run_check` raised -- here
    through the real check and the real `diagnosis` hints, not hand-made issues.

    The defect this pins: the frame was translated, the problem lines were the
    checker's English and the 💡 hints `diagnosis`'s Russian, so neither
    language got a card in its own language.
    """

    CREDITS = "Your prepayment credits are depleted. See https://ai.studio/projects"
    PER_DAY = "Quota exceeded for metric generate_content requests per day"

    @pytest.fixture(autouse=True)
    def _a_bad_cycle(self, pool):
        pool.fetchval.side_effect = [1, 5, 2]  # db ok, messages, two fallbacks
        pool.fetch.return_value = [
            _failure_row("transcription", 5, error=self.CREDITS),
            _failure_row("embeddings", 3, error=self.PER_DAY),
        ]

    @pytest.mark.asyncio
    async def test_russian_card_has_no_checker_english(self, pool, bot):
        result = await HealthChecker(pool=pool, bot=bot)._run_check()

        text = HealthChecker._format_alert(result, lang="ru")

        for english in ("AI calls failed", "AI fallback activated", "holding since", "time(s)"):
            assert english not in text, english
        assert "Вызовы ИИ не прошли за 15 минут: расшифровка ×5 (gemini), с " in text
        assert "(2 ч 0 мин)" in text
        assert "Запасной ИИ включался 2 раза за 15 минут" in text
        assert "💡 Кредиты провайдера кончились" in text
        # The provider's own words are shown as they came.
        assert self.CREDITS.split(" See")[0] in text

    @pytest.mark.asyncio
    async def test_english_card_has_no_cyrillic(self, pool, bot):
        result = await HealthChecker(pool=pool, bot=bot)._run_check()

        text = HealthChecker._format_alert(result, lang="en")

        assert re.search("[А-Яа-яЁё]", text) is None, text
        assert "AI calls failed outright in 15 min: transcription ×5 (gemini), since " in text
        assert "(2h 0m)" in text
        assert "💡 Provider credits have run out" in text
        assert "https://ai.studio/projects" in text

    @pytest.mark.asyncio
    async def test_what_is_stored_and_compared_did_not_change(self, pool, bot):
        """Rewording the card must not reword `health_log` or the fingerprint,
        or every open problem alerts once more after the deploy."""
        result = await HealthChecker(pool=pool, bot=bot)._run_check()

        by_task = {i.key.split(":")[1]: i for i in result.issues if i.key.startswith("ai_failure")}
        transcription = by_task["transcription"]
        assert transcription.message.startswith(
            "AI calls failed outright in last 15 min: transcription x5 (gemini), holding since "
        )
        assert transcription.fingerprint() == (
            "warning:ai_failure:transcription:RateLimitError:" + diagnosis.hint_for(self.CREDITS)
        )
        fallback = next(i for i in result.issues if i.key == "ai_fallback")
        assert fallback.message == "AI fallback activated 2 time(s) in last 15 min"
        assert fallback.fingerprint() == "warning:ai_fallback"
        assert db_issue("query").message == "Database connectivity failed: query error"
        assert db_issue("query").fingerprint() == "critical:db"

    def test_every_diagnosis_hint_has_an_english_line(self):
        for _pattern, hint in diagnosis._HINTS:
            english = cards.hint_line("en", hint)
            assert english and re.search("[А-Яа-яЁё]", english) is None, hint
            assert cards.hint_line("ru", hint) == hint

    def test_a_failure_row_without_since_or_provider(self):
        issue = ai_failure_issue(
            {"task_type": "vision", "count": 1, "provider": None, "error_message": None}
        )
        result = HealthCheckResult(status=HealthStatus.WARNING, issues=[issue])

        assert "⚠️ Вызовы ИИ не прошли за 15 минут: картинки ×1\n" in (
            HealthChecker._format_alert(result) + "\n"
        )
