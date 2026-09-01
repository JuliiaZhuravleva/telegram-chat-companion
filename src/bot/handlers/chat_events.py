"""Handlers for non-message chat updates: message edits and bot membership changes.

Both were handled by the reference n8n bot's Main Handler (WF 1) and had no
Python counterpart, so an edited message stayed in the history in its original
wording (and was fed to the AI and /summary that way), and the bot never
noticed it had been removed from a chat.
"""

from __future__ import annotations

import structlog
from aiogram import Bot, F, Router
from aiogram.types import ChatMemberUpdated, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.access_requests import AccessRequest, AccessRequestService, SubmitResult
from src.database.repositories.chat_migration import ChatMigrationRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService

router = Router(name="chat_events")
logger = structlog.get_logger(__name__)

# Bot is no longer able to read/post in the chat.
_GONE_STATUSES = frozenset({"left", "kicked"})
# Bot is present (plain member or admin).
#
# `restricted` is a status-string approximation and is known to be one:
# aiogram's ChatMemberRestricted carries `is_member`, which can be False — a bot
# restricted OUT of the chat. So a `restricted(is_member=False) -> member`
# transition is a real join that `_is_join` below reads as "already present"
# and does not queue. Narrow, deliberate, and written down rather than
# discovered later.
_PRESENT_STATUSES = frozenset({"member", "administrator", "creator", "restricted"})

# Chat types an admin can meaningfully whitelist. A DM is NOT one of them, and
# the gate is load-bearing rather than tidy: Telegram delivers `my_chat_member`
# in a private chat when a user blocks or unblocks the bot, and an unblock is
# `kicked -> member`, i.e. exactly the shape of a join. Without this, every
# unblock by any stranger would file an access request and DM every admin a
# card for a "chat" that is one person's DM. A stranger who actually writes
# still produces a card through the message path.
_WHITELISTABLE_CHAT_TYPES = frozenset({"group", "supergroup", "channel"})

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

    That description assumed a *text* edit, and for six months it was the whole
    story only because nobody asked what an edit means for a message that has no
    text. Telegram delivers ``edited_message`` for voice notes, video notes and
    photos too, and the middleware then saves ``text or caption`` — ``None`` —
    over a row whose content the bot itself had written. See
    ``MessageRepository.save``'s docstring for the 52 transcripts and image
    descriptions that cost, and for the COALESCE that now makes a content-less
    re-save a no-op rather than an erasure.

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
    access_requests: FromDishka[AccessRequestService],
    bot: Bot,
) -> None:
    """Track the bot being added to or removed from a chat.

    Removal flips ``enabled`` off so the bot stops counting a chat it can no
    longer reach as whitelisted. Being added still does NOT enable anything —
    access stays opt-in — but it now QUEUES the chat for approval and tells the
    admins (TD-025). Before, a group the bot had just joined was invisible in
    the «Ожидают» tab (which reads ``unauthorized_attempts``) until some human
    happened to post in it, and the log line claimed otherwise.

    No middleware is registered on ``dp.my_chat_member`` and none should be:
    ``AccessControlMiddleware._extract_event_info`` returns ``(None, None, None)``
    for a ``ChatMemberUpdated`` and would gate nothing while looking like a
    whitelist check — and if it ever learned the type it would short-circuit on
    ``enabled = false`` and kill the very handler that records the chat.

    Three guards decide whether to file a request, and each closes a distinct
    way of crying wolf: ``joined`` (a member→administrator promotion is not a
    new chat), ``whitelistable`` (a DM block/unblock is not a chat an admin
    whitelists — see ``_WHITELISTABLE_CHAT_TYPES``), and ``already_enabled``
    (re-adding the bot to an approved chat is not an unauthorized access).
    Note the deliberate asymmetry in the last one: a chat that was approved,
    then REMOVED, comes back disabled and therefore files a fresh request. That
    is re-consent on purpose — auto-restoring would let anyone who can add the
    bot re-enable a chat with no admin in the loop.
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
            row = await chat_settings_repo.ensure_exists(
                chat.id,
                chat.title or chat.full_name,
                chat.type or "group",
            )
        except Exception:
            logger.exception("Failed to record chat after bot was added", chat_id=chat.id)
            return

        joined = event.old_chat_member.status not in _PRESENT_STATUSES
        whitelistable = (chat.type or "") in _WHITELISTABLE_CHAT_TYPES
        already_enabled = bool(row.get("enabled")) if row else False

        result = SubmitResult()
        if joined and whitelistable and not already_enabled:
            result = await access_requests.submit(
                AccessRequest(
                    chat_id=chat.id,
                    chat_title=chat.title or chat.full_name,
                    chat_type=chat.type,
                    chat_username=chat.username,
                    user_id=event.from_user.id if event.from_user else None,
                    user_first_name=event.from_user.first_name if event.from_user else None,
                    user_last_name=event.from_user.last_name if event.from_user else None,
                    user_username=event.from_user.username if event.from_user else None,
                    reason="added",
                ),
                bot=bot,
            )

        # Every field states an outcome that was actually observed. The old line
        # here claimed the chat was "awaiting whitelist approval" while nothing
        # had been enqueued for approval and no admin had been told — TD-025 was
        # that sentence being false. `access_request_id` proves only the row;
        # `notified` is the separate question of whether a human heard about it.
        logger.info(
            "Bot membership changed",
            chat_id=chat.id,
            chat_title=chat.title,
            status=status,
            joined=joined,
            whitelistable=whitelistable,
            already_enabled=already_enabled,
            access_request_id=result.attempt_id,
            notified=result.notified,
            suppressed=result.suppressed,
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
            "Chat migrated to supergroup: settings, knowledge base, rules "
            "and participant names re-keyed",
            old_chat_id=old_chat_id,
            new_chat_id=new_chat_id,
            settings_moved=outcome.settings_moved,
            facts_moved=outcome.facts_moved,
            rules_moved=outcome.rules_moved,
            aliases_moved=outcome.aliases_moved,
            not_moved=NOT_MOVED,
        )
    elif outcome.status == "target_occupied":
        logger.warning(
            "Chat migration needs a human: the new chat already has settings — "
            "settings, knowledge base AND rules all stayed on the old id",
            old_chat_id=old_chat_id,
            new_chat_id=new_chat_id,
        )
