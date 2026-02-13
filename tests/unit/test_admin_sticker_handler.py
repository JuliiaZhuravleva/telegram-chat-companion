"""Tests for admin sticker reply handler and helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers.admin_sticker import (
    _extract_file_unique_id_from_reply,
    handle_admin_sticker_reply,
)

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
