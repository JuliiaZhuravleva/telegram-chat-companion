"""Tests for the access card: the DM an admin gets when an unknown chat adds the bot.

The card itself is built in ``screens/cards.py`` (its rows are checked by the
screen rules over its samples); these are the handler tests — what a tap does
to the card — plus the two shared operations the panel's requests screen
calls (`approve_attempt`, `reject_attempt`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from src.bot.screens import cards

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
    inner_msg.chat.id = 500042
    inner_msg.message_id = 7
    inner_msg.date = datetime.now(UTC) - timedelta(minutes=5)
    inner_msg.text = "🙏 Новый чат просит доступ"
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


def _sent_text(cb: MagicMock) -> str:
    return cb.message.edit_text.await_args.args[0]


def _sent_callbacks(cb: MagicMock) -> list[str]:
    markup = cb.message.edit_text.await_args.kwargs["reply_markup"]
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


class TestApproveNotification:
    @pytest.mark.asyncio
    async def test_approves_and_redraws_the_card_with_a_status_line(self):
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_pending(chat_title="Тестовый чат"))
        admin_repo.approve_all_for_chat = AsyncMock(return_value=1)
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(cb, admin_repo, chat_settings_repo, is_admin=True)

        admin_repo.approve_all_for_chat.assert_awaited_once_with(CHAT_ID)
        text = _sent_text(cb)
        assert text.splitlines()[0] == "<i>✓ Одобрено</i>"
        assert "Тестовый чат" in text, "the request stays readable under the status"
        assert _sent_callbacks(cb) == ["adm_pnl_menu:ru:-100", "adm_wl_rm_ask:ru:-100:0"]
        markup = cb.message.edit_text.await_args.kwargs["reply_markup"]
        assert [[b.text for b in row] for row in markup.inline_keyboard] == [
            ["⚙️ Настройки чата", "🔌 Отключить"]
        ], "the way back sits beside the way on, in one row"
        assert cb.answer.await_args.args[0] == "Одобрено"

    @pytest.mark.asyncio
    async def test_an_added_card_keeps_its_heading(self):
        cb = _make_callback("adm_approve:en:42")
        cb.message.text = "➕ Bot added to a new chat"
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_pending())

        await handle_approve_notification(cb, admin_repo, _make_chat_settings_repo(), is_admin=True)

        assert _sent_text(cb).splitlines()[2].startswith("➕")

    @pytest.mark.asyncio
    async def test_a_card_older_than_48_hours_keeps_its_text(self):
        cb = _make_callback("adm_approve:ru:42")
        cb.message.date = datetime.now(UTC) - access_card.TEXT_EDIT_WINDOW - timedelta(minutes=1)
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_pending())

        await handle_approve_notification(cb, admin_repo, _make_chat_settings_repo(), is_admin=True)

        cb.message.edit_text.assert_not_awaited()
        markup = cb.message.edit_reply_markup.await_args.kwargs["reply_markup"]
        assert [b.callback_data for row in markup.inline_keyboard for b in row] == [
            "adm_pnl_menu:ru:-100",
            "adm_wl_rm_ask:ru:-100:0",
        ]

    @pytest.mark.asyncio
    async def test_a_second_tap_redraws_the_resolved_state_without_an_alert(self):
        """Double tap: the request is approved already; show that, do not shout."""
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value={**_pending(), "status": "approved"})
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(cb, admin_repo, chat_settings_repo, is_admin=True)

        admin_repo.approve_all_for_chat.assert_not_awaited()
        assert cb.answer.call_args.kwargs.get("show_alert") is not True
        assert _sent_text(cb).startswith("<i>✓ Одобрено</i>")

    @pytest.mark.asyncio
    async def test_a_second_tap_on_an_unchanged_card_is_silent(self):
        """Telegram says "not modified" when the card already shows this state."""
        from aiogram.exceptions import TelegramBadRequest

        cb = _make_callback("adm_approve:ru:42")
        cb.message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="message is not modified")
        )
        cb.message.answer = AsyncMock()
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value={**_pending(), "status": "approved"})

        await handle_approve_notification(cb, admin_repo, _make_chat_settings_repo(), is_admin=True)

        cb.message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_approve_on_a_card_rejected_from_the_panel_shows_rejected(self):
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value={**_pending(), "status": "rejected"})

        await handle_approve_notification(cb, admin_repo, _make_chat_settings_repo(), is_admin=True)

        assert _sent_callbacks(cb) == ["adm_wl_rejected:ru:0"]

    @pytest.mark.asyncio
    async def test_a_vanished_request_is_an_alert(self):
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=None)

        await handle_approve_notification(cb, admin_repo, _make_chat_settings_repo(), is_admin=True)

        assert cb.answer.call_args.kwargs.get("show_alert") is True
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_approve:ru:42")
        admin_repo = _make_admin_repo()
        chat_settings_repo = _make_chat_settings_repo()

        await handle_approve_notification(cb, admin_repo, chat_settings_repo, is_admin=False)

        admin_repo.get_attempt.assert_not_awaited()
        assert cb.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", ["adm_approve:ru:not-a-number", "adm_approve:ru"])
    async def test_garbled_payload_shows_the_stale_panel(self, data, monkeypatch):
        cb = _make_callback(data)
        admin_repo = _make_admin_repo()
        stale = AsyncMock()
        monkeypatch.setattr(access_card, "show_stale", stale)

        await handle_approve_notification(cb, admin_repo, _make_chat_settings_repo(), is_admin=True)

        admin_repo.get_attempt.assert_not_awaited()
        stale.assert_awaited_once()
        assert stale.await_args.args[1] == "ru"


class TestRejectNotification:
    @pytest.mark.asyncio
    async def test_rejects_and_redraws_the_card(self):
        cb = _make_callback("adm_reject:ru:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value=_pending())
        admin_repo.update_attempt_status = AsyncMock(return_value={"id": 42, "status": "rejected"})

        await handle_reject_notification(cb, admin_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_awaited_once_with(42, "rejected")
        assert _sent_text(cb).startswith("<i>✓ Отклонено.")
        assert _sent_callbacks(cb) == ["adm_wl_rejected:ru:0"]

    @pytest.mark.asyncio
    async def test_already_handled(self):
        cb = _make_callback("adm_reject:en:42")
        admin_repo = _make_admin_repo()
        admin_repo.get_attempt = AsyncMock(return_value={**_pending(), "status": "rejected"})

        await handle_reject_notification(cb, admin_repo, is_admin=True)

        admin_repo.update_attempt_status.assert_not_awaited()
        assert _sent_text(cb).startswith("<i>✓ Rejected.")

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_reject:ru:42")
        admin_repo = _make_admin_repo()

        await handle_reject_notification(cb, admin_repo, is_admin=False)

        admin_repo.get_attempt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_garbled_payload_shows_the_stale_panel(self, monkeypatch):
        cb = _make_callback("adm_reject:en:x")
        stale = AsyncMock()
        monkeypatch.setattr(access_card, "show_stale", stale)

        await handle_reject_notification(cb, _make_admin_repo(), is_admin=True)

        stale.assert_awaited_once()
        assert stale.await_args.args[1] == "en"


def test_the_card_without_a_queue_row_has_no_buttons():
    who = cards.Applicant(chat_id=CHAT_ID, chat_title="Тестовый чат")
    assert cards.access_card("ru", who, attempt_id=None).rows == ()
    assert cards.access_card("ru", who, attempt_id=ATTEMPT_ID).rows
