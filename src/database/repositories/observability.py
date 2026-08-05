"""Repository for decision_log / retrieval_log (migration 022).

Durable observability for the two runtime decisions that used to vanish:
whether the bot chose silence (and which mechanism chose it), and what
retrieval returned (and what of it actually reached the prompt).

Writers sit on the message hot path, so every caller wraps these in a
``_safe_*`` / fire-and-forget pattern — this repository itself stays a plain
data-access layer and lets exceptions propagate to that wrapper.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg


class ObservabilityRepository:
    """Data access layer for decision_log and retrieval_log."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def log_decision(
        self,
        chat_id: int,
        *,
        stage: str,
        decision: str,
        tier: str | None = None,
        reason: str | None = None,
        message_id: int | None = None,
        user_id: int | None = None,
    ) -> None:
        """Record one respond/silence decision."""
        await self._pool.execute(
            """
            INSERT INTO decision_log
                (chat_id, message_id, user_id, stage, decision, tier, reason)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            chat_id,
            message_id,
            user_id,
            stage,
            decision,
            tier,
            reason,
        )

    async def log_retrieval(
        self,
        chat_id: int,
        *,
        source: str,
        query_text: str | None = None,
        params: dict[str, Any] | None = None,
        results: list[dict[str, Any]] | None = None,
        n_results: int = 0,
        n_injected: int = 0,
        duration_ms: int | None = None,
        message_id: int | None = None,
        error: str | None = None,
    ) -> None:
        """Record one retrieval pass (one row per source per pipeline turn)."""
        await self._pool.execute(
            """
            INSERT INTO retrieval_log
                (chat_id, message_id, source, query_text,
                 params, results, n_results, n_injected, duration_ms, error)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10)
            """,
            chat_id,
            message_id,
            source,
            query_text,
            None if params is None else json.dumps(params),
            None if results is None else json.dumps(results),
            n_results,
            n_injected,
            duration_ms,
            error,
        )
