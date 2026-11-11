"""Tests for the admin panel handlers: hub, requests, rejected chats, status, settings.

Costs live in ``test_costs_handler.py``; the defaults screens in
``test_admin_defaults_handler.py``; the builders' own rules in
``test_screen_rules.py``.

Who may press is decided by the filters each handler is registered with, so the
"non-admin" and "group chat" cases are asserted on the registration
(`TestFilters`), not by calling a handler with ``is_admin=False``: a handler
called directly runs whatever its filters would have said.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from aiogram import F
from aiogram.dispatcher.event.handler import FilterObject
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters.admin import IsAdmin
from src.bot.handlers import admin as A
from src.bot.handlers.admin import (
    handle_admin_command,
    handle_board,
    handle_close,
    handle_health,
    handle_language_menu,
    handle_language_set,
    handle_menu,
    handle_noop,
    handle_notification_toggle,
    handle_notifications_menu,
    handle_settings,
    handle_settings_command,
    handle_stats,
    handle_sticker_notification_cycle,
    handle_wl_approve,
    handle_wl_delete,
    handle_wl_delete_ask,
    handle_wl_on,
    handle_wl_pending,
    handle_wl_reject,
    handle_wl_rejected,
    handle_wl_remove,
    handle_wl_remove_ask,
    handle_wl_restore,
)
from src.bot.screens.hub import build_hub, show_hub
from src.bot.widgets.api import SendWidget
from src.database.repositories.admin import AdminRepository
from src.database.repositories.response_log import ResponseLogRepository
from src.services.chat_config import ChatConfigService

# Invented values only: the repository is public.
CHAT_ID = -1009999999999
ATTEMPT_ID = 99999
USER_ID = 555000111

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_callback(data: str) -> MagicMock:
    """A CallbackQuery double from the private chat with the bot."""
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = USER_ID
    callback.answer = AsyncMock()
    callback.bot = None
    msg = MagicMock(spec=Message)
    msg.edit_text = AsyncMock()
    msg.answer = AsyncMock()
    msg.delete = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.type = "private"
    callback.message = msg
    return callback


def _make_message(text: str = "/admin") -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.type = "private"
    msg.from_user = MagicMock()
    msg.from_user.id = USER_ID
    msg.answer = AsyncMock()
    return msg


def _make_admin_repo(language: str = "ru", **notif: Any) -> AsyncMock:
    repo = AsyncMock()
    repo.get_admin_language = AsyncMock(return_value=language)
    repo.get_notification_settings = AsyncMock(
        return_value={
            "sticker": "on",
            "unauthorized": True,
            "jailbreak": True,
            "blacklist": True,
            "ai_fallback": True,
            **notif,
        }
    )
    repo.get_latest_health_check = AsyncMock(return_value=None)
    repo.count_attempts = AsyncMock(return_value=0)
    return repo


def _attempt(**extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": ATTEMPT_ID,
        "chat_id": CHAT_ID,
        "chat_title": "Test Chat",
        "chat_type": "supergroup",
        "user_id": USER_ID,
        "user_first_name": "Alice",
        "user_username": "alice",
        "message_text": "hello",
        "status": "pending",
        "created_at": None,
    }
    row.update(extra)
    return row


class _Container:
    """Just enough of a Dishka container for `resolve()`."""

    def __init__(self, deps: dict[type, Any]) -> None:
        self._deps = deps

    async def get(self, dependency: type) -> Any:
        return self._deps[dependency]


def _markup(callback: MagicMock) -> Any:
    return callback.message.edit_text.call_args.kwargs["reply_markup"]


def _edited(callback: MagicMock) -> tuple[str, list[str]]:
    """The text and the callback payloads of the screen edited into the message."""
    callback.message.edit_text.assert_awaited_once()
    markup = _markup(callback)
    payloads = [b.callback_data for row in (markup.inline_keyboard if markup else []) for b in row]
    return callback.message.edit_text.call_args.args[0], [p for p in payloads if p]


def _shown(show: AsyncMock) -> Any:
    """The `Screen` a patched `show` was called with."""
    show.assert_awaited_once()
    return show.call_args.args[1]


# ---------------------------------------------------------------------------
# Filters: who may press, and where
# ---------------------------------------------------------------------------


def _probe(chat_type: str) -> SimpleNamespace:
    chat = SimpleNamespace(type=chat_type)
    return SimpleNamespace(data="", text="", chat=chat, message=SimpleNamespace(chat=chat))


def _private_only(filters: list[Any]) -> bool:
    """One of the magic filters passes in a private chat and fails in a group."""
    return any(
        f.magic is not None and f.callback(_probe("private")) and not f.callback(_probe("group"))
        for f in filters
    )


def _admin_only(filters: list[Any]) -> bool:
    return any(isinstance(f.callback, IsAdmin) for f in filters)


class TestFilters:
    def test_every_button_needs_an_admin_in_the_private_chat(self):
        handlers = A.router.observers["callback_query"].handlers
        assert len(handlers) >= 20  # positive control: the walk sees the handlers
        for handler in handlers:
            if handler.callback is handle_noop:
                continue
            name = handler.callback.__name__
            assert _admin_only(handler.filters), name
            assert _private_only(handler.filters), name

    def test_every_command_needs_an_admin_in_the_private_chat(self):
        handlers = A.router.observers["message"].handlers
        assert {h.callback.__name__ for h in handlers} == {
            "handle_admin_command",
            "handle_settings_command",
            "handle_costs_command",
        }
        for handler in handlers:
            assert _admin_only(handler.filters), handler.callback.__name__
            assert _private_only(handler.filters), handler.callback.__name__

    def test_the_checks_can_fail(self):
        """Negative controls: a filter set without the guards is caught."""
        only_prefix = [FilterObject(F.data.startswith("adm_menu:"))]
        assert not _private_only(only_prefix)
        assert not _admin_only(only_prefix)
        assert _private_only([FilterObject(F.message.chat.type == "private")])

    def test_admin_and_settings_are_two_commands(self):
        def commands(func: Any) -> set[str]:
            for h in A.router.observers["message"].handlers:
                if h.callback is func:
                    return {
                        str(c)
                        for f in h.filters
                        if isinstance(f.callback, Command)
                        for c in f.callback.commands
                    }
            raise AssertionError(func)

        assert commands(handle_admin_command) == {"admin"}
        assert commands(handle_settings_command) == {"settings"}

    @pytest.mark.parametrize(
        ("payload", "handler"),
        [
            ("adm_menu:ru", "admin.handle_menu"),
            ("adm_settings:ru", "admin.handle_settings"),
            ("adm_lang:ru", "admin.handle_language_menu"),
            ("adm_lang_set:ru:en", "admin.handle_language_set"),
            ("adm_wl_rm_ask:ru:-1009999999999:0", "admin.handle_wl_remove_ask"),
            ("adm_wl_rm:ru:-1009999999999:0", "admin.handle_wl_remove"),
            ("adm_wl_on:ru:-1009999999999:0", "admin.handle_wl_on"),
            ("adm_wl_del_ask:ru:99999:0", "admin.handle_wl_delete_ask"),
            ("adm_wl_del:ru:99999:0", "admin.handle_wl_delete"),
            ("adm_health:ru:now", "admin.handle_health"),
            ("noop", "admin.handle_noop"),
            # Retired: the old whitelist submenu and chat list answer as stale now.
            ("adm_wl:ru", "stale.handle_stale"),
            ("adm_wl_chats:ru:0", "stale.handle_stale"),
        ],
    )
    def test_payload_reaches_its_handler(self, payload: str, handler: str):
        from src.bot.handlers import router
        from src.bot.screens.lint import first_handler

        assert first_handler(router, payload) == handler


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class TestCommands:
    @pytest.mark.asyncio
    async def test_admin_opens_the_hub_in_the_admin_language(self):
        msg = _make_message("/admin")
        admin_repo = _make_admin_repo("en")
        bot_config_repo = AsyncMock()

        with patch.object(A, "show_hub", new=AsyncMock()) as hub:
            await handle_admin_command(msg, admin_repo, bot_config_repo, extra="x")

        hub.assert_awaited_once_with(msg, "en", data={"extra": "x"})
        admin_repo.get_admin_language.assert_awaited_once_with(bot_config_repo)

    @pytest.mark.asyncio
    async def test_settings_opens_general_settings_as_a_new_message(self):
        msg = _make_message("/settings")

        await handle_settings_command(msg, _make_admin_repo(), AsyncMock())

        msg.answer.assert_awaited_once()
        assert "Общие настройки" in msg.answer.call_args.args[0]
        markup = msg.answer.call_args.kwargs["reply_markup"]
        payloads = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert payloads == ["adm_defs:ru:0", "adm_notif:ru", "adm_lang:ru", "adm_menu:ru"]


# ---------------------------------------------------------------------------
# The hub
# ---------------------------------------------------------------------------


class TestHub:
    @pytest.mark.asyncio
    async def test_menu_button_shows_the_hub(self):
        cb = _make_callback("adm_menu:en")

        with patch.object(A, "show_hub", new=AsyncMock()) as hub:
            await handle_menu(cb, extra=1)

        hub.assert_awaited_once_with(cb, "en", data={"extra": 1})

    @pytest.mark.asyncio
    async def test_close_deletes_the_panel(self):
        cb = _make_callback("adm_close:ru")

        await handle_close(cb)

        cb.answer.assert_awaited_once()
        cb.message.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_survives_a_message_that_cannot_be_deleted(self):
        cb = _make_callback("adm_close:ru")
        cb.message.delete = AsyncMock(side_effect=RuntimeError("too old"))

        await handle_close(cb)

        cb.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_noop_only_answers(self):
        cb = _make_callback("noop")

        await handle_noop(cb)

        cb.answer.assert_awaited_once_with()
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_show_hub_reads_the_last_check_not_a_live_one(self):
        """The hub's line: last recorded check + 24 h spend + waiting requests."""
        cb = _make_callback("adm_menu:ru")
        admin_repo = _make_admin_repo()
        admin_repo.get_latest_health_check = AsyncMock(return_value={"status": "warning"})
        admin_repo.count_attempts = AsyncMock(return_value=2)
        response_log = AsyncMock()
        response_log.get_total_cost = AsyncMock(return_value=Decimal("0.0031"))
        checker = MagicMock()
        checker.run_check_now = AsyncMock()
        data = {
            "dishka_container": _Container(
                {AdminRepository: admin_repo, ResponseLogRepository: response_log}
            ),
            "health_checker": checker,
        }

        await show_hub(cb, "ru", data=data)

        text, payloads = _edited(cb)
        assert "Есть предупреждение" in text
        assert "$0.0031" in text
        response_log.get_total_cost.assert_awaited_once_with(timedelta(hours=24))
        admin_repo.count_attempts.assert_awaited_once_with("pending")
        checker.run_check_now.assert_not_awaited()
        assert [b.text for b in _markup(cb).inline_keyboard[0]] == ["🗂 Чаты", "🙏 Запросы · 2"]
        assert payloads[:2] == ["adm_pnl:ru:0", "adm_wl_pending:ru:0"]

    @pytest.mark.asyncio
    async def test_show_hub_without_its_data_still_opens(self):
        """No container (or a dead database): the title alone, no invented status."""
        cb = _make_callback("adm_menu:en")

        await show_hub(cb, "en", data={})

        text, payloads = _edited(cb)
        assert text == "🛠 <b>Panel</b>"
        assert "adm_close:en" in payloads


class TestHubRows:
    """The hub's buttons and their payloads (``adm_menu:`` leads here; its name is load-bearing)."""

    @pytest.mark.parametrize(
        ("lang", "labels"),
        [
            (
                "ru",
                [
                    ["🗂 Чаты", "🙏 Запросы · 1"],
                    ["📦 Паки стикеров"],
                    ["📊 Состояние"],
                    ["⚙️ Общие настройки"],
                    ["✖️ Закрыть"],
                ],
            ),
            (
                "en",
                [
                    ["🗂 Chats", "🙏 Requests · 1"],
                    ["📦 Sticker packs"],
                    ["📊 Status"],
                    ["⚙️ General settings"],
                    ["✖️ Close"],
                ],
            ),
        ],
    )
    def test_rows(self, lang: str, labels: list[list[str]]):
        """Waiting requests are one tap from the hub, beside the chats, not a count on them."""
        screen = build_hub(lang, health={"status": "healthy"}, spend=Decimal("0.04"), pending=1)

        assert [[b.label for b in row] for row in screen.rows] == labels
        assert [[b.callback for b in row] for row in screen.rows] == [
            [f"adm_pnl:{lang}:0", f"adm_wl_pending:{lang}:0"],
            [f"adm_stk_sets:{lang}:0"],
            [f"adm_health:{lang}"],
            [f"adm_settings:{lang}"],
            [f"adm_close:{lang}"],
        ]
        assert screen.rows[-1][0].kind == "exit"

    def test_no_requests_no_requests_button(self):
        screen = build_hub("ru", health=None, spend=Decimal(0), pending=0)

        assert [[b.label for b in row] for row in screen.rows] == [
            ["🗂 Чаты"],
            ["📦 Паки стикеров"],
            ["📊 Состояние"],
            ["⚙️ Общие настройки"],
            ["✖️ Закрыть"],
        ]
        assert "Проверок пока не было" in screen.text
        assert "$0.00" in screen.text

    def test_many_requests_keep_their_number_whole(self):
        """Three digits still fit half a row (R10 measures it on the ``hub_many`` sample)."""
        screen = build_hub("ru", health=None, spend=Decimal(0), pending=120)

        assert [b.label for b in screen.rows[0]] == ["🗂 Чаты", "🙏 Запросы · 120"]

    def test_sub_cent_spend_keeps_four_places(self):
        assert "$0.0031" in build_hub("en", spend=Decimal("0.0031")).text
        assert "$1.27" in build_hub("en", spend=Decimal("1.27")).text


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class TestRequests:
    @pytest.mark.asyncio
    async def test_shows_one_request(self):
        cb = _make_callback("adm_wl_pending:ru:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_pending_attempts_page = AsyncMock(return_value=([_attempt()], 1))

        await handle_wl_pending(cb, admin_repo)

        text, payloads = _edited(cb)
        assert "Запрос доступа 1 из 1" in text
        assert "Test Chat" in text
        assert "@alice" in text
        assert "супергруппа" in text  # the chat type stays visible
        assert f"adm_wl_apr:ru:{ATTEMPT_ID}:0" in payloads
        assert f"adm_wl_rej:ru:{ATTEMPT_ID}:0" in payloads
        assert payloads[-1] == "adm_pnl:ru:0"
        admin_repo.get_pending_attempts_page.assert_awaited_once_with(0, 1)

    @pytest.mark.asyncio
    async def test_chat_id_is_copyable_code(self):
        """A bare 9-11 digit run renders as a dead "phone" link; <code> prevents it."""
        cb = _make_callback("adm_wl_pending:en:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_pending_attempts_page = AsyncMock(return_value=([_attempt()], 1))

        await handle_wl_pending(cb, admin_repo)

        assert f"<code>{CHAT_ID}</code>" in _edited(cb)[0]

    @pytest.mark.asyncio
    async def test_empty(self):
        cb = _make_callback("adm_wl_pending:en:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_pending_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_pending(cb, admin_repo)

        text, payloads = _edited(cb)
        assert "No new requests." in text
        assert payloads == ["adm_pnl:en:0"]

    @pytest.mark.asyncio
    async def test_a_page_past_the_end_shows_the_last_request(self):
        cb = _make_callback("adm_wl_pending:ru:5")
        admin_repo = _make_admin_repo()
        admin_repo.get_pending_attempts_page = AsyncMock(side_effect=[([], 3), ([_attempt()], 3)])

        await handle_wl_pending(cb, admin_repo)

        assert admin_repo.get_pending_attempts_page.await_args_list == [call(5, 1), call(2, 1)]
        assert "Запрос доступа 3 из 3" in _edited(cb)[0]

    @pytest.mark.asyncio
    async def test_a_missing_title_is_asked_from_telegram(self):
        cb = _make_callback("adm_wl_pending:ru:0")
        cb.bot = MagicMock()
        cb.bot.get_chat = AsyncMock(return_value=SimpleNamespace(title="Fetched", full_name=""))
        admin_repo = _make_admin_repo()
        admin_repo.get_pending_attempts_page = AsyncMock(
            return_value=([_attempt(chat_title=None)], 1)
        )

        await handle_wl_pending(cb, admin_repo)

        cb.bot.get_chat.assert_awaited_once_with(CHAT_ID)
        assert "Fetched" in _edited(cb)[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", ["adm_wl_pending:ru", "adm_wl_pending:ru:x"])
    async def test_garbled_payload_is_stale(self, payload: str):
        cb = _make_callback(payload)
        admin_repo = _make_admin_repo()

        with patch.object(A, "show_stale", new=AsyncMock()) as stale:
            await handle_wl_pending(cb, admin_repo, extra=1)

        stale.assert_awaited_once_with(cb, "ru", data={"extra": 1})
        admin_repo.get_pending_attempts_page.assert_not_called()


class TestApprove:
    @pytest.mark.asyncio
    async def test_enables_the_chat_then_opens_it(self):
        cb = _make_callback(f"adm_wl_apr:ru:{ATTEMPT_ID}:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_attempt())
        chat_settings_repo = AsyncMock()
        service = MagicMock()
        data = {"dishka_container": _Container({ChatConfigService: service})}

        order: list[str] = []
        chat_settings_repo.upsert = AsyncMock(side_effect=lambda *_a, **_k: order.append("enable"))

        async def opened(*_a: Any, **_k: Any) -> None:
            order.append("show")

        with patch.object(A, "show_chat", new=AsyncMock(side_effect=opened)) as show_chat:
            await handle_wl_approve(cb, admin_repo, chat_settings_repo, **data)

        assert order == ["enable", "show"]
        chat_settings_repo.upsert.assert_awaited_once_with(
            CHAT_ID, enabled=True, chat_title="Test Chat", chat_type="supergroup"
        )
        admin_repo.approve_all_for_chat.assert_awaited_once_with(CHAT_ID)
        show_chat.assert_awaited_once_with(
            cb, "ru", CHAT_ID, notice="Новый чат подключён", data=data
        )
        service.invalidate.assert_called_once_with(CHAT_ID)

    @pytest.mark.asyncio
    async def test_already_handled_shows_the_requests_with_a_notice(self):
        cb = _make_callback(f"adm_wl_apr:en:{ATTEMPT_ID}:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_attempt(status="approved"))
        admin_repo.get_pending_attempts_page = AsyncMock(return_value=([], 0))
        chat_settings_repo = AsyncMock()

        with patch.object(A, "show_chat", new=AsyncMock()) as show_chat:
            await handle_wl_approve(cb, admin_repo, chat_settings_repo)

        show_chat.assert_not_awaited()
        chat_settings_repo.upsert.assert_not_awaited()
        assert "The request is already resolved" in _edited(cb)[0]

    @pytest.mark.asyncio
    async def test_garbled_payload_is_stale(self):
        cb = _make_callback("adm_wl_apr:ru:abc")
        admin_repo = _make_admin_repo()

        with patch.object(A, "show_stale", new=AsyncMock()) as stale:
            await handle_wl_approve(cb, admin_repo, AsyncMock())

        stale.assert_awaited_once()
        admin_repo.get_attempt.assert_not_called()


class TestReject:
    @pytest.mark.asyncio
    async def test_shows_the_next_request_with_a_notice(self):
        cb = _make_callback(f"adm_wl_rej:ru:{ATTEMPT_ID}:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_attempt())
        admin_repo.count_attempts = AsyncMock(return_value=1)
        admin_repo.get_pending_attempts_page = AsyncMock(
            return_value=([_attempt(id=ATTEMPT_ID + 1, chat_title="Next")], 1)
        )

        await handle_wl_reject(cb, admin_repo)

        admin_repo.update_attempt_status.assert_awaited_once_with(ATTEMPT_ID, "rejected")
        text, _ = _edited(cb)
        assert text.startswith("<i>✓ Отклонено</i>")
        assert "Next" in text

    @pytest.mark.asyncio
    async def test_the_last_one_goes_to_the_chat_list(self):
        cb = _make_callback(f"adm_wl_rej:en:{ATTEMPT_ID}:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_attempt())
        admin_repo.count_attempts = AsyncMock(return_value=0)

        with patch.object(A, "show_chats", new=AsyncMock()) as chats:
            await handle_wl_reject(cb, admin_repo, extra=1)

        chats.assert_awaited_once_with(cb, "en", 0, notice="Rejected", data={"extra": 1})
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_already_handled(self):
        cb = _make_callback(f"adm_wl_rej:ru:{ATTEMPT_ID}:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=None)
        admin_repo.get_pending_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_reject(cb, admin_repo)

        admin_repo.update_attempt_status.assert_not_awaited()
        assert "Запрос уже решён" in _edited(cb)[0]


# ---------------------------------------------------------------------------
# Rejected chats
# ---------------------------------------------------------------------------


class TestRejected:
    @pytest.mark.asyncio
    async def test_shows_one_rejected_chat(self):
        cb = _make_callback("adm_wl_rejected:ru:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_rejected_attempts_page = AsyncMock(
            return_value=([_attempt(status="rejected")], 2)
        )

        await handle_wl_rejected(cb, admin_repo)

        text, payloads = _edited(cb)
        assert "Test Chat" in text
        assert f"adm_wl_restore:ru:{ATTEMPT_ID}:0" in payloads
        assert f"adm_wl_del_ask:ru:{ATTEMPT_ID}:0" in payloads
        assert "adm_wl_rejected:ru:1" in payloads  # the pager

    @pytest.mark.asyncio
    async def test_empty(self):
        cb = _make_callback("adm_wl_rejected:en:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_rejected(cb, admin_repo)

        assert "No rejected chats." in _edited(cb)[0]

    @pytest.mark.asyncio
    async def test_restore_puts_it_back_among_the_requests(self):
        cb = _make_callback(f"adm_wl_restore:ru:{ATTEMPT_ID}:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_attempt(status="rejected"))
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_restore(cb, admin_repo)

        admin_repo.update_attempt_status.assert_awaited_once_with(ATTEMPT_ID, "pending")
        assert _edited(cb)[0].startswith("<i>✓ Запрос возвращён</i>")

    @pytest.mark.asyncio
    async def test_after_a_restore_the_requests_are_one_tap_away(self):
        """Approving the restored chat is the step the admin came for: the list
        that holds it is the first row, counted after the write."""
        cb = _make_callback(f"adm_wl_restore:ru:{ATTEMPT_ID}:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_attempt(status="rejected"))
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([_attempt()], 2))
        admin_repo.count_attempts = AsyncMock(return_value=3)

        await handle_wl_restore(cb, admin_repo)

        assert admin_repo.method_calls.index(
            call.update_attempt_status(ATTEMPT_ID, "pending")
        ) < admin_repo.method_calls.index(call.count_attempts("pending"))
        markup = _markup(cb)
        assert markup is not None
        first = markup.inline_keyboard[0]
        assert [b.text for b in first] == ["🙏 Запросы доступа · 3"]
        assert first[0].callback_data == "adm_wl_pending:ru:0"

    @pytest.mark.asyncio
    async def test_the_rejected_list_opened_plainly_has_no_requests_row(self):
        """Control: the row belongs to the render after a restore, not to the list."""
        cb = _make_callback("adm_wl_rejected:ru:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([_attempt()], 2))
        admin_repo.count_attempts = AsyncMock(return_value=3)

        await handle_wl_rejected(cb, admin_repo)

        assert not [p for p in _edited(cb)[1] if p.startswith("adm_wl_pending:")]

    @pytest.mark.asyncio
    async def test_restore_of_a_request_no_longer_rejected_writes_nothing(self):
        cb = _make_callback(f"adm_wl_restore:ru:{ATTEMPT_ID}:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_attempt(status="pending"))
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_restore(cb, admin_repo)

        admin_repo.update_attempt_status.assert_not_awaited()
        text = _edited(cb)[0]
        assert text.startswith("<i>✓ Запрос уже решён</i>"), text
        assert "Запрос возвращён" not in text

    @pytest.mark.asyncio
    async def test_restore_of_a_gone_record_says_so(self):
        cb = _make_callback(f"adm_wl_restore:en:{ATTEMPT_ID}:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=None)
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_restore(cb, admin_repo)

        assert _edited(cb)[0].startswith("<i>✓ Record not found</i>")
        cb.answer.assert_awaited_once_with()  # a line on the screen, no popup

    @pytest.mark.asyncio
    async def test_forget_asks_first(self):
        cb = _make_callback(f"adm_wl_del_ask:ru:{ATTEMPT_ID}:1")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_attempt(status="rejected"))

        await handle_wl_delete_ask(cb, admin_repo)

        text, payloads = _edited(cb)
        assert "Забыть отказ?" in text
        assert payloads == [f"adm_wl_del:ru:{ATTEMPT_ID}:1", "adm_wl_rejected:ru:1"]
        admin_repo.delete_attempt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_forget_ask_of_a_gone_record(self):
        cb = _make_callback(f"adm_wl_del_ask:ru:{ATTEMPT_ID}:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=None)
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_delete_ask(cb, admin_repo)

        assert _edited(cb)[0].startswith("<i>✓ Запись не найдена</i>")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("deleted", "notice"), [(True, "Отказ забыт"), (False, "Запись не найдена")]
    )
    async def test_forget(self, deleted: bool, notice: str):
        cb = _make_callback(f"adm_wl_del:ru:{ATTEMPT_ID}:0")
        admin_repo = _make_admin_repo()
        admin_repo.delete_attempt = AsyncMock(return_value=deleted)
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_delete(cb, admin_repo)

        admin_repo.delete_attempt.assert_awaited_once_with(ATTEMPT_ID)
        assert _edited(cb)[0].startswith(f"<i>✓ {notice}</i>")


# ---------------------------------------------------------------------------
# Turning the bot off in one chat
# ---------------------------------------------------------------------------


def _rejected_before(value: bool) -> AsyncMock:
    repo = AsyncMock()
    repo.has_rejected_attempt = AsyncMock(return_value=value)
    return repo


def _bot_config(*admin_ids: int) -> AsyncMock:
    """`BotConfigRepository` whose ``admin_ids`` are these (the JSON-array form)."""
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=list(admin_ids))
    return repo


def _settings(row: dict[str, Any] | None, *, written: bool = True) -> AsyncMock:
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=row)
    repo.set_field = AsyncMock(return_value=written)
    return repo


_ON = {"chat_title": "Test Chat", "chat_type": "supergroup", "enabled": True}


class TestTurnOff:
    @pytest.mark.asyncio
    async def test_asks_first_and_writes_nothing(self):
        cb = _make_callback(f"adm_wl_rm_ask:ru:{CHAT_ID}:2")
        chat_settings_repo = AsyncMock()
        chat_settings_repo.get = AsyncMock(
            return_value={"chat_title": "Test Chat", "chat_type": "supergroup"}
        )
        admin_repo = _rejected_before(False)

        await handle_wl_remove_ask(cb, chat_settings_repo, admin_repo, _bot_config())

        text, payloads = _edited(cb)
        assert text.splitlines()[2:] == [
            "<b>Test Chat</b>",
            "Бот перестанет отвечать, и чат уйдёт из «🗂 Чаты». Настройки сохранятся.",
            "Вернуть сразу — «↩️ Вернуть» в списке.",
            "Позже — когда в чате напишут, появится запрос доступа (не чаще раза в полчаса): "
            "одобрите его.",
        ]
        assert payloads == [f"adm_wl_rm:ru:{CHAT_ID}:2", f"adm_pnl_menu:ru:{CHAT_ID}"]
        admin_repo.has_rejected_attempt.assert_awaited_once_with(CHAT_ID)
        chat_settings_repo.set_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_chat_rejected_before_is_told_it_will_not_ask_again(self):
        """An old rejection outlives the approval that enabled the chat: once off,
        the gate's `has_rejected_attempt` swallows every request, so the screen
        must not promise one."""
        cb = _make_callback(f"adm_wl_rm_ask:ru:{CHAT_ID}:0")
        chat_settings_repo = AsyncMock()
        chat_settings_repo.get = AsyncMock(
            return_value={"chat_title": "Test Chat", "chat_type": "supergroup"}
        )

        await handle_wl_remove_ask(cb, chat_settings_repo, _rejected_before(True), _bot_config())

        text = _edited(cb)[0]
        assert text.splitlines()[-1] == (
            "Позже чат сам не попросит: его уже отклоняли. "
            "Верните его из «🚫 Отклонённые» в запросы и одобрите."
        )
        assert "появится запрос доступа" not in text

    @pytest.mark.asyncio
    async def test_a_channel_is_told_to_add_the_bot_again(self):
        """Channel posts never reach the access gate: only adding the bot files a request."""
        cb = _make_callback(f"adm_wl_rm_ask:en:{CHAT_ID}:0")
        chat_settings_repo = AsyncMock()
        chat_settings_repo.get = AsyncMock(
            return_value={"chat_title": "Test Channel", "chat_type": "channel"}
        )

        await handle_wl_remove_ask(cb, chat_settings_repo, _rejected_before(False), _bot_config())

        assert _edited(cb)[0].splitlines()[-1] == (
            "Later: remove the bot from the channel and add it again; an access request "
            "appears (at most once per half hour): approve it."
        )

    @pytest.mark.asyncio
    async def test_ask_without_a_title_names_the_id(self):
        cb = _make_callback(f"adm_wl_rm_ask:en:{CHAT_ID}:0")
        chat_settings_repo = AsyncMock()
        chat_settings_repo.get = AsyncMock(return_value=None)

        await handle_wl_remove_ask(cb, chat_settings_repo, _rejected_before(False), _bot_config())

        assert f"<code>{CHAT_ID}</code>" in _edited(cb)[0]

    @pytest.mark.asyncio
    async def test_an_admins_own_private_chat_is_asked_honestly(self):
        """`AccessControlMiddleware` answers an admin's DM whatever ``enabled``
        says, and a DM files no request: neither ordinary line holds there."""
        cb = _make_callback(f"adm_wl_rm_ask:ru:{USER_ID}:0")
        row = {"chat_title": "Админ", "chat_type": "private", "enabled": True}
        bot_config = _bot_config(USER_ID)

        await handle_wl_remove_ask(cb, _settings(row), _rejected_before(False), bot_config)

        lines = _edited(cb)[0].splitlines()
        assert lines[0] == "🔌 <b>Отключить этот чат?</b>"
        assert lines[3] == (
            "Это личный чат администратора: здесь бот отвечает всегда, включён чат или нет. "
            "Отключение только уберёт его из «🗂 Чаты»; настройки сохранятся."
        )
        assert lines[-1] == "Позже запроса не будет: из личного чата администратора он не приходит."
        bot_config.get.assert_awaited_once_with("admin_ids")

    @pytest.mark.asyncio
    async def test_a_private_chat_of_a_non_admin_is_asked_as_any_chat(self):
        """Positive control: the same private row, its id not among the admins."""
        cb = _make_callback(f"adm_wl_rm_ask:ru:{USER_ID}:0")
        row = {"chat_title": "Кто-то", "chat_type": "private", "enabled": True}

        await handle_wl_remove_ask(cb, _settings(row), _rejected_before(False), _bot_config(1))

        assert _edited(cb)[0].splitlines()[0] == "🔌 <b>Отключить бота здесь?</b>"
        assert "Бот перестанет отвечать" in _edited(cb)[0]

    @pytest.mark.asyncio
    async def test_a_chat_already_off_is_told_so_with_no_turn_off_button(self):
        cb = _make_callback(f"adm_wl_rm_ask:ru:{CHAT_ID}:2")

        await handle_wl_remove_ask(
            cb, _settings({**_ON, "enabled": False}), _rejected_before(False), _bot_config()
        )

        text, payloads = _edited(cb)
        assert text.splitlines()[0] == "🔌 <b>Бот здесь уже отключён</b>"
        assert "Вернуть сразу" not in text
        assert payloads == [f"adm_pnl_menu:ru:{CHAT_ID}"]

    @pytest.mark.asyncio
    async def test_turns_off_and_shows_the_chat_list(self):
        cb = _make_callback(f"adm_wl_rm:ru:{CHAT_ID}:2")
        chat_settings_repo = _settings(_ON)
        service = MagicMock()
        data = {"dishka_container": _Container({ChatConfigService: service})}

        with patch.object(A, "show_chats", new=AsyncMock()) as chats:
            await handle_wl_remove(
                cb, chat_settings_repo, _rejected_before(False), _bot_config(), **data
            )

        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "enabled", False)
        service.invalidate.assert_called_once_with(CHAT_ID)
        chats.assert_awaited_once_with(
            cb,
            "ru",
            2,
            notice="Бот отключён в чате «Test Chat»",
            undo=CHAT_ID,
            undo_rejected=False,
            data=data,
        )

    @pytest.mark.asyncio
    async def test_a_rejection_on_record_before_the_turn_off_marks_the_undo(self):
        """The undo then knows that rejection is not one made after the tap."""
        cb = _make_callback(f"adm_wl_rm:ru:{CHAT_ID}:0")

        with patch.object(A, "show_chats", new=AsyncMock()) as chats:
            await handle_wl_remove(cb, _settings(_ON), _rejected_before(True), _bot_config())

        assert chats.call_args.kwargs["undo"] == CHAT_ID
        assert chats.call_args.kwargs["undo_rejected"] is True

    @pytest.mark.asyncio
    async def test_a_chat_already_off_is_not_turned_off_again_and_offers_no_undo(self):
        """An old ask screen tapped after another admin turned the chat off:
        «↩️ Вернуть» here would turn ON a chat that was off before the tap."""
        cb = _make_callback(f"adm_wl_rm:ru:{CHAT_ID}:1")
        chat_settings_repo = _settings({**_ON, "chat_title": "A & B", "enabled": False})
        service = MagicMock()
        data = {"dishka_container": _Container({ChatConfigService: service})}

        with patch.object(A, "show_chats", new=AsyncMock()) as chats:
            await handle_wl_remove(
                cb, chat_settings_repo, _rejected_before(False), _bot_config(), **data
            )

        chat_settings_repo.set_field.assert_not_awaited()
        service.invalidate.assert_not_called()
        chats.assert_awaited_once_with(
            cb,
            "ru",
            1,
            notice_raw="<i>Бот в чате «A &amp; B» уже был отключён, ничего не изменилось.</i>",
            data=data,
        )

    @pytest.mark.asyncio
    async def test_an_admins_own_private_chat_is_not_reported_silent(self):
        cb = _make_callback(f"adm_wl_rm:ru:{USER_ID}:0")
        row = {"chat_title": "Админ", "chat_type": "private", "enabled": True}

        with patch.object(A, "show_chats", new=AsyncMock()) as chats:
            await handle_wl_remove(
                cb, _settings(row), _rejected_before(False), _bot_config(USER_ID)
            )

        assert chats.call_args.kwargs["notice"] == (
            "Чат «Админ» убран из «🗂 Чаты», бот отвечает в нём, как раньше"
        )
        assert chats.call_args.kwargs["undo"] == USER_ID

    @pytest.mark.asyncio
    async def test_a_chat_without_settings_is_not_reported_off(self):
        """No row: nothing to turn off, so no write and nothing to undo."""
        cb = _make_callback(f"adm_wl_rm:ru:{CHAT_ID}:0")
        chat_settings_repo = _settings(None, written=False)

        with patch.object(A, "show_chats", new=AsyncMock()) as chats:
            await handle_wl_remove(cb, chat_settings_repo, _rejected_before(False), _bot_config())

        chat_settings_repo.set_field.assert_not_awaited()
        assert chats.call_args.kwargs.get("undo") is None
        assert "notice" not in chats.call_args.kwargs
        assert chats.call_args.kwargs["notice_raw"].startswith("<i>Этого чата нет в настройках")

    @pytest.mark.asyncio
    async def test_a_row_gone_between_the_read_and_the_write_is_not_reported_off(self):
        """``set_field`` matched no row after the read found one."""
        cb = _make_callback(f"adm_wl_rm:ru:{CHAT_ID}:0")
        chat_settings_repo = _settings(_ON, written=False)

        with patch.object(A, "show_chats", new=AsyncMock()) as chats:
            await handle_wl_remove(cb, chat_settings_repo, _rejected_before(False), _bot_config())

        chat_settings_repo.set_field.assert_awaited_once()
        assert chats.call_args.kwargs.get("undo") is None
        assert chats.call_args.kwargs["notice_raw"].startswith("<i>Этого чата нет в настройках")

    @pytest.mark.asyncio
    async def test_turn_off_without_a_container_still_completes(self):
        """`invalidate` is defensive (the cache is request-scoped): its absence is no error."""
        cb = _make_callback(f"adm_wl_rm:en:{CHAT_ID}:0")
        chat_settings_repo = _settings({"chat_type": "supergroup", "enabled": True})

        with patch.object(A, "show_chats", new=AsyncMock()) as chats:
            await handle_wl_remove(cb, chat_settings_repo, _rejected_before(False), _bot_config())

        chat_settings_repo.set_field.assert_awaited_once()
        assert chats.call_args.kwargs["notice"] == f"The bot is off in «{CHAT_ID}»"

    @pytest.mark.asyncio
    async def test_garbled_payload_is_stale_and_writes_nothing(self):
        cb = _make_callback("adm_wl_rm:ru:not-a-chat:0")
        chat_settings_repo = AsyncMock()

        with patch.object(A, "show_stale", new=AsyncMock()) as stale:
            await handle_wl_remove(cb, chat_settings_repo, _rejected_before(False), _bot_config())

        stale.assert_awaited_once()
        chat_settings_repo.set_field.assert_not_awaited()


def _bot_in_the_chat() -> MagicMock:
    """A bot that Telegram says is still a member of every chat it is asked about."""
    bot = MagicMock()
    bot.id = 4242
    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="member"))
    return bot


class TestTurnBackOn:
    """``adm_wl_on:`` — «↩️ Вернуть» after a turn-off."""

    @pytest.mark.asyncio
    async def test_turns_on_and_shows_the_list_without_an_undo(self):
        cb = _make_callback(f"adm_wl_on:ru:{CHAT_ID}:1")
        cb.bot = _bot_in_the_chat()
        chat_settings_repo = AsyncMock()
        chat_settings_repo.get = AsyncMock(
            return_value={"chat_title": "Test Chat", "enabled": False}
        )
        service = MagicMock()
        data = {"dishka_container": _Container({ChatConfigService: service})}

        with patch.object(A, "show_chats", new=AsyncMock()) as chats:
            await handle_wl_on(cb, chat_settings_repo, _rejected_before(False), **data)

        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "enabled", True)
        service.invalidate.assert_called_once_with(CHAT_ID)
        chats.assert_awaited_once_with(
            cb, "ru", 1, notice="Бот снова работает в чате «Test Chat»", data=data
        )

    @pytest.mark.asyncio
    async def test_already_on_writes_nothing_and_says_the_same(self):
        cb = _make_callback(f"adm_wl_on:en:{CHAT_ID}:0")
        chat_settings_repo = AsyncMock()
        chat_settings_repo.get = AsyncMock(return_value={"chat_title": None, "enabled": True})

        with patch.object(A, "show_chats", new=AsyncMock()) as chats:
            await handle_wl_on(cb, chat_settings_repo, _rejected_before(False))

        chat_settings_repo.set_field.assert_not_awaited()
        assert chats.call_args.kwargs["notice"] == f"The bot is back on in «{CHAT_ID}»"
        assert "undo" not in chats.call_args.kwargs

    @pytest.mark.asyncio
    async def test_a_chat_without_settings_says_so(self):
        cb = _make_callback(f"adm_wl_on:ru:{CHAT_ID}:0")
        chat_settings_repo = AsyncMock()
        chat_settings_repo.get = AsyncMock(return_value=None)

        with patch.object(A, "show_chats", new=AsyncMock()) as chats:
            await handle_wl_on(cb, chat_settings_repo, _rejected_before(False))

        chat_settings_repo.set_field.assert_not_awaited()
        assert chats.call_args.kwargs["notice_raw"].startswith("<i>Этого чата нет в настройках")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload", ["adm_wl_on:ru:not-a-chat:0", "adm_wl_on:ru:-100", "adm_wl_on:ru"]
    )
    async def test_garbled_payload_is_stale_and_writes_nothing(self, payload: str):
        cb = _make_callback(payload)
        chat_settings_repo = AsyncMock()

        with patch.object(A, "show_stale", new=AsyncMock()) as stale:
            await handle_wl_on(cb, chat_settings_repo, _rejected_before(False))

        stale.assert_awaited_once()
        chat_settings_repo.get.assert_not_awaited()
        chat_settings_repo.set_field.assert_not_awaited()


# ---------------------------------------------------------------------------
# Status and statistics
# ---------------------------------------------------------------------------


def _health_row(status: str = "healthy", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "status": status,
        "checked_at": datetime(2026, 1, 15, 11, 55, tzinfo=UTC),
        "db_ok": True,
        "messages_30m": 17,
        "fallbacks_15m": 0,
        "issues": [],
    }
    row.update(extra)
    return row


def _flags(**values: Any) -> AsyncMock:
    """A ``BotConfigRepository`` double: ``get(key)`` returns ``values.get(key)``."""
    repo = AsyncMock()
    repo.get = AsyncMock(side_effect=lambda key: values.get(key))
    return repo


class TestHealth:
    @pytest.mark.asyncio
    async def test_shows_the_last_recorded_check_without_running_one(self):
        cb = _make_callback("adm_health:ru")
        admin_repo = _make_admin_repo()
        admin_repo.get_latest_health_check = AsyncMock(return_value=_health_row())
        checker = MagicMock()
        checker.run_check_now = AsyncMock()

        with patch.object(A, "show", new=AsyncMock()) as show:
            await handle_health(cb, admin_repo, _flags(), health_checker=checker)

        checker.run_check_now.assert_not_awaited()
        screen = _shown(show)
        assert screen.id == "status"
        assert "Всё работает" in screen.text
        assert show.call_args.kwargs["answered"] is False

    @pytest.mark.asyncio
    async def test_refresh_runs_a_check_first(self):
        cb = _make_callback("adm_health:en:now")
        admin_repo = _make_admin_repo()
        checker = MagicMock()
        order: list[str] = []
        checker.run_check_now = AsyncMock(side_effect=lambda: order.append("check"))
        cb.answer = AsyncMock(side_effect=lambda *_a, **_k: order.append("answer"))

        async def latest() -> dict[str, Any]:
            order.append("read")
            return _health_row("critical", db_ok=False)

        admin_repo.get_latest_health_check = AsyncMock(side_effect=latest)

        await handle_health(cb, admin_repo, _flags(), health_checker=checker)

        assert order == ["answer", "check", "read"]  # the spinner stops before the wait
        text, payloads = _edited(cb)
        assert "Something is failing" in text
        assert "adm_health:en:now" in payloads

    @pytest.mark.asyncio
    async def test_a_failed_refresh_still_shows_the_last_check(self):
        cb = _make_callback("adm_health:ru:now")
        admin_repo = _make_admin_repo()
        admin_repo.get_latest_health_check = AsyncMock(return_value=_health_row())
        checker = MagicMock()
        checker.run_check_now = AsyncMock(side_effect=RuntimeError("db down"))

        await handle_health(cb, admin_repo, _flags(), health_checker=checker)

        assert "Всё работает" in _edited(cb)[0]
        cb.answer.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_no_checks_yet(self):
        cb = _make_callback("adm_health:en")

        await handle_health(cb, _make_admin_repo(), _flags())

        assert "No checks yet" in _edited(cb)[0]

    @pytest.mark.asyncio
    async def test_a_bad_check_is_its_own_screen(self):
        cb = _make_callback("adm_health:ru")
        admin_repo = _make_admin_repo()
        issue = {"severity": "warning", "message": "AI calls failed", "hint": "Пополнить"}
        admin_repo.get_latest_health_check = AsyncMock(
            return_value=_health_row("warning", issues=[issue])
        )

        with patch.object(A, "show", new=AsyncMock()) as show:
            await handle_health(cb, admin_repo, _flags())

        screen = _shown(show)
        assert screen.id == "status.bad"
        assert "Пополнить" in screen.text

    @pytest.mark.asyncio
    async def test_no_language_is_stale(self):
        cb = _make_callback("adm_health:")

        with patch.object(A, "show_stale", new=AsyncMock()) as stale:
            await handle_health(cb, _make_admin_repo(), _flags())

        stale.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_board_button_follows_the_kill_switch(self):
        admin_repo = _make_admin_repo()
        admin_repo.get_latest_health_check = AsyncMock(return_value=_health_row())

        cb = _make_callback("adm_health:ru")
        await handle_health(cb, admin_repo, _flags())
        assert "adm_board:ru" in _edited(cb)[1]

        cb = _make_callback("adm_health:ru")
        await handle_health(cb, admin_repo, _flags(admin_widgets=False))
        assert not any(p.startswith("adm_board:") for p in _edited(cb)[1])

    def test_format_health_status_is_the_screen_text(self):
        """Kept for the alert-policy tests, which import it from here."""
        row = _health_row()
        assert A._format_health_status(row, "en") == A.health_text(row, "en")


def _board_callback(data: str = "adm_board:ru") -> MagicMock:
    cb = _make_callback(data)
    cb.message.chat.id = USER_ID
    cb.bot = AsyncMock()
    return cb


class TestBoard:
    @pytest.mark.asyncio
    async def test_sends_the_board_as_a_new_message(self):
        cb = _board_callback()
        admin_repo = _make_admin_repo()
        admin_repo.get_latest_health_check = AsyncMock(return_value=_health_row("warning"))
        admin_repo.get_health_by_hour = AsyncMock(return_value=[])

        await handle_board(cb, admin_repo, _flags())

        (method,), _ = cb.bot.call_args
        assert isinstance(method, SendWidget)
        assert method.chat_id == USER_ID
        assert method.allow_queries is True and method.parse_mode == "HTML"
        assert "Проверить сейчас" in method.html  # Russian words baked in
        assert json.loads(method.chat_data)["v"] == "w"
        assert "Есть предупреждение" in method.caption
        admin_repo.get_health_by_hour.assert_awaited_once_with(24)
        cb.message.edit_text.assert_not_awaited()  # the text screen stays
        cb.answer.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_kill_switch_sends_nothing(self):
        cb = _board_callback("adm_board:en")
        admin_repo = _make_admin_repo()

        await handle_board(cb, admin_repo, _flags(admin_widgets=False))

        cb.bot.assert_not_awaited()
        args, kwargs = cb.answer.call_args
        assert "board is off" in args[0] and kwargs["show_alert"] is True

    @pytest.mark.asyncio
    async def test_a_refused_widget_is_said_not_swallowed(self):
        cb = _board_callback()
        cb.bot.side_effect = TelegramBadRequest(method=MagicMock(), message="WIDGET_TOO_LARGE")
        admin_repo = _make_admin_repo()
        admin_repo.get_latest_health_check = AsyncMock(return_value=None)
        admin_repo.get_health_by_hour = AsyncMock(return_value=[])

        await handle_board(cb, admin_repo, _flags())

        args, kwargs = cb.answer.call_args
        assert "Telegram не принял доску" in args[0] and kwargs["show_alert"] is True

    @pytest.mark.asyncio
    async def test_no_language_is_stale(self):
        cb = _board_callback("adm_board:")

        with patch.object(A, "show_stale", new=AsyncMock()) as stale:
            await handle_board(cb, _make_admin_repo(), _flags())

        stale.assert_awaited_once()
        cb.bot.assert_not_awaited()


class TestStats:
    @staticmethod
    def _repo() -> AsyncMock:
        repo = _make_admin_repo()
        repo.get_enabled_chats_count = AsyncMock(return_value=4)
        repo.get_active_chats_count = AsyncMock(return_value=3)
        repo.get_message_count = AsyncMock(return_value=812)
        repo.get_response_count = AsyncMock(return_value=64)
        repo.get_unauth_count = AsyncMock(return_value=2)
        return repo

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("payload", "interval"),
        [
            ("adm_stats:ru", timedelta(hours=24)),
            ("adm_stats:ru:1h", timedelta(hours=1)),
            ("adm_stats:ru:7d", timedelta(days=7)),
        ],
    )
    async def test_periods(self, payload: str, interval: timedelta):
        cb = _make_callback(payload)
        repo = self._repo()

        await handle_stats(cb, repo)

        repo.get_message_count.assert_awaited_once_with(interval)
        assert "812" in _edited(cb)[0]

    @pytest.mark.asyncio
    async def test_unknown_period_is_stale(self):
        cb = _make_callback("adm_stats:ru:30d")
        repo = self._repo()

        with patch.object(A, "show_stale", new=AsyncMock()) as stale:
            await handle_stats(cb, repo)

        stale.assert_awaited_once()
        repo.get_message_count.assert_not_awaited()


# ---------------------------------------------------------------------------
# General settings: the screen, notifications, language
# ---------------------------------------------------------------------------


class TestSettings:
    @pytest.mark.asyncio
    async def test_button_opens_general_settings(self):
        cb = _make_callback("adm_settings:en")

        await handle_settings(cb)

        text, payloads = _edited(cb)
        assert "General settings" in text
        assert payloads == ["adm_defs:en:0", "adm_notif:en", "adm_lang:en", "adm_menu:en"]


class TestNotifications:
    @pytest.mark.asyncio
    async def test_shows_the_two_switches_only(self):
        cb = _make_callback("adm_notif:ru")

        await handle_notifications_menu(cb, _make_admin_repo(), AsyncMock())

        text, payloads = _edited(cb)
        assert "Уведомления" in text
        assert payloads == ["adm_nstk:ru", "adm_ntog:ru:unauthorized", "adm_settings:ru"]
        assert "не присылаются" not in text

    @pytest.mark.asyncio
    async def test_requests_off_is_said_on_the_screen(self):
        cb = _make_callback("adm_notif:ru")

        await handle_notifications_menu(cb, _make_admin_repo(unauthorized=False), AsyncMock())

        assert "Запросы доступа сейчас не присылаются" in _edited(cb)[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("current", "following"), [("off", "on"), ("on", "detailed"), ("detailed", "off")]
    )
    async def test_sticker_notice_cycles(self, current: str, following: str):
        cb = _make_callback("adm_nstk:ru")
        admin_repo = _make_admin_repo(sticker=current)
        bot_config_repo = AsyncMock()

        await handle_sticker_notification_cycle(cb, admin_repo, bot_config_repo)

        admin_repo.set_notification_setting.assert_awaited_once_with(
            bot_config_repo, "sticker", following
        )
        label = {"off": "выкл", "on": "кратко", "detailed": "подробно"}[following]
        assert _markup(cb).inline_keyboard[0][0].text == f"🖼 Новые стикеры: {label}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("current", [True, False])
    async def test_requests_switch_flips(self, current: bool):
        cb = _make_callback("adm_ntog:ru:unauthorized")
        admin_repo = _make_admin_repo(unauthorized=current)
        bot_config_repo = AsyncMock()

        await handle_notification_toggle(cb, admin_repo, bot_config_repo)

        admin_repo.set_notification_setting.assert_awaited_once_with(
            bot_config_repo, "unauthorized", not current
        )
        label = _markup(cb).inline_keyboard[1][0].text
        assert label.startswith("✅" if not current else "⚪")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload", ["adm_ntog:ru:jailbreak", "adm_ntog:ru:ai_fallback", "adm_ntog:ru"]
    )
    async def test_hidden_or_garbled_switch_is_stale_and_writes_nothing(self, payload: str):
        cb = _make_callback(payload)
        admin_repo = _make_admin_repo()

        with patch.object(A, "show_stale", new=AsyncMock()) as stale:
            await handle_notification_toggle(cb, admin_repo, AsyncMock())

        stale.assert_awaited_once()
        admin_repo.set_notification_setting.assert_not_awaited()


class TestLanguage:
    @pytest.mark.asyncio
    async def test_menu_offers_both(self):
        cb = _make_callback("adm_lang:ru")

        await handle_language_menu(cb)

        text, payloads = _edited(cb)
        assert "Язык панели" in text
        assert payloads == ["adm_lang_set:ru:ru", "adm_lang_set:ru:en", "adm_settings:ru"]

    @pytest.mark.asyncio
    async def test_saving_shows_general_settings_in_the_new_language(self):
        cb = _make_callback("adm_lang_set:ru:en")
        admin_repo = _make_admin_repo()
        bot_config_repo = AsyncMock()

        await handle_language_set(cb, admin_repo, bot_config_repo)

        admin_repo.set_admin_language.assert_awaited_once_with(bot_config_repo, "en")
        text, payloads = _edited(cb)
        assert text.startswith("<i>✓ Panel language: English</i>")
        assert "General settings" in text
        assert payloads[-1] == "adm_menu:en"

    @pytest.mark.asyncio
    async def test_unknown_language_is_stale(self):
        cb = _make_callback("adm_lang_set:ru:de")
        admin_repo = _make_admin_repo()

        with patch.object(A, "show_stale", new=AsyncMock()) as stale:
            await handle_language_set(cb, admin_repo, AsyncMock())

        stale.assert_awaited_once()
        admin_repo.set_admin_language.assert_not_awaited()
