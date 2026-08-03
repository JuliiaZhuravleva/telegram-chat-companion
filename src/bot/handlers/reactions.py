"""Handler for `message_reaction` updates -- records who added/removed a
reaction on which message (ADR-0004, R-1).

Registering `@router.message_reaction()` is also what satisfies
`resolve_used_update_types()`: `dp.start_polling(bot)` is called without an
explicit `allowed_updates` in `main.py`, so aiogram derives the list from
registered observers -- no separate wiring needed for the update type itself.
"""

from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.types import MessageReactionUpdated
from dishka.integrations.aiogram import FromDishka

from src.database.repositories.reactions import ReactionRepository
from src.models.chat_config import ChatConfig
from src.services.modules.reactions.models import diff

router = Router(name="reactions")
logger = structlog.get_logger(__name__)


@router.message_reaction()
async def handle_message_reaction(
    event: MessageReactionUpdated,
    chat_config: ChatConfig,
    reaction_repo: FromDishka[ReactionRepository],
) -> None:
    """Diff old/new reaction state and persist the changed rows.

    `reactions_enabled` gates everything -- the module is off for this chat
    at all -- while `reactions_history_enabled` gates only the INSERT, so an
    owner can opt out of behavioral logging without losing R-5's bot-initiated
    reactions (ADR-0004 Decision 3).
    """
    if not chat_config.reactions_enabled:
        return

    events = diff(event.old_reaction, event.new_reaction)
    if not events:
        # Same reaction set before/after (e.g. a duplicate update) -- nothing changed.
        return

    if not chat_config.reactions_history_enabled:
        logger.debug(
            "Reaction history recording disabled, skipping insert",
            chat_id=event.chat.id,
            message_id=event.message_id,
        )
        return

    user_id = event.user.id if event.user else None
    actor_chat_id = event.actor_chat.id if event.actor_chat else None

    try:
        await reaction_repo.insert_events(
            chat_id=event.chat.id,
            message_id=event.message_id,
            user_id=user_id,
            actor_chat_id=actor_chat_id,
            events=events,
        )
    except Exception:
        logger.exception(
            "Failed to record reaction events",
            chat_id=event.chat.id,
            message_id=event.message_id,
        )
        return

    logger.info(
        "Recorded reaction events",
        chat_id=event.chat.id,
        message_id=event.message_id,
        user_id=user_id,
        actor_chat_id=actor_chat_id,
        added=sum(1 for e in events if e.action == "added"),
        removed=sum(1 for e in events if e.action == "removed"),
    )
