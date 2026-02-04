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
    ) -> None:
        """Save a chat message."""
        await self._pool.execute(
            """
            INSERT INTO chat_messages (
                chat_id, message_id, user_id, username, first_name,
                message_type, content, raw_data, reply_to_message_id,
                is_bot_message, sticker_file_id, sticker_file_unique_id,
                sticker_set_name, sticker_emoji
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13, $14)
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
    ) -> list[asyncpg.Record]:
        """Get messages for summary generation."""
        result: list[asyncpg.Record] = await self._pool.fetch(
            """
            SELECT user_id, username, first_name, content,
                   is_bot_message, created_at
            FROM chat_messages
            WHERE chat_id = $1 AND content IS NOT NULL
            ORDER BY created_at DESC
            LIMIT $2
            """,
            chat_id, limit,
        )
        return result
