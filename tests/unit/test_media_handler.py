"""Tests for media handlers (voice, photo, sticker)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.ai.base import TranscriptionResult


def _make_message(
    *,
    chat_id: int = -100123,
    message_id: int = 42,
    user_id: int = 1,
    user_first_name: str = "Alice",
    text: str | None = None,
    caption: str | None = None,
):
    """Create a mock aiogram Message."""
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.message_id = message_id

    user = MagicMock()
    user.id = user_id
    user.first_name = user_first_name
    user.is_bot = False
    msg.from_user = user

    msg.text = text
    msg.caption = caption
    msg.reply_to_message = None
    msg.reply = AsyncMock()
    msg.answer = AsyncMock()

    return msg


def _make_chat_config(**overrides):
    """Create a mock ChatConfig."""
    config = MagicMock()
    config.transcribe_voice = True
    config.transcribe_video_notes = True
    config.image_analysis_enabled = True
    config.sticker_learning_enabled = True
    config.language = "ru"
    for key, val in overrides.items():
        setattr(config, key, val)
    return config


def _make_bot():
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    bot_info = MagicMock()
    bot_info.id = 999
    bot.me = AsyncMock(return_value=bot_info)
    return bot


# ── Voice handler tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_handler_transcribes_and_replies():
    from src.bot.handlers.media import handle_voice_message

    message = _make_message()
    voice = MagicMock()
    voice.file_id = "voice-file-id"
    message.voice = voice
    message.video_note = None

    chat_config = _make_chat_config()
    bot = _make_bot()

    voice_service = MagicMock()
    voice_service.transcribe = AsyncMock(
        return_value=TranscriptionResult(
            text="Hello world",
            model="whisper-1",
            provider="openai",
        )
    )

    with patch(
        "src.bot.handlers.media.download_telegram_file",
        new_callable=AsyncMock,
        return_value=b"fake-audio",
    ):
        await handle_voice_message(message, chat_config, voice_service, bot)

    voice_service.transcribe.assert_awaited_once()
    message.reply.assert_awaited_once()
    reply_text = message.reply.call_args.args[0]
    assert "Hello world" in reply_text


@pytest.mark.asyncio
async def test_voice_handler_disabled():
    from src.bot.handlers.media import handle_voice_message

    message = _make_message()
    voice = MagicMock()
    voice.file_id = "voice-file-id"
    message.voice = voice
    message.video_note = None

    chat_config = _make_chat_config(transcribe_voice=False)
    bot = _make_bot()
    voice_service = MagicMock()

    await handle_voice_message(message, chat_config, voice_service, bot)

    voice_service.transcribe.assert_not_called()


@pytest.mark.asyncio
async def test_voice_handler_transcription_returns_none():
    from src.bot.handlers.media import handle_voice_message

    message = _make_message()
    voice = MagicMock()
    voice.file_id = "voice-file-id"
    message.voice = voice
    message.video_note = None

    chat_config = _make_chat_config()
    bot = _make_bot()

    voice_service = MagicMock()
    voice_service.transcribe = AsyncMock(return_value=None)

    with patch(
        "src.bot.handlers.media.download_telegram_file",
        new_callable=AsyncMock,
        return_value=b"fake-audio",
    ):
        await handle_voice_message(message, chat_config, voice_service, bot)

    message.reply.assert_not_awaited()


# ── Photo handler tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_photo_handler_no_caption_saves_description():
    from src.bot.handlers.media import handle_photo_message

    message = _make_message(caption=None)
    photo = MagicMock()
    photo.file_id = "photo-file-id"
    message.photo = [photo]

    chat_config = _make_chat_config()
    bot = _make_bot()

    image_service = MagicMock()
    image_service.analyze = AsyncMock(return_value="A cat on a table")

    pipeline = MagicMock()
    message_repo = MagicMock()
    message_repo.save = AsyncMock()

    with patch(
        "src.bot.handlers.media.download_telegram_file",
        new_callable=AsyncMock,
        return_value=b"fake-image",
    ):
        await handle_photo_message(
            message, chat_config, image_service, pipeline, message_repo, bot
        )

    message_repo.save.assert_awaited_once()
    call_kwargs = message_repo.save.call_args.kwargs
    assert "A cat on a table" in call_kwargs["content"]
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_photo_handler_disabled():
    from src.bot.handlers.media import handle_photo_message

    message = _make_message()
    message.photo = [MagicMock()]

    chat_config = _make_chat_config(image_analysis_enabled=False)
    bot = _make_bot()
    image_service = MagicMock()
    pipeline = MagicMock()
    message_repo = MagicMock()

    await handle_photo_message(
        message, chat_config, image_service, pipeline, message_repo, bot
    )

    image_service.analyze.assert_not_called()


@pytest.mark.asyncio
async def test_photo_handler_analysis_fails():
    from src.bot.handlers.media import handle_photo_message

    message = _make_message(caption=None)
    photo = MagicMock()
    photo.file_id = "photo-file-id"
    message.photo = [photo]

    chat_config = _make_chat_config()
    bot = _make_bot()

    image_service = MagicMock()
    image_service.analyze = AsyncMock(return_value=None)

    pipeline = MagicMock()
    message_repo = MagicMock()

    with patch(
        "src.bot.handlers.media.download_telegram_file",
        new_callable=AsyncMock,
        return_value=b"fake-image",
    ):
        await handle_photo_message(
            message, chat_config, image_service, pipeline, message_repo, bot
        )

    message_repo.save.assert_not_called()


# ── Sticker handler tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sticker_handler_learns_static():
    from src.bot.handlers.media import handle_sticker_message

    message = _make_message()
    sticker = MagicMock()
    sticker.file_id = "sticker-file-id"
    sticker.file_unique_id = "unique-1"
    sticker.is_animated = False
    sticker.is_video = False
    message.sticker = sticker

    chat_config = _make_chat_config()
    bot = _make_bot()

    sticker_service = MagicMock()
    sticker_service.learn = AsyncMock()

    message_repo = MagicMock()
    message_repo.get_recent = AsyncMock(return_value=[])

    with patch(
        "src.bot.handlers.media.download_telegram_file",
        new_callable=AsyncMock,
        return_value=b"fake-webp",
    ):
        await handle_sticker_message(
            message, chat_config, sticker_service, message_repo, bot
        )

    sticker_service.learn.assert_awaited_once()
    # Silent — no reply
    message.reply.assert_not_awaited()
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_sticker_handler_skips_animated():
    from src.bot.handlers.media import handle_sticker_message

    message = _make_message()
    sticker = MagicMock()
    sticker.file_id = "sticker-file-id"
    sticker.file_unique_id = "unique-1"
    sticker.is_animated = True
    sticker.is_video = False
    message.sticker = sticker

    chat_config = _make_chat_config()
    bot = _make_bot()
    sticker_service = MagicMock()
    message_repo = MagicMock()

    await handle_sticker_message(
        message, chat_config, sticker_service, message_repo, bot
    )

    sticker_service.learn.assert_not_called()


@pytest.mark.asyncio
async def test_sticker_handler_disabled():
    from src.bot.handlers.media import handle_sticker_message

    message = _make_message()
    sticker = MagicMock()
    sticker.is_animated = False
    sticker.is_video = False
    message.sticker = sticker

    chat_config = _make_chat_config(sticker_learning_enabled=False)
    bot = _make_bot()
    sticker_service = MagicMock()
    message_repo = MagicMock()

    await handle_sticker_message(
        message, chat_config, sticker_service, message_repo, bot
    )

    sticker_service.learn.assert_not_called()
