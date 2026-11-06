"""Tests for the access card: the DM an admin gets when an unknown chat adds the bot.

The card's keyboards and its approve/reject handlers moved out of
``keyboards/admin.py`` / ``handlers/admin.py`` unchanged; these are the tests
that moved with them, plus the two shared operations the panel's requests
screen calls (`approve_attempt`, `reject_attempt`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from src.bot.handlers import access_card
from src.bot.handlers.access_card import (
    approve_attempt,
    handle_approve_notification,
    handle_reject_notification,
    reject_attempt,
)
from src.bot.keyboards.access_card import (
    access_keyboard,
    approved_notification_keyboard,
    rejected_notification_keyboard,
)

# Obviously-fake ids (this repo is public).
CHAT_ID = -100
ATTEMPT_ID = 42


def _get_callbacks(keyboard):
    return [btn.callback_data for row in keyboard.inline_keyboard for btn in row]


def _get_labels(keyboard):
    return [btn.text for row in keyboard.inline_keyboard for btn in row]


def _make_callback(data: str, user_id: int = 12345) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.answer = AsyncMock()
    inner_msg = MagicMock(spec=Message)
    inner_msg.edit_text = AsyncMock()
    inner_msg.edit_reply_markup = AsyncMock()
    inner_msg.delete = AsyncMock()
    inner_msg.chat = MagicMock()
    inner_msg.chat.type = "private"
    callback.message = inner_msg
    return callback


def _make_admin_repo() -> AsyncMock:
    return AsyncMock()


def _make_chat_settings_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.upsert = AsyncMock()
    return repo


def _pending(**extra):
    return {"id": ATTEMPT_ID, "chat_id": CHAT_ID, "status": "pending", **extra}


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------


class TestAccessKeyboard:
    def test_has_approve_and_reject(self):
        kb = access_keyboard("ru", attempt_id=42)
        callbacks = _get_callbacks(kb)
        assert "adm_approve:ru:42" in callbacks
        assert "adm_reject:ru:42" in callbacks

    def test_english_labels(self):
        kb = access_keyboard("en", attempt_id=1)
        labels = _get_labels(kb)
        assert any("Approve" in lab for lab in labels)
        assert any("Reject" in lab for lab in labels)


class TestNotificationStatusKeyboards:
    def test_approved_keyboard_ru(self):
        kb = approved_notification_keyboard("ru", CHAT_ID)
        labels = _get_labels(kb)
        assert any("Одобрено" in label for label in labels)

    def test_approved_keyboard_en(self):
        kb = approved_notification_keyboard("en", CHAT_ID)
        labels = _get_labels(kb)
        assert any("Approved" in label for label in labels)

    def test_approved_keyboard_links_to_chat_panel(self):
        """D-1: settings button deep-links straight to adm_pnl_menu: (ADR-0006
        Decision 4) with the same chat_id, no picker step."""
        kb = approved_notification_keyboard("ru", CHAT_ID)
        callbacks = _get_callbacks(kb)
        assert "adm_pnl_menu:ru:-100" in callbacks

    def test_approved_keyboard_settings_label_both_languages(self):
        """The label used to come from ``keyboards.admin._t("chat_panel")``; the
        move inlined it, so pin both spellings."""
        assert "⚙️ Настройки чата" in _get_labels(approved_notification_keyboard("ru", CHAT_ID))
        assert "⚙️ Chat settings" in _get_labels(approved_notification_keyboard("en", CHAT_ID))

    def test_rejected_keyboard_ru(self):
        kb = rejected_notification_keyboard("ru")
        labels = _get_labels(kb)
        assert any("Отклонено" in label for label in labels)

    def test_rejected_keyboard_en(self):
        kb = rejected_notification_keyboard("en")
        labels = _get_labels(kb)
        assert any("Rejected" in label for label in labels)


# ---------------------------------------------------------------------------
# The two shared operations
# ---------------------------------------------------------------------------


class TestApproveAttempt:
    @pytest.mark.asyncio
    async def test_enables_chat_and_carries_title_and_type(self):
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(
            return_value=_pending(chat_title="Тестовый чат", chat_type="supergroup")
        )
        chat_settings_repo = _make_chat_settings_repo()

        attempt = await approve_attempt(admin_repo, chat_settings_repo, ATTEMPT_ID)

        assert attempt is not None and attempt["chat_id"] == CHAT_ID
        admin_repo.approve_all_for_chat.assert_awaited_once_with(CHAT_ID)
        chat_settings_repo.upsert.assert_awaited_once_with(
            CHAT_ID, enabled=True, chat_title="Тестовый чат", chat_type="supergroup"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["approved", "rejected"])
    async def test_not_pending_is_a_no_op(self, status):
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value={**_pending(), "status": status})
        chat_settings_repo = _make_chat_settings_repo()

        assert await approve_attempt(admin_repo, chat_settings_repo, ATTEMPT_ID) is None
        admin_repo.approve_all_for_chat.assert_not_awaited()
        chat_settings_repo.upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_attempt_is_a_no_op(self):
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=None)
        chat_settings_repo = _make_chat_settings_repo()

        assert await approve_attempt(admin_repo, chat_settings_repo, ATTEMPT_ID) is None
        chat_settings_repo.upsert.assert_not_awaited()


class TestRejectAttempt:
    @pytest.mark.asyncio
    async def test_marks_rejected(self):
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_pending())

        attempt = await reject_attempt(admin_repo, ATTEMPT_ID)

        assert attempt is not None
        admin_repo.update_attempt_status.assert_awaited_once_with(ATTEMPT_ID, "rejected")

    @pytest.mark.asyncio
    async def test_not_pending_is_a_no_op(self):
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value={**_pending(), "status": "approved"})

        assert await reject_attempt(admin_repo, ATTEMPT_ID) is None
        admin_repo.update_attempt_status.assert_not_awaited()


def test_panel_requests_use_the_same_operations():
    """The panel's requests screen must resolve a request exactly as the card
    does; it imports these two functions rather than keeping its own copy."""
    from src.bot.handlers import admin

    assert admin.approve_attempt is access_card.approve_attempt
    assert admin.reject_attempt is access_card.reject_attempt


# ---------------------------------------------------------------------------
# adm_approve / adm_reject (notification) callbacks
# ---------------------------------------------------------------------------


class TestApproveNotification:
    @pytest.mark.asyncio
    async def test_approves_pending_attempt(self):
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_pending())
        admin_repo.approve_all_for_chat = AsyncMock(return_value=1)
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(cb, admin_repo, chat_settings_repo, is_admin=True)

        admin_repo.approve_all_for_chat.assert_awaited_once_with(CHAT_ID)
        chat_settings_repo.upsert.assert_awaited_once_with(CHAT_ID, enabled=True)
        cb.message.edit_reply_markup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_approved_keyboard_links_to_chat_settings_panel(self):
        """D-1 regression: settings button after approve carries the right chat_id."""
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_pending())
        admin_repo.approve_all_for_chat = AsyncMock(return_value=1)
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(cb, admin_repo, chat_settings_repo, is_admin=True)

        keyboard = cb.message.edit_reply_markup.call_args.kwargs["reply_markup"]
        assert "adm_pnl_menu:ru:-100" in _get_callbacks(keyboard)

    @pytest.mark.asyncio
    async def test_already_handled(self):
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value={**_pending(), "status": "approved"})
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
        assert cb.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_garbled_attempt_id(self):
        cb = _make_callback("adm_approve:ru:not-a-number")
        admin_repo = _make_admin_repo()

        await handle_approve_notification(cb, admin_repo, _make_chat_settings_repo(), is_admin=True)

        admin_repo.get_attempt.assert_not_awaited()
        assert cb.answer.call_args.kwargs.get("show_alert") is True


class TestRejectNotification:
    @pytest.mark.asyncio
    async def test_rejects_pending_attempt(self):
        cb = _make_callback("adm_reject:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_pending())
        admin_repo.update_attempt_status = AsyncMock(return_value={"id": 42, "status": "rejected"})

        await handle_reject_notification(cb, admin_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_awaited_once_with(42, "rejected")
        cb.message.edit_reply_markup.assert_awaited_once()
        keyboard = cb.message.edit_reply_markup.call_args.kwargs["reply_markup"]
        assert _get_callbacks(keyboard) == ["noop"]

    @pytest.mark.asyncio
    async def test_already_handled(self):
        cb = _make_callback("adm_reject:en:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value={**_pending(), "status": "rejected"})

        await handle_reject_notification(cb, admin_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_reject:ru:42")
        admin_repo = _make_admin_repo()

        await handle_reject_notification(cb, admin_repo, is_admin=False)

        admin_repo.get_attempt.assert_not_awaited()
