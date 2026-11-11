"""The status board widget: its builders, and ``widget_query`` through a real Dispatcher.

Invented values only: the repository is public.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.methods import EditMessageCaption

from src.bot.widgets import queries as Q
from src.bot.widgets import status_board as B
from src.bot.widgets.api import AnswerWidgetQuery, EditMessageWidgetData
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository

ADMIN = 555000111
STRANGER = 555000222
NOW = datetime(2026, 1, 15, 12, 20, tzinfo=UTC)


def _row(status: str = "healthy", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "status": status,
        "checked_at": NOW - timedelta(minutes=5),
        "db_ok": True,
        "messages_30m": 17,
        "fallbacks_15m": 0,
        "issues": [],
    }
    row.update(extra)
    return row


def _hour(hours_ago: int) -> datetime:
    return (NOW - timedelta(hours=hours_ago)).replace(minute=0, tzinfo=None)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


class TestStrip:
    def test_current_hour_is_last_and_oldest_first(self):
        history = [{"hour": _hour(0), "worst": 1}, {"hour": _hour(23), "worst": 3}]
        assert B.strip(history, NOW) == "c" + "." * 22 + "h"

    def test_every_rank_has_its_letter(self):
        history = [{"hour": _hour(k), "worst": w} for k, w in ((3, 0), (2, 1), (1, 2), (0, 3))]
        assert B.strip(history, NOW)[-4:] == "shwc"

    def test_rows_outside_the_window_are_ignored(self):
        history = [{"hour": _hour(24), "worst": 3}, {"hour": _hour(-1), "worst": 3}]
        assert B.strip(history, NOW) == "." * 24

    def test_an_aware_hour_is_read_as_utc(self):
        from zoneinfo import ZoneInfo

        aware = _hour(0).replace(tzinfo=UTC).astimezone(ZoneInfo("Asia/Tbilisi"))
        assert B.strip([{"hour": aware, "worst": 2}], NOW)[-1] == "w"


class TestState:
    def test_no_checks_yet(self):
        state = B.board_state("ru", None, [], now=NOW)
        assert state == {"r": 0, "v": "", "i": [], "h": "." * 24}

    def test_a_row_becomes_integers_and_a_verdict_code(self):
        row = _row("critical", db_ok=False, messages_30m=0, fallbacks_15m=2)
        state = B.board_state("ru", row, [], now=NOW)
        assert state["r"] == int((NOW - timedelta(minutes=5)).timestamp())
        assert (state["v"], state["d"], state["m"], state["f"]) == ("c", 0, 0, 2)

    def test_an_unknown_status_is_not_called_healthy(self):
        assert B.board_state("ru", _row("weird"), [], now=NOW)["v"] == "?"

    def test_issues_use_the_screen_words_and_are_capped(self):
        issues = [
            {"severity": "critical" if k == 0 else "warning", "message": f"m{k}", "key": "x"}
            for k in range(5)
        ]
        state = B.board_state("ru", _row("critical", issues=issues), [], now=NOW)
        assert [sev for sev, _ in state["i"]] == ["c", "w", "w"]
        assert state["x"] == 2


class TestEncode:
    def test_long_cyrillic_issues_fit_in_1024_bytes(self):
        long = "Ж" * 400
        issues = [["w", long] for _ in range(3)]
        raw = json.dumps({"i": issues}, ensure_ascii=False)
        assert len(raw.encode()) > B.STATE_LIMIT  # control: without the cap it would not fit

        text = B.encode({"r": 1, "v": "w", "i": issues, "h": "h" * 24, "x": 2}, "slow")

        assert len(text.encode()) <= B.STATE_LIMIT
        data = json.loads(text)
        assert data["e"] == "slow"
        assert len(data["i"]) + data["x"] == 5  # nothing is lost from the count

    def test_cyrillic_stays_letters(self):
        assert "Ж" in B.encode({"i": [["w", "Ж"]]})

    def test_issue_text_is_cut_on_a_word(self):
        row = _row("warning", issues=[{"severity": "warning", "message": "слово " * 40}])
        (_, text), *_ = B.board_state("ru", row, [], now=NOW)["i"]
        assert len(text) <= B.ISSUE_CHARS and text.endswith("…")


class TestPage:
    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_fits_telegram_with_margin(self, lang: str):
        html = B.board_html(lang)
        assert "/*T*/" not in html
        assert B.compressed_size(html) <= 3800  # the guide's margin under 4096

    def test_words_are_baked_per_language(self):
        assert "Проверить сейчас" in B.board_html("ru")
        assert "Check now" in B.board_html("en")
        assert "Проверить" not in B.board_html("en")

    def test_a_closing_tag_in_a_word_cannot_end_the_script(self):
        with patch.dict(B._WORDS, {"go": B.Tr("</script><b>", "</script><b>")}):
            html = B.board_html("ru")
        assert html.count("</script>") == 1

    def test_every_note_and_verdict_has_words(self):
        table = B.words("en")
        assert set(table["v"]) == {"h", "w", "c", "s", "?", ""}
        assert set(table["e"]) == set(B.NOTES)


class TestCaption:
    def test_title_time_and_verdict(self):
        text = B.board_caption("ru", _row("warning"), now=NOW)
        assert text.startswith("📊 <b>Состояние</b> · ")
        assert "Есть предупреждение" in text

    def test_no_checks(self):
        assert "No checks yet" in B.board_caption("en", None, now=NOW)

    def test_within_the_caption_limit(self):
        assert len(B.board_caption("ru", _row("critical"), now=NOW)) <= 1024


# ---------------------------------------------------------------------------
# widget_query, through aiogram's own dispatch
# ---------------------------------------------------------------------------


class _Container:
    def __init__(self, **by_type: Any) -> None:
        self.by_type = by_type

    async def get(self, cls: type) -> Any:
        return self.by_type[cls.__name__]


def _repos(*, flag: Any = None, row: dict[str, Any] | None = None) -> _Container:
    bot_config = MagicMock()
    values = {"admin_ids": [ADMIN], "admin_widgets": flag}
    bot_config.get = AsyncMock(side_effect=lambda key: values.get(key))
    admin_repo = MagicMock()
    admin_repo.get_latest_health_check = AsyncMock(return_value=row)
    this_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    admin_repo.get_health_by_hour = AsyncMock(return_value=[{"hour": this_hour, "worst": 1}])
    return _Container(
        **{BotConfigRepository.__name__: bot_config, AdminRepository.__name__: admin_repo}
    )


def _update(data: str, *, user: int = ADMIN, chat_type: str = "private") -> dict[str, Any]:
    return {
        "update_id": 1,
        "widget_query": {
            "id": "8123",
            "from": {"id": user, "is_bot": False, "first_name": "A"},
            "chat": {"id": user, "type": chat_type, "first_name": "A"},
            "message_id": 42,
            "chat_instance": "1",
            "data": data,
        },
    }


async def _feed(
    raw: dict[str, Any], container: _Container, checker: Any = None
) -> tuple[list[Any], Dispatcher]:
    """Feed one raw update to a Dispatcher wired like main.py; return the Bot API calls."""
    dp = Dispatcher()
    dp.include_router(Router())  # an unrelated router first, as in production
    Q.register(dp)
    bot = Bot("42:TEST")
    calls: list[Any] = []

    async def record(_bot: Bot, method: Any, *_a: Any, **_k: Any) -> Any:
        calls.append(method)
        return True

    with patch.object(Bot, "__call__", new=record):
        from aiogram.types import Update

        await dp.feed_update(
            bot,
            Update.model_validate(raw, context={"bot": bot}),
            dishka_container=container,
            health_checker=checker,
        )
    await bot.session.close()
    return calls, dp


def _answer(calls: list[Any]) -> AnswerWidgetQuery:
    answers = [c for c in calls if isinstance(c, AnswerWidgetQuery)]
    assert len(answers) == 1, calls  # every query answered, exactly once
    assert answers[0].widget_query_id == "8123"
    return answers[0]


class TestWidgetQuery:
    @pytest.mark.asyncio
    async def test_check_now_runs_a_check_answers_then_edits(self):
        checker = MagicMock()
        checker.run_check_now = AsyncMock()
        calls, _ = await _feed(_update("n:ru"), _repos(row=_row("warning")), checker)

        checker.run_check_now.assert_awaited_once()
        assert [type(c) for c in calls] == [
            AnswerWidgetQuery,
            EditMessageWidgetData,
            EditMessageCaption,
        ]  # the answer first: it is the one with a deadline
        state = json.loads(_answer(calls).data or "")
        assert state["v"] == "w" and "e" not in state
        edit = calls[1]
        assert (edit.chat_id, edit.message_id) == (ADMIN, 42)
        assert json.loads(edit.chat_data)["h"][-1] == "h"
        assert "Есть предупреждение" in calls[2].caption

    @pytest.mark.asyncio
    async def test_a_stranger_is_refused_and_nothing_runs(self):
        checker = MagicMock()
        checker.run_check_now = AsyncMock()
        container = _repos(row=_row())
        calls, _ = await _feed(_update("n:ru", user=STRANGER), container, checker)

        assert json.loads(_answer(calls).data or "") == {"e": "no"}
        checker.run_check_now.assert_not_awaited()
        container.by_type["AdminRepository"].get_latest_health_check.assert_not_awaited()
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_an_admin_outside_the_private_chat_is_refused(self):
        calls, _ = await _feed(_update("n:ru", chat_type="supergroup"), _repos(row=_row()))
        assert json.loads(_answer(calls).data or "") == {"e": "no"}

    @pytest.mark.asyncio
    async def test_kill_switch_answers_off(self):
        calls, _ = await _feed(_update("s:ru:0"), _repos(flag=False, row=_row()))
        assert json.loads(_answer(calls).data or "") == {"e": "off"}
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_appear_with_the_newest_check_answers_nothing(self):
        row = _row()
        rev = int(row["checked_at"].timestamp())
        calls, _ = await _feed(_update(f"s:ru:{rev}"), _repos(row=row))
        assert _answer(calls).data is None
        assert len(calls) == 1  # and no edit

    @pytest.mark.asyncio
    async def test_appear_with_an_older_check_answers_the_new_one(self):
        row = _row()
        rev = int(row["checked_at"].timestamp()) - 60
        calls, _ = await _feed(_update(f"s:en:{rev}"), _repos(row=row))
        assert json.loads(_answer(calls).data or "")["v"] == "h"
        assert "All good" in calls[-1].caption

    @pytest.mark.asyncio
    async def test_a_slow_check_is_said_and_not_cancelled(self):
        finished = asyncio.Event()

        async def slow() -> None:
            await asyncio.sleep(0.05)
            finished.set()

        checker = MagicMock()
        checker.run_check_now = slow
        with patch.object(Q, "CHECK_TIMEOUT", 0.01):
            calls, _ = await _feed(_update("n:ru"), _repos(row=_row()), checker)

        assert json.loads(_answer(calls).data or "")["e"] == "slow"
        await asyncio.wait_for(finished.wait(), 1)  # it ran to the end in the background

    @pytest.mark.asyncio
    async def test_a_failure_is_still_answered(self):
        container = _repos(row=_row())
        container.by_type["AdminRepository"].get_latest_health_check.side_effect = RuntimeError
        calls, _ = await _feed(_update("s:ru:0"), container)
        assert json.loads(_answer(calls).data or "") == {"e": "x"}

    @pytest.mark.asyncio
    async def test_other_updates_do_not_reach_the_handler(self):
        raw = {
            "update_id": 2,
            "message": {
                "message_id": 1,
                "date": 0,
                "chat": {"id": ADMIN, "type": "private"},
                "text": "hi",
            },
        }
        calls, _ = await _feed(raw, _repos(row=_row()))
        assert calls == []


def test_polling_asks_telegram_for_widget_queries():
    async def noop(*_a: Any, **_k: Any) -> None:
        return None

    router = Router()
    router.message.register(noop)
    router.callback_query.register(noop)
    dp = Dispatcher()
    dp.include_router(router)
    Q.register(dp)
    wanted = Q.allowed_updates(dp)
    assert "widget_query" in wanted
    assert {"message", "callback_query"} <= set(wanted)
    assert len(wanted) == len(set(wanted))


def test_main_polls_with_widget_queries():
    """The call site, not the helper: main.py must pass the list and register the handler."""
    import inspect

    import src.main as main

    source = inspect.getsource(main)
    assert "allowed_updates=allowed_updates(dp)" in source
    assert "register_widget_queries(dp)" in source
