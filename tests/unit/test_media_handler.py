"""Tests for media handlers (voice, photo, sticker)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.enums import ResponseType, TriggerType
from src.services.ai.base import TranscriptionResult
from src.services.relevancy.gate import GateDecision
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
    # Explicitly awaitable: a bare MagicMock attribute raises TypeError when
    # awaited, and finish_reply() swallows send failures — so without this the
    # sticker test would "pass" against a call that never succeeded.
    msg.answer_sticker = AsyncMock()

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


# ── Photo handler tests ──────────────────────────────────────────────


def _permissive_gate():
    """A relevancy gate that always allows — these tests are not about gating."""
    gate = MagicMock()
    gate.evaluate = AsyncMock(
        return_value=GateDecision(should_respond=True, tier="fast_rules", reason="test-allow")
    )
    return gate


def _no_spend_warning():
    svc = MagicMock()
    svc.get_warning_if_exceeded = AsyncMock(return_value=None)
    return svc


def _no_cooldown():
    checker = MagicMock()
    checker.is_in_cooldown = AsyncMock(return_value=False)
    return checker


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
            message,
            chat_config,
            image_service,
            pipeline,
            sticker_responder,
            message_repo,
            _permissive_gate(),
            _no_spend_warning(),
            _no_cooldown(),
            bot,
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
        message,
        chat_config,
        image_service,
        pipeline,
        sticker_responder,
        message_repo,
        _permissive_gate(),
        _no_spend_warning(),
        _no_cooldown(),
        bot,
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
            message,
            chat_config,
            image_service,
            pipeline,
            sticker_responder,
            message_repo,
            _permissive_gate(),
            _no_spend_warning(),
            _no_cooldown(),
            bot,
        )

    message_repo.save.assert_not_called()


@pytest.mark.asyncio
async def test_photo_handler_with_caption_forwards_reply_quote_to_pipeline():
    """Q-1: handle_photo_message's caption branch must use the shared
    extract_reply_context() helper and forward reply_quote_text /
    reply_quote_is_manual to pipeline.process() -- previously this branch
    duplicated the reply-extraction block inline and had no quote support."""
    from src.bot.handlers.media import handle_photo_message

    reply_to = MagicMock()
    reply_to.text = "full original message with detail"
    reply_to.caption = None
    reply_user = MagicMock()
    reply_user.first_name = "Bob"
    reply_user.is_bot = False
    reply_to.from_user = reply_user

    quote = MagicMock()
    quote.text = "detail"
    quote.is_manual = True

    message = _make_message(caption="check this out bot")
    message.reply_to_message = reply_to
    message.quote = quote
    photo = MagicMock()
    photo.file_id = "photo-file-id"
    message.photo = [photo]

    chat_config = _make_chat_config(trigger_words=["bot"], random_response_chance=0.0)
    bot = _make_bot()

    image_service = MagicMock()
    image_service.analyze = AsyncMock(return_value="A cat on a table")

    pipeline_result = MagicMock()
    pipeline_result.should_respond = False  # short-circuit right after the call we're asserting
    pipeline = MagicMock()
    pipeline.process = AsyncMock(return_value=pipeline_result)

    message_repo = MagicMock()
    sticker_responder = MagicMock()

    with patch(
        "src.bot.handlers.media.download_telegram_file",
        new_callable=AsyncMock,
        return_value=b"fake-image",
    ):
        await handle_photo_message(
            message,
            chat_config,
            image_service,
            pipeline,
            sticker_responder,
            message_repo,
            _permissive_gate(),
            _no_spend_warning(),
            _no_cooldown(),
            bot,
        )

    pipeline.process.assert_awaited_once()
    call_kwargs = pipeline.process.call_args.kwargs
    assert call_kwargs["reply_author"] == "Bob"
    assert call_kwargs["reply_text"] == "full original message with detail"
    assert call_kwargs["reply_quote_text"] == "detail"
    assert call_kwargs["reply_quote_is_manual"] is True


# ── Photo handler: typing-indicator wiring (I-3) ───────────────────────


def _make_photo_deps(
    *,
    caption: str | None,
    thread_id: int | None = None,
    random_reply: bool = False,
):
    """Common mocks for handle_photo_message indicator tests.

    ``random_reply=False`` (default) makes the caption match a trigger word, so
    should_respond() returns TriggerType.TRIGGER — an explicitly requested
    reply. ``random_reply=True`` forces the unprompted RANDOM branch instead,
    which per owner decision Q1 must NOT show the indicator.
    """
    message = _make_message(caption=caption)
    photo = MagicMock()
    photo.file_id = "photo-file-id"
    message.photo = [photo]

    if random_reply:
        chat_config = _make_chat_config(trigger_words=(), random_response_chance=1.0)
    else:
        chat_config = _make_chat_config(trigger_words=("look",), random_response_chance=0.0)
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

    # TD-028 collaborators. Defaults deliberately permissive: the gate allows,
    # nobody is in cooldown, no spend warning — so every pre-existing assertion
    # in this file keeps testing what it was written to test.
    relevancy_gate = MagicMock()
    relevancy_gate.evaluate = AsyncMock(
        return_value=GateDecision(should_respond=True, tier="fast_rules", reason="test-allow")
    )
    spend_limit_svc = MagicMock()
    spend_limit_svc.get_warning_if_exceeded = AsyncMock(return_value=None)
    abuse_checker = MagicMock()
    abuse_checker.is_in_cooldown = AsyncMock(return_value=False)

    return {
        "message": message,
        "chat_config": chat_config,
        "bot": bot,
        "image_service": image_service,
        "pipeline": pipeline,
        "message_repo": message_repo,
        "sticker_responder": sticker_responder,
        "message_thread_id": thread_id,
        "relevancy_gate": relevancy_gate,
        "spend_limit_svc": spend_limit_svc,
        "abuse_checker": abuse_checker,
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
                deps["relevancy_gate"],
                deps["spend_limit_svc"],
                deps["abuse_checker"],
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
                deps["relevancy_gate"],
                deps["spend_limit_svc"],
                deps["abuse_checker"],
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
                deps["relevancy_gate"],
                deps["spend_limit_svc"],
                deps["abuse_checker"],
                deps["bot"],
                message_thread_id=777,
            )

        mock_indicator.assert_called_once_with(
            deps["bot"], deps["message"].chat.id, 777, enabled=True
        )

    @pytest.mark.asyncio
    async def test_no_indicator_for_random_reply_to_captioned_photo(self):
        """Owner decision Q1: unprompted RANDOM replies get no indicator.

        The text path (handlers/message.py) has always honoured this; the photo
        path decided `enabled` from the caption alone, before the trigger type
        was known, so a random reply to a captioned photo still announced
        itself. Guards against that divergence returning.
        """
        from src.bot.handlers.media import handle_photo_message

        deps = _make_photo_deps(caption="look at this", random_reply=True)

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
                deps["relevancy_gate"],
                deps["spend_limit_svc"],
                deps["abuse_checker"],
                deps["bot"],
            )

        mock_indicator.assert_called_once_with(
            deps["bot"], deps["message"].chat.id, None, enabled=False
        )
        # The reply itself still happens — only the indicator is suppressed.
        deps["pipeline"].process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_indicator_closes_before_reply_is_sent(self):
        """The indicator must not still be running once the reply is visible.

        post_send() generates an embedding (a network call), so leaving it
        inside the block made "typing" linger after the answer had landed.
        """
        from src.bot.handlers.media import handle_photo_message

        deps = _make_photo_deps(caption="look at this")
        order: list[str] = []

        def _record_answer(*_args, **_kwargs):
            order.append("answer")
            return MagicMock(message_id=42)

        def _record_post_send(*_args, **_kwargs):
            order.append("post_send")

        def _record_indicator_off(*_args):
            order.append("indicator_off")
            return False

        deps["message"].answer = AsyncMock(side_effect=_record_answer)
        deps["pipeline"].post_send = AsyncMock(side_effect=_record_post_send)

        with (
            patch(
                "src.bot.handlers.media.download_telegram_file",
                new_callable=AsyncMock,
                return_value=b"fake-image",
            ),
            patch("src.bot.handlers.media.typing_indicator") as mock_indicator,
        ):
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(side_effect=_record_indicator_off)

            await handle_photo_message(
                deps["message"],
                deps["chat_config"],
                deps["image_service"],
                deps["pipeline"],
                deps["sticker_responder"],
                deps["message_repo"],
                deps["relevancy_gate"],
                deps["spend_limit_svc"],
                deps["abuse_checker"],
                deps["bot"],
            )

        assert order == ["indicator_off", "answer", "post_send"], order

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
                    deps["relevancy_gate"],
                    deps["spend_limit_svc"],
                    deps["abuse_checker"],
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
async def test_sticker_handler_notify_admins_threads_tolerance_level():
    """A-1: notify_admins() receives the originating chat's own resolved
    ChatConfig.tolerance_level (уровень приличия) -- this is the one DM
    sticker card with a real chat in scope, unlike the catalog-browsing
    cards in admin_sticker.py which fall back to the global default."""
    from src.bot.handlers.media import handle_sticker_message
    from src.services.modules.sticker.models import StickerLearningResult

    message = _make_message()
    sticker = MagicMock()
    sticker.file_id = "sticker-file-id"
    sticker.file_unique_id = "unique-1"
    sticker.set_name = None
    sticker.is_animated = False
    sticker.is_video = False
    message.sticker = sticker

    chat_config = _make_chat_config(tolerance_level=0.73)
    bot = _make_bot()

    sticker_service = MagicMock()
    sticker_service.learn = AsyncMock(
        return_value=StickerLearningResult(
            is_new=True,
            file_unique_id="unique-1",
            visual_description="a cat",
            analysis_failed=False,
        )
    )
    sticker_service.notify_admins = AsyncMock()

    sticker_responder = MagicMock()
    sticker_responder.find_sticker_for_sticker_reply = AsyncMock(return_value=None)

    bot_config_repo = MagicMock()
    bot_config_repo.get = AsyncMock(return_value="12345")

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
        return_value=b"fake-webp",
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

    sticker_service.notify_admins.assert_awaited_once()
    assert sticker_service.notify_admins.call_args.kwargs["tolerance_level"] == 0.73


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


# TD-028: three things the text path does that the photo path did not.
#
# handlers/media.py grew as a partial copy of handlers/message.py, and the copy
# lost the relevancy gate, the spend-limit warning and the AI-chosen sticker.
# The first was a live defect, not just duplication: an unprompted reply to a
# captioned photo was sent without ever asking whether it was warranted, while
# the identical text message was gated.
#
# These assert the photo handler's OWN behaviour. Every pre-existing test in
# this file kept passing the whole time the gaps were there, because none of
# them ever looked.


def _photo_deps_for_random_reply(*, gate_allows: bool):
    deps = _make_photo_deps(caption="just chatting", random_reply=True)
    deps["relevancy_gate"].evaluate = AsyncMock(
        return_value=GateDecision(should_respond=gate_allows, tier="llm_judge", reason="test")
    )
    return deps


async def _run_photo_handler(deps):
    from src.bot.handlers.media import handle_photo_message

    with patch(
        "src.bot.handlers.media.download_telegram_file",
        new_callable=AsyncMock,
        return_value=b"fake-image",
    ):
        await handle_photo_message(
            deps["message"],
            deps["chat_config"],
            deps["image_service"],
            deps["pipeline"],
            deps["sticker_responder"],
            deps["message_repo"],
            deps["relevancy_gate"],
            deps["spend_limit_svc"],
            deps["abuse_checker"],
            deps["bot"],
        )


@pytest.mark.asyncio
async def test_random_photo_reply_is_blocked_when_gate_declines():
    """The live defect. Before the fix the bot replied regardless."""
    deps = _photo_deps_for_random_reply(gate_allows=False)

    await _run_photo_handler(deps)

    deps["relevancy_gate"].evaluate.assert_awaited_once()
    deps["pipeline"].process.assert_not_awaited()
    deps["message"].answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_random_photo_reply_proceeds_when_gate_allows():
    """False-positive control for the test above.

    Without it, that assertion would also pass if the gate blocked
    everything — including replies it approved.
    """
    deps = _photo_deps_for_random_reply(gate_allows=True)

    await _run_photo_handler(deps)

    deps["relevancy_gate"].evaluate.assert_awaited_once()
    deps["pipeline"].process.assert_awaited_once()
    deps["message"].answer.assert_awaited()


@pytest.mark.asyncio
async def test_declined_random_reply_still_analyses_but_skips_generation():
    """What the gate does and does not save, pinned deliberately.

    It does NOT skip the Vision call: the description is written to message
    history whether or not the bot replies, so analyse() runs either way. It
    DOES skip pipeline.process(), the text generation.

    Written the other way round first — asserting analyse() was skipped — and
    it failed, correctly: the assumption was wrong and a comment in media.py
    had already been written to it. Kept as a test so the distinction is
    recorded rather than re-derived.
    """
    deps = _photo_deps_for_random_reply(gate_allows=False)

    await _run_photo_handler(deps)

    deps["image_service"].analyze.assert_awaited_once()
    deps["pipeline"].process.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_trigger_on_a_photo_is_never_gated():
    """A mention or trigger word is an invitation — gating it would be a
    behaviour change, not a fix. Guards against over-applying the gate."""
    deps = _make_photo_deps(caption="look at this")  # trigger word: "look"

    await _run_photo_handler(deps)

    deps["relevancy_gate"].evaluate.assert_not_awaited()
    deps["pipeline"].process.assert_awaited_once()


@pytest.mark.asyncio
async def test_ai_chosen_sticker_is_sent_on_the_photo_path():
    """Was computed by the pipeline and silently dropped."""
    deps = _make_photo_deps(caption="look at this")
    deps["pipeline"].process = AsyncMock(
        return_value=PipelineResult(
            should_respond=True,
            html_text="Nice cat!",
            trigger_type=TriggerType.TRIGGER,
            response_type=ResponseType.NORMAL,
            sticker_file_id="sticker-123",
        )
    )

    await _run_photo_handler(deps)

    deps["message"].answer_sticker.assert_awaited_once_with("sticker-123")


@pytest.mark.asyncio
async def test_spend_limit_warning_fires_on_the_photo_path():
    """A daily limit that silently does not apply to photo traffic is worse
    than no limit — it still reads as enforced."""
    deps = _make_photo_deps(caption="look at this")
    deps["spend_limit_svc"].get_warning_if_exceeded = AsyncMock(return_value="⚠️ over budget")

    await _run_photo_handler(deps)

    warned = [call for call in deps["message"].answer.await_args_list if "over budget" in str(call)]
    assert warned, "the photo path never emitted the spend warning"


@pytest.mark.asyncio
async def test_spend_warning_is_checked_after_post_send_writes_the_cost_row():
    """Ordering is load-bearing: post_send writes the usage row, so checking
    the limit before it reads a stale total and the warning is always one
    message late."""
    deps = _make_photo_deps(caption="look at this")
    order: list[str] = []

    async def _record_post_send(*_args, **_kwargs):
        order.append("post_send")

    async def _record_spend_check(*_args, **_kwargs):
        order.append("spend_check")
        return None

    deps["pipeline"].post_send = AsyncMock(side_effect=_record_post_send)
    deps["spend_limit_svc"].get_warning_if_exceeded = AsyncMock(side_effect=_record_spend_check)

    await _run_photo_handler(deps)

    assert order == ["post_send", "spend_check"], f"wrong order: {order}"
