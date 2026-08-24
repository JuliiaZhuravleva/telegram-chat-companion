"""Handlers for non-message chat updates: message edits and bot membership changes.

Both were handled by the reference n8n bot's Main Handler (WF 1) and had no
Python counterpart, so an edited message stayed in the history in its original
wording (and was fed to the AI and /summary that way), and the bot never
noticed it had been removed from a chat.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.types import ChatMemberUpdated, Message
from dishka.integrations.aiogram import FromDishka

from src.database.repositories.chat_migration import ChatMigrationRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService

router = Router(name="chat_events")
logger = structlog.get_logger(__name__)

# Bot is no longer able to read/post in the chat.
_GONE_STATUSES = frozenset({"left", "kicked"})
# Bot is present (plain member or admin).
_PRESENT_STATUSES = frozenset({"member", "administrator", "creator", "restricted"})

# What a supergroup re-key leaves on the OLD chat_id, named in full so the gap
# is a log field rather than an assumption. `custom_rules` was silently in this
# set until TD-112 and belonged in the moved set instead; `unauthorized_attempts`
# is here on purpose (a rejection is a ban record — re-keying it is its own
# decision, see ChatMigrationRepository's module docstring).
NOT_MOVED = "chat_memory, chat_messages, chat_chunks, unauthorized_attempts, observability logs"


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


@router.message(F.migrate_to_chat_id | F.migrate_from_chat_id)
async def handle_chat_migration(
    message: Message,
    migration_repo: FromDishka[ChatMigrationRepository],
    chat_config_service: FromDishka[ChatConfigService],
) -> None:
    """Re-key a chat's settings, knowledge base and rules on supergroup upgrade.

    Telegram issues a NEW ``chat_id`` on upgrade and announces it twice: once
    in the old chat (``migrate_to_chat_id`` set, ``chat.id`` = old) and once in
    the new one (``migrate_from_chat_id`` set, ``chat.id`` = new). Either
    update carries both ids, so both are accepted and the second is a quiet
    no-op -- ``migrate()`` reports ``nothing_to_move`` once the rows are gone
    from the old id.

    Filters, not a body guard: once a handler's filters match, aiogram
    consumes the update, so a chat-type or field check inside the body would
    silently swallow ordinary messages (CLAUDE.md).

    Known limitation, deliberate: ``AccessControlMiddleware`` gates on
    ``chat_settings.enabled``, so a chat that was never enabled never reaches
    this handler. That is acceptable today -- a disabled chat cannot run
    ``/remember``, so it has no facts to strand -- but it stops being true the
    moment anything writes ``chat_facts`` outside the enabled path.

    Second consequence of that same gate, and it is live rather than
    hypothetical: an admin's *rejection* of a chat lives in
    ``unauthorized_attempts`` and is never re-keyed from here. For a currently
    rejected chat that is unreachable anyway (``enabled`` is false, so the
    update never arrives). But approval only flips rows that are still
    ``pending``, so a chat that accumulated two attempts and was then rejected
    on one and approved on the other is ENABLED while still carrying a
    ``status='rejected'`` row -- that chat does reach this handler, and its
    ban record stays on the old id, lifting the moment the chat is later
    de-whitelisted under the new one.
    """
    old_chat_id = message.chat.id
    new_chat_id = message.migrate_to_chat_id
    if new_chat_id is None:
        # The announcement seen from the new side: chat.id is already the new id.
        new_chat_id = old_chat_id
        old_chat_id = message.migrate_from_chat_id or old_chat_id

    if old_chat_id == new_chat_id:
        return

    outcome = await migration_repo.migrate(old_chat_id, new_chat_id)

    if outcome.status == "migrated":
        # The cached ChatConfig is keyed by the old id and would otherwise
        # serve this request's remaining handlers a config for a chat that no
        # longer exists.
        chat_config_service.invalidate(old_chat_id)
        logger.info(
            "Chat migrated to supergroup: settings, knowledge base and rules re-keyed",
            old_chat_id=old_chat_id,
            new_chat_id=new_chat_id,
            settings_moved=outcome.settings_moved,
            facts_moved=outcome.facts_moved,
            rules_moved=outcome.rules_moved,
            not_moved=NOT_MOVED,
        )
    elif outcome.status == "target_occupied":
        logger.warning(
            "Chat migration needs a human: the new chat already has settings — "
            "settings, knowledge base AND rules all stayed on the old id",
            old_chat_id=old_chat_id,
            new_chat_id=new_chat_id,
        )
