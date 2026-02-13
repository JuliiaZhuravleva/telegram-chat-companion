"""Tests for admin sticker reply handler, detail/back navigation, and helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.admin_sticker import (
    _extract_file_unique_id_from_reply,
    handle_admin_sticker_reply,
    handle_sticker_back,
    handle_sticker_detail,
)
from src.bot.keyboards.admin_sticker import sticker_detail_keyboard

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reply_message(
    text: str | None = None,
    caption: str | None = None,
) -> MagicMock:
    """Mock a replied-to message with optional text/caption."""
    msg = MagicMock()
    msg.text = text
    msg.caption = caption
    msg.message_id = 100
    return msg


def _make_message(
    text: str = "new description",
    user_id: int = 12345,
    chat_type: str = "private",
    reply_text: str | None = "🆔 AgADvh4AAlkbCFI\n<b>Описание:</b> test",
    reply_caption: str | None = None,
) -> MagicMock:
    """Mock aiogram Message for admin sticker reply tests."""
    msg = MagicMock()
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.reply = AsyncMock()

    reply = _make_reply_message(text=reply_text, caption=reply_caption)
    msg.reply_to_message = reply
    return msg


# ---------------------------------------------------------------------------
# _extract_file_unique_id_from_reply
# ---------------------------------------------------------------------------


class TestExtractFileUniqueIdFromReply:
    def test_extracts_id_from_text(self) -> None:
        reply = _make_reply_message(text="🆔 AgADvh4AAlkbCFI\nОписание: test")
        assert _extract_file_unique_id_from_reply(reply) == "AgADvh4AAlkbCFI"

    def test_extracts_id_from_caption(self) -> None:
        reply = _make_reply_message(caption="🆔 AgADvh4AAlkbCFI")
        assert _extract_file_unique_id_from_reply(reply) == "AgADvh4AAlkbCFI"

    def test_prefers_text_over_caption(self) -> None:
        reply = _make_reply_message(
            text="🆔 ID_FROM_TEXT",
            caption="🆔 ID_FROM_CAPTION",
        )
        assert _extract_file_unique_id_from_reply(reply) == "ID_FROM_TEXT"

    def test_returns_none_when_no_id(self) -> None:
        reply = _make_reply_message(text="Just some text without ID")
        assert _extract_file_unique_id_from_reply(reply) is None

    def test_returns_none_when_both_none(self) -> None:
        reply = _make_reply_message(text=None, caption=None)
        assert _extract_file_unique_id_from_reply(reply) is None

    def test_handles_id_with_dashes_and_underscores(self) -> None:
        reply = _make_reply_message(text="🆔 AgAD-vh_4AA-lkb")
        assert _extract_file_unique_id_from_reply(reply) == "AgAD-vh_4AA-lkb"

    def test_handles_no_space_after_emoji(self) -> None:
        reply = _make_reply_message(text="🆔AgADvh4AAlkbCFI")
        assert _extract_file_unique_id_from_reply(reply) == "AgADvh4AAlkbCFI"


# ---------------------------------------------------------------------------
# handle_admin_sticker_reply
# ---------------------------------------------------------------------------


class TestHandleAdminStickerReply:
    @pytest.fixture()
    def sticker_repo(self) -> MagicMock:
        repo = MagicMock()
        repo.get_notification_by_reply = AsyncMock(return_value=None)
        return repo

    @pytest.fixture()
    def sticker_service(self) -> MagicMock:
        svc = MagicMock()
        svc.merge_admin_description = AsyncMock(return_value="Updated desc")
        return svc

    @pytest.mark.asyncio()
    async def test_db_lookup_success(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = {
            "file_unique_id": "AgADvh4AAlkbCFI"
        }
        msg = _make_message(text="better description")

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service)

        sticker_service.merge_admin_description.assert_awaited_once_with(
            "AgADvh4AAlkbCFI", "better description"
        )
        msg.reply.assert_awaited_once()
        assert "обновлено" in msg.reply.call_args[0][0].lower()

    @pytest.mark.asyncio()
    async def test_text_fallback_when_db_returns_none(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = None
        msg = _make_message(
            text="better description",
            reply_text="🆔 FallbackID123\nОписание: old",
        )

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service)

        sticker_service.merge_admin_description.assert_awaited_once_with(
            "FallbackID123", "better description"
        )

    @pytest.mark.asyncio()
    async def test_caption_fallback_for_collage_reply(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = None
        msg = _make_message(
            text="better description",
            reply_text=None,
            reply_caption="🆔 CaptionID456",
        )

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service)

        sticker_service.merge_admin_description.assert_awaited_once_with(
            "CaptionID456", "better description"
        )

    @pytest.mark.asyncio()
    async def test_silent_return_when_no_id_found(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = None
        msg = _make_message(
            text="better description",
            reply_text="No ID here",
        )

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service)

        sticker_service.merge_admin_description.assert_not_awaited()
        msg.reply.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_merge_failure_shows_error(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = {
            "file_unique_id": "AgADvh4AAlkbCFI"
        }
        sticker_service.merge_admin_description.return_value = None
        msg = _make_message(text="better description")

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service)

        msg.reply.assert_awaited_once()
        assert "не удалось" in msg.reply.call_args[0][0].lower()

    @pytest.mark.asyncio()
    async def test_ignores_non_private_chat(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        msg = _make_message(chat_type="group")

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service)

        sticker_repo.get_notification_by_reply.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_ignores_empty_text(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = {
            "file_unique_id": "AgADvh4AAlkbCFI"
        }
        msg = _make_message(text="   ")

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service)

        sticker_service.merge_admin_description.assert_not_awaited()


# ---------------------------------------------------------------------------
# Callback test helpers
# ---------------------------------------------------------------------------

_SAMPLE_STICKER = {
    "file_id": "CAACAgIAAxkB",
    "file_unique_id": "AgADvh4AAlkbCFI",
    "set_name": "test_set",
    "visual_description": "happy cat",
    "emotion": "joy",
    "character_or_meme": None,
    "suggested_contexts": None,
    "total_uses": 5,
    "bot_uses": 2,
    "emoji": "😺",
    "is_animated": False,
    "is_video": False,
    "analysis_failed": False,
}


def _make_callback(
    data: str,
    user_id: int = 12345,
    chat_type: str = "private",
    message_id: int = 200,
) -> MagicMock:
    """Mock aiogram CallbackQuery for sticker admin tests."""
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.answer = AsyncMock()

    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.chat.type = chat_type
    msg.message_id = message_id
    msg.delete = AsyncMock()
    msg.edit_text = AsyncMock()
    msg.answer = AsyncMock()

    sticker_sent = MagicMock()
    sticker_sent.message_id = message_id + 1
    msg.answer_sticker = AsyncMock(return_value=sticker_sent)

    bot = MagicMock()
    bot.delete_message = AsyncMock()
    msg.bot = bot

    cb.message = msg
    return cb


def _make_bot_config_repo(admin_ids: str = "12345") -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=admin_ids)
    return repo


def _make_sticker_repo(
    sticker: dict | None = None,
    set_count: int = 0,
    sticker_count: int = 0,
) -> MagicMock:
    repo = MagicMock()
    repo.get_by_file_unique_id = AsyncMock(return_value=sticker or _SAMPLE_STICKER)
    repo.count_stickers_in_set = AsyncMock(return_value=sticker_count)
    repo.get_stickers_in_set = AsyncMock(return_value=[])
    repo.count_sets = AsyncMock(return_value=set_count)
    repo.get_all_sets_with_stats = AsyncMock(return_value=[])
    return repo


# ---------------------------------------------------------------------------
# handle_sticker_detail — delete + re-send pattern
# ---------------------------------------------------------------------------


class TestHandleStickerDetail:
    @pytest.mark.asyncio()
    async def test_deletes_old_message_and_sends_sticker_then_description(self) -> None:
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI")
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        # Old message deleted
        cb.message.delete.assert_awaited_once()
        # Sticker sent
        cb.message.answer_sticker.assert_awaited_once_with("CAACAgIAAxkB")
        # Description sent as new message (not edit_text)
        cb.message.answer.assert_awaited_once()
        call_kwargs = cb.message.answer.call_args
        assert "Описание" in call_kwargs[0][0]
        # edit_text should NOT be called
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_keyboard_contains_sticker_msg_id_in_back_button(self) -> None:
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI")
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        call_kwargs = cb.message.answer.call_args
        keyboard = call_kwargs[1]["reply_markup"]
        # Back button should contain sticker message ID (201)
        back_button = keyboard.inline_keyboard[-1][0]
        assert "adm_stk_back:" in back_button.callback_data
        assert ":201" in back_button.callback_data

    @pytest.mark.asyncio()
    async def test_not_authorized(self) -> None:
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI", user_id=99999)
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo("12345")

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        cb.answer.assert_awaited_once()
        assert cb.answer.call_args[1].get("show_alert") is True
        cb.message.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_sticker_back — cleanup sticker + description, send set list
# ---------------------------------------------------------------------------


class TestHandleStickerBack:
    @pytest.mark.asyncio()
    async def test_deletes_sticker_and_description_messages(self) -> None:
        cb = _make_callback("adm_stk_back:ru:test_set:0:150")
        sticker_repo = _make_sticker_repo(sticker_count=5)
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_back(cb, sticker_repo, bot_config_repo)

        # Sticker message deleted by ID
        cb.message.bot.delete_message.assert_awaited_once_with(
            chat_id=cb.message.chat.id,
            message_id=150,
        )
        # Description message deleted
        cb.message.delete.assert_awaited_once()
        # New set list message sent
        cb.message.answer.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_works_without_sticker_msg_id(self) -> None:
        """Graceful fallback if sticker_msg_id is missing."""
        cb = _make_callback("adm_stk_back:ru:test_set:0")
        sticker_repo = _make_sticker_repo(sticker_count=5)
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_back(cb, sticker_repo, bot_config_repo)

        # No sticker deletion attempted
        cb.message.bot.delete_message.assert_not_awaited()
        # Description still deleted, set list still sent
        cb.message.delete.assert_awaited_once()
        cb.message.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# sticker_detail_keyboard — callback_data format
# ---------------------------------------------------------------------------


class TestStickerDetailKeyboard:
    def test_back_button_includes_sticker_msg_id(self) -> None:
        kb = sticker_detail_keyboard(
            "AgADvh4AAlkbCFI",
            lang="ru",
            set_name="test_set",
            sticker_msg_id=150,
        )
        back_btn = kb.inline_keyboard[-1][0]
        assert back_btn.callback_data == "adm_stk_back:ru:test_set:0:150"

    def test_back_button_without_sticker_msg_id(self) -> None:
        kb = sticker_detail_keyboard(
            "AgADvh4AAlkbCFI",
            lang="en",
            set_name="test_set",
        )
        back_btn = kb.inline_keyboard[-1][0]
        assert back_btn.callback_data == "adm_stk_set:en:test_set:0"

    def test_falls_back_when_callback_data_too_long(self) -> None:
        long_set_name = "a" * 50  # Makes adm_stk_back:... exceed 64 bytes
        kb = sticker_detail_keyboard(
            "AgADvh4AAlkbCFI",
            lang="ru",
            set_name=long_set_name,
            sticker_msg_id=1234567890,
        )
        back_btn = kb.inline_keyboard[-1][0]
        # Should fall back to non-cleanup callback
        assert back_btn.callback_data.startswith("adm_stk_set:")

    def test_without_set_name(self) -> None:
        kb = sticker_detail_keyboard("AgADvh4AAlkbCFI", lang="ru")
        back_btn = kb.inline_keyboard[-1][0]
        assert back_btn.callback_data == "adm_stk_sets:ru:0"
