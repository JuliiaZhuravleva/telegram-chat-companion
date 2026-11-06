"""Health problems reach the admin in the admin's language -- on the card AND on
the status screen -- while what is stored and compared stays word for word.

The defect: the alert card said each problem from its facts, but the status
screen (``status.bad``) printed the row ``health_log`` keeps -- the checker's
English message and ``diagnosis``'s Russian advice -- so a Russian admin read
English there and an English admin read Russian. The screen now says the line
at render time (``cards.stored_issue_line`` / ``cards.hint_line``). Storage
cannot move to fix it: de-duplication compares keys from that row, and a
keyless row's fingerprint is its message, so rewording either re-alerts every
open problem once.

Every kind the checker raises is covered: the database (three causes), the
fallback provider, and an outright AI failure for each of ``diagnosis``'s
hints, without a hint, and without provider or start time.
"""

from __future__ import annotations

import copy
import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.bot.screens import cards
from src.bot.screens.render import render_text
from src.bot.screens.status import status_screen
from src.services.health import diagnosis
from src.services.health.checker import (
    HealthChecker,
    _issue_row,
    _issue_text,
    ai_failure_issue,
    db_issue,
    fallback_issue,
)
from src.services.health.models import HealthCheckResult, HealthIssue, HealthStatus

CYRILLIC = re.compile("[А-Яа-яЁё]")
CHECKER_ENGLISH = (
    "Database connectivity failed",
    "AI fallback activated",
    "AI calls failed outright",
    "holding since",
    "time(s)",
)

# One provider error per ``diagnosis`` hint. The first test checks the set is
# complete, so a hint added there without an entry here fails loudly.
PROVIDER_ERRORS = (
    "429 RESOURCE_EXHAUSTED. Your prepayment credits are depleted.",
    "400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key.",
    "403 PERMISSION_DENIED. The caller does not have permission",
    "429 Quota exceeded for metric generate_content requests per day",
    "429 Quota exceeded for metric generate_content requests per minute",
    "429 insufficient_quota: You exceeded your current quota, check your billing",
)
UNRECOGNISED_ERROR = "500 INTERNAL. An internal error has occurred."
TASKS = ("embeddings", "transcription", "vision", "text_generation", "embeddings", "vision")


def _failure(task: str, count: int, **extra: Any) -> dict[str, Any]:
    """A row as ``HealthRepository.get_ai_failure_details`` returns it."""
    row: dict[str, Any] = {
        "task_type": task,
        "count": count,
        "provider": "gemini",
        "error_type": "RateLimitError",
        "error_message": None,
        "since": None,
    }
    row.update(extra)
    return row


def _every_kind(base: datetime | None = None) -> list[HealthIssue]:
    """Every issue ``_run_check`` can raise, through the real constructors.

    ``since`` is 2 h 5 min 30 s before ``base``: the checker measures it against
    the real clock when it builds the message, the card against ``checked_at``,
    and the half minute keeps both on the same minute. Two cycles of one test
    pass the same ``base`` so their messages are the same text.
    """
    base = base or datetime.now(UTC)
    since = base - timedelta(hours=2, minutes=5, seconds=30)
    issues: list[HealthIssue] = [db_issue(c) for c in ("connection", "query", "unexpected")]
    issues += [fallback_issue(1), fallback_issue(4)]
    issues += [
        ai_failure_issue(_failure(task, 12, error_message=error, since=since))
        for task, error in zip(TASKS, PROVIDER_ERRORS, strict=True)
    ]
    issues.append(ai_failure_issue(_failure("transcription", 2, error_message=UNRECOGNISED_ERROR)))
    issues.append(ai_failure_issue(_failure("vision", 1, provider=None, error_message=None)))
    # Since but no provider, and minutes only.
    recent = base - timedelta(minutes=7, seconds=30)
    issues.append(ai_failure_issue(_failure("embeddings", 3, provider=None, since=recent)))
    return issues


def _result(issues: list[HealthIssue]) -> HealthCheckResult:
    critical = any(i.severity == HealthStatus.CRITICAL for i in issues)
    return HealthCheckResult(
        status=HealthStatus.CRITICAL if critical else HealthStatus.WARNING,
        issues=issues,
        db_ok=not critical,
        messages_30m=5,
        fallbacks_15m=4,
        checked_at=datetime.now(UTC),
    )


def _stored(result: HealthCheckResult) -> dict[str, Any]:
    """The ``health_log`` row as the status screen gets it: issues as JSON text."""
    return {
        "status": result.status.value,
        "checked_at": result.checked_at,
        "db_ok": result.db_ok,
        "messages_30m": result.messages_30m,
        "fallbacks_15m": result.fallbacks_15m,
        "issues": json.dumps([_issue_row(i) for i in result.issues]),
    }


def test_every_diagnosis_hint_is_covered_here():
    assert {diagnosis.hint_for(e) for e in PROVIDER_ERRORS} == {h for _, h in diagnosis._HINTS}
    assert diagnosis.hint_for(UNRECOGNISED_ERROR) is None


# ---------------------------------------------------------------------------
# status.bad
# ---------------------------------------------------------------------------


class TestTheStatusScreen:
    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_each_stored_issue_reads_as_the_card_says_it(self, lang):
        """The line from the stored row is the card's line, word for word."""
        result = _result(_every_kind())
        rows = json.loads(_stored(result)["issues"])

        for issue, row in zip(result.issues, rows, strict=True):
            card_line = _issue_text(issue, lang, at=result.checked_at)
            assert card_line != issue.message, issue.message  # the card does translate it
            assert cards.stored_issue_line(lang, row) == card_line, issue.message
            if issue.hint:
                assert cards.hint_line(lang, row["hint"]) == cards.hint_line(lang, issue.hint)

    def test_russian_status_has_no_checker_english(self):
        screen = status_screen("ru", _stored(_result(_every_kind())), now=datetime.now(UTC))
        text = render_text(screen)

        assert screen.id == "status.bad"
        for english in CHECKER_ENGLISH:
            assert english not in text, english
        assert "🔥 База данных недоступна: нет соединения" in text
        assert "Запасной ИИ включался 1 раз за 15 минут" in text
        assert "Запасной ИИ включался 4 раза за 15 минут" in text
        assert "⚠️ Вызовы ИИ не прошли за 15 минут: расшифровка ×12 (gemini), с " in text
        assert "(2 ч 5 мин)" in text
        assert "⚠️ Вызовы ИИ не прошли за 15 минут: картинки ×1\n" in text
        assert ", с " in text and "(7 мин)" in text
        for _pattern, hint in diagnosis._HINTS:
            assert f"💡 {hint}" in text, hint
        # The provider's own words are shown as they came.
        assert "Your prepayment credits are depleted." in text

    def test_english_status_has_no_cyrillic(self):
        screen = status_screen("en", _stored(_result(_every_kind())), now=datetime.now(UTC))
        text = render_text(screen)

        assert screen.id == "status.bad"
        assert CYRILLIC.search(text) is None, text
        assert "🔥 The database is unreachable: no connection" in text
        assert "The fallback AI stepped in 1 time in 15 min" in text
        assert "The fallback AI stepped in 4 times in 15 min" in text
        assert "AI calls failed outright in 15 min: transcription ×12 (gemini), since " in text
        assert "(2h 5m)" in text
        assert "AI calls failed outright in 15 min: images ×1\n" in text
        assert "(7m)" in text
        for _pattern, hint in diagnosis._HINTS:
            assert f"💡 {cards.hint_line('en', hint)}" in text, hint

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_a_kind_the_screen_does_not_know_shows_its_stored_text(self, lang):
        rows = [
            {"severity": "warning", "message": "Disk <95%> full"},  # keyless
            {"severity": "warning", "key": "disk", "message": "Disk is slow"},
            # A known key whose message has been reworded: shown as stored,
            # never a guess at its numbers.
            {"severity": "warning", "key": "ai_fallback", "message": "Fallback used a lot"},
            {"severity": "critical", "key": "db", "message": "Database is on fire"},
            {"severity": "warning", "key": "ai_failure:x", "message": "AI calls failed: x"},
        ]
        for row in rows:
            assert cards.stored_issue_line(lang, row) == row["message"]

        row = {"status": "warning", "checked_at": datetime.now(UTC), "issues": rows}
        text = render_text(status_screen(lang, row, now=datetime.now(UTC)))
        assert "⚠️ Disk &lt;95%&gt; full" in text
        assert "⚠️ Fallback used a lot" in text
        assert "🔥 Database is on fire" in text

    def test_a_hint_diagnosis_does_not_know_is_shown_as_stored(self):
        row = {
            "status": "warning",
            "issues": [{"severity": "warning", "message": "x", "hint": "Do <this>"}],
        }
        assert "💡 Do &lt;this&gt;" in render_text(status_screen("en", row))


# ---------------------------------------------------------------------------
# The alert card, every kind
# ---------------------------------------------------------------------------


class TestTheAlertCard:
    def test_russian_card_has_no_checker_english(self):
        text = HealthChecker._format_alert(_result(_every_kind()), lang="ru")

        for english in CHECKER_ENGLISH:
            assert english not in text, english
        assert "База данных недоступна: запросы не выполняются" in text
        assert "Запасной ИИ включался 4 раза за 15 минут" in text
        for _pattern, hint in diagnosis._HINTS:
            assert f"💡 {hint}" in text, hint

    def test_english_card_has_no_cyrillic(self):
        text = HealthChecker._format_alert(_result(_every_kind()), lang="en")

        assert CYRILLIC.search(text) is None, text
        assert "The database is unreachable: queries fail" in text
        assert "The fallback AI stepped in 4 times in 15 min" in text
        for _pattern, hint in diagnosis._HINTS:
            assert f"💡 {cards.hint_line('en', hint)}" in text, hint

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_screen_and_the_card_say_the_same_lines(self, lang):
        result = _result(_every_kind())
        card = HealthChecker._format_alert(result, lang=lang)
        status = render_text(status_screen(lang, _stored(result), now=result.checked_at))

        for issue in result.issues:
            line = _issue_text(issue, lang, at=result.checked_at)
            assert line in card, line
            assert line in status, line


# ---------------------------------------------------------------------------
# What is stored and compared did not move
# ---------------------------------------------------------------------------


class TestStorageAndDeduplicationAreUntouched:
    def test_the_stored_rows_word_for_word(self):
        credits = diagnosis.hint_for(PROVIDER_ERRORS[0])
        since = datetime(2026, 1, 15, 9, 50, tzinfo=UTC)
        issue = ai_failure_issue(_failure("embeddings", 12, error_message=PROVIDER_ERRORS[0]))

        assert _issue_row(db_issue("connection")) == {
            "severity": "critical",
            "message": "Database connectivity failed: connection unavailable",
            "key": "db",
        }
        assert _issue_row(db_issue("query"))["message"] == (
            "Database connectivity failed: query error"
        )
        assert _issue_row(db_issue("unexpected"))["message"] == (
            "Database connectivity failed: unexpected error"
        )
        assert _issue_row(fallback_issue(4)) == {
            "severity": "warning",
            "message": "AI fallback activated 4 time(s) in last 15 min",
            "key": "ai_fallback",
        }
        assert _issue_row(issue) == {
            "severity": "warning",
            "message": "AI calls failed outright in last 15 min: embeddings x12 (gemini)",
            "key": f"ai_failure:embeddings:RateLimitError:{credits}",
            "detail": f"gemini: {PROVIDER_ERRORS[0]}",
            "hint": credits,  # diagnosis's Russian, in every language
        }
        held = ai_failure_issue(_failure("vision", 1, since=since)).message
        assert held.startswith(
            "AI calls failed outright in last 15 min: vision x1 (gemini), "
            "holding since 15.01 09:50 UTC ("
        )

    @pytest.mark.asyncio
    async def test_a_repeated_check_stays_quiet_and_rendering_changes_nothing(self):
        """The real ``_persist_result`` over the real repository: the first
        cycle alerts and stores; the same issues read back from that stored
        JSON do not alert again; showing the row in either language leaves the
        row as it was."""
        pool, bot = AsyncMock(), AsyncMock()
        checker = HealthChecker(pool=pool, bot=bot)
        config = {"admin_ids": "12345", "admin_settings": json.dumps({"lang": "en"})}

        pool.fetchrow.side_effect = [None, {"id": 1}]  # no previous alert; insert_log
        pool.fetchval.return_value = 0  # cleanup_old_logs
        base = datetime.now(UTC)
        first = _result(_every_kind(base))
        await checker._persist_result(first, config)

        assert first.alert_sent is True
        sent = bot.send_message.await_args_list[0].args[1]
        assert CYRILLIC.search(sent) is None  # the English admin got an English card
        stored_json = pool.fetchrow.await_args_list[1].args[6]
        assert json.loads(stored_json) == [_issue_row(i) for i in first.issues]
        for row in json.loads(stored_json):
            assert CYRILLIC.search(row["message"]) is None, row  # still the checker's English

        # The same problems one minute later, read back from what was stored.
        bot.send_message.reset_mock()
        last_alert = {"ts": time.time() - 60, "status": "warning", "issues": stored_json}
        pool.fetchrow.side_effect = [last_alert, {"id": 2}]
        second = _result(_every_kind(base))
        as_read = {**last_alert, "issues": json.loads(stored_json)}
        assert checker._decide_alert(second, config, as_read) == (None, [])
        await checker._persist_result(second, config)
        assert second.alert_sent is False
        bot.send_message.assert_not_awaited()
        assert pool.fetchrow.await_args_list[-1].args[6] == stored_json

        row = _stored(second)
        before = copy.deepcopy(row)
        for lang in ("ru", "en"):
            render_text(status_screen(lang, row, now=second.checked_at))
        assert row == before
        assert row["issues"] == stored_json
