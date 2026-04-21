"""Tests for Telegram file download utilities."""

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.utils.telegram import (
    TelegramFileError,
    build_chat_url,
    detect_mime_type,
    download_telegram_file,
)


@pytest.mark.asyncio
async def test_download_success():
    bot = MagicMock()
    file_obj = MagicMock()
    file_obj.file_path = "photos/file_123.jpg"

    bot.get_file = AsyncMock(return_value=file_obj)

    buf = BytesIO(b"fake-image-data")
    bot.download_file = AsyncMock(
        side_effect=lambda _fp, destination: destination.write(buf.getvalue())
    )

    result = await download_telegram_file(bot, "test-file-id")
    assert result == b"fake-image-data"
    bot.get_file.assert_awaited_once_with("test-file-id")
    bot.download_file.assert_awaited_once()
    call_args = bot.download_file.call_args
    assert call_args.args[0] == "photos/file_123.jpg"
    assert isinstance(call_args.kwargs["destination"], BytesIO)


@pytest.mark.asyncio
async def test_download_no_file_path():
    bot = MagicMock()
    file_obj = MagicMock()
    file_obj.file_path = None
    bot.get_file = AsyncMock(return_value=file_obj)

    with pytest.raises(TelegramFileError, match="No file_path"):
        await download_telegram_file(bot, "test-file-id")


@pytest.mark.asyncio
async def test_download_api_error():
    bot = MagicMock()
    bot.get_file = AsyncMock(side_effect=RuntimeError("API error"))

    with pytest.raises(TelegramFileError, match="Failed to download"):
        await download_telegram_file(bot, "test-file-id")


class TestDetectMimeType:
    def test_jpeg(self):
        assert detect_mime_type("photos/file_123.jpg") == "image/jpeg"

    def test_png(self):
        assert detect_mime_type("photos/file_123.png") == "image/png"

    def test_ogg(self):
        assert detect_mime_type("voice/file_456.ogg") == "audio/ogg"

    def test_webp(self):
        assert detect_mime_type("stickers/file_789.webp") == "image/webp"

    def test_webm(self):
        assert detect_mime_type("video/file.webm") == "video/webm"

    def test_tgs(self):
        # Python's mimetypes may return either depending on system config
        result = detect_mime_type("stickers/file.tgs")
        assert result in ("application/x-tgsticker", "application/gzip")

    def test_unknown(self):
        assert detect_mime_type("file_no_ext") == "application/octet-stream"


class TestBuildChatUrl:
    def test_public_username_wins_over_everything(self):
        url = build_chat_url(
            -1001234567890,
            chat_type="supergroup",
            chat_username="mychat",
        )
        assert url == "https://t.me/mychat"

    def test_username_for_legacy_group(self):
        url = build_chat_url(-100, chat_type="group", chat_username="oldgroup")
        assert url == "https://t.me/oldgroup"

    def test_supergroup_without_username_uses_c_link(self):
        url = build_chat_url(-1001234567890, chat_type="supergroup")
        # Strip sign + "100" prefix: -1001234567890 → 1234567890
        assert url == "https://t.me/c/1234567890"

    def test_channel_uses_c_link(self):
        url = build_chat_url(-1004200000000, chat_type="channel")
        assert url == "https://t.me/c/4200000000"

    def test_supergroup_id_threshold_not_crossed(self):
        # Above the -1_000_000_000_000 threshold → not a real supergroup ID
        assert build_chat_url(-999, chat_type="supergroup") is None

    def test_private_chat_uses_tg_user_link(self):
        assert build_chat_url(1234567890, chat_type="private") == ("tg://user?id=1234567890")

    def test_private_with_negative_id_not_a_link(self):
        # Defensive: private must have a positive user_id
        assert build_chat_url(-1, chat_type="private") is None

    def test_legacy_group_no_username_returns_none(self):
        assert build_chat_url(-100, chat_type="group") is None

    def test_unknown_chat_type_returns_none(self):
        assert build_chat_url(-100) is None
        assert build_chat_url(-100, chat_type=None) is None
