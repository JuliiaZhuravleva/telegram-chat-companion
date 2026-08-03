"""Repository for chat_messages table."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


class MessageRepository:
    """Data access layer for chat messages."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(
        self,
        chat_id: int,
        message_id: int,
        message_type: str,
        *,
        user_id: int | None = None,
        username: str | None = None,
        first_name: str | None = None,
        content: str | None = None,
        raw_data: dict[str, Any] | None = None,
        reply_to_message_id: int | None = None,
        is_bot_message: bool = False,
        sticker_file_id: str | None = None,
        sticker_file_unique_id: str | None = None,
        sticker_set_name: str | None = None,
        sticker_emoji: str | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        """Save a chat message."""
        await self._pool.execute(
            """
            INSERT INTO chat_messages (
                chat_id, message_id, user_id, username, first_name,
                message_type, content, raw_data, reply_to_message_id,
                is_bot_message, sticker_file_id, sticker_file_unique_id,
                sticker_set_name, sticker_emoji, message_thread_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13, $14, $15)
            ON CONFLICT (chat_id, message_id) DO UPDATE
            SET content = EXCLUDED.content,
                edited_at = NOW(),
                edit_count = chat_messages.edit_count + 1,
                original_content = COALESCE(
                    chat_messages.original_content, chat_messages.content
                )
            """,
            chat_id,
            message_id,
            user_id,
            username,
            first_name,
            message_type,
            content,
            None if raw_data is None else json.dumps(raw_data),
            reply_to_message_id,
            is_bot_message,
            sticker_file_id,
            sticker_file_unique_id,
            sticker_set_name,
            sticker_emoji,
            message_thread_id,
        )

    async def get_recent(
        self,
        chat_id: int,
        limit: int = 20,
        *,
        exclude_bot: bool = False,
    ) -> list[asyncpg.Record]:
        """Get recent messages for a chat, ordered by newest first."""
        query = """
            SELECT id, chat_id, message_id, user_id, username, first_name,
                   message_type, content, is_bot_message, created_at
            FROM chat_messages
            WHERE chat_id = $1
        """
        if exclude_bot:
            query += " AND is_bot_message = false"
        query += " ORDER BY created_at DESC LIMIT $2"
        result: list[asyncpg.Record] = await self._pool.fetch(query, chat_id, limit)
        return result

    async def get_recent_with_topic_context(
        self,
        chat_id: int,
        message_thread_id: int | None,
        *,
        current_topic_limit: int = 20,
        other_topics_limit: int = 10,
    ) -> list[asyncpg.Record]:
        """Get topic-weighted message context for AI.

        When message_thread_id is provided (forum mode):
        - Returns up to current_topic_limit messages from current topic
        - Plus up to other_topics_limit messages from other topics
        - Each row has 'topic_scope' column: 'current', 'other', or NULL

        When message_thread_id is None (non-forum):
        - Falls back to standard get_recent() behavior with NULL topic_scope
        """
        if message_thread_id is None:
            # Non-forum: standard query with NULL topic_scope
            result: list[asyncpg.Record] = await self._pool.fetch(
                """
                SELECT id, chat_id, message_id, user_id, username, first_name,
                       message_type, content, is_bot_message, created_at,
                       NULL::text AS topic_scope
                FROM chat_messages
                WHERE chat_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                chat_id,
                current_topic_limit + other_topics_limit,
            )
            return result

        # Forum mode: UNION ALL for topic-weighted context
        result = await self._pool.fetch(
            """
            (SELECT id, chat_id, message_id, user_id, username, first_name,
                    message_type, content, is_bot_message, created_at,
                    'current' AS topic_scope
               FROM chat_messages
              WHERE chat_id = $1 AND message_thread_id = $2
              ORDER BY created_at DESC LIMIT $3)
            UNION ALL
            (SELECT id, chat_id, message_id, user_id, username, first_name,
                    message_type, content, is_bot_message, created_at,
                    'other' AS topic_scope
               FROM chat_messages
              WHERE chat_id = $1 AND message_thread_id IS DISTINCT FROM $2
              ORDER BY created_at DESC LIMIT $4)
            """,
            chat_id,
            message_thread_id,
            current_topic_limit,
            other_topics_limit,
        )
        return result

    async def get_recent_lengths(
        self,
        chat_id: int,
        limit: int = 15,
        min_length: int = 5,
    ) -> list[int]:
        """Get lengths of recent non-bot messages for adaptive response length."""
        rows = await self._pool.fetch(
            """
            SELECT LENGTH(content) as msg_length
            FROM chat_messages
            WHERE chat_id = $1
              AND is_bot_message = false
              AND content IS NOT NULL
              AND LENGTH(content) >= $3
            ORDER BY created_at DESC
            LIMIT $2
            """,
            chat_id,
            limit,
            min_length,
        )
        return [row["msg_length"] for row in rows]

    async def get_bot_message_stats(
        self,
        chat_id: int,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get bot message statistics for the relevancy gate.

        Returns aggregated signals from the last `limit` messages in one query:
        total_count, bot_count, bot_ratio, seconds_since_last_bot,
        velocity_per_minute, consecutive_bot_at_end.
        """
        # Single query computes all engagement signals in one round-trip.
        # CTE 'recent' fetches last N messages; 'stats' aggregates counts;
        # 'consecutive' counts trailing bot messages at the end of history.
        row = await self._pool.fetchrow(
            """
            WITH recent AS (
                SELECT is_bot_message, created_at,
                       ROW_NUMBER() OVER (ORDER BY created_at DESC) AS rn
                FROM chat_messages
                WHERE chat_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            ),
            stats AS (
                SELECT
                    COUNT(*)::int AS total_count,
                    COUNT(*) FILTER (WHERE is_bot_message)::int AS bot_count,
                    MAX(created_at) FILTER (WHERE is_bot_message) AS last_bot_at,
                    COUNT(*) FILTER (
                        WHERE created_at > NOW() - INTERVAL '5 minutes'
                    )::int AS recent_5min_count
                FROM recent
            ),
            first_non_bot AS (
                SELECT MIN(rn) AS rn
                FROM recent
                WHERE NOT is_bot_message
            ),
            consecutive AS (
                SELECT COUNT(*)::int AS consecutive_bot
                FROM recent
                WHERE is_bot_message = true
                  AND rn < COALESCE((SELECT rn FROM first_non_bot), $2 + 1)
            )
            SELECT
                s.total_count,
                s.bot_count,
                CASE WHEN s.total_count > 0
                     THEN s.bot_count::float / s.total_count
                     ELSE 0.0 END AS bot_ratio,
                EXTRACT(EPOCH FROM (NOW() - s.last_bot_at)) AS seconds_since_last_bot,
                CASE WHEN s.recent_5min_count > 0
                     THEN s.recent_5min_count / 5.0
                     ELSE 0.0 END AS velocity_per_minute,
                c.consecutive_bot AS consecutive_bot_at_end
            FROM stats s, consecutive c
            """,
            chat_id,
            limit,
        )
        if row is None:
            return {
                "total_count": 0,
                "bot_count": 0,
                "bot_ratio": 0.0,
                "seconds_since_last_bot": None,
                "velocity_per_minute": 0.0,
                "consecutive_bot_at_end": 0,
            }
        return dict(row)

    async def find_by_username(self, chat_id: int, username: str) -> asyncpg.Record | None:
        """Find the most recent user_id seen posting under `username` in this chat.

        Case-insensitive (Telegram usernames are case-insensitive). Used to
        resolve a plain ``@username`` reply into a concrete user id when
        adding a KB organizer (B-1 stage 2) — the Bot API has no
        username-to-user lookup, so this chat-scoped message history is the
        only available index. Returns ``None`` if this chat's history has no
        record of that username (caller should then check
        ``username_seen_elsewhere`` to phrase the right not-found copy).
        """
        return await self._pool.fetchrow(
            """
            SELECT user_id, first_name
            FROM chat_messages
            WHERE chat_id = $1
              AND user_id IS NOT NULL
              AND username IS NOT NULL
              AND LOWER(username) = LOWER($2)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            chat_id,
            username,
        )

    async def username_seen_elsewhere(self, chat_id: int, username: str) -> bool:
        """Check whether `username` has posted in a chat other than `chat_id`.

        Lets the organizer-add flow distinguish "don't know this username at
        all" from "know them, but not in this chat" (B-1 stage 2).
        """
        row = await self._pool.fetchrow(
            """
            SELECT 1
            FROM chat_messages
            WHERE chat_id != $1
              AND user_id IS NOT NULL
              AND username IS NOT NULL
              AND LOWER(username) = LOWER($2)
            LIMIT 1
            """,
            chat_id,
            username,
        )
        return row is not None

    async def get_top_active_users(
        self, chat_id: int, page: int, per_page: int = 5
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated distinct posters in a chat, ranked by message count desc.

        Powers the KB organizer picker (B-2): lets an admin pick a candidate
        from chat participants instead of guessing a forward/@username.
        Excludes bot messages and rows with no `user_id` (e.g. service
        messages). `username`/`first_name` reflect that user's most recent
        message in this chat -- same chat-scoped-history constraint as
        `find_by_username` (no live Bot API lookup here, and no dedicated
        index beyond the existing `idx_chat_messages_user(chat_id, user_id,
        created_at DESC)`, which already supports this GROUP BY without a
        sort -- acceptable for a low-frequency admin action, same call as
        B-1 stage 2). `user_id ASC` is a stable tiebreaker so page contents
        don't reshuffle across requests when counts are equal. Returns
        (candidates, total_distinct_posters).
        """
        total = (
            await self._pool.fetchval(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM chat_messages
                WHERE chat_id = $1 AND user_id IS NOT NULL AND is_bot_message = false
                """,
                chat_id,
            )
            or 0
        )
        rows = await self._pool.fetch(
            """
            SELECT user_id,
                   (array_agg(username ORDER BY created_at DESC))[1] AS username,
                   (array_agg(first_name ORDER BY created_at DESC))[1] AS first_name,
                   COUNT(*)::int AS message_count
            FROM chat_messages
            WHERE chat_id = $1 AND user_id IS NOT NULL AND is_bot_message = false
            GROUP BY user_id
            ORDER BY message_count DESC, user_id ASC
            LIMIT $2 OFFSET $3
            """,
            chat_id,
            per_page,
            page * per_page,
        )
        return [dict(r) for r in rows], int(total)

    async def get_for_summary(
        self,
        chat_id: int,
        limit: int = 100,
        *,
        message_thread_id: int | None = None,
    ) -> list[asyncpg.Record]:
        """Get messages for summary generation.

        When message_thread_id is provided, filters to only that topic.
        When None, returns messages from entire chat.
        """
        if message_thread_id is not None:
            result: list[asyncpg.Record] = await self._pool.fetch(
                """
                SELECT user_id, username, first_name, content,
                       is_bot_message, created_at
                FROM chat_messages
                WHERE chat_id = $1
                  AND content IS NOT NULL
                  AND message_thread_id = $3
                ORDER BY created_at DESC
                LIMIT $2
                """,
                chat_id,
                limit,
                message_thread_id,
            )
        else:
            result = await self._pool.fetch(
                """
                SELECT user_id, username, first_name, content,
                       is_bot_message, created_at
                FROM chat_messages
                WHERE chat_id = $1 AND content IS NOT NULL
                ORDER BY created_at DESC
                LIMIT $2
                """,
                chat_id,
                limit,
            )
        return result
