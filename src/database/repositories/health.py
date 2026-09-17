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

    async def get_last_alert(self) -> dict[str, Any] | None:
        """The most recent health check that actually sent an alert.

        Read instead of keeping the previous alert in memory so that
        de-duplication survives a restart -- and the bot restarts on every
        deploy. In-process state would re-alert on each release and, worse,
        lose the "this cleared" transition across one.
        """
        row = await self._pool.fetchrow(
            """
            SELECT EXTRACT(EPOCH FROM checked_at) AS ts, status, issues
            FROM health_log
            WHERE alert_sent = true
            ORDER BY checked_at DESC
            LIMIT 1
            """,
        )
        if row is None:
            return None
        raw_issues = row["issues"]
        # asyncpg hands back jsonb as text unless a codec is registered, and
        # this repository registers none. Parsed here rather than at the call
        # site so a caller cannot mistake the string for a list and get a
        # fingerprint of characters.
        if isinstance(raw_issues, str):
            try:
                issues = json.loads(raw_issues)
            except json.JSONDecodeError:
                issues = []
        else:
            issues = raw_issues or []
        return {
            "ts": float(row["ts"]),
            "status": row["status"],
            "issues": issues if isinstance(issues, list) else [],
        }

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

    async def get_ai_failure_counts(self, interval: timedelta) -> dict[str, int]:
        """Failed AI calls per task type within an interval (migration 031).

        Counted per task so the alert can name what broke: "transcription"
        failing while text generation is fine is a different incident from
        the whole provider being down, and the five-day video-note outage
        (ba8ce2c) was exactly the first kind.
        """
        rows = await self._pool.fetch(
            """
            SELECT task_type, COUNT(*) AS n
            FROM ai_failure_log
            WHERE created_at > NOW() - $1::interval
            GROUP BY task_type
            ORDER BY n DESC
            """,
            interval,
        )
        return {row["task_type"]: int(row["n"]) for row in rows}

    async def get_ai_failure_details(self, interval: timedelta) -> list[dict[str, Any]]:
        """Per task: how many failures, since when, and the provider's own words.

        The count alone was what the alert used to carry, and it is the least
        useful part of the row: "embeddings x47" is identical on the first
        minute of an outage and on its second day. `since` and the last
        `error_message` are what distinguish those, and the message is where a
        billing URL lives.

        `DISTINCT ON` takes the most recent error per task -- the oldest one is
        a stale diagnosis of a situation that may have changed underneath it.

        **`since` means "no successful call of this task since then".** The
        first version defined the streak by a gap between FAILURES (more than
        an hour apart started a new one), which is wrong for any task whose
        requests are sparse: a review measured `transcription` broken for 2
        days 22 hours reporting `holding since 0m`, because voice notes arrive
        further apart than the gap. Asking the success side instead is exact
        for both dense and sparse tasks.

        The two tables disagree on task names -- `response_log` stores
        `embedding`, `ai_failure_log` stores `embeddings`; success is `text`,
        failure is `text_generation` -- so the join goes through an explicit
        mapping. That discrepancy already produced one false conclusion during
        this incident ("zero successes in 30 hours", from querying the plural
        name against the success table), which is why it is spelled out here
        rather than guessed at the call site.
        """
        rows = await self._pool.fetch(
            """
            WITH names(failure_task, success_task) AS (
                VALUES ('embeddings', 'embedding'),
                       ('text_generation', 'text'),
                       ('vision', 'vision'),
                       ('transcription', 'transcription')
            ),
            recent AS (
                SELECT task_type, provider, error_type, error_message, created_at
                FROM ai_failure_log
                WHERE created_at > NOW() - $1::interval
            ),
            counts AS (
                SELECT task_type, COUNT(*) AS n FROM recent GROUP BY task_type
            ),
            last_err AS (
                SELECT DISTINCT ON (task_type)
                       task_type, provider, error_type, error_message
                FROM recent
                ORDER BY task_type, created_at DESC
            ),
            last_ok AS (
                SELECT c.task_type,
                       (SELECT MAX(r.created_at)
                          FROM response_log r
                          JOIN names n ON n.success_task = r.task_type
                         WHERE n.failure_task = c.task_type) AS ok_at
                FROM counts c
            ),
            streak AS (
                SELECT c.task_type,
                       (SELECT MIN(f.created_at)
                          FROM ai_failure_log f
                         WHERE f.task_type = c.task_type
                           AND (o.ok_at IS NULL OR f.created_at > o.ok_at)) AS since
                FROM counts c
                JOIN last_ok o USING (task_type)
            )
            SELECT c.task_type, c.n, l.provider, l.error_type, l.error_message,
                   s.since
            FROM counts c
            JOIN last_err l USING (task_type)
            LEFT JOIN streak s USING (task_type)
            ORDER BY c.n DESC
            """,
            interval,
        )
        return [
            {
                "task_type": row["task_type"],
                "provider": row["provider"],
                "error_type": row["error_type"],
                "error_message": row["error_message"],
                "count": int(row["n"]),
                "since": row["since"],
            }
            for row in rows
        ]

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
