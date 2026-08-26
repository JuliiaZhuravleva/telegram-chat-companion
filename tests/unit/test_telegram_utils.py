"""Tests for Telegram file download utilities."""

import asyncio
import inspect
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.utils.telegram import (
    TelegramFileError,
    build_chat_url,
    detect_mime_type,
    download_telegram_file,
    parse_chat_reference,
    typing_indicator,
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
        url = build_chat_url(-1009999000042, chat_type="channel")
        assert url == "https://t.me/c/9999000042"

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


class TestParseChatReference:
    """D-1 shortcut's link/id parser."""

    def test_tme_c_link_round_trips_with_build_chat_url(self):
        # build_chat_url(-1001234567890, "supergroup") == "https://t.me/c/1234567890"
        ref = parse_chat_reference("https://t.me/c/1234567890")
        assert ref is not None
        assert ref.chat_id == -1001234567890
        assert ref.username is None
        # Round trip: feeding the resolved id back through build_chat_url
        # reproduces the exact link that was parsed.
        assert build_chat_url(ref.chat_id, chat_type="supergroup") == "https://t.me/c/1234567890"

    def test_tme_c_link_channel_round_trips(self):
        ref = parse_chat_reference("t.me/c/9999000042")
        assert ref is not None
        assert ref.chat_id == -1009999000042
        assert build_chat_url(ref.chat_id, chat_type="channel") == "https://t.me/c/9999000042"

    def test_tme_c_link_with_message_id_suffix(self):
        ref = parse_chat_reference("https://t.me/c/1234567890/42")
        assert ref == parse_chat_reference("https://t.me/c/1234567890")

    def test_bare_negative_chat_id(self):
        ref = parse_chat_reference("-1001234567890")
        assert ref is not None
        assert ref.chat_id == -1001234567890
        assert ref.username is None

    def test_bare_positive_number_is_not_a_chat_id(self):
        # No legitimate whitelisted chat id is positive -- a plain positive
        # number is far more likely to be a title (e.g. an event year).
        assert parse_chat_reference("2024") is None

    def test_at_username(self):
        ref = parse_chat_reference("@mychat")
        assert ref == parse_chat_reference("@mychat")
        assert ref is not None
        assert ref.username == "mychat"
        assert ref.chat_id is None

    def test_tme_username_link(self):
        ref = parse_chat_reference("https://t.me/mychat")
        assert ref is not None
        assert ref.username == "mychat"
        assert ref.chat_id is None

    def test_tme_username_link_no_scheme(self):
        ref = parse_chat_reference("t.me/mychat")
        assert ref is not None
        assert ref.username == "mychat"

    def test_tme_username_link_with_message_id_suffix(self):
        ref = parse_chat_reference("https://t.me/mychat/123")
        assert ref is not None
        assert ref.username == "mychat"

    def test_reserved_tme_paths_are_not_usernames(self):
        for path in ("s", "iv", "joinchat", "proxy", "addstickers", "share"):
            assert parse_chat_reference(f"https://t.me/{path}/foo") is None, path

    def test_private_invite_link_is_none(self):
        # t.me/+<hash> is not resolvable without joining -- falls through so
        # the caller treats it as a (harmless, zero-match) title search.
        assert parse_chat_reference("https://t.me/+AbCdEf12345") is None

    def test_plain_title_text_is_none(self):
        assert parse_chat_reference("Мой любимый чат") is None

    def test_empty_and_whitespace_only(self):
        assert parse_chat_reference("") is None
        assert parse_chat_reference("   ") is None

    def test_strips_surrounding_whitespace(self):
        assert parse_chat_reference("  @mychat  ") == parse_chat_reference("@mychat")


class TestTypingIndicator:
    """Tests for the shared "bot is typing" keep-alive helper (I-6)."""

    def test_message_thread_id_has_no_default(self):
        """Regression guard for I-9: callers must pass message_thread_id
        explicitly — a silent default=None would let forum indicators leak
        to the General topic the way the old media.py call did.
        """
        sig = inspect.signature(typing_indicator)
        assert sig.parameters["message_thread_id"].default is inspect.Parameter.empty

    def test_message_thread_id_missing_raises_type_error(self):
        with pytest.raises(TypeError):
            typing_indicator(MagicMock(), 1)  # type: ignore[call-arg]

    @pytest.mark.asyncio
    async def test_sends_action_with_given_type_and_thread_id(self):
        bot = MagicMock()
        bot.send_chat_action = AsyncMock()

        async with typing_indicator(bot, -100123, 42, "choose_sticker"):
            await asyncio.sleep(0.01)

        bot.send_chat_action.assert_awaited()
        call_kwargs = bot.send_chat_action.call_args.kwargs
        assert call_kwargs["chat_id"] == -100123
        assert call_kwargs["message_thread_id"] == 42
        assert call_kwargs["action"] == "choose_sticker"

    @pytest.mark.asyncio
    async def test_defaults_to_typing_action(self):
        bot = MagicMock()
        bot.send_chat_action = AsyncMock()

        async with typing_indicator(bot, -100123, None):
            await asyncio.sleep(0.01)

        assert bot.send_chat_action.call_args.kwargs["action"] == "typing"

    @pytest.mark.asyncio
    async def test_disabled_skips_sending_entirely(self):
        bot = MagicMock()
        bot.send_chat_action = AsyncMock()

        async with typing_indicator(bot, -100123, None, enabled=False):
            await asyncio.sleep(0.01)

        bot.send_chat_action.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stops_and_propagates_on_exception(self):
        """Guaranteed stop even when the wrapped operation raises."""
        bot = MagicMock()
        bot.send_chat_action = AsyncMock()

        with pytest.raises(ValueError, match="boom"):
            async with typing_indicator(bot, -100123, None):
                await asyncio.sleep(0.01)
                raise ValueError("boom")
