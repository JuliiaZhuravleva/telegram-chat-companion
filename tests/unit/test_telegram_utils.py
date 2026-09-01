"""Tests for Telegram file download utilities."""

import asyncio
import inspect
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from src.utils.telegram import (
    TelegramFileError,
    build_chat_url,
    detect_mime_type,
    download_telegram_file,
    parse_chat_reference,
    typing_indicator,
)


def _download_bot(payload: bytes = b"fake-image-data", file_path: str = "photos/file_123.jpg"):
    """A bot whose download writes `payload` into whatever buffer it is handed."""
    bot = MagicMock()
    file_obj = MagicMock()
    file_obj.file_path = file_path
    bot.get_file = AsyncMock(return_value=file_obj)
    # The parameter really is called `timeout`: production passes it by
    # keyword, so renaming it to silence ARG005 makes every call raise
    # TypeError -- which the retry loop then swallows as a transient failure.
    bot.download_file = AsyncMock(
        side_effect=lambda _fp, destination, timeout=None: destination.write(  # noqa: ARG005
            payload
        )
    )
    return bot


class _Recorder:
    """Records the delays a retry asks for without spending them.

    Same shape as tests/unit/test_flood_control.py's recorder: a test that
    actually slept through this module's backoff would add ~3 seconds to the
    suite and make the assertions depend on a wall clock.
    """

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


@pytest.mark.asyncio
async def test_download_success():
    bot = _download_bot()

    result = await download_telegram_file(bot, "test-file-id")

    assert result == b"fake-image-data"
    bot.get_file.assert_awaited_once_with("test-file-id", request_timeout=30)
    bot.download_file.assert_awaited_once()
    call_args = bot.download_file.call_args
    assert call_args.args[0] == "photos/file_123.jpg"
    assert isinstance(call_args.kwargs["destination"], BytesIO)
    assert call_args.kwargs["timeout"] == 30, (
        "the timeout must be passed, not inherited: aiogram is unpinned in "
        "pyproject.toml, so its own default can move without a repo change"
    )


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
    sleep = _Recorder()

    with pytest.raises(TelegramFileError, match="Failed to download"):
        await download_telegram_file(bot, "test-file-id", sleep=sleep, jitter=lambda: 0.0)


class TestDownloadRetries:
    """The nine voice notes lost in production were transient CDN stalls: eight
    of nine failed at 30-34 seconds and the same files downloaded in under a
    second days later. Retrying is the fix; these pin what it may and may not
    retry."""

    @pytest.mark.asyncio
    async def test_a_transient_failure_is_retried_and_can_succeed(self):
        bot = _download_bot()
        bot.get_file = AsyncMock(side_effect=[TimeoutError(), MagicMock(file_path="voice/f.oga")])
        sleep = _Recorder()

        result = await download_telegram_file(bot, "test-file-id", sleep=sleep, jitter=lambda: 0.0)

        assert result == b"fake-image-data"
        assert bot.get_file.await_count == 2
        assert sleep.delays == [1.0]

    @pytest.mark.asyncio
    async def test_it_gives_up_after_the_configured_attempts(self):
        bot = _download_bot()
        bot.get_file = AsyncMock(side_effect=TimeoutError())
        sleep = _Recorder()

        with pytest.raises(TelegramFileError, match="after 3 attempts"):
            await download_telegram_file(bot, "x", sleep=sleep, jitter=lambda: 0.0)

        assert bot.get_file.await_count == 3
        assert sleep.delays == [1.0, 2.0], "linear backoff, and no sleep after the last try"

    @pytest.mark.asyncio
    async def test_a_permanent_telegram_error_is_not_retried(self):
        """ "The file is gone" does not become truer on the third ask. Retrying
        it would triple the wall clock in front of the caller's own
        degradation."""
        bot = _download_bot()
        bot.get_file = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="file is too big")
        )
        sleep = _Recorder()

        with pytest.raises(TelegramFileError, match="TelegramBadRequest"):
            await download_telegram_file(bot, "x", sleep=sleep, jitter=lambda: 0.0)

        assert bot.get_file.await_count == 1
        assert sleep.delays == []

    @pytest.mark.asyncio
    async def test_an_empty_file_is_not_retried(self):
        bot = _download_bot(payload=b"")
        sleep = _Recorder()

        with pytest.raises(TelegramFileError, match="Empty file"):
            await download_telegram_file(bot, "x", sleep=sleep, jitter=lambda: 0.0)

        assert bot.download_file.await_count == 1
        assert sleep.delays == []

    @pytest.mark.asyncio
    async def test_a_retry_does_not_append_to_the_partial_first_attempt(self):
        """The corrupt-retry trap, and the reason the buffer is built inside
        the attempt rather than hoisted out of the loop.

        `download_file` writes chunks straight into `destination` and only
        rewinds once the stream finishes, so a mid-stream timeout leaves
        partial bytes behind. A shared buffer would concatenate the two
        attempts, pass the emptiness check, and hand Whisper a corrupt file --
        billed, transcribed into nonsense, and invisible to every test that
        mocks the download as writing once.
        """
        calls = {"n": 0}

        async def flaky(_fp, destination, timeout=None):  # noqa: ARG001
            calls["n"] += 1
            if calls["n"] == 1:
                destination.write(b"PARTIAL")
                raise TimeoutError()
            destination.write(b"WHOLE-FILE")

        bot = _download_bot()
        bot.download_file = AsyncMock(side_effect=flaky)
        sleep = _Recorder()

        result = await download_telegram_file(bot, "x", sleep=sleep, jitter=lambda: 0.0)

        assert result == b"WHOLE-FILE", "the retry inherited the partial download"
        assert b"PARTIAL" not in result


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
