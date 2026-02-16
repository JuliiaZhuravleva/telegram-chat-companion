"""Repository for user_activity table."""

from __future__ import annotations

import asyncpg


class ActivityRepository:
    """Data access layer for user activity tracking."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def track(
        self,
        chat_id: int,
        user_id: int,
        *,
        username: str | None = None,
        first_name: str | None = None,
        activity_type: str = "message",
    ) -> None:
        """Record a user activity event."""
        await self._pool.execute(
            """
            INSERT INTO user_activity
                (chat_id, user_id, username, first_name, activity_type)
            VALUES ($1, $2, $3, $4, $5)
            """,
            chat_id, user_id, username, first_name, activity_type,
        )

    async def count_recent(
        self,
        chat_id: int,
        user_id: int,
        window_seconds: int = 60,
    ) -> int:
        """Count recent activity events within a time window."""
        row = await self._pool.fetchrow(
            """
            SELECT COUNT(*)::INTEGER as cnt
            FROM user_activity
            WHERE chat_id = $1 AND user_id = $2
              AND created_at > NOW() - make_interval(secs => $3)
            """,
            chat_id, user_id, float(window_seconds),
        )
        return int(row["cnt"]) if row else 0
