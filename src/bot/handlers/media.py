"""Media handlers — voice, photo, and sticker message processing."""

from __future__ import annotations

import random
import time
from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka

from src.bot.progress import MIN_SECONDS_TO_ANNOUNCE, ProgressNotice
from src.bot.reply_flow import (
    finish_reply,
    relevancy_allows_reply,
    send_quoted_reply,
)
from src.bot.utils import ReplyContext, extract_reply_context, should_respond
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.messages import MessageRepository
from src.database.repositories.stickers import StickerRepository
from src.models.chat_config import ChatConfig
from src.models.enums import TriggerType
from src.services.abuse.checker import AntiAbuseChecker
from src.services.costs.spend_limit import SpendLimitService
from src.services.modules.image import ImageAnalysisService
from src.services.modules.sticker import StickerLearningService, StickerResponderService
from src.services.modules.voice import VoiceTranscriptionService
from src.services.relevancy.gate import RelevancyGate
from src.services.text.pipeline import PipelineResult, TextProcessingPipeline
from src.utils import parse_admin_ids
from src.utils.telegram import TelegramFileError, download_telegram_file, typing_indicator

router = Router(name="media")
logger = structlog.get_logger(__name__)

_TRANSCRIBING = {
    "ru": "🎙 Расшифровываю голосовое ({duration})…",
    "en": "🎙 Transcribing voice message ({duration})…",
}
_TRANSCRIBE_FAILED = {
    "ru": "⚠️ Не удалось расшифровать это голосовое. Попробуйте ещё раз.",
    "en": "⚠️ Could not transcribe this voice message. Please try again.",
}


def _format_duration(seconds: int) -> str:
    """``m:ss`` for the progress notice — Telegram shows the same on the bubble."""
    minutes, secs = divmod(max(seconds, 0), 60)
    return f"{minutes}:{secs:02d}"


# TTL cache for sticker sets that failed registration (avoid repeated API calls)
_FAILED_SET_REGISTRATION: dict[str, float] = {}
_FAILED_SET_TTL = 300  # 5 minutes


async def _resolve_bot_id(bot: Bot, kwargs: dict[str, Any]) -> int:
    """The bot's own user id, from `dp["bot_id"]` or a live `getMe` fallback."""
    bot_id: int | None = kwargs.get("bot_id")
    if bot_id is None:
        bot_id = (await bot.me()).id
    return bot_id


# ── Voice / Video Note ────────────────────────────────────────────────


@router.message(F.voice | F.video_note)
async def handle_voice_message(
    message: Message,
    chat_config: ChatConfig,
    voice_service: FromDishka[VoiceTranscriptionService],
    pipeline: FromDishka[TextProcessingPipeline],
    message_repo: FromDishka[MessageRepository],
    relevancy_gate: FromDishka[RelevancyGate],
    spend_limit_svc: FromDishka[SpendLimitService],
    abuse_checker: FromDishka[AntiAbuseChecker],
    bot: Bot,
    message_thread_id: int | None = None,
    **kwargs: Any,
) -> None:
    """Handle voice messages and video notes via Whisper transcription."""
    # Determine type
    is_voice = message.voice is not None
    media = message.voice if is_voice else message.video_note

    if media is None:
        return

    # Check per-chat toggle
    if is_voice and not chat_config.transcribe_voice:
        return
    if not is_voice and not chat_config.transcribe_video_notes:
        return

    # Download audio
    try:
        audio_data = await download_telegram_file(bot, media.file_id)
    except TelegramFileError:
        logger.warning(
            "Failed to download voice file",
            chat_id=message.chat.id,
            file_id=media.file_id,
        )
        return

    # Keep "typing" alive for the whole transcription (route to correct topic)
    user = message.from_user
    user_name = (user.first_name if user else None) or "Unknown"
    message_type = "voice" if is_voice else "video_note"

    # A long voice note takes ten to twenty seconds to come back, and until it
    # does the chat sees only the typing indicator -- which Telegram also shows
    # for a human typing, and which says nothing about *what* is happening.
    # Announce the work for anything long enough that the wait is noticeable;
    # below that threshold the round trip is about a second and a placeholder
    # that appears and vanishes reads as a glitch.
    duration = getattr(media, "duration", 0) or 0
    notice_text = _TRANSCRIBING.get(chat_config.language, _TRANSCRIBING["en"]).format(
        duration=_format_duration(duration)
    )
    async with (
        ProgressNotice(
            message,
            text=notice_text,
            enabled=duration >= MIN_SECONDS_TO_ANNOUNCE,
            reply_to_message_id=message.message_id,
        ) as notice,
        typing_indicator(bot, message.chat.id, message_thread_id),
    ):
        result = await voice_service.transcribe(
            audio_data=audio_data,
            chat_id=message.chat.id,
            message_id=message.message_id,
            user_first_name=user_name,
            message_type=message_type,
            language=chat_config.language,
            user_id=user.id if user else None,
            username=user.username if user else None,
        )

    if result is None:
        # Only speaks when the notice was enabled, i.e. when we already told
        # the chat we were working on something. Leaving a "🎙 Расшифровываю…"
        # standing over a transcription that will never arrive is the same
        # silent-failure shape this whole change exists to remove.
        await notice.fail(_TRANSCRIBE_FAILED.get(chat_config.language, _TRANSCRIBE_FAILED["en"]))
        return

    # Send the transcription through the same deletion-tolerant sender as the
    # answer below. The window is shorter here (Whisper only, not Whisper plus
    # generation) but the race is identical: delete the voice note while it is
    # being transcribed and an unguarded `message.reply` raises, crashing the
    # handler. Telegram's own error is all the user gets — the global handler
    # only replies to CallbackQuery events, never to a Message — so the
    # transcription is lost with no explanation, Whisper is paid for anyway,
    # and the link row below never gets written either.
    # Parts, not one string: a transcript longer than Telegram's 4096-character
    # limit was rejected outright, and because the rejection was re-raised out
    # of the handler the chat got no transcription, no error, and no hint that
    # anything had happened -- while Whisper had been paid for and the words
    # were already in the database. Any voice message over roughly four
    # minutes hits this; four such transcripts were lost in production before
    # this call learned to split.
    reply_parts = VoiceTranscriptionService.format_reply_parts(user_name, result.text)
    # `finish` turns the placeholder into the first part, so the transcription
    # lands where the reader was already looking rather than further down the
    # chat. With no placeholder (a short voice note) it degrades to an ordinary
    # quoted send.
    sent = await notice.finish(reply_parts, language=chat_config.language)
    if not sent:
        logger.warning(
            "Transcription could not be delivered",
            chat_id=message.chat.id,
            message_id=message.message_id,
            part_count=len(reply_parts),
        )
        return

    if len(sent) < len(reply_parts):
        logger.warning(
            "Transcription was delivered only in part",
            chat_id=message.chat.id,
            message_id=message.message_id,
            delivered=len(sent),
            part_count=len(reply_parts),
        )

    # Record what those messages ARE, before anything else can fail. These rows
    # are how a later reply to one gets routed to the speaker instead of the bot
    # (migration 028); without them the bot answers every such reply. Written
    # for EVERY part, not just the first: someone replying to the tail of a long
    # transcript is quoting the speaker just as much as someone replying to its
    # head, and a row that exists for only one of three messages makes the
    # routing depend on which part the reader happened to hit.
    for part in sent:
        await voice_service.record_transcription_message(
            chat_id=message.chat.id,
            message_id=part.message_id,
            source_message_id=message.message_id,
            message_thread_id=message_thread_id,
        )

    await _maybe_answer_transcript(
        message=message,
        chat_config=chat_config,
        transcript=result.text,
        user_name=user_name,
        pipeline=pipeline,
        message_repo=message_repo,
        relevancy_gate=relevancy_gate,
        spend_limit_svc=spend_limit_svc,
        abuse_checker=abuse_checker,
        bot=bot,
        bot_id=await _resolve_bot_id(bot, kwargs),
        message_thread_id=message_thread_id,
    )


async def _maybe_answer_transcript(
    *,
    message: Message,
    chat_config: ChatConfig,
    transcript: str,
    user_name: str,
    pipeline: TextProcessingPipeline,
    message_repo: MessageRepository,
    relevancy_gate: RelevancyGate,
    spend_limit_svc: SpendLimitService,
    abuse_checker: AntiAbuseChecker,
    bot: Bot,
    bot_id: int,
    message_thread_id: int | None,
) -> None:
    """Decide about the transcript the way an ordinary text message is decided.

    Once the bot has relayed the speaker's words, the transcript is just chat
    content: trigger words in it count, a random reply may fire, and an
    unprompted one still has to clear the relevancy gate — the same sequence
    `handlers/message.py` runs. Before this existed a voice message could never
    draw a reply at all, no matter what was said in it.

    Two deliberate differences from the text path:

    * The answer always quotes the **voice message**, never the transcription,
      and quotes it even on a RANDOM trigger (where the text path deliberately
      quotes nothing). The bot's own transcription sits between the voice note
      and the answer, so an unquoted reply here would read as addressed to
      nobody.
    * `should_respond` is fed the transcript explicitly — a voice `Message`
      carries no text of its own, so the default scan would see an empty
      string and only ever produce RANDOM.

    Anti-abuse is not double-counted: `AntiAbuseChecker.check()` writes on four
    paths (CLAUDE.md), and this is the only pipeline pass a voice message ever
    makes.
    """
    reply_ctx = await extract_reply_context(message, bot_id, message_repo)
    respond, trigger_type = should_respond(
        message, chat_config, reply_ctx=reply_ctx, text=transcript
    )
    if not respond:
        return

    if not await relevancy_allows_reply(
        message=message,
        chat_config=chat_config,
        trigger_type=trigger_type,
        message_text=transcript,
        relevancy_gate=relevancy_gate,
        abuse_checker=abuse_checker,
    ):
        return

    user = message.from_user
    user_id = user.id if user else 0

    logger.info(
        "Responding to voice transcript",
        chat_id=message.chat.id,
        user_id=user_id,
        trigger_type=trigger_type.value,
        message_thread_id=message_thread_id,
    )

    # Same Q1 rule as the text path: no "typing" before an unsolicited reply.
    async with typing_indicator(
        bot,
        message.chat.id,
        message_thread_id,
        enabled=trigger_type != TriggerType.RANDOM,
    ):
        result = await pipeline.process(
            chat_id=message.chat.id,
            user_id=user_id,
            user_name=user_name,
            message_text=transcript,
            trigger_type=trigger_type,
            config=chat_config,
            reply_author=reply_ctx.author,
            reply_text=reply_ctx.text,
            reply_is_bot=reply_ctx.is_bot,
            reply_quote_text=reply_ctx.quote_text,
            reply_quote_is_manual=reply_ctx.quote_is_manual,
            message_thread_id=message_thread_id,
            message_id=message.message_id,
        )

    if not result.should_respond or not result.html_text:
        logger.info(
            "Pipeline suppressed response to voice transcript",
            chat_id=message.chat.id,
            trigger_type=trigger_type.value,
            response_type=result.response_type.value if result.response_type else None,
            has_text=bool(result.html_text),
        )
        return

    sent = await send_quoted_reply(
        message=message,
        html_text=result.html_text,
        reply_to_message_id=message.message_id,
        language=chat_config.language,
    )

    if sent is None:
        # Undeliverable, but the generation already happened. post_send is what
        # writes the cost row (generate_text does not self-log, per ADR), so
        # skipping it here would make the spend limit under-report real money.
        await pipeline.post_send(result, bot_message_id=None)
        return

    await finish_reply(
        message=message,
        result=result,
        sent_message_id=sent.message_id,
        chat_config=chat_config,
        pipeline=pipeline,
        spend_limit_svc=spend_limit_svc,
    )


# ── Photo ─────────────────────────────────────────────────────────────


@router.message(F.photo)
async def handle_photo_message(
    message: Message,
    chat_config: ChatConfig,
    image_service: FromDishka[ImageAnalysisService],
    pipeline: FromDishka[TextProcessingPipeline],
    sticker_responder: FromDishka[StickerResponderService],
    message_repo: FromDishka[MessageRepository],
    relevancy_gate: FromDishka[RelevancyGate],
    spend_limit_svc: FromDishka[SpendLimitService],
    abuse_checker: FromDishka[AntiAbuseChecker],
    bot: Bot,
    message_thread_id: int | None = None,
    **kwargs: Any,
) -> None:
    """Handle photo messages — analyze and optionally respond."""
    if not chat_config.image_analysis_enabled:
        return

    if not message.photo:
        return

    # Select highest resolution (last in array)
    photo = message.photo[-1]
    caption = message.caption

    # Download image
    try:
        image_data = await download_telegram_file(bot, photo.file_id)
    except TelegramFileError:
        logger.warning(
            "Failed to download photo",
            chat_id=message.chat.id,
            file_id=photo.file_id,
        )
        return

    # Decide the response intent BEFORE the analysis so the indicator can be
    # scoped honestly: should_respond() reads the caption, not the vision
    # description, so it does not need the analysis to have run.
    respond = False
    trigger_type = TriggerType.NONE
    reply_ctx = ReplyContext()
    if caption:
        bot_id = await _resolve_bot_id(bot, kwargs)
        # Extracted before the decision, not after: it is what decides whether
        # a reply addresses the bot (a reply to one of the bot's transcriptions
        # does not), and pipeline.process() below needs the same object.
        reply_ctx = await extract_reply_context(message, bot_id, message_repo)
        respond, trigger_type = should_respond(message, chat_config, reply_ctx=reply_ctx)

        # Relevancy gate (TD-028). Its absence here was a live defect: an
        # unprompted reply to a captioned photo was sent without ever asking
        # whether it was warranted, while the identical text message went
        # through the gate.
        #
        # This does NOT avoid the Vision call — image_service.analyze() runs
        # below regardless of `respond`, because the description is written to
        # message history even when the bot stays silent. What a declined
        # reply does save is the pipeline's text generation. (An earlier
        # version of this comment claimed the Vision saving; a test written to
        # that claim failed and was right to.)
        if respond and not await relevancy_allows_reply(
            message=message,
            chat_config=chat_config,
            trigger_type=trigger_type,
            message_text=caption,
            relevancy_gate=relevancy_gate,
            abuse_checker=abuse_checker,
        ):
            respond = False

    # Show "typing" only when a reply is actually coming AND it was asked for.
    # A captioned photo is NOT a guarantee of a reply: should_respond() can
    # decline, and the pipeline can still suppress afterwards. RANDOM triggers
    # are excluded per owner decision Q1 — same rule as the text path in
    # handlers/message.py; unprompted replies get no indicator.
    # "typing" (not "upload_photo") is the honest action: the reply is text,
    # the bot never uploads an image of its own.
    result: PipelineResult | None = None
    async with typing_indicator(
        bot,
        message.chat.id,
        message_thread_id,
        enabled=respond and trigger_type != TriggerType.RANDOM,
    ):
        # Analyze image
        description = await image_service.analyze(image_data)

        logger.info(
            "Photo analysis result",
            chat_id=message.chat.id,
            has_description=description is not None,
            has_caption=bool(caption),
        )

        if description is None:
            return

        # `and caption` is redundant at runtime (respond implies a caption) but
        # narrows it to str for the type checker.
        if respond and caption:
            user = message.from_user
            user_id = user.id if user else 0
            user_name = (user.first_name if user else None) or "Unknown"

            result = await pipeline.process(
                chat_id=message.chat.id,
                user_id=user_id,
                user_name=user_name,
                message_text=caption,
                trigger_type=trigger_type,
                config=chat_config,
                reply_author=reply_ctx.author,
                reply_text=reply_ctx.text,
                reply_is_bot=reply_ctx.is_bot,
                reply_quote_text=reply_ctx.quote_text,
                reply_quote_is_manual=reply_ctx.quote_is_manual,
                image_context=description,
                message_thread_id=message_thread_id,
                message_id=message.message_id,
            )

    # Indicator is off from here on: the reply is sent and its bookkeeping runs
    # outside the block, so "typing" never lingers after the reply is visible
    # (post_send generates an embedding — a network call).
    if caption:
        if not respond:
            # Still save the description to message history
            await _update_message_content(
                message_repo, message.chat.id, message.message_id, description
            )
            return

        if result is None or not result.should_respond or not result.html_text:
            return

        reply_to = message.message_id if trigger_type != TriggerType.RANDOM else None

        # Send to correct topic in forum chats
        # Note: message.answer() inherits message_thread_id from the original message
        # Same splitting sender as the text and voice paths. A photo caption
        # can draw a long answer just as a message can, and this site was the
        # last bare `message.answer` carrying model output.
        sent = await send_quoted_reply(
            message=message,
            html_text=result.html_text,
            reply_to_message_id=reply_to,
            language=chat_config.language,
        )

        await finish_reply(
            message=message,
            result=result,
            sent_message_id=sent.message_id if sent else None,
            chat_config=chat_config,
            pipeline=pipeline,
            spend_limit_svc=spend_limit_svc,
        )
    else:
        # photo_only: save description to message history, no response
        await _update_message_content(
            message_repo, message.chat.id, message.message_id, description
        )

    # Image comment sticker (works for both photo_with_text and photo_only)
    if (
        description
        and chat_config.image_comment_sticker_enabled
        and chat_config.sticker_learning_enabled
        and random.random() < chat_config.image_comment_sticker_chance
    ):
        try:
            sticker_match = await sticker_responder.get_sticker_candidates(
                description, limit=1, tolerance_level=chat_config.tolerance_level
            )
            if sticker_match:
                await message.reply_sticker(sticker_match[0].file_id)
                await sticker_responder.record_bot_use(sticker_match[0].file_unique_id)
        except Exception:
            logger.warning(
                "Image comment sticker failed",
                chat_id=message.chat.id,
            )


async def _update_message_content(
    message_repo: MessageRepository,
    chat_id: int,
    message_id: int,
    description: str,
) -> None:
    """Update a message's content with the image description.

    The message was already saved by MessageSaverMiddleware.
    We update its content field with the vision analysis result.
    """
    try:
        await message_repo.save(
            chat_id=chat_id,
            message_id=message_id,
            message_type="photo",
            content=f"[Image: {description}]",
        )
    except Exception:
        logger.warning(
            "Failed to update message with image description",
            chat_id=chat_id,
            message_id=message_id,
        )


# ── Sticker ───────────────────────────────────────────────────────────


@router.message(F.sticker)
async def handle_sticker_message(
    message: Message,
    chat_config: ChatConfig,
    sticker_service: FromDishka[StickerLearningService],
    sticker_responder: FromDishka[StickerResponderService],
    sticker_repo: FromDishka[StickerRepository],
    message_repo: FromDishka[MessageRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    admin_repo: FromDishka[AdminRepository],
    bot: Bot,
) -> None:
    """Handle sticker messages — learn via Vision API + optional sticker reply."""
    if not chat_config.sticker_learning_enabled:
        return

    sticker = message.sticker
    if sticker is None:
        return

    # Download sticker file (all types: static, animated, video)
    try:
        image_data = await download_telegram_file(bot, sticker.file_id)
    except TelegramFileError:
        logger.warning(
            "Failed to download sticker",
            chat_id=message.chat.id,
            file_id=sticker.file_id,
        )
        return

    # Get preceding messages for usage context
    preceding: list[str] = []
    try:
        recent = await message_repo.get_recent(message.chat.id, limit=3, exclude_bot=True)
        preceding = [
            r["content"]
            for r in reversed(recent)
            if r["content"] and r["message_id"] != message.message_id
        ]
    except Exception:
        pass

    # Learn sticker (all types)
    learning_result = await sticker_service.learn(
        sticker=sticker,
        image_data=image_data,
        preceding_messages=preceding or None,
    )

    # Register sticker set for admin panel visibility.
    # Not gated on is_new: if set registration failed on first encounter,
    # subsequent stickers from the same set will retry (self-healing).
    # TTL cache prevents hammering the API for persistently failing sets.
    if sticker.set_name:
        failed_at = _FAILED_SET_REGISTRATION.get(sticker.set_name)
        if failed_at and time.monotonic() - failed_at < _FAILED_SET_TTL:
            pass  # skip — recently failed, wait for TTL
        else:
            try:
                existing_set = await sticker_repo.get_sticker_set(sticker.set_name)
                if not existing_set:
                    tg_set = await bot.get_sticker_set(sticker.set_name)
                    await sticker_repo.upsert_sticker_set(
                        set_name=tg_set.name,
                        set_title=tg_set.title,
                        total_count=len(tg_set.stickers),
                        thumbnail_file_id=(tg_set.thumbnail.file_id if tg_set.thumbnail else None),
                        is_animated=any(s.is_animated for s in tg_set.stickers[:1]),
                        is_video=any(s.is_video for s in tg_set.stickers[:1]),
                    )
                _FAILED_SET_REGISTRATION.pop(sticker.set_name, None)
            except Exception:
                _FAILED_SET_REGISTRATION[sticker.set_name] = time.monotonic()
                logger.warning(
                    "Failed to register sticker set (will retry in 5m)",
                    set_name=sticker.set_name,
                )

    # Notify admins about new stickers
    if learning_result.is_new and not learning_result.analysis_failed:
        try:
            notif_settings = await admin_repo.get_notification_settings(bot_config_repo)
            sticker_mode = str(notif_settings.get("sticker", "on"))
            if sticker_mode != "off":
                admin_ids_raw = await bot_config_repo.get("admin_ids")
                if admin_ids_raw:
                    admin_ids = parse_admin_ids(admin_ids_raw)
                    if admin_ids:
                        await sticker_service.notify_admins(
                            bot,
                            sticker,
                            learning_result,
                            admin_ids,
                            notification_mode=sticker_mode,
                            collage_png=learning_result.collage_png,
                            tolerance_level=chat_config.tolerance_level,
                        )
        except Exception:
            logger.exception("Failed to notify admins about new sticker")

    # Sticker-to-sticker reply
    if (
        chat_config.sticker_reply_to_sticker_enabled
        and random.random() < chat_config.sticker_reply_to_sticker_chance
    ):
        try:
            response = await sticker_responder.find_sticker_for_sticker_reply(
                sticker.file_unique_id, tolerance_level=chat_config.tolerance_level
            )
            if response:
                await message.reply_sticker(response.file_id)
                await sticker_responder.record_bot_use(response.file_unique_id)
        except Exception:
            logger.warning(
                "Sticker-to-sticker reply failed",
                chat_id=message.chat.id,
            )
