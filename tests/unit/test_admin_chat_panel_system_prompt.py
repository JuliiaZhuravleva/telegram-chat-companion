"""Tests for the per-chat system_prompt editor in the chat settings panel.

Before this the panel's «Системный промпт» button carried ``callback_data="noop"``
and nothing happened on a tap (Julia, 2026-11-05).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Chat, Message, MessageEntity, Sticker, User
from magic_filter import MagicFilter

from src.bot.custom_emoji import FENCE
from src.bot.handlers.admin_chat_panel import (
    handle_chat_panel_sp_cancel,
    handle_chat_panel_sp_input,
    handle_chat_panel_sp_prompt,
    handle_chat_panel_sp_redo,
    handle_chat_panel_sp_save,
    router,
)
from src.bot.screens import deps
from src.bot.screens.chat import SP_MAX_CHARS as _SP_MAX_CHARS
from src.bot.screens.chat import build_sp_preview, build_sp_prompt, build_sp_refused
from src.bot.screens.render import to_markup
from src.bot.states.admin import AdminStates
from src.database.repositories.admin import AdminRepository
from src.models.chat_config import ChatConfig
from src.utils.telegram_text import parsed_length

ADMIN_ID = 111
REV = 10  # draft version = id of the last message added; _editing() parts end at 10
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
    base = {
        "sp_chat_id": chat_id,
        "sp_lang": "ru",
        "sp_parts": [],
        "sp_previews": [],
        "sp_rev": REV,
        "sp_overflow": False,
    }
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


def _admin_data() -> tuple[dict[str, Any], MagicMock]:
    """The handler's ``**kwargs`` with a container that holds the chat list's repository."""
    admin_repo = MagicMock(spec=AdminRepository)
    admin_repo.count_attempts = AsyncMock(return_value=0)
    admin_repo.get_enabled_chats_page_by_activity = AsyncMock(
        return_value=([{"chat_id": -100200301, "chat_title": "Второй чат", "msg_count": 3}], 1)
    )
    container = MagicMock()
    container.get = AsyncMock(
        side_effect=lambda dependency: {AdminRepository: admin_repo}[dependency]
    )
    return {deps.SCREEN_CONTAINER: container}, admin_repo


_CHAT_GONE_RU = "<i>Этого чата нет в настройках, ничего не изменилось. Вот подключённые чаты.</i>"


def _assert_chat_list_under_chat_gone(callback: MagicMock, admin_repo: MagicMock) -> None:
    """What every panel screen shows for a chat without a settings row."""
    admin_repo.get_enabled_chats_page_by_activity.assert_awaited()
    text = callback.message.edit_text.await_args.args[0]
    assert text.startswith(_CHAT_GONE_RU + "\n\n"), text
    datas = [
        b.callback_data
        for row in callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert "adm_pnl_menu:ru:-100200301" in datas
    assert all(c.kwargs.get("show_alert") is not True for c in callback.answer.await_args_list)


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


def _message(
    text: str,
    message_id: int,
    user_id: int = ADMIN_ID,
    entities: list[MessageEntity] | None = None,
) -> MagicMock:
    message = MagicMock()
    message.text = text
    message.entities = entities
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
    async def test_a_chat_title_starting_with_an_emoji_is_fenced(self) -> None:
        """The title sits at the start of a line, where the emoji middleware draws marks."""
        callback = _callback(f"adm_pnl_sp:ru:{CHAT_ID}")
        repo = _chat_settings_repo({"chat_title": "🔥 Чат", "system_prompt": ""})

        await handle_chat_panel_sp_prompt(callback, FakeState(), repo, _bot_config_repo())

        (text,) = _sent_texts(callback.message.answer)
        assert f"\n{FENCE}🔥 Чат <code>{CHAT_ID}</code>" in text

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
    async def test_unknown_chat_shows_the_chat_list_without_entering_the_state(self) -> None:
        callback = _callback(f"adm_pnl_sp:ru:{CHAT_ID}")
        state = FakeState()
        data, admin_repo = _admin_data()

        await handle_chat_panel_sp_prompt(
            callback, state, _chat_settings_repo(None), _bot_config_repo(), **data
        )

        assert state.state is None
        callback.message.answer.assert_not_awaited()
        _assert_chat_list_under_chat_gone(callback, admin_repo)


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
        assert markup.inline_keyboard[0][0].callback_data == f"adm_pnl_spok:ru:{CHAT_ID}:10"
        assert state.data["sp_parts"] == [[10, "Новая <инструкция>"]]
        assert len(state.data["sp_previews"]) == 1

    @pytest.mark.asyncio
    async def test_markdown_the_client_turned_into_entities_is_saved_as_typed(self) -> None:
        """The admin pasted ``## Тон\n**Коротко** и `code```; Telegram sent bold and code."""
        state = _editing()
        message = _message(
            "## Тон\nКоротко и code",
            message_id=10,
            entities=[
                MessageEntity(type="bold", offset=7, length=7),
                MessageEntity(type="code", offset=17, length=4),
            ],
        )

        await handle_chat_panel_sp_input(message, state, _bot(), _bot_config_repo())

        assert state.data["sp_parts"] == [[10, "## Тон\n**Коротко** и `code`"]]
        (text,) = _sent_texts(message.answer)
        assert "**Коротко** и `code`" in text  # the preview shows what will be saved

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
        assert state.data["sp_overflow"] is True
        bot.delete_message.assert_not_awaited()
        markup = message.reply.call_args.kwargs["reply_markup"]
        assert not any(
            b.callback_data.startswith("adm_pnl_spok:") for r in markup.inline_keyboard for b in r
        )

    @pytest.mark.asyncio
    async def test_after_a_refused_part_later_parts_do_not_fill_the_hole(self) -> None:
        """Review finding: parts 1, 2 accepted, 3 refused, 4 short enough — the
        preview of 1+2+4 looked complete and Save wrote a prompt missing its middle."""
        bot = _bot()
        state = _editing()
        chunk = "z" * 4096
        for mid in (10, 11, 12):
            await handle_chat_panel_sp_input(_message(chunk, mid), state, bot, _bot_config_repo())
        fourth = _message("short tail", 13)
        await handle_chat_panel_sp_input(fourth, state, bot, _bot_config_repo())

        assert [p[0] for p in state.data["sp_parts"]] == [10, 11]
        fourth.answer.assert_not_awaited()
        fourth.reply.assert_awaited_once()

        repo = _chat_settings_repo({"chat_title": "Чат"})
        old_save = _callback(f"adm_pnl_spok:ru:{CHAT_ID}:11")
        await handle_chat_panel_sp_save(
            old_save, state, repo, _bot_config_repo(), _config_service()
        )
        repo.set_field.assert_not_awaited()
        assert old_save.answer.call_args.kwargs.get("show_alert") is True

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
        callback = _callback(f"adm_pnl_spok:ru:{CHAT_ID}:{REV}")

        await handle_chat_panel_sp_save(callback, state, repo, _bot_config_repo(), service)

        repo.set_field.assert_awaited_once_with(CHAT_ID, "system_prompt", "первое\nвторое")
        service.invalidate.assert_called_once_with(CHAT_ID)
        assert state.state is None
        text = callback.message.edit_text.call_args.args[0]
        assert text.startswith("<i>✓ Инструкция сохранена")
        assert "Поведение" in text or "Behavior" in text
        markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
        # The back button is the last one of the last row (it shares it with «Инструкция»).
        assert markup.inline_keyboard[-1][-1].callback_data == f"adm_pnl_menu:ru:{CHAT_ID}"

    @pytest.mark.asyncio
    async def test_stale_button_after_restart_writes_nothing(self) -> None:
        repo = _chat_settings_repo({"chat_title": "Чат"})
        callback = _callback(f"adm_pnl_spok:ru:{CHAT_ID}:{REV}")

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
            _callback(f"adm_pnl_spok:ru:{CHAT_ID}:{REV}"),
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
            _callback(f"adm_pnl_spok:ru:{CHAT_ID}:{REV}"),
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
        callback = _callback(f"adm_pnl_spok:ru:{CHAT_ID}:{REV}")
        data, admin_repo = _admin_data()

        await handle_chat_panel_sp_save(callback, state, repo, _bot_config_repo(), service, **data)

        service.invalidate.assert_not_called()
        # The draft has nowhere to go: the input closes rather than eat the next message.
        assert state.state is None
        _assert_chat_list_under_chat_gone(callback, admin_repo)
        assert "сохранена" not in callback.message.edit_text.await_args.args[0]

    @pytest.mark.asyncio
    async def test_non_admin_cannot_save(self) -> None:
        state = _editing(sp_parts=[[10, "текст"]])
        repo = _chat_settings_repo({"chat_title": "Чат"})

        await handle_chat_panel_sp_save(
            _callback(f"adm_pnl_spok:ru:{CHAT_ID}:{REV}", user_id=999),
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
        callback = _callback(f"adm_pnl_spre:ru:{CHAT_ID}:{REV}", message_id=900)

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

        with patch("src.bot.handlers.admin_chat_panel.show_stale", new=AsyncMock()) as stale:
            await handle_chat_panel_sp_cancel(
                callback, state, _chat_settings_repo({}), _bot_config_repo(), _config_service()
            )

        assert state.state is None
        stale.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_from_another_chats_screen_keeps_this_draft(self) -> None:
        state = _editing(chat_id=-100999, sp_parts=[[10, "черновик"]])

        await handle_chat_panel_sp_cancel(
            _callback(f"adm_pnl_spx:ru:{CHAT_ID}"),
            state,
            _chat_settings_repo({"chat_title": "Чат"}),
            _bot_config_repo(),
            _config_service(),
        )

        assert state.state == AdminStates.awaiting_system_prompt.state
        assert state.data["sp_parts"] == [[10, "черновик"]]

    @pytest.mark.asyncio
    async def test_cancel_does_not_close_another_flow(self) -> None:
        state = FakeState(AdminStates.awaiting_setting_value.state, {"chat_id": CHAT_ID})

        await handle_chat_panel_sp_cancel(
            _callback(f"adm_pnl_spx:ru:{CHAT_ID}"),
            state,
            _chat_settings_repo({"chat_title": "Чат"}),
            _bot_config_repo(),
            _config_service(),
        )

        assert state.state == AdminStates.awaiting_setting_value.state


class TestDraftVersion:
    """Review finding: Save and «Start over» checked only state and chat_id,
    so a button under an older preview acted on a draft it did not show."""

    @pytest.mark.asyncio
    async def test_save_under_an_older_preview_writes_nothing(self) -> None:
        state = _editing()
        bot = _bot()
        await handle_chat_panel_sp_input(_message("A", 20), state, bot, _bot_config_repo())
        await handle_chat_panel_sp_input(_message("B", 21), state, bot, _bot_config_repo())
        repo = _chat_settings_repo({"chat_title": "Чат"})

        old = _callback(f"adm_pnl_spok:ru:{CHAT_ID}:20")
        await handle_chat_panel_sp_save(old, state, repo, _bot_config_repo(), _config_service())

        repo.set_field.assert_not_awaited()
        assert old.answer.call_args.kwargs.get("show_alert") is True
        assert state.state == AdminStates.awaiting_system_prompt.state

        new = _callback(f"adm_pnl_spok:ru:{CHAT_ID}:21")
        await handle_chat_panel_sp_save(new, state, repo, _bot_config_repo(), _config_service())
        repo.set_field.assert_awaited_once_with(CHAT_ID, "system_prompt", "A\nB")

    @pytest.mark.asyncio
    async def test_preview_from_an_earlier_session_cannot_save_the_new_draft(self) -> None:
        """Send A, cancel, reopen, send B, tap Save under A's preview."""
        repo = _chat_settings_repo({"chat_title": "Чат", "system_prompt": ""})
        state = FakeState()
        bot = _bot()
        await handle_chat_panel_sp_prompt(
            _callback(f"adm_pnl_sp:ru:{CHAT_ID}"), state, repo, _bot_config_repo()
        )
        await handle_chat_panel_sp_input(_message("A", 30), state, bot, _bot_config_repo())
        await handle_chat_panel_sp_cancel(
            _callback(f"adm_pnl_spx:ru:{CHAT_ID}"),
            state,
            repo,
            _bot_config_repo(),
            _config_service(),
        )
        await handle_chat_panel_sp_prompt(
            _callback(f"adm_pnl_sp:ru:{CHAT_ID}"), state, repo, _bot_config_repo()
        )
        await handle_chat_panel_sp_input(_message("B", 32), state, bot, _bot_config_repo())

        stale = _callback(f"adm_pnl_spok:ru:{CHAT_ID}:30")
        await handle_chat_panel_sp_save(stale, state, repo, _bot_config_repo(), _config_service())

        repo.set_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_over_under_an_older_preview_keeps_the_draft(self) -> None:
        state = _editing(sp_parts=[[10, "x"], [11, "y"]], sp_rev=11)

        await handle_chat_panel_sp_redo(
            _callback(f"adm_pnl_spre:ru:{CHAT_ID}:10"),
            state,
            _bot(),
            _chat_settings_repo({}),
            _bot_config_repo(),
            _config_service(),
        )

        assert len(state.data["sp_parts"]) == 2

    @pytest.mark.asyncio
    async def test_start_over_resets_version_and_overflow(self) -> None:
        state = _editing(sp_parts=[[10, "x"]], sp_overflow=True)

        await handle_chat_panel_sp_redo(
            _callback(f"adm_pnl_spre:ru:{CHAT_ID}:{REV}"),
            state,
            _bot(),
            _chat_settings_repo({}),
            _bot_config_repo(),
            _config_service(),
        )

        assert state.data["sp_rev"] == 0
        assert state.data["sp_overflow"] is False
        message = _message("заново", 40)
        await handle_chat_panel_sp_input(message, state, _bot(), _bot_config_repo())
        assert state.data["sp_parts"] == [[40, "заново"]]


class TestSystemPromptKeyboards:
    def test_system_prompt_keyboards_carry_distinct_prefixes(self) -> None:
        """``startswith("adm_pnl_sp:")`` must not also catch save/redo/cancel."""
        cancel = to_markup(build_sp_prompt(CHAT_ID, "Чат", "", "ru"))
        confirm = to_markup(build_sp_preview(CHAT_ID, "текст", 2147483647, "ru"))
        datas = [b.callback_data for kb in (cancel, confirm) for r in kb.inline_keyboard for b in r]
        assert sorted(datas) == sorted(
            [
                f"adm_pnl_spx:ru:{CHAT_ID}",
                f"adm_pnl_spok:ru:{CHAT_ID}:2147483647",
                f"adm_pnl_spre:ru:{CHAT_ID}:2147483647",
                f"adm_pnl_spx:ru:{CHAT_ID}",
            ]
        )
        assert not any(d.startswith("adm_pnl_sp:") for d in datas)
        assert all(len(d.encode()) <= 64 for d in datas)

    @pytest.mark.parametrize("length", [None, 12345])
    def test_incomplete_draft_keyboard_has_no_save(self, length: int | None) -> None:
        kb = to_markup(build_sp_refused(CHAT_ID, 5, "ru", length=length))
        datas = [b.callback_data for r in kb.inline_keyboard for b in r]
        assert datas == [f"adm_pnl_spre:ru:{CHAT_ID}:5", f"adm_pnl_spx:ru:{CHAT_ID}"]

    def test_the_exit_is_the_panel_wide_cancel(self) -> None:
        """One name per thing: the editor's way out reads like every other prompt's."""
        for screen in (
            build_sp_prompt(CHAT_ID, None, "", "ru"),
            build_sp_preview(CHAT_ID, "текст", 5, "ru"),
            build_sp_refused(CHAT_ID, 5, "ru"),
        ):
            cancels = [b for row in screen.rows for b in row if b.kind == "cancel"]
            assert [b.label for b in cancels] == ["✖️ Отмена"], screen.id


class TestEditorCopy:
    """The editor's own words address the admin as «вы», like the rest of the panel."""

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_every_editor_screen_passes_the_screen_rules(self, lang: str) -> None:
        from src.bot.screens.lint import check_screen
        from src.bot.screens.registry import load_all

        editor = [s for i, v in load_all().items() if i.startswith("sp.") for s in v]
        assert {s.screen_id for s in editor} == {"sp.prompt", "sp.preview", "sp.refused", "sp.redo"}
        found = {
            (s.screen_id, s.title): check_screen(s.build(lang), sample=s, lang=lang) for s in editor
        }
        assert all(not v for v in found.values()), found

    def test_handler_alerts_use_the_polite_form(self) -> None:
        from src.bot.handlers import admin_chat_panel as handler
        from src.bot.screens.lint import SINGULAR_IMPERATIVES, THOU_WORDS

        words = re.compile(
            r"(?<!\w)(?:" + "|".join((*THOU_WORDS, *SINGULAR_IMPERATIVES)) + r")(?!\w)",
            re.IGNORECASE,
        )
        copy = [
            v["ru"]
            for name, v in vars(handler).items()
            if name.startswith("_SP_") and isinstance(v, dict) and "ru" in v
        ]
        assert copy, "no editor copy found: the control would pass vacuously"
        assert [c for c in copy if words.search(c)] == []
