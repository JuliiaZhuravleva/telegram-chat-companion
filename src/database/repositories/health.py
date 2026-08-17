"""Repository for the health_log table."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import asyncpg


class HealthRepository:
    """Data access for health monitoring."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert_log(
        self,
        *,
        status: str,
        db_ok: bool,
        messages_30m: int,
        fallbacks_15m: int,
        ai_provider: str | None,
        issues: list[dict[str, str]],
        alert_sent: bool,
    ) -> int:
        """Insert a health check result. Returns the log ID."""
        row = await self._pool.fetchrow(
            """
            INSERT INTO health_log
                (status, db_ok, messages_30m, fallbacks_15m,
                 ai_provider, issues, alert_sent)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            RETURNING id
            """,
            status,
            db_ok,
            messages_30m,
            fallbacks_15m,
            ai_provider,
            json.dumps(issues),
            alert_sent,
        )
        return int(row["id"])

    async def get_latest(self) -> dict[str, Any] | None:
        """Get the most recent health check result."""
        row = await self._pool.fetchrow(
            "SELECT * FROM health_log ORDER BY checked_at DESC LIMIT 1",
        )
        return dict(row) if row else None

    async def get_last_alert_time(self) -> float | None:
        """Get timestamp of last alert sent (epoch seconds), or None."""
        row = await self._pool.fetchrow(
            """
            SELECT EXTRACT(EPOCH FROM checked_at) AS ts
            FROM health_log
            WHERE alert_sent = true
            ORDER BY checked_at DESC
            LIMIT 1
            """,
        )
        return float(row["ts"]) if row else None

    async def get_fallback_count(self, interval: timedelta) -> int:
        """Count fallback responses within an interval."""
        return (
            await self._pool.fetchval(
                """
                SELECT COUNT(*) FROM response_log
                WHERE was_fallback = true
                  AND created_at > NOW() - $1::interval
                """,
                interval,
            )
            or 0
        )

    async def get_message_count_30m(self) -> int:
        """Count messages in the last 30 minutes."""
        return (
            await self._pool.fetchval(
                """
                SELECT COUNT(*) FROM chat_messages
                WHERE created_at > NOW() - interval '30 minutes'
                  AND message_type <> 'transcription'  -- migration 028
                """,
            )
            or 0
        )

    async def cleanup_old_logs(self, keep_days: int = 30) -> int:
        """Delete health logs older than keep_days. Returns deleted count."""
        return (
            await self._pool.fetchval(
                """
                WITH deleted AS (
                    DELETE FROM health_log
                    WHERE checked_at < NOW() - $1::interval
                    RETURNING 1
                )
                SELECT COUNT(*) FROM deleted
                """,
                timedelta(days=keep_days),
            )
            or 0
        )
