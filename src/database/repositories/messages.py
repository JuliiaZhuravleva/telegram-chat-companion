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
            chat_id, message_id, user_id, username, first_name,
            message_type, content,
            None if raw_data is None else json.dumps(raw_data),
            reply_to_message_id, is_bot_message,
            sticker_file_id, sticker_file_unique_id,
            sticker_set_name, sticker_emoji,
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
            chat_id, limit, min_length,
        )
        return [row["msg_length"] for row in rows]

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
