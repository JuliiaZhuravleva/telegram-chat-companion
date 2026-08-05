"""Repository for periodic data retention.

Replaces the reference n8n bot's ``periodic_cleanup()`` SQL function
(internal/n8n-reference/data/data-lifecycle.md).  Without it the append-only
tables — ``chat_messages`` above all — grow without bound.
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# Tables this repository is allowed to prune, mapped to their age column.
# Interpolated into SQL below, so this must stay a hardcoded constant —
# never accept a caller-supplied table name (see the SQL-composition ADR).
RETENTION_TABLES: dict[str, str] = {
    "user_activity": "created_at",
    "chat_messages": "created_at",
    "response_log": "created_at",
    "unauthorized_attempts": "created_at",
    "abuse_blocked_log": "created_at",
    "message_reactions": "created_at",
    "decision_log": "created_at",
    "retrieval_log": "created_at",
}


class MaintenanceRepository:
    """Age-based deletion for the append-only tables."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def delete_older_than(self, table: str, older_than: timedelta) -> int:
        """Delete rows in `table` older than `older_than`. Returns deleted count.

        Raises ValueError for any table outside RETENTION_TABLES.
        """
        age_column = RETENTION_TABLES.get(table)
        if age_column is None:
            raise ValueError(f"Table not eligible for retention cleanup: {table}")

        # noqa: S608 — table/age_column come from RETENTION_TABLES, never from a caller.
        return (
            await self._pool.fetchval(  # noqa: S608
                f"""
                WITH deleted AS (
                    DELETE FROM {table}
                    WHERE {age_column} < NOW() - $1::interval
                    RETURNING 1
                )
                SELECT COUNT(*) FROM deleted
                """,
                older_than,  # asyncpg rejects string intervals — pass timedelta
            )
            or 0
        )
