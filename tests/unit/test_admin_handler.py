"""Tests for admin panel handlers."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers.admin import (
    handle_admin_command,
    handle_approve_notification,
    handle_close,
    handle_language_menu,
    handle_language_set,
    handle_menu,
    handle_reject_notification,
    handle_stats,
    handle_wl_approve,
    handle_wl_chats,
    handle_wl_pending,
    handle_wl_reject,
    handle_wl_remove,
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
    inner_msg.edit_reply_markup = AsyncMock()
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


def _make_chat_settings_repo() -> MagicMock:
    """Mock ChatSettingsRepository."""
    repo = AsyncMock()
    repo.set_field = AsyncMock()
    repo.upsert = AsyncMock()
    return repo


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


# ---------------------------------------------------------------------------
# adm_wl_chats callback
# ---------------------------------------------------------------------------


class TestWlChats:
    @pytest.mark.asyncio
    async def test_shows_chats_list(self):
        cb = _make_callback("adm_wl_chats:ru:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_enabled_chats_page = AsyncMock(
            return_value=(
                [
                    {"chat_id": -100, "chat_title": "Alpha", "chat_type": "group"},
                    {"chat_id": -200, "chat_title": "Beta", "chat_type": "supergroup"},
                ],
                2,
            )
        )

        await handle_wl_chats(cb, admin_repo, is_admin=True)

        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "Alpha" in text
        assert "Beta" in text

    @pytest.mark.asyncio
    async def test_shows_empty_message(self):
        cb = _make_callback("adm_wl_chats:en:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_enabled_chats_page = AsyncMock(return_value=([], 0))

        await handle_wl_chats(cb, admin_repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "No whitelisted" in text

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_wl_chats:ru:0")
        admin_repo = _make_admin_repo()

        await handle_wl_chats(cb, admin_repo, is_admin=False)

        cb.message.edit_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# adm_wl_pending callback
# ---------------------------------------------------------------------------


class TestWlPending:
    @pytest.mark.asyncio
    async def test_shows_pending_list(self):
        cb = _make_callback("adm_wl_pending:ru:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_pending_attempts_page = AsyncMock(
            return_value=(
                [
                    {
                        "id": 1,
                        "chat_id": -100,
                        "chat_title": "Test Chat",
                        "chat_type": "group",
                        "user_id": 456,
                        "user_first_name": "Alice",
                        "user_username": "alice",
                        "message_text": "hello",
                        "created_at": None,
                    }
                ],
                1,
            )
        )

        await handle_wl_pending(cb, admin_repo, is_admin=True)

        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "Test Chat" in text
        assert "@alice" in text

    @pytest.mark.asyncio
    async def test_shows_empty_message(self):
        cb = _make_callback("adm_wl_pending:en:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_pending_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_pending(cb, admin_repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "No pending" in text

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_wl_pending:ru:0")
        admin_repo = _make_admin_repo()

        await handle_wl_pending(cb, admin_repo, is_admin=False)

        cb.message.edit_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# adm_wl_rm callback
# ---------------------------------------------------------------------------


class TestWlRemove:
    @pytest.mark.asyncio
    async def test_removes_chat_and_rerenders(self):
        cb = _make_callback("adm_wl_rm:ru:-100:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_enabled_chats_page = AsyncMock(return_value=([], 0))
        chat_settings_repo = _make_chat_settings_repo()

        await handle_wl_remove(cb, admin_repo, chat_settings_repo, is_admin=True)

        chat_settings_repo.set_field.assert_awaited_once_with(-100, "enabled", False)
        cb.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_wl_rm:ru:-100:0")
        admin_repo = _make_admin_repo()
        chat_settings_repo = _make_chat_settings_repo()

        await handle_wl_remove(cb, admin_repo, chat_settings_repo, is_admin=False)

        chat_settings_repo.set_field.assert_not_awaited()


# ---------------------------------------------------------------------------
# adm_approve / adm_reject (notification) callbacks
# ---------------------------------------------------------------------------


class TestApproveNotification:
    @pytest.mark.asyncio
    async def test_approves_pending_attempt(self):
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 42, "chat_id": -100, "status": "pending"}
        )
        admin_repo.update_attempt_status = AsyncMock(
            return_value={"id": 42, "status": "approved"}
        )
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(
            cb, admin_repo, chat_settings_repo, is_admin=True
        )

        admin_repo.update_attempt_status.assert_awaited_once_with(42, "approved")
        chat_settings_repo.upsert.assert_awaited_once_with(-100, enabled=True)
        cb.message.edit_reply_markup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_already_handled(self):
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 42, "chat_id": -100, "status": "approved"}
        )
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(
            cb, admin_repo, chat_settings_repo, is_admin=True
        )

        admin_repo.update_attempt_status.assert_not_awaited()
        cb.answer.assert_awaited()
        assert cb.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(
            cb, admin_repo, chat_settings_repo, is_admin=False
        )

        admin_repo.get_attempt.assert_not_awaited()


class TestRejectNotification:
    @pytest.mark.asyncio
    async def test_rejects_pending_attempt(self):
        cb = _make_callback("adm_reject:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 42, "chat_id": -100, "status": "pending"}
        )
        admin_repo.update_attempt_status = AsyncMock(
            return_value={"id": 42, "status": "rejected"}
        )

        await handle_reject_notification(cb, admin_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_awaited_once_with(42, "rejected")
        cb.message.edit_reply_markup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_already_handled(self):
        cb = _make_callback("adm_reject:en:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 42, "chat_id": -100, "status": "rejected"}
        )

        await handle_reject_notification(cb, admin_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_not_awaited()


# ---------------------------------------------------------------------------
# adm_wl_apr / adm_wl_rej (panel) callbacks
# ---------------------------------------------------------------------------


class TestWlApprove:
    @pytest.mark.asyncio
    async def test_approves_and_rerenders(self):
        cb = _make_callback("adm_wl_apr:ru:42:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 42, "chat_id": -100, "status": "pending"}
        )
        admin_repo.update_attempt_status = AsyncMock(
            return_value={"id": 42, "status": "approved"}
        )
        admin_repo.get_pending_attempts_page = AsyncMock(return_value=([], 0))
        chat_settings_repo = _make_chat_settings_repo()

        await handle_wl_approve(
            cb, admin_repo, chat_settings_repo, is_admin=True
        )

        admin_repo.update_attempt_status.assert_awaited_once_with(42, "approved")
        chat_settings_repo.upsert.assert_awaited_once_with(-100, enabled=True)
        # Should re-render pending list
        cb.message.edit_text.assert_awaited_once()


class TestWlReject:
    @pytest.mark.asyncio
    async def test_rejects_and_rerenders(self):
        cb = _make_callback("adm_wl_rej:ru:42:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 42, "chat_id": -100, "status": "pending"}
        )
        admin_repo.update_attempt_status = AsyncMock(
            return_value={"id": 42, "status": "rejected"}
        )
        admin_repo.get_pending_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_reject(cb, admin_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_awaited_once_with(42, "rejected")
        cb.message.edit_text.assert_awaited_once()
