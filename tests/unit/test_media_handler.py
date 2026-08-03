"""Tests for media handlers (voice, photo, sticker)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.enums import ResponseType, TriggerType
from src.services.ai.base import TranscriptionResult
from src.services.text.pipeline import PipelineResult


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
    config.sticker_reply_to_sticker_enabled = False
    config.sticker_reply_to_sticker_chance = 0.0
    config.image_comment_sticker_enabled = False
    config.image_comment_sticker_chance = 0.0
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

    # Mock get_sticker_set for sticker set registration
    tg_set = MagicMock()
    tg_set.name = "test_set"
    tg_set.title = "Test Set"
    tg_sticker = MagicMock()
    tg_sticker.is_animated = False
    tg_sticker.is_video = False
    tg_set.stickers = [tg_sticker]
    tg_set.thumbnail = None
    bot.get_sticker_set = AsyncMock(return_value=tg_set)

    return bot


def _make_sticker_repo():
    repo = MagicMock()
    repo.get_sticker_set = AsyncMock(return_value=None)
    repo.upsert_sticker_set = AsyncMock()
    return repo


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
async def test_voice_handler_forwards_message_thread_id_to_typing_indicator():
    """Regression guard for I-9 (forum topic routing) after the I-6 refactor
    to the shared typing_indicator helper.
    """
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

    with (
        patch(
            "src.bot.handlers.media.download_telegram_file",
            new_callable=AsyncMock,
            return_value=b"fake-audio",
        ),
        patch("src.bot.handlers.media.typing_indicator") as mock_indicator,
    ):
        mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

        await handle_voice_message(message, chat_config, voice_service, bot, message_thread_id=777)

    mock_indicator.assert_called_once_with(bot, message.chat.id, 777)


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

    sticker_responder = MagicMock()
    sticker_responder.get_sticker_candidates = AsyncMock(return_value=[])

    with patch(
        "src.bot.handlers.media.download_telegram_file",
        new_callable=AsyncMock,
        return_value=b"fake-image",
    ):
        await handle_photo_message(
            message, chat_config, image_service, pipeline, sticker_responder, message_repo, bot
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
    sticker_responder = MagicMock()
    message_repo = MagicMock()

    await handle_photo_message(
        message, chat_config, image_service, pipeline, sticker_responder, message_repo, bot
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

    sticker_responder = MagicMock()

    with patch(
        "src.bot.handlers.media.download_telegram_file",
        new_callable=AsyncMock,
        return_value=b"fake-image",
    ):
        await handle_photo_message(
            message, chat_config, image_service, pipeline, sticker_responder, message_repo, bot
        )

    message_repo.save.assert_not_called()


# ── Photo handler: typing-indicator wiring (I-3) ───────────────────────


def _make_photo_deps(*, caption: str | None, thread_id: int | None = None):
    """Common mocks for handle_photo_message indicator tests."""
    message = _make_message(caption=caption)
    photo = MagicMock()
    photo.file_id = "photo-file-id"
    message.photo = [photo]

    chat_config = _make_chat_config(trigger_words=(), random_response_chance=1.0)
    bot = _make_bot()

    image_service = MagicMock()
    image_service.analyze = AsyncMock(return_value="A cat on a table")

    pipeline = MagicMock()
    pipeline.process = AsyncMock(
        return_value=PipelineResult(
            should_respond=True,
            html_text="Nice cat!",
            trigger_type=TriggerType.TRIGGER,
            response_type=ResponseType.NORMAL,
        )
    )
    pipeline.post_send = AsyncMock()

    message_repo = MagicMock()
    message_repo.save = AsyncMock()

    sticker_responder = MagicMock()
    sticker_responder.get_sticker_candidates = AsyncMock(return_value=[])

    return {
        "message": message,
        "chat_config": chat_config,
        "bot": bot,
        "image_service": image_service,
        "pipeline": pipeline,
        "message_repo": message_repo,
        "sticker_responder": sticker_responder,
        "message_thread_id": thread_id,
    }


class TestHandlePhotoMessageTypingIndicator:
    """Regression guard: image_service.analyze() and, for captioned photos,
    the follow-on pipeline.process() text generation must run under the
    shared typing_indicator helper. Photos without a caption may produce no
    response at all, so per owner decision (Q2) they get no indicator.
    """

    @pytest.mark.asyncio
    async def test_wraps_analysis_and_pipeline_for_caption(self):
        from src.bot.handlers.media import handle_photo_message

        deps = _make_photo_deps(caption="look at this")

        with (
            patch(
                "src.bot.handlers.media.download_telegram_file",
                new_callable=AsyncMock,
                return_value=b"fake-image",
            ),
            patch("src.bot.handlers.media.typing_indicator") as mock_indicator,
        ):
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_photo_message(
                deps["message"],
                deps["chat_config"],
                deps["image_service"],
                deps["pipeline"],
                deps["sticker_responder"],
                deps["message_repo"],
                deps["bot"],
            )

        mock_indicator.assert_called_once_with(
            deps["bot"], deps["message"].chat.id, None, enabled=True
        )
        deps["image_service"].analyze.assert_awaited_once()
        deps["pipeline"].process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_indicator_without_caption(self):
        from src.bot.handlers.media import handle_photo_message

        deps = _make_photo_deps(caption=None)

        with (
            patch(
                "src.bot.handlers.media.download_telegram_file",
                new_callable=AsyncMock,
                return_value=b"fake-image",
            ),
            patch("src.bot.handlers.media.typing_indicator") as mock_indicator,
        ):
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_photo_message(
                deps["message"],
                deps["chat_config"],
                deps["image_service"],
                deps["pipeline"],
                deps["sticker_responder"],
                deps["message_repo"],
                deps["bot"],
            )

        mock_indicator.assert_called_once_with(
            deps["bot"], deps["message"].chat.id, None, enabled=False
        )
        deps["image_service"].analyze.assert_awaited_once()
        deps["pipeline"].process.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_forwards_message_thread_id(self):
        from src.bot.handlers.media import handle_photo_message

        deps = _make_photo_deps(caption="look at this", thread_id=777)

        with (
            patch(
                "src.bot.handlers.media.download_telegram_file",
                new_callable=AsyncMock,
                return_value=b"fake-image",
            ),
            patch("src.bot.handlers.media.typing_indicator") as mock_indicator,
        ):
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_photo_message(
                deps["message"],
                deps["chat_config"],
                deps["image_service"],
                deps["pipeline"],
                deps["sticker_responder"],
                deps["message_repo"],
                deps["bot"],
                message_thread_id=777,
            )

        mock_indicator.assert_called_once_with(
            deps["bot"], deps["message"].chat.id, 777, enabled=True
        )

    @pytest.mark.asyncio
    async def test_indicator_stops_even_if_analysis_raises(self):
        from src.bot.handlers.media import handle_photo_message

        deps = _make_photo_deps(caption="look at this")
        deps["image_service"].analyze = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(
            "src.bot.handlers.media.download_telegram_file",
            new_callable=AsyncMock,
            return_value=b"fake-image",
        ):
            with pytest.raises(RuntimeError):
                await handle_photo_message(
                    deps["message"],
                    deps["chat_config"],
                    deps["image_service"],
                    deps["pipeline"],
                    deps["sticker_responder"],
                    deps["message_repo"],
                    deps["bot"],
                )


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

    from src.services.modules.sticker.models import StickerLearningResult

    sticker_service = MagicMock()
    sticker_service.learn = AsyncMock(
        return_value=StickerLearningResult(
            is_new=True,
            file_unique_id="unique-1",
            analysis_failed=True,
        )
    )

    sticker_responder = MagicMock()
    sticker_responder.find_sticker_for_sticker_reply = AsyncMock(return_value=None)

    bot_config_repo = MagicMock()
    bot_config_repo.get_value = AsyncMock(return_value="")

    message_repo = MagicMock()
    message_repo.get_recent = AsyncMock(return_value=[])

    sticker_repo = _make_sticker_repo()

    with patch(
        "src.bot.handlers.media.download_telegram_file",
        new_callable=AsyncMock,
        return_value=b"fake-webp",
    ):
        admin_repo = MagicMock()
        admin_repo.get_notification_settings = AsyncMock(
            return_value={
                "sticker": "on",
                "unauthorized": True,
                "jailbreak": True,
                "blacklist": True,
                "ai_fallback": True,
            }
        )
        await handle_sticker_message(
            message,
            chat_config,
            sticker_service,
            sticker_responder,
            sticker_repo,
            message_repo,
            bot_config_repo,
            admin_repo,
            bot,
        )

    sticker_service.learn.assert_awaited_once()
    # Silent — no reply
    message.reply.assert_not_awaited()
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_sticker_handler_learns_animated():
    """Animated stickers are now learned (not skipped)."""
    from src.bot.handlers.media import handle_sticker_message
    from src.services.modules.sticker.models import StickerLearningResult

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
    sticker_service.learn = AsyncMock(
        return_value=StickerLearningResult(
            is_new=True,
            file_unique_id="unique-1",
            analysis_failed=True,
        )
    )

    sticker_responder = MagicMock()
    sticker_responder.find_sticker_for_sticker_reply = AsyncMock(return_value=None)

    bot_config_repo = MagicMock()
    bot_config_repo.get_value = AsyncMock(return_value="")

    message_repo = MagicMock()
    message_repo.get_recent = AsyncMock(return_value=[])

    admin_repo = MagicMock()
    admin_repo.get_notification_settings = AsyncMock(
        return_value={
            "sticker": "on",
            "unauthorized": True,
            "jailbreak": True,
            "blacklist": True,
            "ai_fallback": True,
        }
    )

    sticker_repo = _make_sticker_repo()

    with patch(
        "src.bot.handlers.media.download_telegram_file",
        new_callable=AsyncMock,
        return_value=b"fake-tgs",
    ):
        await handle_sticker_message(
            message,
            chat_config,
            sticker_service,
            sticker_responder,
            sticker_repo,
            message_repo,
            bot_config_repo,
            admin_repo,
            bot,
        )

    sticker_service.learn.assert_awaited_once()


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
    sticker_responder = MagicMock()
    sticker_repo = _make_sticker_repo()
    bot_config_repo = MagicMock()
    message_repo = MagicMock()
    admin_repo = MagicMock()

    await handle_sticker_message(
        message,
        chat_config,
        sticker_service,
        sticker_responder,
        sticker_repo,
        message_repo,
        bot_config_repo,
        admin_repo,
        bot,
    )

    sticker_service.learn.assert_not_called()
