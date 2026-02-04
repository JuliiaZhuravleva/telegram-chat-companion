"""Repository for response_log table."""

from __future__ import annotations

import asyncpg


class ResponseLogRepository:
    """Data access layer for AI response logging."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def log(
        self,
        chat_id: int,
        *,
        user_id: int | None = None,
        message_id: int | None = None,
        trigger_type: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
        response_time_ms: int | None = None,
        was_fallback: bool = False,
    ) -> None:
        """Log an AI response event."""
        await self._pool.execute(
            """
            INSERT INTO response_log (
                chat_id, user_id, message_id, trigger_type,
                provider, model, tokens_input, tokens_output,
                response_time_ms, was_fallback
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            chat_id, user_id, message_id, trigger_type,
            provider, model, tokens_input, tokens_output,
            response_time_ms, was_fallback,
        )
