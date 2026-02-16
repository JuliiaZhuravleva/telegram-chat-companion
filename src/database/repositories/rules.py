"""Repository for the custom_rules table."""

from __future__ import annotations

import json
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# Columns that can be updated via update().
_UPDATABLE_COLUMNS: frozenset[str] = frozenset({
    "rule_type",
    "config",
    "weight",
    "mandatory",
    "enabled",
    "status",
})


class RulesRepository:
    """Data access layer for custom rules."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- Query active rules --

    async def get_active_rules(self, chat_id: int) -> list[dict[str, Any]]:
        """Get all active, enabled rules for a chat."""
        rows = await self._pool.fetch(
            """
            SELECT id, chat_id, rule_type, config, weight, mandatory,
                   enabled, status, trigger_count, last_triggered_at
            FROM custom_rules
            WHERE chat_id = $1
              AND enabled = true
              AND (status = 'active' OR status IS NULL)
            ORDER BY weight DESC, id ASC
            """,
            chat_id,
        )
        return [dict(r) for r in rows]

    # -- CRUD --

    async def create(
        self,
        *,
        chat_id: int,
        rule_type: str,
        config: dict[str, Any],
        weight: int = 1,
        mandatory: bool = False,
    ) -> int:
        """Create a new rule. Returns the rule ID."""
        row = await self._pool.fetchrow(
            """
            INSERT INTO custom_rules (chat_id, rule_type, config, weight, mandatory)
            VALUES ($1, $2, $3::jsonb, $4, $5)
            RETURNING id
            """,
            chat_id,
            rule_type,
            json.dumps(config),
            weight,
            mandatory,
        )
        assert row is not None
        return int(row["id"])

    async def get(self, rule_id: int) -> dict[str, Any] | None:
        """Get a single rule by ID."""
        row = await self._pool.fetchrow(
            "SELECT * FROM custom_rules WHERE id = $1",
            rule_id,
        )
        return dict(row) if row else None

    async def update(self, rule_id: int, **fields: Any) -> dict[str, Any] | None:
        """Update rule fields. Returns updated rule or None."""
        bad = set(fields) - _UPDATABLE_COLUMNS
        if bad:
            raise ValueError(f"Unknown custom_rules columns: {bad}")

        if not fields:
            return await self.get(rule_id)

        if "config" in fields and isinstance(fields["config"], dict):
            fields["config"] = json.dumps(fields["config"])

        columns = list(fields.keys())
        values = list(fields.values())
        set_clause = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(columns))

        row = await self._pool.fetchrow(
            f"UPDATE custom_rules SET {set_clause} WHERE id = $1 RETURNING *",  # noqa: S608
            rule_id,
            *values,
        )
        return dict(row) if row else None

    async def delete(self, rule_id: int) -> bool:
        """Delete a rule. Returns True if deleted."""
        result = await self._pool.execute(
            "DELETE FROM custom_rules WHERE id = $1",
            rule_id,
        )
        return bool(result == "DELETE 1")

    async def toggle(self, rule_id: int, *, enabled: bool) -> dict[str, Any] | None:
        """Enable/disable a rule."""
        return await self.update(rule_id, enabled=enabled)

    # -- Trigger tracking --

    async def record_trigger(self, rule_id: int) -> None:
        """Increment trigger_count and update last_triggered_at."""
        await self._pool.execute(
            """
            UPDATE custom_rules
            SET trigger_count = trigger_count + 1,
                last_triggered_at = NOW()
            WHERE id = $1
            """,
            rule_id,
        )

    # -- Stateful queries for sticker_flood and spam_detect --

    async def count_stickers_in_interval(
        self, chat_id: int, interval_seconds: int
    ) -> int:
        """Count sticker messages in a chat within the last N seconds."""
        return (
            await self._pool.fetchval(
                """
                SELECT COUNT(*) FROM chat_messages
                WHERE chat_id = $1
                  AND message_type = 'sticker'
                  AND created_at > NOW() - make_interval(secs => $2)
                """,
                chat_id,
                float(interval_seconds),
            )
            or 0
        )

    async def count_user_messages_in_interval(
        self, chat_id: int, user_id: int, interval_seconds: int
    ) -> tuple[int, int]:
        """Count messages and max length from a user in a chat within N seconds.

        Returns (message_count, max_message_length).
        """
        row = await self._pool.fetchrow(
            """
            SELECT COUNT(*) AS cnt,
                   COALESCE(MAX(LENGTH(content)), 0) AS max_len
            FROM chat_messages
            WHERE chat_id = $1
              AND user_id = $2
              AND created_at > NOW() - make_interval(secs => $3)
            """,
            chat_id,
            user_id,
            float(interval_seconds),
        )
        if row is None:
            return 0, 0
        return int(row["cnt"]), int(row["max_len"])

    # -- Paginated list for admin UI --

    async def get_rules_page(
        self, chat_id: int, page: int, per_page: int = 5
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated rules for a chat. Returns (rules, total_count)."""
        total = (
            await self._pool.fetchval(
                "SELECT COUNT(*) FROM custom_rules WHERE chat_id = $1",
                chat_id,
            )
            or 0
        )
        rows = await self._pool.fetch(
            """
            SELECT * FROM custom_rules
            WHERE chat_id = $1
            ORDER BY mandatory DESC, weight DESC, id ASC
            LIMIT $2 OFFSET $3
            """,
            chat_id,
            per_page,
            page * per_page,
        )
        return [dict(r) for r in rows], int(total)
