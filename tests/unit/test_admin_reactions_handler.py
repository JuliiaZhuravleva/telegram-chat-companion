"""Tests for the Reactions admin sub-router (R-D1, ADR-0004 Decision 5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ChatMemberStatus
from aiogram.types import Message

from src.bot.handlers.admin_reactions import (
    handle_reactions_menu,
    handle_reactions_picker,
    handle_reactions_toggle,
)

ADMIN_ID = 111
CHAT_ID = -1001234567890
BOT_ID = 999


def _make_callback(data: str, user_id: int = ADMIN_ID, chat_type: str = "private", bot=None):
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.type = chat_type
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = bot
    return callback


def _make_bot(status: ChatMemberStatus | None = ChatMemberStatus.ADMINISTRATOR):
    bot = MagicMock()
    bot_info = MagicMock()
    bot_info.id = BOT_ID
    bot.me = AsyncMock(return_value=bot_info)
    member = MagicMock()
    member.status = status
    bot.get_chat_member = AsyncMock(return_value=member)
    return bot


def _make_bot_config_repo(
    admin_ids: list[int] | None = None,
    default_reactions_enabled: bool | None = None,
    default_reactions_history_enabled: bool | None = None,
) -> MagicMock:
    repo = MagicMock()

    async def _get(key: str):
        if key == "admin_ids":
            return admin_ids or [ADMIN_ID]
        if key == "default_reactions_enabled":
            return default_reactions_enabled
        if key == "default_reactions_history_enabled":
            return default_reactions_history_enabled
        return None

    repo.get = AsyncMock(side_effect=_get)
    return repo


def _make_chat_settings_repo(
    reactions_enabled: bool | None = False,
    reactions_history_enabled: bool | None = True,
) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(
        return_value={
            "reactions_enabled": reactions_enabled,
            "reactions_history_enabled": reactions_history_enabled,
        }
    )
    repo.set_field = AsyncMock()
    return repo


def _make_admin_repo(chats=None, total=0) -> MagicMock:
    repo = MagicMock()
    repo.get_enabled_chats_page = AsyncMock(return_value=(chats or [], total))
    return repo


class TestHandleReactionsPicker:
    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback("adm_react:ru:0", user_id=999)
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_picker(callback, admin_repo, bot_config_repo)

        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True
        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_renders_chat_list_for_admin(self) -> None:
        callback = _make_callback("adm_react:ru:0")
        admin_repo = _make_admin_repo(
            chats=[{"chat_id": CHAT_ID, "chat_title": "Test Chat"}], total=1
        )
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_picker(callback, admin_repo, bot_config_repo)

        callback.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ignores_non_private_chat(self) -> None:
        callback = _make_callback("adm_react:ru:0", chat_type="group")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_picker(callback, admin_repo, bot_config_repo)

        admin_repo.get_enabled_chats_page.assert_not_awaited()


class TestHandleReactionsMenu:
    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback(f"adm_react_menu:ru:{CHAT_ID}", user_id=999, bot=_make_bot())
        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_menu(callback, chat_settings_repo, bot_config_repo)

        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shows_admin_ok_status_line(self) -> None:
        bot = _make_bot(ChatMemberStatus.ADMINISTRATOR)
        callback = _make_callback(f"adm_react_menu:ru:{CHAT_ID}", bot=bot)
        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_menu(callback, chat_settings_repo, bot_config_repo)

        bot.get_chat_member.assert_awaited_once_with(CHAT_ID, BOT_ID)
        text = callback.message.edit_text.call_args.args[0]
        assert "✅" in text

    @pytest.mark.asyncio
    async def test_shows_missing_admin_status_line(self) -> None:
        bot = _make_bot(ChatMemberStatus.MEMBER)
        callback = _make_callback(f"adm_react_menu:ru:{CHAT_ID}", bot=bot)
        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_menu(callback, chat_settings_repo, bot_config_repo)

        text = callback.message.edit_text.call_args.args[0]
        assert "НЕ администратор" in text

    @pytest.mark.asyncio
    async def test_invalid_chat_id(self) -> None:
        callback = _make_callback("adm_react_menu:ru:not-a-number", bot=_make_bot())
        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_menu(callback, chat_settings_repo, bot_config_repo)

        callback.message.edit_text.assert_not_awaited()
        assert callback.answer.call_args.kwargs.get("show_alert") is True


class TestHandleReactionsToggle:
    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback(
            f"adm_react_toggle:ru:{CHAT_ID}:reactions_enabled", user_id=999, bot=_make_bot()
        )
        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_toggle(callback, chat_settings_repo, bot_config_repo)

        chat_settings_repo.set_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_unknown_field(self) -> None:
        callback = _make_callback(
            f"adm_react_toggle:ru:{CHAT_ID}:not_a_real_field", bot=_make_bot()
        )
        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_toggle(callback, chat_settings_repo, bot_config_repo)

        chat_settings_repo.set_field.assert_not_awaited()
        assert callback.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_flips_history_toggle_without_gating_on_admin_status(self) -> None:
        """reactions_history_enabled never GATES on admin status (it's not
        the module master toggle -- ADR-0004 Decision 3): the flip commits
        even when the bot isn't an admin. (The subsequent menu re-render
        still does its own live check for the status line -- Decision 5(b) --
        so get_chat_member IS called, just not as a gate on this field.)"""
        bot = _make_bot(ChatMemberStatus.MEMBER)
        callback = _make_callback(
            f"adm_react_toggle:ru:{CHAT_ID}:reactions_history_enabled", bot=bot
        )
        chat_settings_repo = _make_chat_settings_repo(reactions_history_enabled=True)
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_toggle(callback, chat_settings_repo, bot_config_repo)

        chat_settings_repo.set_field.assert_awaited_once_with(
            CHAT_ID, "reactions_history_enabled", False
        )
        popup = callback.answer.call_args.args[0]
        assert "не администратор" not in popup

    @pytest.mark.asyncio
    async def test_enabling_reactions_when_bot_is_admin_no_warning(self) -> None:
        bot = _make_bot(ChatMemberStatus.ADMINISTRATOR)
        callback = _make_callback(f"adm_react_toggle:ru:{CHAT_ID}:reactions_enabled", bot=bot)
        chat_settings_repo = _make_chat_settings_repo(reactions_enabled=False)
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_toggle(callback, chat_settings_repo, bot_config_repo)

        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "reactions_enabled", True)
        bot.get_chat_member.assert_awaited_with(CHAT_ID, BOT_ID)
        # Normal toggle-on confirmation, not the not-admin warning.
        popup = callback.answer.call_args.args[0]
        assert "не администратор" not in popup

    @pytest.mark.asyncio
    async def test_enabling_reactions_when_bot_not_admin_warns_immediately(self) -> None:
        """ADR-0004 Decision 5(a): active check at toggle-time, popup warning
        right away -- the silent-failure risk (source plan §5.1) this item
        exists to close."""
        bot = _make_bot(ChatMemberStatus.MEMBER)
        callback = _make_callback(f"adm_react_toggle:ru:{CHAT_ID}:reactions_enabled", bot=bot)
        chat_settings_repo = _make_chat_settings_repo(reactions_enabled=False)
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_toggle(callback, chat_settings_repo, bot_config_repo)

        # The toggle itself still commits -- owner's choice, just informed.
        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "reactions_enabled", True)
        popup = callback.answer.call_args.args[0]
        assert callback.answer.call_args.kwargs.get("show_alert") is True
        assert "не администратор" in popup

    @pytest.mark.asyncio
    async def test_disabling_reactions_skips_the_toggle_time_check(self) -> None:
        """Turning the module OFF must not trigger the toggle-time "just
        enabled, check now" admin-rights probe (Decision 5(a) is specifically
        about turning it ON) -- only a single get_chat_member call, from the
        menu re-render's own status line (Decision 5(b))."""
        bot = _make_bot(ChatMemberStatus.MEMBER)
        callback = _make_callback(f"adm_react_toggle:ru:{CHAT_ID}:reactions_enabled", bot=bot)
        chat_settings_repo = _make_chat_settings_repo(reactions_enabled=True)
        bot_config_repo = _make_bot_config_repo()

        await handle_reactions_toggle(callback, chat_settings_repo, bot_config_repo)

        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "reactions_enabled", False)
        assert bot.get_chat_member.await_count == 1

    @pytest.mark.asyncio
    async def test_null_column_falls_back_to_global_default(self) -> None:
        """Mirrors admin_kb.py's NULL-column-plus-global-default toggle fix:
        raw column NULL + default_reactions_enabled=True means the chat is
        effectively ON, so the first tap must turn it OFF."""
        bot = _make_bot(ChatMemberStatus.ADMINISTRATOR)
        callback = _make_callback(f"adm_react_toggle:ru:{CHAT_ID}:reactions_enabled", bot=bot)
        chat_settings_repo = _make_chat_settings_repo(reactions_enabled=None)
        bot_config_repo = _make_bot_config_repo(default_reactions_enabled=True)

        await handle_reactions_toggle(callback, chat_settings_repo, bot_config_repo)

        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "reactions_enabled", False)
