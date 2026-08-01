"""Handlers for non-message chat updates: message edits and bot membership changes.

Both were handled by the reference n8n bot's Main Handler (WF 1) and had no
Python counterpart, so an edited message stayed in the history in its original
wording (and was fed to the AI and /summary that way), and the bot never
noticed it had been removed from a chat.
"""

from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.types import ChatMemberUpdated, Message
from dishka.integrations.aiogram import FromDishka

from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService

router = Router(name="chat_events")
logger = structlog.get_logger(__name__)

# Bot is no longer able to read/post in the chat.
_GONE_STATUSES = frozenset({"left", "kicked"})
# Bot is present (plain member or admin).
_PRESENT_STATUSES = frozenset({"member", "administrator", "creator", "restricted"})


@router.edited_message()
async def handle_edited_message(message: Message) -> None:
    """Persist a message edit.

    The write itself is done by ``MessageSaverMiddleware``: an edited update
    carries the same ``Message`` type and the same (chat_id, message_id) key, so
    ``MessageRepository.save()`` takes its ON CONFLICT branch and updates
    ``content`` while stamping ``edited_at``, bumping ``edit_count`` and
    preserving the first wording in ``original_content``.

    This handler exists so that chain actually runs — ``dp.edited_message``
    middlewares are *inner* middlewares in aiogram, so they only fire once a
    handler matches the update.
    """
    logger.info(
        "Message edited",
        chat_id=message.chat.id,
        message_id=message.message_id,
        user_id=message.from_user.id if message.from_user else None,
    )


@router.my_chat_member()
async def handle_my_chat_member(
    event: ChatMemberUpdated,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    chat_config_service: FromDishka[ChatConfigService],
) -> None:
    """Track the bot being added to or removed from a chat.

    Removal flips ``enabled`` off so the bot stops counting a chat it can no
    longer reach as whitelisted.  Being added only records the chat's metadata —
    it deliberately does NOT enable anything, because access stays opt-in via
    the admin approval flow.
    """
    chat = event.chat
    status = event.new_chat_member.status

    if status in _GONE_STATUSES:
        try:
            await chat_settings_repo.set_field(chat.id, "enabled", False)
        except Exception:
            logger.exception("Failed to disable chat after bot removal", chat_id=chat.id)
            return
        # Config is cached for 60s; drop it so the change takes effect at once.
        chat_config_service.invalidate(chat.id)
        logger.info(
            "Bot removed from chat — disabled",
            chat_id=chat.id,
            chat_title=chat.title,
            status=status,
        )
        return

    if status in _PRESENT_STATUSES:
        try:
            await chat_settings_repo.ensure_exists(
                chat.id,
                chat.title or chat.full_name,
                chat.type or "group",
            )
        except Exception:
            logger.exception("Failed to record chat after bot was added", chat_id=chat.id)
            return
        logger.info(
            "Bot added to chat — awaiting whitelist approval",
            chat_id=chat.id,
            chat_title=chat.title,
            status=status,
        )
