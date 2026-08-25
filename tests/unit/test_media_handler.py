"""Tests for media handlers (voice, photo, sticker)."""

import re
from contextlib import ExitStack
from itertools import chain, count
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.enums import ResponseType, TriggerType
from src.services.ai.base import TranscriptionResult
from src.services.modules.voice.transcription import _HEADER_LABEL
from src.services.relevancy.gate import GateDecision
from src.services.text.pipeline import PipelineResult
from src.utils.telegram_text import TELEGRAM_MESSAGE_LIMIT, parsed_length


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
    # Explicit, not inherited from MagicMock: should_respond() iterates
    # trigger_words and compares random_response_chance to a float, and a bare
    # MagicMock makes the second raise TypeError inside the handler. Defaults
    # are "never speak unprompted" so each test opts in to what it is testing.
    config.trigger_words = ()
    config.random_response_chance = 0.0
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


def _repo(transcription_row=None):
    """A MessageRepository whose transcription lookup returns `transcription_row`."""
    repo = MagicMock()
    repo.get_transcription_source = AsyncMock(return_value=transcription_row)
    repo.save = AsyncMock()
    return repo


def _make_sticker_repo():
    repo = MagicMock()
    repo.get_sticker_set = AsyncMock(return_value=None)
    repo.upsert_sticker_set = AsyncMock()
    return repo


# ── Voice handler tests ──────────────────────────────────────────────

BOT_ID = 999


def _sent_message(message_id: int):
    """What Telegram hands back from sendMessage — with awaitable methods.

    `edit_text` and `delete` must be AsyncMocks, not bare MagicMock attributes:
    the progress placeholder is edited *into* the result, and the code that
    does it deliberately never raises. A non-awaitable attribute therefore does
    not fail the test — it makes the handler silently take its fallback path,
    so a test would pass while proving something else entirely.
    """
    sent = MagicMock(message_id=message_id)
    sent.edit_text = AsyncMock(return_value=sent)
    sent.delete = AsyncMock()
    return sent


def _make_voice_deps(
    *,
    transcript: str | None = "Hello world",
    trigger_words=(),
    random_chance: float = 0.0,
    gate_allows: bool = True,
    voice_message_id: int = 42,
    duration: int = 3,
):
    message = _make_message(message_id=voice_message_id)
    voice = MagicMock()
    voice.file_id = "voice-file-id"
    # Explicit, like every other field on these mocks: the handler compares
    # `duration` against MIN_SECONDS_TO_ANNOUNCE to decide whether to post a
    # progress placeholder, and a bare MagicMock makes that comparison raise
    # TypeError inside the handler. The default is short on purpose -- most
    # tests here are about transcription, not about the placeholder, and a
    # short note posts none, so their message-id arithmetic is unchanged.
    voice.duration = duration
    message.voice = voice
    message.video_note = None
    # Both sends now go through send_quoted_reply -> message.answer: the
    # transcription first (id 43), then the AI reply (id 77).
    # Chained onto an unbounded counter: a transcript over Telegram's limit is
    # delivered as several messages, and a fixed list of four ids turns "the
    # handler sent a fifth message" into StopIteration — a crash that reads as
    # a bug in the handler rather than as the fixture running out.
    _sent_ids = chain([43, 77, 78, 79], count(80))
    # Keep the objects we handed back: a body can reach the chat as a fresh
    # send OR as an edit of one, and only these mocks know about the second.
    sent_messages: list = []

    def _answer(*_a, **_kw):
        sent = _sent_message(next(_sent_ids))
        sent_messages.append(sent)
        return sent

    message.answer = AsyncMock(side_effect=_answer)
    message.sent_messages = sent_messages

    chat_config = _make_chat_config(
        trigger_words=tuple(trigger_words), random_response_chance=random_chance
    )

    voice_service = MagicMock()
    voice_service.record_transcription_message = AsyncMock()
    voice_service.transcribe = AsyncMock(
        return_value=(
            None
            if transcript is None
            else TranscriptionResult(text=transcript, model="whisper-1", provider="openai")
        )
    )

    pipeline = MagicMock()
    pipeline.process = AsyncMock(
        return_value=PipelineResult(
            should_respond=True,
            html_text="и тебе привет",
            trigger_type=TriggerType.TRIGGER,
            response_type=ResponseType.NORMAL,
        )
    )
    pipeline.post_send = AsyncMock()

    relevancy_gate = MagicMock()
    relevancy_gate.evaluate = AsyncMock(
        return_value=GateDecision(should_respond=gate_allows, tier="llm_judge", reason="test")
    )

    return {
        "message": message,
        "chat_config": chat_config,
        "voice_service": voice_service,
        "pipeline": pipeline,
        "message_repo": _repo(),
        "relevancy_gate": relevancy_gate,
        "spend_limit_svc": _no_spend_warning(),
        "abuse_checker": _no_cooldown(),
        "bot": _make_bot(),
    }


async def _run_voice_handler(deps, *, message_thread_id=None, patch_indicator=False):
    from src.bot.handlers.media import handle_voice_message

    stack = [
        patch(
            "src.bot.handlers.media.download_telegram_file",
            new_callable=AsyncMock,
            return_value=b"fake-audio",
        )
    ]
    indicator = None
    if patch_indicator:
        indicator = patch("src.bot.handlers.media.typing_indicator")
        stack.append(indicator)

    with ExitStack() as ctx:
        entered = [ctx.enter_context(cm) for cm in stack]
        if patch_indicator:
            mock_indicator = entered[-1]
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

        await handle_voice_message(
            deps["message"],
            deps["chat_config"],
            deps["voice_service"],
            deps["pipeline"],
            deps["message_repo"],
            deps["relevancy_gate"],
            deps["spend_limit_svc"],
            deps["abuse_checker"],
            deps["bot"],
            message_thread_id=message_thread_id,
            bot_id=BOT_ID,
        )

    return entered[-1] if patch_indicator else None


@pytest.mark.asyncio
async def test_voice_handler_transcribes_and_replies():
    deps = _make_voice_deps()

    await _run_voice_handler(deps)

    deps["voice_service"].transcribe.assert_awaited_once()
    deps["message"].answer.assert_awaited_once()
    assert "Hello world" in deps["message"].answer.call_args.args[0]


@pytest.mark.asyncio
async def test_voice_handler_disabled():
    from src.bot.handlers.media import handle_voice_message

    deps = _make_voice_deps()
    deps["chat_config"].transcribe_voice = False

    await handle_voice_message(
        deps["message"],
        deps["chat_config"],
        deps["voice_service"],
        deps["pipeline"],
        deps["message_repo"],
        deps["relevancy_gate"],
        deps["spend_limit_svc"],
        deps["abuse_checker"],
        deps["bot"],
    )

    deps["voice_service"].transcribe.assert_not_called()


@pytest.mark.asyncio
async def test_voice_handler_transcription_returns_none():
    deps = _make_voice_deps(transcript=None)

    await _run_voice_handler(deps)

    deps["message"].answer.assert_not_awaited()
    deps["pipeline"].process.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_handler_forwards_message_thread_id_to_typing_indicator():
    """Regression guard for I-9 (forum topic routing) after the I-6 refactor
    to the shared typing_indicator helper.

    Asserts the *transcription* indicator specifically. Step 2.5 opens a second
    one when it decides to answer; this config never does, so the call count is
    still exactly one.
    """
    deps = _make_voice_deps()

    mock_indicator = await _run_voice_handler(deps, message_thread_id=777, patch_indicator=True)

    mock_indicator.assert_called_once_with(deps["bot"], deps["message"].chat.id, 777)


# ── Voice step 2.5: deciding about the transcript ─────────────────────
#
# A voice message used to be a dead end: transcribed, posted, and never
# considered as something to answer, no matter what was said in it. The
# transcript is now put through the same decision the text path makes — with
# one difference, that the answer quotes the voice message rather than the
# transcription, so it is visibly aimed at the person who spoke.


@pytest.mark.asyncio
async def test_trigger_word_in_the_transcript_draws_an_answer():
    deps = _make_voice_deps(transcript="привет бот, как дела", trigger_words=("бот",))

    await _run_voice_handler(deps)

    assert deps["message"].answer.await_count == 2  # transcription, then the answer
    deps["pipeline"].process.assert_awaited_once()
    call = deps["pipeline"].process.call_args.kwargs
    assert call["message_text"] == "привет бот, как дела"
    assert call["trigger_type"] == TriggerType.TRIGGER
    assert call["message_id"] == 42


@pytest.mark.asyncio
async def test_transcript_without_a_trigger_is_transcribed_but_not_answered():
    """Control for the test above — same path, nothing addressed to the bot."""
    deps = _make_voice_deps(transcript="привет всем, как дела", trigger_words=("бот",))

    await _run_voice_handler(deps)

    deps["message"].answer.assert_awaited_once()  # transcription still posted
    assert "привет всем" in deps["message"].answer.call_args.args[0]
    deps["pipeline"].process.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_answer_quotes_the_voice_message():
    """The owner's ask: the reply must land on the original audio, never on
    the bot's own transcription."""
    deps = _make_voice_deps(transcript="привет бот", trigger_words=("бот",), voice_message_id=4242)

    await _run_voice_handler(deps)

    assert deps["message"].answer.call_args.kwargs["reply_to_message_id"] == 4242


@pytest.mark.asyncio
async def test_a_random_answer_also_quotes_the_voice_message():
    """Deliberate divergence from the text path, which quotes nothing on
    RANDOM: here the bot's transcription sits between the voice note and the
    answer, so an unquoted reply would read as addressed to nobody."""
    deps = _make_voice_deps(transcript="сегодня хорошая погода", random_chance=1.0)

    await _run_voice_handler(deps)

    assert deps["pipeline"].process.call_args.kwargs["trigger_type"] == TriggerType.RANDOM
    assert deps["message"].answer.call_args.kwargs["reply_to_message_id"] == 42


@pytest.mark.asyncio
async def test_an_unprompted_answer_is_blocked_when_the_gate_declines():
    deps = _make_voice_deps(
        transcript="сегодня хорошая погода", random_chance=1.0, gate_allows=False
    )

    await _run_voice_handler(deps)

    deps["relevancy_gate"].evaluate.assert_awaited_once()
    deps["pipeline"].process.assert_not_awaited()
    deps["message"].answer.assert_awaited_once()  # only the transcription


@pytest.mark.asyncio
async def test_a_trigger_word_is_never_sent_to_the_gate():
    """An explicit address is an invitation — gating it would be a behaviour
    change, not a fix. Mirrors the photo path's rule."""
    deps = _make_voice_deps(transcript="бот, привет", trigger_words=("бот",))

    await _run_voice_handler(deps)

    deps["relevancy_gate"].evaluate.assert_not_awaited()
    deps["pipeline"].process.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_transcript_pipeline_runs_at_most_once():
    """CLAUDE.md: AntiAbuseChecker.check() writes on four paths, so a second
    pipeline pass over one message pushes the speaker toward a ban faster.
    pipeline.process() is where that check lives."""
    deps = _make_voice_deps(transcript="привет бот", trigger_words=("бот",))

    await _run_voice_handler(deps)

    assert deps["pipeline"].process.await_count == 1


@pytest.mark.asyncio
async def test_post_send_bookkeeping_runs_for_a_voice_answer():
    """finish_reply(): cost row, cooldown and the spend warning. The photo path
    shipped without these once (TD-028); this path must not repeat it."""
    deps = _make_voice_deps(transcript="привет бот", trigger_words=("бот",))
    deps["spend_limit_svc"].get_warning_if_exceeded = AsyncMock(return_value="⚠️ over budget")

    await _run_voice_handler(deps)

    deps["pipeline"].post_send.assert_awaited_once()
    assert deps["pipeline"].post_send.call_args.kwargs["bot_message_id"] == 77
    warned = [c for c in deps["message"].answer.await_args_list if "over budget" in str(c)]
    assert warned, "the voice path never emitted the spend warning"


@pytest.mark.asyncio
async def test_a_suppressed_pipeline_result_sends_nothing():
    deps = _make_voice_deps(transcript="привет бот", trigger_words=("бот",))
    deps["pipeline"].process = AsyncMock(return_value=PipelineResult(should_respond=False))

    await _run_voice_handler(deps)

    deps["message"].answer.assert_awaited_once()  # only the transcription
    deps["pipeline"].post_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_video_note_transcript_is_decided_the_same_way():
    """Both media types share the handler; a guard that only covers voice
    would leave half the traffic on the old dead-end path."""
    deps = _make_voice_deps(transcript="привет бот", trigger_words=("бот",))
    deps["message"].voice = None
    note = MagicMock()
    note.file_id = "note-file-id"
    note.duration = 3  # explicit for the same reason as voice.duration above
    deps["message"].video_note = note

    await _run_voice_handler(deps)

    deps["pipeline"].process.assert_awaited_once()


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
    deps["message"].answer.assert_not_awaited()  # the photo path sends nothing


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


# ── Voice: the transcription is recorded as such ──────────────────────
#
# The row written here is the entire basis on which a later reply to the
# transcription gets routed to the speaker instead of the bot. Recognition used
# to be a regex over the rendered header, which a user could forge by asking
# the bot to echo that text back.


@pytest.mark.asyncio
async def test_the_posted_transcription_is_linked_to_its_audio():
    deps = _make_voice_deps(voice_message_id=4242)

    await _run_voice_handler(deps)

    deps["voice_service"].record_transcription_message.assert_awaited_once()
    call = deps["voice_service"].record_transcription_message.call_args.kwargs
    # The id of the message the BOT posted, linked to the audio it transcribes.
    assert call["message_id"] == 43
    assert call["source_message_id"] == 4242


@pytest.mark.asyncio
async def test_nothing_is_recorded_when_there_is_no_transcription():
    deps = _make_voice_deps(transcript=None)

    await _run_voice_handler(deps)

    deps["voice_service"].record_transcription_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_answer_survives_the_voice_message_being_deleted():
    """Owner's ask: if the original is gone, still answer — and say so.

    The bot has already paid for the generation by this point (Whisper, the
    relevancy judge, the model). Dropping the reply because the quote target
    vanished is the worst of the options.
    """
    from aiogram.exceptions import TelegramBadRequest

    deps = _make_voice_deps(transcript="привет бот", trigger_words=("бот",))
    deps["message"].answer = AsyncMock(
        side_effect=[
            MagicMock(message_id=43),  # the transcription lands fine
            TelegramBadRequest(
                method=MagicMock(), message="Bad Request: message to be replied not found"
            ),
            MagicMock(message_id=77),  # the retry, unquoted
        ]
    )

    await _run_voice_handler(deps)

    assert deps["message"].answer.await_count == 3
    _transcription, first, second = deps["message"].answer.await_args_list
    # First attempt quotes the audio; the retry drops the quote and explains.
    assert first.kwargs["reply_to_message_id"] == 42
    assert "reply_to_message_id" not in second.kwargs
    assert "удалено" in second.args[0]
    assert "и тебе привет" in second.args[0]
    # Bookkeeping still runs against the message that actually landed.
    deps["pipeline"].post_send.assert_awaited_once()
    assert deps["pipeline"].post_send.call_args.kwargs["bot_message_id"] == 77


@pytest.mark.asyncio
async def test_an_unrelated_send_failure_is_not_disguised_as_a_deletion():
    """Control. Only "the quote target is gone" earns the note — a broken
    payload must not be relabelled as a deleted message and retried blind."""
    from aiogram.exceptions import TelegramBadRequest

    deps = _make_voice_deps(transcript="привет бот", trigger_words=("бот",))
    deps["message"].answer = AsyncMock(
        side_effect=[
            MagicMock(message_id=43),  # the transcription lands fine
            TelegramBadRequest(method=MagicMock(), message="Bad Request: can't parse entities"),
        ]
    )

    with pytest.raises(TelegramBadRequest):
        await _run_voice_handler(deps)

    # Two calls, not three: no blind retry of a payload Telegram rejected.
    assert deps["message"].answer.await_count == 2


@pytest.mark.asyncio
async def test_cost_is_still_recorded_when_the_answer_cannot_be_delivered():
    """post_send writes the cost row, and generate_text does not self-log (ADR).
    If an undeliverable reply skipped it, the spend limit would under-report
    money that was genuinely spent."""
    from aiogram.exceptions import TelegramBadRequest

    deps = _make_voice_deps(transcript="привет бот", trigger_words=("бот",))
    deps["message"].answer = AsyncMock(
        side_effect=[
            MagicMock(message_id=43),  # the transcription lands fine
            TelegramBadRequest(
                method=MagicMock(), message="Bad Request: message to be replied not found"
            ),
            RuntimeError("network gone"),
        ]
    )

    await _run_voice_handler(deps)

    deps["pipeline"].post_send.assert_awaited_once()
    assert deps["pipeline"].post_send.call_args.kwargs["bot_message_id"] is None


# ── Long transcripts and progress (the production regression) ─────────


def _part_number(part: str) -> int:
    """The N from a '(N/M)' transcription header — 1 when there is no counter."""
    match = re.search(r"\((\d+)/(\d+)\):", part)
    return int(match.group(1)) if match else 1


def _sent_texts(message) -> list[str]:
    """Every body the chat ended up with, in order.

    Two channels, because the progress placeholder is *edited into* the first
    part of a result rather than being deleted and re-sent: a body can arrive
    either as a fresh `answer` or as an `edit_text` on something answered
    earlier. Reading only the first channel would miss exactly the part this
    change added.

    Order is the order the MOCKS recorded, which is not the order the reader
    sees: part 1 of a split result arrives by editing the placeholder, so it is
    recorded last. Callers that care about reading order must sort by the
    header counter (see `_part_number`).
    """
    bodies: list[str] = []
    for call in message.answer.await_args_list:
        if call.args:
            bodies.append(call.args[0])
    for sent in message.sent_messages:
        for edit in sent.edit_text.await_args_list:
            if edit.args:
                bodies.append(edit.args[0])
    return bodies


@pytest.mark.asyncio
async def test_a_transcript_over_the_telegram_limit_is_delivered_in_parts():
    """The exact production failure: a six-minute voice note lost entirely.

    Whisper returned 4648 characters, `sendMessage` answered "message is too
    long", `send_quoted_reply` re-raised it because it matches none of the
    reply-target-gone markers, and the global error handler only replies to
    CallbackQuery events. The transcript reached the database and the bill,
    and never reached the chat. It happened four times.
    """
    # Every word distinct. "слово " * 1500 would have compared a multiset with
    # one distinct value, so it could detect loss and duplication but NOT
    # reordering or mis-numbering — reversing the parts left it green.
    words = [f"слово{index}" for index in range(1500)]
    transcript = " ".join(words)
    deps = _make_voice_deps(transcript=transcript, duration=372)

    await _run_voice_handler(deps)

    bodies = _sent_texts(deps["message"])
    # The progress placeholder is plain text; the transcription parts are not.
    parts = [body for body in bodies if _HEADER_LABEL in body]
    assert len(parts) > 1, "a transcript this long must be split, not sent whole"
    for part in parts:
        assert parsed_length(part) <= TELEGRAM_MESSAGE_LIMIT

    # Ordered by the counter the READER sees, not by the order the mocks
    # recorded: part 1 arrives by editing the placeholder, so the raw call order
    # really is (2/N), (3/N), (1/N). Sorting by the header makes the counter
    # itself load-bearing — a mis-numbered or reversed split now fails here.
    numbered = sorted((_part_number(part), part) for part in parts)
    assert [n for n, _ in numbered] == list(range(1, len(parts) + 1))

    spoken = " ".join(part.split("\n\n", 1)[1] for _, part in numbered)
    assert spoken.split() == words


@pytest.mark.asyncio
async def test_every_part_of_a_split_transcription_is_linked_to_its_audio():
    """Migration 028's link row decides whether a reply goes to the speaker.

    Recorded for the first part only, a reply to the tail of a long transcript
    would be treated as addressed to the bot — so the routing would depend on
    which message the reader happened to answer.
    """
    deps = _make_voice_deps(transcript="слово " * 1500, duration=372)

    await _run_voice_handler(deps)

    recorded = deps["voice_service"].record_transcription_message.await_args_list
    parts = [b for b in _sent_texts(deps["message"]) if _HEADER_LABEL in b]
    assert len(recorded) == len(parts) > 1
    for call in recorded:
        assert call.kwargs["source_message_id"] == deps["message"].message_id


@pytest.mark.asyncio
async def test_a_long_voice_note_says_it_is_working():
    """Fifteen silent seconds are indistinguishable from a bot that ignored you."""
    deps = _make_voice_deps(duration=372)

    await _run_voice_handler(deps)

    assert any("Расшифровываю" in body for body in _sent_texts(deps["message"]))


@pytest.mark.asyncio
async def test_a_short_voice_note_does_not():
    """A placeholder that appears and vanishes inside a second reads as a glitch."""
    deps = _make_voice_deps(duration=3)

    await _run_voice_handler(deps)

    assert not any("Расшифровываю" in body for body in _sent_texts(deps["message"]))


@pytest.mark.asyncio
async def test_a_failed_transcription_of_a_long_note_is_reported():
    """Leaving the placeholder standing over work that never arrives is the
    same silent failure in a new costume."""
    deps = _make_voice_deps(transcript=None, duration=372)

    await _run_voice_handler(deps)

    assert any("Не удалось расшифровать" in text for text in _sent_texts(deps["message"]))


@pytest.mark.asyncio
async def test_a_failed_transcription_of_a_short_note_stays_quiet():
    """Control for the test above: it must not have made the bot chattier."""
    deps = _make_voice_deps(transcript=None, duration=3)

    await _run_voice_handler(deps)

    deps["message"].answer.assert_not_awaited()
