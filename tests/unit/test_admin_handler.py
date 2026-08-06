"""Tests for admin panel handlers."""

from __future__ import annotations

from datetime import UTC, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers.admin import (
    handle_admin_command,
    handle_approve_notification,
    handle_close,
    handle_health,
    handle_language_menu,
    handle_language_set,
    handle_menu,
    handle_notification_toggle,
    handle_notifications_menu,
    handle_reject_notification,
    handle_stats,
    handle_sticker_notification_cycle,
    handle_wl_approve,
    handle_wl_chats,
    handle_wl_delete,
    handle_wl_delete_ask,
    handle_wl_pending,
    handle_wl_reject,
    handle_wl_rejected,
    handle_wl_remove,
    handle_wl_remove_ask,
    handle_wl_restore,
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
    repo.get_notification_settings = AsyncMock(
        return_value={
            "sticker": "on",
            "unauthorized": True,
            "jailbreak": True,
            "blacklist": True,
            "ai_fallback": True,
        }
    )
    repo.set_notification_setting = AsyncMock()
    return repo


def _make_bot_config_repo() -> MagicMock:
    """Mock BotConfigRepository."""
    return AsyncMock()


def _make_chat_settings_repo() -> MagicMock:
    """Mock ChatSettingsRepository."""
    repo = AsyncMock()
    repo.set_field = AsyncMock()
    repo.upsert = AsyncMock()
    repo.get = AsyncMock(return_value=None)
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

        await handle_language_set(cb, admin_repo, bot_config_repo, is_admin=True)

        admin_repo.set_admin_language.assert_awaited_once_with(bot_config_repo, "en")
        # Menu refreshed in new language
        text = cb.message.edit_text.call_args.args[0]
        assert "Admin Panel" in text

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_lang_set:ru:en")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_language_set(cb, admin_repo, bot_config_repo, is_admin=False)

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
        chat_settings_repo = _make_chat_settings_repo()
        admin_repo.get_enabled_chats_page = AsyncMock(
            return_value=(
                [
                    {"chat_id": -100, "chat_title": "Alpha", "chat_type": "group"},
                    {"chat_id": -200, "chat_title": "Beta", "chat_type": "supergroup"},
                ],
                2,
            )
        )

        await handle_wl_chats(cb, admin_repo, chat_settings_repo, is_admin=True)

        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "Alpha" in text
        assert "Beta" in text

    @pytest.mark.asyncio
    async def test_shows_empty_message(self):
        cb = _make_callback("adm_wl_chats:en:0")
        admin_repo = _make_admin_repo()
        chat_settings_repo = _make_chat_settings_repo()
        admin_repo.get_enabled_chats_page = AsyncMock(return_value=([], 0))

        await handle_wl_chats(cb, admin_repo, chat_settings_repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "No whitelisted" in text

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_wl_chats:ru:0")
        admin_repo = _make_admin_repo()
        chat_settings_repo = _make_chat_settings_repo()

        await handle_wl_chats(cb, admin_repo, chat_settings_repo, is_admin=False)

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
    async def test_chat_id_is_rendered_as_copyable_code(self):
        """Chat ids must go out wrapped in <code>, never as a bare number.

        Telegram clients auto-detect a bare 9-11 digit run as a phone number and
        style it as a link that goes nowhere — which reads as a broken link. A
        <code> entity suppresses that detection and makes the id tap-to-copy.
        """
        cb = _make_callback("adm_wl_pending:en:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_pending_attempts_page = AsyncMock(
            return_value=(
                [
                    {
                        "id": 1,
                        "chat_id": -1001234567890,
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

        text = cb.message.edit_text.call_args.args[0]
        assert "<code>-1001234567890</code>" in text
        # the old italic-parenthesised form must not come back
        assert "<i>(-1001234567890)</i>" not in text

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
# adm_wl_rm_ask callback (confirmation step)
# ---------------------------------------------------------------------------


class TestWlRemoveAsk:
    @pytest.mark.asyncio
    async def test_shows_confirmation_and_does_not_delete(self):
        cb = _make_callback("adm_wl_rm_ask:ru:-100:0")
        chat_settings_repo = _make_chat_settings_repo()
        chat_settings_repo.get = AsyncMock(return_value={"chat_title": "Alpha"})

        await handle_wl_remove_ask(cb, chat_settings_repo, is_admin=True)

        # Must NOT disable the chat at this stage
        chat_settings_repo.set_field.assert_not_awaited()
        # Shows confirmation screen with title + buttons
        cb.message.edit_text.assert_awaited_once()
        call = cb.message.edit_text.call_args
        text = call.args[0]
        assert "Alpha" in text
        assert "-100" in text
        kb = call.kwargs["reply_markup"]
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]
        assert "adm_wl_rm:ru:-100:0" in callbacks
        assert "adm_wl_chats:ru:0" in callbacks

    @pytest.mark.asyncio
    async def test_falls_back_to_chat_id_when_title_missing(self):
        cb = _make_callback("adm_wl_rm_ask:en:-200:2")
        chat_settings_repo = _make_chat_settings_repo()
        chat_settings_repo.get = AsyncMock(return_value=None)

        await handle_wl_remove_ask(cb, chat_settings_repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "-200" in text
        # Confirm callback preserves page=2
        kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]
        assert "adm_wl_rm:en:-200:2" in callbacks

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_wl_rm_ask:ru:-100:0")
        chat_settings_repo = _make_chat_settings_repo()

        await handle_wl_remove_ask(cb, chat_settings_repo, is_admin=False)

        chat_settings_repo.set_field.assert_not_awaited()
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
        admin_repo.approve_all_for_chat = AsyncMock(return_value=1)
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(cb, admin_repo, chat_settings_repo, is_admin=True)

        admin_repo.approve_all_for_chat.assert_awaited_once_with(-100)
        chat_settings_repo.upsert.assert_awaited_once_with(-100, enabled=True)
        cb.message.edit_reply_markup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_approved_keyboard_links_to_chat_settings_panel(self):
        """D-1 regression: settings button after approve carries the right chat_id."""
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 42, "chat_id": -100, "status": "pending"}
        )
        admin_repo.approve_all_for_chat = AsyncMock(return_value=1)
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(cb, admin_repo, chat_settings_repo, is_admin=True)

        keyboard = cb.message.edit_reply_markup.call_args.kwargs["reply_markup"]
        callback_datas = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert "adm_pnl_menu:ru:-100" in callback_datas

    @pytest.mark.asyncio
    async def test_already_handled(self):
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 42, "chat_id": -100, "status": "approved"}
        )
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(cb, admin_repo, chat_settings_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_not_awaited()
        cb.answer.assert_awaited()
        assert cb.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(cb, admin_repo, chat_settings_repo, is_admin=False)

        admin_repo.get_attempt.assert_not_awaited()


class TestRejectNotification:
    @pytest.mark.asyncio
    async def test_rejects_pending_attempt(self):
        cb = _make_callback("adm_reject:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 42, "chat_id": -100, "status": "pending"}
        )
        admin_repo.update_attempt_status = AsyncMock(return_value={"id": 42, "status": "rejected"})

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
    async def test_approves_and_shows_settings_link(self):
        """D-1: pending-list approve shows a settings-panel link instead of
        re-rendering the (now shorter) pending list -- the approved attempt
        no longer matches ``get_pending_attempts_page`` and would simply
        vanish, leaving no row to attach the "⚙️ Chat settings" button to.
        """
        cb = _make_callback("adm_wl_apr:ru:42:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 42, "chat_id": -100, "status": "pending"}
        )
        admin_repo.approve_all_for_chat = AsyncMock(return_value=1)
        chat_settings_repo = _make_chat_settings_repo()

        await handle_wl_approve(cb, admin_repo, chat_settings_repo, is_admin=True)

        admin_repo.approve_all_for_chat.assert_awaited_once_with(-100)
        chat_settings_repo.upsert.assert_awaited_once_with(-100, enabled=True)
        admin_repo.get_pending_attempts_page.assert_not_awaited()
        cb.message.edit_text.assert_awaited_once()
        keyboard = cb.message.edit_text.call_args.kwargs["reply_markup"]
        callback_datas = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert "adm_pnl_menu:ru:-100" in callback_datas
        assert "adm_wl_pending:ru:0" in callback_datas


class TestWlReject:
    @pytest.mark.asyncio
    async def test_rejects_and_rerenders(self):
        cb = _make_callback("adm_wl_rej:ru:42:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 42, "chat_id": -100, "status": "pending"}
        )
        admin_repo.update_attempt_status = AsyncMock(return_value={"id": 42, "status": "rejected"})
        admin_repo.get_pending_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_reject(cb, admin_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_awaited_once_with(42, "rejected")
        cb.message.edit_text.assert_awaited_once()


# ---------------------------------------------------------------------------
# adm_notif callback (notification settings)
# ---------------------------------------------------------------------------


class TestNotificationsMenu:
    @pytest.mark.asyncio
    async def test_shows_notification_settings(self):
        cb = _make_callback("adm_notif:ru")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_notifications_menu(cb, admin_repo, bot_config_repo, is_admin=True)

        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "Уведомления" in text

    @pytest.mark.asyncio
    async def test_english_title(self):
        cb = _make_callback("adm_notif:en")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_notifications_menu(cb, admin_repo, bot_config_repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "Notifications" in text

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_notif:ru")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_notifications_menu(cb, admin_repo, bot_config_repo, is_admin=False)

        cb.message.edit_text.assert_not_awaited()


class TestStickerNotificationCycle:
    @pytest.mark.asyncio
    async def test_cycles_on_to_detailed(self):
        cb = _make_callback("adm_nstk:ru")
        admin_repo = _make_admin_repo()
        admin_repo.get_notification_settings.return_value = {
            "sticker": "on",
            "unauthorized": True,
            "jailbreak": True,
            "blacklist": True,
            "ai_fallback": True,
        }
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_notification_cycle(cb, admin_repo, bot_config_repo, is_admin=True)

        admin_repo.set_notification_setting.assert_awaited_once_with(
            bot_config_repo, "sticker", "detailed"
        )
        cb.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cycles_detailed_to_off(self):
        cb = _make_callback("adm_nstk:ru")
        admin_repo = _make_admin_repo()
        admin_repo.get_notification_settings.return_value = {
            "sticker": "detailed",
            "unauthorized": True,
            "jailbreak": True,
            "blacklist": True,
            "ai_fallback": True,
        }
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_notification_cycle(cb, admin_repo, bot_config_repo, is_admin=True)

        admin_repo.set_notification_setting.assert_awaited_once_with(
            bot_config_repo, "sticker", "off"
        )

    @pytest.mark.asyncio
    async def test_cycles_off_to_on(self):
        cb = _make_callback("adm_nstk:ru")
        admin_repo = _make_admin_repo()
        admin_repo.get_notification_settings.return_value = {
            "sticker": "off",
            "unauthorized": True,
            "jailbreak": True,
            "blacklist": True,
            "ai_fallback": True,
        }
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_notification_cycle(cb, admin_repo, bot_config_repo, is_admin=True)

        admin_repo.set_notification_setting.assert_awaited_once_with(
            bot_config_repo, "sticker", "on"
        )

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_nstk:ru")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_notification_cycle(cb, admin_repo, bot_config_repo, is_admin=False)

        admin_repo.set_notification_setting.assert_not_awaited()


class TestNotificationToggle:
    @pytest.mark.asyncio
    async def test_toggles_unauthorized_off(self):
        cb = _make_callback("adm_ntog:ru:unauthorized")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_notification_toggle(cb, admin_repo, bot_config_repo, is_admin=True)

        admin_repo.set_notification_setting.assert_awaited_once_with(
            bot_config_repo, "unauthorized", False
        )
        cb.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_toggles_disabled_back_on(self):
        cb = _make_callback("adm_ntog:ru:jailbreak")
        admin_repo = _make_admin_repo()
        admin_repo.get_notification_settings.return_value = {
            "sticker": "on",
            "unauthorized": True,
            "jailbreak": False,
            "blacklist": True,
            "ai_fallback": True,
        }
        bot_config_repo = _make_bot_config_repo()

        await handle_notification_toggle(cb, admin_repo, bot_config_repo, is_admin=True)

        admin_repo.set_notification_setting.assert_awaited_once_with(
            bot_config_repo, "jailbreak", True
        )

    @pytest.mark.asyncio
    async def test_rejects_invalid_type(self):
        cb = _make_callback("adm_ntog:ru:invalid_type")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_notification_toggle(cb, admin_repo, bot_config_repo, is_admin=True)

        admin_repo.set_notification_setting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_ntog:ru:unauthorized")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_notification_toggle(cb, admin_repo, bot_config_repo, is_admin=False)

        admin_repo.set_notification_setting.assert_not_awaited()


# ---------------------------------------------------------------------------
# adm_health callback
# ---------------------------------------------------------------------------


class TestHealthCallback:
    @pytest.mark.asyncio
    async def test_shows_latest_health_result(self):
        from datetime import datetime

        cb = _make_callback("adm_health:ru")
        admin_repo = _make_admin_repo()
        admin_repo.get_latest_health_check = AsyncMock(
            return_value={
                "id": 1,
                "status": "healthy",
                "checked_at": datetime(2026, 2, 10, 15, 0, tzinfo=UTC),
                "db_ok": True,
                "messages_30m": 42,
                "fallbacks_15m": 0,
                "ai_provider": "gemini",
                "issues": [],
                "alert_sent": False,
            }
        )

        await handle_health(cb, admin_repo, is_admin=True)

        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "HEALTHY" in text
        assert "42" in text  # messages_30m

    @pytest.mark.asyncio
    async def test_shows_no_data_when_empty(self):
        cb = _make_callback("adm_health:en")
        admin_repo = _make_admin_repo()
        admin_repo.get_latest_health_check = AsyncMock(return_value=None)

        await handle_health(cb, admin_repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "No health check data" in text

    @pytest.mark.asyncio
    async def test_shows_issues_when_present(self):
        from datetime import datetime

        cb = _make_callback("adm_health:en")
        admin_repo = _make_admin_repo()
        admin_repo.get_latest_health_check = AsyncMock(
            return_value={
                "id": 2,
                "status": "warning",
                "checked_at": datetime(2026, 2, 10, 15, 0, tzinfo=UTC),
                "db_ok": True,
                "messages_30m": 5,
                "fallbacks_15m": 3,
                "ai_provider": "gemini",
                "issues": [
                    {"severity": "warning", "message": "AI fallback activated 3 time(s)"},
                ],
                "alert_sent": True,
            }
        )

        await handle_health(cb, admin_repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "WARNING" in text
        assert "fallback" in text.lower()

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_health:ru")
        admin_repo = _make_admin_repo()

        await handle_health(cb, admin_repo, is_admin=False)

        cb.message.edit_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# adm_wl_rejected / restore / delete callbacks
# ---------------------------------------------------------------------------


class TestWlRejected:
    @pytest.mark.asyncio
    async def test_shows_rejected_list(self):
        cb = _make_callback("adm_wl_rejected:ru:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_rejected_attempts_page = AsyncMock(
            return_value=(
                [
                    {
                        "id": 7,
                        "chat_id": -1001234567890,
                        "chat_title": "Rejected Chat",
                        "chat_type": "supergroup",
                        "user_id": 42,
                        "user_first_name": "Mallory",
                        "user_username": "mal",
                        "message_text": "spam",
                        "created_at": None,
                    },
                ],
                1,
            )
        )

        await handle_wl_rejected(cb, admin_repo, is_admin=True)

        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "Rejected Chat" in text
        # Clickable chat link for supergroup
        assert "https://t.me/c/1234567890" in text
        assert "@mal" in text

    @pytest.mark.asyncio
    async def test_shows_empty_message_in_english(self):
        cb = _make_callback("adm_wl_rejected:en:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_rejected(cb, admin_repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "No rejected" in text

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_wl_rejected:ru:0")
        admin_repo = _make_admin_repo()

        await handle_wl_rejected(cb, admin_repo, is_admin=False)

        cb.message.edit_text.assert_not_awaited()


class TestWlRestore:
    @pytest.mark.asyncio
    async def test_restores_to_pending_and_rerenders(self):
        cb = _make_callback("adm_wl_restore:ru:7:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 7, "chat_id": -100, "status": "rejected"}
        )
        admin_repo.update_attempt_status = AsyncMock()
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_restore(cb, admin_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_awaited_once_with(7, "pending")
        cb.message.edit_text.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_update_when_not_rejected(self):
        cb = _make_callback("adm_wl_restore:ru:7:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={"id": 7, "chat_id": -100, "status": "approved"}
        )
        admin_repo.update_attempt_status = AsyncMock()
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_restore(cb, admin_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shows_alert_when_attempt_missing(self):
        cb = _make_callback("adm_wl_restore:en:999:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=None)
        admin_repo.update_attempt_status = AsyncMock()
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_restore(cb, admin_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_not_awaited()
        # Alert shown to admin
        assert cb.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_wl_restore:ru:7:0")
        admin_repo = _make_admin_repo()

        await handle_wl_restore(cb, admin_repo, is_admin=False)

        admin_repo.get_attempt.assert_not_called()


class TestWlDeleteAsk:
    @pytest.mark.asyncio
    async def test_shows_confirm_and_does_not_delete(self):
        cb = _make_callback("adm_wl_del_ask:ru:7:0")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value={
                "id": 7,
                "chat_id": -1001234567890,
                "chat_title": "Mallory's chat",
                "chat_type": "supergroup",
                "status": "rejected",
            }
        )
        admin_repo.delete_attempt = AsyncMock()

        await handle_wl_delete_ask(cb, admin_repo, is_admin=True)

        admin_repo.delete_attempt.assert_not_awaited()
        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "Mallory" in text
        kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]
        assert "adm_wl_del:ru:7:0" in callbacks
        assert "adm_wl_rejected:ru:0" in callbacks

    @pytest.mark.asyncio
    async def test_shows_not_found_alert(self):
        cb = _make_callback("adm_wl_del_ask:en:99:2")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=None)
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_delete_ask(cb, admin_repo, is_admin=True)

        assert cb.answer.call_args.kwargs.get("show_alert") is True


class TestWlDelete:
    @pytest.mark.asyncio
    async def test_deletes_and_rerenders(self):
        cb = _make_callback("adm_wl_del:ru:7:0")
        admin_repo = _make_admin_repo()
        admin_repo.delete_attempt = AsyncMock(return_value=True)
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_delete(cb, admin_repo, is_admin=True)

        admin_repo.delete_attempt.assert_awaited_once_with(7)
        cb.message.edit_text.assert_awaited()

    @pytest.mark.asyncio
    async def test_falsy_delete_still_rerenders(self):
        cb = _make_callback("adm_wl_del:ru:7:0")
        admin_repo = _make_admin_repo()
        admin_repo.delete_attempt = AsyncMock(return_value=False)
        admin_repo.get_rejected_attempts_page = AsyncMock(return_value=([], 0))

        await handle_wl_delete(cb, admin_repo, is_admin=True)

        # Shows "not found" toast but still re-renders list
        cb.message.edit_text.assert_awaited()

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_wl_del:ru:7:0")
        admin_repo = _make_admin_repo()

        await handle_wl_delete(cb, admin_repo, is_admin=False)

        admin_repo.delete_attempt.assert_not_called()
