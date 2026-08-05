"""Media handlers — voice, photo, and sticker message processing."""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka

from src.bot.utils import extract_reply_context, should_respond
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.messages import MessageRepository
from src.database.repositories.stickers import StickerRepository
from src.models.chat_config import ChatConfig
from src.models.enums import TriggerType
from src.services.modules.image import ImageAnalysisService
from src.services.modules.sticker import StickerLearningService, StickerResponderService
from src.services.modules.voice import VoiceTranscriptionService
from src.services.text.pipeline import TextProcessingPipeline
from src.utils import parse_admin_ids
from src.utils.telegram import TelegramFileError, download_telegram_file

router = Router(name="media")
logger = structlog.get_logger(__name__)

# TTL cache for sticker sets that failed registration (avoid repeated API calls)
_FAILED_SET_REGISTRATION: dict[str, float] = {}
_FAILED_SET_TTL = 300  # 5 minutes


# ── Voice / Video Note ────────────────────────────────────────────────


@router.message(F.voice | F.video_note)
async def handle_voice_message(
    message: Message,
    chat_config: ChatConfig,
    voice_service: FromDishka[VoiceTranscriptionService],
    bot: Bot,
    message_thread_id: int | None = None,
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

    # Send typing action in parallel with transcription (route to correct topic)
    user = message.from_user
    user_name = (user.first_name if user else None) or "Unknown"
    message_type = "voice" if is_voice else "video_note"

    typing_task = asyncio.create_task(
        bot.send_chat_action(
            message.chat.id,
            ChatAction.TYPING,
            message_thread_id=message_thread_id,
        )
    )

    result = await voice_service.transcribe(
        audio_data=audio_data,
        chat_id=message.chat.id,
        message_id=message.message_id,
        user_first_name=user_name,
        message_type=message_type,
        language=chat_config.language,
    )

    # Ensure typing task completes (ignore errors)
    with contextlib.suppress(Exception):
        await typing_task

    if result is None:
        return

    # Send formatted reply (reply routes to same topic via reply_to_message_id)
    reply_text = VoiceTranscriptionService.format_reply(user_name, result.text)
    await message.reply(reply_text, parse_mode="Markdown")


# ── Photo ─────────────────────────────────────────────────────────────


@router.message(F.photo)
async def handle_photo_message(
    message: Message,
    chat_config: ChatConfig,
    image_service: FromDishka[ImageAnalysisService],
    pipeline: FromDishka[TextProcessingPipeline],
    sticker_responder: FromDishka[StickerResponderService],
    message_repo: FromDishka[MessageRepository],
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

    # Analyze image
    description = await image_service.analyze(image_data)

    logger.info(
        "Photo analysis result",
        chat_id=message.chat.id,
        has_description=description is not None,
        has_caption=bool(message.caption),
    )

    if description is None:
        return

    caption = message.caption

    if caption:
        # photo_with_text: decide whether to respond via text pipeline
        bot_id: int | None = kwargs.get("bot_id")
        if bot_id is None:
            bot_id = (await bot.me()).id
        respond, trigger_type = should_respond(message, chat_config, bot_id)

        if not respond:
            # Still save the description to message history
            await _update_message_content(
                message_repo, message.chat.id, message.message_id, description
            )
            return

        user = message.from_user
        user_id = user.id if user else 0
        user_name = (user.first_name if user else None) or "Unknown"

        # Extract reply context (full message + manually-highlighted quote, if any)
        reply_ctx = extract_reply_context(message)

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

        if not result.should_respond or not result.html_text:
            return

        reply_to = message.message_id if trigger_type != TriggerType.RANDOM else None

        # Send to correct topic in forum chats
        # Note: message.answer() inherits message_thread_id from the original message
        sent = await message.answer(
            result.html_text,
            parse_mode="HTML",
            reply_to_message_id=reply_to,
        )

        await pipeline.post_send(result, bot_message_id=sent.message_id)
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
            sticker_match = await sticker_responder.get_sticker_candidates(description, limit=1)
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
                sticker.file_unique_id
            )
            if response:
                await message.reply_sticker(response.file_id)
                await sticker_responder.record_bot_use(response.file_unique_id)
        except Exception:
            logger.warning(
                "Sticker-to-sticker reply failed",
                chat_id=message.chat.id,
            )
