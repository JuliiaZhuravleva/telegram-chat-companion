"""Tests for the per-chat system_prompt editor in the chat settings panel.

Before this the panel's «Системный промпт» button carried ``callback_data="noop"``
and nothing happened on a tap (Julia, 2026-11-05).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, Message, Sticker, User
from magic_filter import MagicFilter

from src.bot.handlers.admin_chat_panel import (
    _SP_MAX_CHARS,
    handle_chat_panel_sp_cancel,
    handle_chat_panel_sp_input,
    handle_chat_panel_sp_prompt,
    handle_chat_panel_sp_redo,
    handle_chat_panel_sp_save,
    router,
)
from src.bot.states.admin import AdminStates
from src.models.chat_config import ChatConfig
from src.utils.telegram_text import parsed_length

ADMIN_ID = 111
CHAT_ID = -1001234567890
DM_ID = 111


class FakeState:
    """An FSMContext that really stores, so read-modify-write races show."""

    def __init__(self, state: str | None = None, data: dict[str, Any] | None = None) -> None:
        self.state = state
        self.data: dict[str, Any] = dict(data or {})

    async def set_state(self, state: Any) -> None:
        await asyncio.sleep(0)
        self.state = state.state if hasattr(state, "state") else state

    async def get_state(self) -> str | None:
        await asyncio.sleep(0)
        return self.state

    async def get_data(self) -> dict[str, Any]:
        await asyncio.sleep(0)
        return dict(self.data)

    async def update_data(self, **kwargs: Any) -> None:
        await asyncio.sleep(0)
        self.data.update(kwargs)

    async def clear(self) -> None:
        self.state = None
        self.data = {}


def _editing(chat_id: int = CHAT_ID, **data: Any) -> FakeState:
    base = {"sp_chat_id": chat_id, "sp_lang": "ru", "sp_parts": [], "sp_previews": []}
    base.update(data)
    return FakeState(AdminStates.awaiting_system_prompt.state, base)


def _bot_config_repo() -> MagicMock:
    repo = MagicMock()

    async def _get(key: str) -> Any:
        return [ADMIN_ID] if key == "admin_ids" else None

    repo.get = AsyncMock(side_effect=_get)
    return repo


def _chat_settings_repo(row: dict[str, Any] | None, written: bool = True) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=row)
    repo.set_field = AsyncMock(return_value=written)
    return repo


def _config_service() -> MagicMock:
    service = MagicMock()
    service.get_config = AsyncMock(return_value=replace(ChatConfig(chat_id=CHAT_ID)))
    service.invalidate = MagicMock()
    return service


_next_id = iter(range(5000, 10_000))


def _sent_message() -> MagicMock:
    sent = MagicMock()
    sent.message_id = next(_next_id)
    return sent


def _callback(data: str, user_id: int = ADMIN_ID, message_id: int = 900) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock(spec=Message)
    callback.message.message_id = message_id
    callback.message.chat = MagicMock()
    callback.message.chat.type = "private"
    callback.message.chat.id = DM_ID
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock(side_effect=lambda *_a, **_k: _sent_message())
    callback.answer = AsyncMock()
    return callback


def _message(text: str, message_id: int, user_id: int = ADMIN_ID) -> MagicMock:
    message = MagicMock()
    message.text = text
    message.message_id = message_id
    message.chat = MagicMock()
    message.chat.id = DM_ID
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.reply = AsyncMock()
    message.answer = AsyncMock(side_effect=lambda *_a, **_k: _sent_message())
    return message


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.delete_message = AsyncMock()
    return bot


def _sent_texts(mock: AsyncMock) -> list[str]:
    return [c.args[0] for c in mock.call_args_list]


# ── opening the editor ──────────────────────────────────────────────────────


class TestOpenEditor:
    @pytest.mark.asyncio
    async def test_shows_current_prompt_escaped_in_expandable_quote(self) -> None:
        callback = _callback(f"adm_pnl_sp:ru:{CHAT_ID}")
        state = FakeState()
        repo = _chat_settings_repo({"chat_title": "Чат", "system_prompt": "Ты <b>бот</b> & друг"})

        await handle_chat_panel_sp_prompt(callback, state, repo, _bot_config_repo())

        assert state.state == AdminStates.awaiting_system_prompt.state
        assert state.data["sp_chat_id"] == CHAT_ID
        assert state.data["sp_parts"] == []
        (text,) = _sent_texts(callback.message.answer)
        assert "<blockquote expandable>Ты &lt;b&gt;бот&lt;/b&gt; &amp; друг</blockquote>" in text
        markup = callback.message.answer.call_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == f"adm_pnl_spx:ru:{CHAT_ID}"

    @pytest.mark.asyncio
    async def test_long_prompt_is_split_and_buttons_go_on_the_last_part(self) -> None:
        callback = _callback(f"adm_pnl_sp:ru:{CHAT_ID}")
        long_prompt = "\n".join(f"Строка правила номер {i}, с деталями." for i in range(400))
        repo = _chat_settings_repo({"chat_title": "Чат", "system_prompt": long_prompt})

        await handle_chat_panel_sp_prompt(callback, FakeState(), repo, _bot_config_repo())

        calls = callback.message.answer.call_args_list
        assert len(calls) >= 3
        for c in calls:
            assert parsed_length(c.args[0]) <= 4096
            assert c.args[0].count("<blockquote expandable>") == c.args[0].count("</blockquote>")
        assert [c.kwargs.get("reply_markup") is not None for c in calls] == [False] * (
            len(calls) - 1
        ) + [True]

    @pytest.mark.asyncio
    async def test_empty_prompt_says_there_is_none(self) -> None:
        callback = _callback(f"adm_pnl_sp:ru:{CHAT_ID}")
        repo = _chat_settings_repo({"chat_title": "Чат", "system_prompt": ""})

        await handle_chat_panel_sp_prompt(callback, FakeState(), repo, _bot_config_repo())

        (text,) = _sent_texts(callback.message.answer)
        assert "нет" in text
        assert "<blockquote" not in text

    @pytest.mark.asyncio
    async def test_non_admin_gets_nothing(self) -> None:
        callback = _callback(f"adm_pnl_sp:ru:{CHAT_ID}", user_id=999)
        state = FakeState()
        repo = _chat_settings_repo({"system_prompt": "secret"})

        await handle_chat_panel_sp_prompt(callback, state, repo, _bot_config_repo())

        assert state.state is None
        callback.message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_chat_is_refused_without_entering_the_state(self) -> None:
        callback = _callback(f"adm_pnl_sp:ru:{CHAT_ID}")
        state = FakeState()

        await handle_chat_panel_sp_prompt(
            callback, state, _chat_settings_repo(None), _bot_config_repo()
        )

        assert state.state is None
        assert callback.answer.call_args.kwargs.get("show_alert") is True


# ── typing the new prompt ───────────────────────────────────────────────────


class TestInput:
    @pytest.mark.asyncio
    async def test_preview_quotes_the_draft_and_offers_save(self) -> None:
        state = _editing()
        message = _message("Новая <инструкция>", message_id=10)

        await handle_chat_panel_sp_input(message, state, _bot(), _bot_config_repo())

        (text,) = _sent_texts(message.answer)
        assert "<blockquote expandable>Новая &lt;инструкция&gt;</blockquote>" in text
        assert "Сохраняем?" in text
        markup = message.answer.call_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == f"adm_pnl_spok:ru:{CHAT_ID}"
        assert state.data["sp_parts"] == [[10, "Новая <инструкция>"]]
        assert len(state.data["sp_previews"]) == 1

    @pytest.mark.asyncio
    async def test_second_message_appends_and_replaces_the_old_preview(self) -> None:
        state = _editing()
        bot = _bot()
        await handle_chat_panel_sp_input(_message("часть 1", 10), state, bot, _bot_config_repo())
        first_preview = state.data["sp_previews"][0]

        second = _message("часть 2", 11)
        await handle_chat_panel_sp_input(second, state, bot, _bot_config_repo())

        assert "часть 1\nчасть 2" in _sent_texts(second.answer)[0]
        bot.delete_message.assert_awaited_once_with(chat_id=DM_ID, message_id=first_preview)

    @pytest.mark.asyncio
    async def test_parts_are_ordered_by_message_id_not_arrival(self) -> None:
        state = _editing()
        await handle_chat_panel_sp_input(_message("второе", 11), state, _bot(), _bot_config_repo())
        last = _message("первое", 10)
        await handle_chat_panel_sp_input(last, state, _bot(), _bot_config_repo())

        assert "первое\nвторое" in _sent_texts(last.answer)[0]

    @pytest.mark.asyncio
    async def test_concurrent_parts_of_one_paste_are_both_kept(self) -> None:
        """Telegram splits a long paste into messages that aiogram handles
        concurrently; without the per-admin lock the second write dropped the
        first part."""
        state = _editing()
        bot = _bot()
        await asyncio.gather(
            handle_chat_panel_sp_input(_message("A" * 10, 10), state, bot, _bot_config_repo()),
            handle_chat_panel_sp_input(_message("B" * 10, 11), state, bot, _bot_config_repo()),
            handle_chat_panel_sp_input(_message("C" * 10, 12), state, bot, _bot_config_repo()),
        )

        assert sorted(p[0] for p in state.data["sp_parts"]) == [10, 11, 12]

    @pytest.mark.asyncio
    async def test_too_long_is_refused_and_the_draft_is_kept(self) -> None:
        state = _editing(sp_parts=[[10, "x" * (_SP_MAX_CHARS - 5)]], sp_previews=[77])
        bot = _bot()
        message = _message("y" * 10, 11)

        await handle_chat_panel_sp_input(message, state, bot, _bot_config_repo())

        message.reply.assert_awaited_once()
        assert "Слишком длинно" in message.reply.call_args.args[0]
        assert state.data["sp_parts"] == [[10, "x" * (_SP_MAX_CHARS - 5)]]
        assert state.data["sp_previews"] == [77]
        bot.delete_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_admin_input_clears_the_state(self) -> None:
        state = _editing()
        message = _message("hi", 10, user_id=999)

        await handle_chat_panel_sp_input(message, state, _bot(), _bot_config_repo())

        assert state.state is None
        message.answer.assert_not_awaited()


class TestInputFilters:
    """The chat-type/text filters, evaluated on real aiogram Messages."""

    @staticmethod
    def _accepts(msg: Message) -> bool:
        handler = next(
            h for h in router.message.handlers if h.callback is handle_chat_panel_sp_input
        )
        checked = 0
        for f in handler.filters or ():
            cb = getattr(f, "callback", f)
            if not isinstance(getattr(cb, "__self__", None), MagicFilter):
                continue
            checked += 1
            if not cb(msg):
                return False
        assert checked == 3
        return True

    @staticmethod
    def _msg(chat_type: str = "private", **kwargs: Any) -> Message:
        return Message(
            message_id=1,
            date=dt.datetime(2026, 11, 5, tzinfo=dt.UTC),
            chat=Chat(id=DM_ID if chat_type == "private" else CHAT_ID, type=chat_type),
            from_user=User(id=ADMIN_ID, is_bot=False, first_name="A"),
            **kwargs,
        )

    def test_private_text_is_taken(self) -> None:
        assert self._accepts(self._msg(text="Ты бот чата")) is True

    def test_commands_pass_through(self) -> None:
        assert self._accepts(self._msg(text="/cancel")) is False

    def test_non_text_does_not_match(self) -> None:
        sticker = Sticker(
            file_id="f",
            file_unique_id="u",
            type="regular",
            width=1,
            height=1,
            is_animated=False,
            is_video=False,
        )
        assert self._accepts(self._msg(sticker=sticker)) is False

    def test_group_text_is_not_taken(self) -> None:
        assert self._accepts(self._msg(chat_type="supergroup", text="hi")) is False

    def test_state_filter_is_the_editor_state(self) -> None:
        handler = next(
            h for h in router.message.handlers if h.callback is handle_chat_panel_sp_input
        )
        callbacks = [f.callback for f in handler.filters or ()]
        assert AdminStates.awaiting_system_prompt in callbacks
        assert AdminStates.awaiting_setting_value not in callbacks


# ── save / start over / cancel ──────────────────────────────────────────────


class TestSave:
    @pytest.mark.asyncio
    async def test_writes_the_assembled_draft_and_returns_to_behavior(self) -> None:
        state = _editing(sp_parts=[[11, "второе  "], [10, "  первое"]])
        repo = _chat_settings_repo({"chat_title": "Чат"})
        service = _config_service()
        callback = _callback(f"adm_pnl_spok:ru:{CHAT_ID}")

        await handle_chat_panel_sp_save(callback, state, repo, _bot_config_repo(), service)

        repo.set_field.assert_awaited_once_with(CHAT_ID, "system_prompt", "первое\nвторое")
        service.invalidate.assert_called_once_with(CHAT_ID)
        assert state.state is None
        text = callback.message.edit_text.call_args.args[0]
        assert text.startswith("<i>✓ Инструкция сохранена")
        assert "Поведение" in text or "Behavior" in text
        markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[-1][0].callback_data == f"adm_pnl_menu:ru:{CHAT_ID}"

    @pytest.mark.asyncio
    async def test_stale_button_after_restart_writes_nothing(self) -> None:
        repo = _chat_settings_repo({"chat_title": "Чат"})
        callback = _callback(f"adm_pnl_spok:ru:{CHAT_ID}")

        await handle_chat_panel_sp_save(
            callback, FakeState(), repo, _bot_config_repo(), _config_service()
        )

        repo.set_field.assert_not_awaited()
        assert "Черновик не найден" in callback.message.edit_text.call_args.args[0]

    @pytest.mark.asyncio
    async def test_draft_of_another_chat_is_not_written_here(self) -> None:
        state = _editing(chat_id=-100999, sp_parts=[[10, "чужое"]])
        repo = _chat_settings_repo({"chat_title": "Чат"})

        await handle_chat_panel_sp_save(
            _callback(f"adm_pnl_spok:ru:{CHAT_ID}"),
            state,
            repo,
            _bot_config_repo(),
            _config_service(),
        )

        repo.set_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_another_flow_with_leftover_keys_is_stale(self) -> None:
        """``update_data`` merges, so the tolerance flow keeps our keys around."""
        state = _editing(sp_parts=[[10, "old"]])
        state.state = AdminStates.awaiting_setting_value.state
        repo = _chat_settings_repo({"chat_title": "Чат"})

        await handle_chat_panel_sp_save(
            _callback(f"adm_pnl_spok:ru:{CHAT_ID}"),
            state,
            repo,
            _bot_config_repo(),
            _config_service(),
        )

        repo.set_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_row_is_reported_not_announced_as_saved(self) -> None:
        state = _editing(sp_parts=[[10, "текст"]])
        repo = _chat_settings_repo({"chat_title": "Чат"}, written=False)
        service = _config_service()
        callback = _callback(f"adm_pnl_spok:ru:{CHAT_ID}")

        await handle_chat_panel_sp_save(callback, state, repo, _bot_config_repo(), service)

        service.invalidate.assert_not_called()
        assert callback.answer.call_args.kwargs.get("show_alert") is True
        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_admin_cannot_save(self) -> None:
        state = _editing(sp_parts=[[10, "текст"]])
        repo = _chat_settings_repo({"chat_title": "Чат"})

        await handle_chat_panel_sp_save(
            _callback(f"adm_pnl_spok:ru:{CHAT_ID}", user_id=999),
            state,
            repo,
            _bot_config_repo(),
            _config_service(),
        )

        repo.set_field.assert_not_awaited()


class TestRedoAndCancel:
    @pytest.mark.asyncio
    async def test_redo_empties_the_draft_and_keeps_waiting(self) -> None:
        state = _editing(sp_parts=[[10, "черновик"]], sp_previews=[55, 900])
        bot = _bot()
        callback = _callback(f"adm_pnl_spre:ru:{CHAT_ID}", message_id=900)

        await handle_chat_panel_sp_redo(
            callback, state, bot, _chat_settings_repo({}), _bot_config_repo(), _config_service()
        )

        assert state.state == AdminStates.awaiting_system_prompt.state
        assert state.data["sp_parts"] == []
        bot.delete_message.assert_awaited_once_with(chat_id=DM_ID, message_id=55)
        assert "Черновик очищен" in callback.message.edit_text.call_args.args[0]

    @pytest.mark.asyncio
    async def test_cancel_clears_and_returns_to_behavior(self) -> None:
        state = _editing(sp_parts=[[10, "черновик"]])
        repo = _chat_settings_repo({"chat_title": "Чат"})
        callback = _callback(f"adm_pnl_spx:ru:{CHAT_ID}")

        await handle_chat_panel_sp_cancel(
            callback, state, repo, _bot_config_repo(), _config_service()
        )

        assert state.state is None
        repo.set_field.assert_not_awaited()
        assert callback.message.edit_text.call_args.args[0].startswith("<i>Инструкция не изменена")

    @pytest.mark.asyncio
    async def test_cancel_with_malformed_data_still_clears(self) -> None:
        state = _editing()
        callback = _callback("adm_pnl_spx:ru:not-an-int")

        await handle_chat_panel_sp_cancel(
            callback, state, _chat_settings_repo({}), _bot_config_repo(), _config_service()
        )

        assert state.state is None
