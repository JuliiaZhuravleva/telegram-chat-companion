"""Repository for response_log table."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

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
        task_type: str = "text",
        cost_usd: Decimal | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """Log an AI response event."""
        await self._pool.execute(
            """
            INSERT INTO response_log (
                chat_id, user_id, message_id, trigger_type,
                provider, model, tokens_input, tokens_output,
                response_time_ms, was_fallback,
                task_type, cost_usd, duration_seconds
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            chat_id,
            user_id,
            message_id,
            trigger_type,
            provider,
            model,
            tokens_input,
            tokens_output,
            response_time_ms,
            was_fallback,
            task_type,
            cost_usd,
            duration_seconds,
        )

    # -- Cost aggregation queries --

    async def get_total_cost(self, interval: timedelta) -> Decimal:
        """Get total cost within an interval."""
        result = await self._pool.fetchval(
            """
            SELECT COALESCE(SUM(cost_usd), 0) FROM response_log
            WHERE created_at > NOW() - $1::interval
            """,
            interval,
        )
        return Decimal(str(result))

    async def get_cost_by_model(self, interval: timedelta) -> list[dict[str, Any]]:
        """Get cost breakdown by model within an interval."""
        rows = await self._pool.fetch(
            """
            SELECT provider, model, task_type,
                   COUNT(*) as call_count,
                   COALESCE(SUM(cost_usd), 0) as total_cost,
                   COALESCE(SUM(tokens_input), 0) as total_tokens_in,
                   COALESCE(SUM(tokens_output), 0) as total_tokens_out
            FROM response_log
            WHERE created_at > NOW() - $1::interval
            GROUP BY provider, model, task_type
            ORDER BY total_cost DESC
            """,
            interval,
        )
        return [dict(r) for r in rows]

    async def get_cost_by_task_type(self, interval: timedelta) -> list[dict[str, Any]]:
        """Get cost breakdown by task type within an interval."""
        rows = await self._pool.fetch(
            """
            SELECT task_type,
                   COUNT(*) as call_count,
                   COALESCE(SUM(cost_usd), 0) as total_cost
            FROM response_log
            WHERE created_at > NOW() - $1::interval
            GROUP BY task_type
            ORDER BY total_cost DESC
            """,
            interval,
        )
        return [dict(r) for r in rows]

    async def get_cost_by_provider(self, interval: timedelta) -> list[dict[str, Any]]:
        """Get cost breakdown by provider within an interval."""
        rows = await self._pool.fetch(
            """
            SELECT provider,
                   COUNT(*) as call_count,
                   COALESCE(SUM(cost_usd), 0) as total_cost
            FROM response_log
            WHERE created_at > NOW() - $1::interval
            GROUP BY provider
            ORDER BY total_cost DESC
            """,
            interval,
        )
        return [dict(r) for r in rows]
