"""The doors into the bot: /start (plain and ``/start admin``), /help, and /admin in a group.

Two kinds of test here. Handler tests call the function and read what was
sent. Filter tests run a handler's *registered* filters against a real
aiogram ``Message`` — the only way to see that a handler does not match,
because aiogram stops at the first handler whose filters pass and that
handler consumes the update even if its body returns early.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, InlineKeyboardMarkup, Message, User

from src.bot.handlers import commands
from src.bot.handlers.commands import (
    handle_admin_in_group,
    handle_help,
    handle_help_dm,
    handle_start,
    handle_start_group,
    handle_start_panel,
    handle_summary,
)
from src.models.chat_config import ChatConfig

USER_ID = 4242
GROUP_ID = -1009000000001
BOT_USERNAME = "example_companion_bot"


def _config(**overrides: Any) -> ChatConfig:
    return ChatConfig(chat_id=GROUP_ID, **overrides)


def _message(chat_type: str = "private", text: str = "/start") -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.type = chat_type
    msg.chat.id = USER_ID if chat_type == "private" else GROUP_ID
    msg.from_user = MagicMock()
    msg.from_user.id = USER_ID
    msg.text = text
    msg.answer = AsyncMock()
    msg.reply = AsyncMock()
    return msg


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.me = AsyncMock(return_value=SimpleNamespace(username=BOT_USERNAME))
    return bot


def _bot_config(admin_lang: str | None = "en") -> MagicMock:
    repo = MagicMock()
    raw = None if admin_lang is None else f'{{"lang": "{admin_lang}"}}'
    repo.get = AsyncMock(return_value=raw)
    return repo


def _sent(msg: MagicMock, method: str = "answer") -> tuple[str, InlineKeyboardMarkup | None]:
    call = getattr(msg, method).await_args
    return call.args[0], call.kwargs.get("reply_markup")


def _buttons(markup: InlineKeyboardMarkup | None) -> list[tuple[str, str | None, str | None]]:
    if markup is None:
        return []
    return [(b.text, b.callback_data, b.url) for row in markup.inline_keyboard for b in row]


def _real_message(text: str, chat_type: str) -> Message:
    chat_id = USER_ID if chat_type == "private" else GROUP_ID
    return Message(
        message_id=1,
        date=datetime(2026, 11, 3, tzinfo=UTC),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=User(id=USER_ID, is_bot=False, first_name="Test"),
        text=text,
    )


def _handler(callback: Any) -> Any:
    return next(h for h in commands.router.message.handlers if h.callback is callback)


async def _matches(callback: Any, text: str, chat_type: str, *, is_admin: bool) -> bool:
    passed, _ = await _handler(callback).check(
        _real_message(text, chat_type), bot=_bot(), is_admin=is_admin
    )
    return passed


async def _first_message_handler(router: Any, event: Message, **data: Any) -> Any:
    """The callback aiogram would run for ``event``: own handlers first, then sub-routers."""
    observer = router.message
    passed, extra = await observer._handler.check(event, **data)
    if passed:
        for handler in observer.handlers:
            matched, _ = await handler.check(event, **{**data, **extra})
            if matched:
                return handler.callback
    for sub in router.sub_routers:
        found = await _first_message_handler(sub, event, **data)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------


class TestStartRouting:
    def test_the_deep_link_is_registered_before_plain_start(self) -> None:
        """A plain CommandStart() matches "/start admin" too: order is the whole mechanism."""
        callbacks = [h.callback for h in commands.router.message.handlers]
        assert callbacks.index(handle_start_panel) < callbacks.index(handle_start)

    @pytest.mark.asyncio
    async def test_deep_link_matches_a_bot_admin_in_the_dm(self) -> None:
        assert await _matches(handle_start_panel, "/start admin", "private", is_admin=True)

    @pytest.mark.asyncio
    async def test_deep_link_does_not_match_anyone_else(self) -> None:
        """…who then falls through to the plain /start: the link is public, the panel is not."""
        assert not await _matches(handle_start_panel, "/start admin", "private", is_admin=False)
        assert await _matches(handle_start, "/start admin", "private", is_admin=False)

    @pytest.mark.asyncio
    async def test_deep_link_needs_its_own_payload(self) -> None:
        assert not await _matches(handle_start_panel, "/start", "private", is_admin=True)
        assert not await _matches(handle_start_panel, "/start other", "private", is_admin=True)

    @pytest.mark.asyncio
    async def test_plain_start_in_a_dm_is_not_admin_gated(self) -> None:
        assert await _matches(handle_start, "/start", "private", is_admin=False)
        assert not await _matches(handle_start, "/start", "supergroup", is_admin=False)
        assert await _matches(handle_start_group, "/start", "supergroup", is_admin=False)


class TestStart:
    @pytest.mark.asyncio
    async def test_the_deep_link_opens_the_hub_in_the_admin_language(self) -> None:
        msg = _message(text="/start admin")

        await handle_start_panel(msg, _bot_config("en"), is_admin=True)

        # Compared with the hub's own builder, so a new hub wording cannot break it.
        from src.bot.screens.hub import build_hub
        from src.bot.screens.render import render_text

        text, _ = _sent(msg)
        assert text == render_text(build_hub("en"))
        assert text != render_text(build_hub("ru")), "the control: the two languages differ"

    @pytest.mark.asyncio
    async def test_a_bot_admin_gets_the_panel_button(self) -> None:
        msg = _message()

        await handle_start(msg, _config(), _bot_config("en"), _bot(), is_admin=True)

        text, markup = _sent(msg)
        assert "Chat companion" in text
        assert ("🛠 Panel", "adm_menu:en", None) in _buttons(markup)

    @pytest.mark.asyncio
    async def test_anyone_else_gets_the_add_link_in_the_chat_language(self) -> None:
        msg = _message()
        repo = _bot_config("en")

        await handle_start(msg, _config(language="ru"), repo, _bot(), is_admin=False)

        text, markup = _sent(msg)
        assert "Чат-компаньон" in text
        labels = _buttons(markup)
        assert (
            "➕ Добавить в свой чат",
            None,
            f"https://t.me/{BOT_USERNAME}?startgroup=true",
        ) in labels
        assert ("📖 Что я умею", "help_dm:ru:help", None) in labels
        repo.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_without_a_username_the_add_link_is_left_out(self) -> None:
        msg = _message()
        bot = MagicMock()
        bot.me = AsyncMock(side_effect=RuntimeError("network"))

        await handle_start(msg, _config(), _bot_config(), bot, is_admin=False)

        _, markup = _sent(msg)
        assert all(url is None for _, _, url in _buttons(markup))

    @pytest.mark.asyncio
    async def test_an_unreadable_admin_language_is_russian(self) -> None:
        msg = _message()
        repo = MagicMock()
        repo.get = AsyncMock(side_effect=RuntimeError("db down"))

        await handle_start(msg, _config(), repo, _bot(), is_admin=True)

        _, markup = _sent(msg)
        assert ("🛠 Панель", "adm_menu:ru", None) in _buttons(markup)

    @pytest.mark.asyncio
    async def test_start_in_a_group_shows_the_group_help(self) -> None:
        msg = _message(chat_type="supergroup")

        await handle_start_group(msg, _config())

        text, markup = _sent(msg)
        assert "Я участник чата" in text
        assert (
            "✖️ Закрыть",
            f"help_close:{USER_ID}",
            None,
        ) in _buttons(markup)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------


class TestHelp:
    @pytest.mark.asyncio
    async def test_group_help_lists_only_what_is_switched_on(self) -> None:
        msg = _message(chat_type="supergroup", text="/help")
        cfg = _config(
            transcribe_voice=True,
            transcribe_video_notes=False,
            rag_enabled=False,
            chunks_enabled=False,
            kb_enabled=False,
            save_messages=True,
        )

        await handle_help(msg, cfg)

        text, markup = _sent(msg)
        assert "Расшифровываю голосовые" in text
        assert "кружочки" not in text
        assert "Помню" not in text
        assert "/kb" not in text
        assert "/summary" in text
        assert [cb for _, cb, _ in _buttons(markup)] == [
            f"help_summary:{USER_ID}:100",
            f"help_summary:{USER_ID}:500",
            f"help_close:{USER_ID}",
        ]

    @pytest.mark.asyncio
    async def test_no_saved_messages_means_no_summary_buttons(self) -> None:
        msg = _message(chat_type="group", text="/help")

        await handle_help(msg, _config(save_messages=False, kb_enabled=True))

        text, markup = _sent(msg)
        assert "/summary" not in text
        assert "/kb" in text
        assert [cb for _, cb, _ in _buttons(markup)] == [f"help_close:{USER_ID}"]

    @pytest.mark.asyncio
    async def test_the_archive_counts_as_memory(self) -> None:
        msg = _message(chat_type="supergroup", text="/help")

        await handle_help(msg, _config(rag_enabled=False, chunks_enabled=True))

        assert "Помню" in _sent(msg)[0]

    @pytest.mark.asyncio
    async def test_the_trigger_word_is_escaped(self) -> None:
        msg = _message(chat_type="supergroup", text="/help")

        await handle_help(msg, _config(trigger_words=("<бот>",)))

        assert "«&lt;бот&gt;»" in _sent(msg)[0]

    @pytest.mark.asyncio
    async def test_dm_help_goes_back_to_the_right_start(self) -> None:
        member = _message(text="/help")
        await handle_help_dm(member, _config(), _bot_config("en"), _bot(), is_admin=False)
        assert ("⬅️ Старт", "help_dm:ru:start", None) in _buttons(_sent(member)[1])

        admin = _message(text="/help")
        await handle_help_dm(admin, _config(), _bot_config("en"), _bot(), is_admin=True)
        assert ("⬅️ Start", "help_dm:en:start", None) in _buttons(_sent(admin)[1])


# ---------------------------------------------------------------------------
# /admin and /settings in a group
# ---------------------------------------------------------------------------


class TestAdminHintInGroup:
    @pytest.mark.asyncio
    async def test_a_bot_admin_in_a_group_gets_a_link_to_the_dm(self) -> None:
        msg = _message(chat_type="supergroup", text="/admin")

        await handle_admin_in_group(msg, _config(), _bot())

        msg.reply.assert_awaited_once()
        text, markup = _sent(msg, "reply")
        assert "в личке" in text
        assert _buttons(markup) == [
            ("🛠 Открыть панель", None, f"https://t.me/{BOT_USERNAME}?start=admin")
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command", ["/admin", "/settings"])
    async def test_the_filters_take_a_group_command_from_a_bot_admin(self, command: str) -> None:
        assert await _matches(handle_admin_in_group, command, "supergroup", is_admin=True)
        assert await _matches(handle_admin_in_group, command, "group", is_admin=True)

    @pytest.mark.asyncio
    async def test_the_filters_leave_the_dm_to_the_admin_router(self) -> None:
        """In a DM /admin belongs to the admin router; this handler must not match there.

        Holds whichever order the routers end up in: the admin router is
        included first and, once its /admin handler is private-only, a group
        /admin reaches this one.
        """
        assert not await _matches(handle_admin_in_group, "/admin", "private", is_admin=True)

    @pytest.mark.asyncio
    async def test_the_filters_ignore_members(self) -> None:
        """A member's /admin in a group stays unanswered: nothing to offer them."""
        assert not await _matches(handle_admin_in_group, "/admin", "supergroup", is_admin=False)

    def test_the_commands_router_comes_after_the_admin_router(self) -> None:
        from src.bot.handlers import router

        names = [r.name for r in router.sub_routers]
        assert names.index("admin") < names.index("commands")

    @pytest.mark.asyncio
    async def test_a_group_admin_command_reaches_the_hint_across_the_whole_router(self) -> None:
        """Walk the real router tree in aiogram's order and find who takes the update.

        The filter tests above hold for this handler alone; whether the hint is
        ever SENT depends on every router included before it. Today the
        panel's own /admin handler (admin router, first in line) has no
        chat-type filter, takes the group command and returns: that is the
        panel's change to make, so this is an expected failure until it lands
        and turns into a plain pass by itself afterwards.
        """
        from src.bot.handlers import admin as admin_handlers
        from src.bot.handlers import router

        event = _real_message("/admin", "supergroup")
        taker = await _first_message_handler(
            router, event, bot=_bot(), is_admin=True, raw_state=None
        )

        if taker is getattr(admin_handlers, "handle_admin_command", None):
            pytest.xfail("the panel's /admin handler still takes group commands (area A1)")
        assert taker is handle_admin_in_group, taker


# ---------------------------------------------------------------------------
# /summary with a count it cannot use
# ---------------------------------------------------------------------------


class TestSummaryInvalid:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["/summary abc", "/summary 5000"])
    async def test_offers_the_two_counts_as_buttons(self, text: str) -> None:
        msg = _message(chat_type="supergroup", text=text)
        service = MagicMock()
        service.generate = AsyncMock()

        await handle_summary(msg, _config(), service)

        service.generate.assert_not_awaited()
        reply, markup = _sent(msg, "reply")
        assert msg.reply.await_args.kwargs.get("parse_mode") == "HTML"
        assert "<code>/summary 300</code>" in reply
        assert [cb for _, cb, _ in _buttons(markup)] == [
            f"help_summary:{USER_ID}:100",
            f"help_summary:{USER_ID}:500",
        ]
