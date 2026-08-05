"""Repository for the message_reactions table (ADR-0004, R-1)."""

from __future__ import annotations

from datetime import datetime

import asyncpg

from src.services.modules.reactions.models import ReactionEvent


class ReactionRepository:
    """Data access layer for recorded reaction add/remove events."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert_events(
        self,
        *,
        chat_id: int,
        message_id: int,
        user_id: int | None,
        actor_chat_id: int | None,
        events: list[ReactionEvent],
        event_date: datetime | None = None,
    ) -> None:
        """Insert one row per diffed reaction event.

        `user_id`/`actor_chat_id` are passed through as-is (both may be None --
        a future Bot API edge case shouldn't hard-fail the insert, per
        ADR-0004 Decision 1; no CHECK constraint enforces "exactly one set").
        A no-op for an empty event list.

        `event_date` is `MessageReactionUpdated.date`. It is what makes the
        insert idempotent: Telegram redelivers an update whenever the polling
        offset was not confirmed (a restart mid-batch is enough), and a
        redelivery carries the identical old/new reaction pair, so the caller's
        diff produces the same events again. `event_date` is stable across those
        redeliveries where our own `created_at` is not, so the unique index from
        migration 019 can recognise the repeat and ON CONFLICT drops it.
        """
        if not events:
            return

        await self._pool.executemany(
            """
            INSERT INTO message_reactions (
                chat_id, message_id, user_id, actor_chat_id,
                action, reaction_type, emoji, custom_emoji_id, event_date
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT DO NOTHING
            """,
            [
                (
                    chat_id,
                    message_id,
                    user_id,
                    actor_chat_id,
                    event.action,
                    event.reaction_type,
                    event.emoji,
                    event.custom_emoji_id,
                    event_date,
                )
                for event in events
            ],
        )
