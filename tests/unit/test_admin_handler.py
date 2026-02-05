"""Tests for admin panel handlers (Stage 3.1.1)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers.admin import (
    handle_admin_command,
    handle_close,
    handle_language_menu,
    handle_language_set,
    handle_menu,
    handle_stats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(
    text: str = "/admin",
    chat_id: int = 12345,
    user_id: int = 12345,
    chat_type: str = "private",
) -> MagicMock:
    """Mock aiogram Message for admin commands."""
    msg = MagicMock()
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


def _make_callback(
    data: str = "adm_menu:ru",
    user_id: int = 12345,
) -> MagicMock:
    """Mock aiogram CallbackQuery for admin callbacks."""
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.answer = AsyncMock()

    # callback.message
    inner_msg = MagicMock()
    inner_msg.edit_text = AsyncMock()
    inner_msg.delete = AsyncMock()
    inner_msg.__class__ = type("Message", (), {})

    # Need isinstance(msg, Message) to pass
    from aiogram.types import Message

    inner_msg = MagicMock(spec=Message)
    inner_msg.edit_text = AsyncMock()
    inner_msg.delete = AsyncMock()
    inner_msg.chat = MagicMock()
    inner_msg.chat.type = "private"
    callback.message = inner_msg

    return callback


def _make_admin_repo() -> MagicMock:
    """Mock AdminRepository."""
    repo = AsyncMock()
    repo.get_admin_language = AsyncMock(return_value="ru")
    repo.set_admin_language = AsyncMock()
    repo.get_message_count = AsyncMock(return_value=42)
    repo.get_response_count = AsyncMock(return_value=10)
    repo.get_unauth_count = AsyncMock(return_value=3)
    repo.get_active_chats_count = AsyncMock(return_value=5)
    repo.get_enabled_chats_count = AsyncMock(return_value=2)
    return repo


def _make_bot_config_repo() -> MagicMock:
    """Mock BotConfigRepository."""
    return AsyncMock()


# ---------------------------------------------------------------------------
# /admin command
# ---------------------------------------------------------------------------


class TestAdminCommand:
    @pytest.mark.asyncio
    async def test_shows_menu_in_private_chat(self):
        msg = _make_message(chat_type="private")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_admin_command(msg, admin_repo, bot_config_repo)

        msg.answer.assert_awaited_once()
        call_kwargs = msg.answer.call_args
        assert "HTML" in str(call_kwargs)
        assert call_kwargs.kwargs.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_ignores_group_chat(self):
        msg = _make_message(chat_type="group")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_admin_command(msg, admin_repo, bot_config_repo)

        msg.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uses_admin_language(self):
        msg = _make_message(chat_type="private")
        admin_repo = _make_admin_repo()
        admin_repo.get_admin_language = AsyncMock(return_value="en")
        bot_config_repo = _make_bot_config_repo()

        await handle_admin_command(msg, admin_repo, bot_config_repo)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args.args[0]
        assert "Admin Panel" in text


# ---------------------------------------------------------------------------
# adm_menu callback
# ---------------------------------------------------------------------------


class TestMenuCallback:
    @pytest.mark.asyncio
    async def test_shows_menu_for_admin(self):
        cb = _make_callback("adm_menu:ru")
        await handle_menu(cb, is_admin=True)

        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "Панель" in text

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_menu:ru")
        await handle_menu(cb, is_admin=False)

        cb.answer.assert_awaited_once()
        assert cb.answer.call_args.kwargs.get("show_alert") is True
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_english_menu(self):
        cb = _make_callback("adm_menu:en")
        await handle_menu(cb, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "Admin Panel" in text


# ---------------------------------------------------------------------------
# adm_lang callback
# ---------------------------------------------------------------------------


class TestLanguageMenu:
    @pytest.mark.asyncio
    async def test_shows_language_options(self):
        cb = _make_callback("adm_lang:ru")
        await handle_language_menu(cb, is_admin=True)

        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_lang:ru")
        await handle_language_menu(cb, is_admin=False)

        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# adm_lang_set callback
# ---------------------------------------------------------------------------


class TestLanguageSet:
    @pytest.mark.asyncio
    async def test_saves_language(self):
        cb = _make_callback("adm_lang_set:ru:en")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_language_set(
            cb, admin_repo, bot_config_repo, is_admin=True
        )

        admin_repo.set_admin_language.assert_awaited_once_with(
            bot_config_repo, "en"
        )
        # Menu refreshed in new language
        text = cb.message.edit_text.call_args.args[0]
        assert "Admin Panel" in text

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_lang_set:ru:en")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_language_set(
            cb, admin_repo, bot_config_repo, is_admin=False
        )

        admin_repo.set_admin_language.assert_not_awaited()


# ---------------------------------------------------------------------------
# adm_close callback
# ---------------------------------------------------------------------------


class TestCloseCallback:
    @pytest.mark.asyncio
    async def test_deletes_message(self):
        cb = _make_callback("adm_close:ru")
        await handle_close(cb, is_admin=True)

        cb.answer.assert_awaited_once()
        cb.message.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_close:ru")
        await handle_close(cb, is_admin=False)

        cb.message.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# adm_stats callback
# ---------------------------------------------------------------------------


class TestStatsCallback:
    @pytest.mark.asyncio
    async def test_shows_stats_default_period(self):
        cb = _make_callback("adm_stats:ru:24h")
        admin_repo = _make_admin_repo()

        await handle_stats(cb, admin_repo, is_admin=True)

        admin_repo.get_message_count.assert_awaited_once_with(timedelta(hours=24))
        admin_repo.get_response_count.assert_awaited_once_with(timedelta(hours=24))
        text = cb.message.edit_text.call_args.args[0]
        assert "42" in text  # message count
        assert "10" in text  # response count

    @pytest.mark.asyncio
    async def test_shows_stats_1h(self):
        cb = _make_callback("adm_stats:ru:1h")
        admin_repo = _make_admin_repo()

        await handle_stats(cb, admin_repo, is_admin=True)

        admin_repo.get_message_count.assert_awaited_once_with(timedelta(hours=1))

    @pytest.mark.asyncio
    async def test_shows_stats_7d(self):
        cb = _make_callback("adm_stats:en:7d")
        admin_repo = _make_admin_repo()

        await handle_stats(cb, admin_repo, is_admin=True)

        admin_repo.get_message_count.assert_awaited_once_with(timedelta(days=7))
        text = cb.message.edit_text.call_args.args[0]
        assert "Bot Statistics" in text

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_stats:ru:24h")
        admin_repo = _make_admin_repo()

        await handle_stats(cb, admin_repo, is_admin=False)

        admin_repo.get_message_count.assert_not_awaited()
