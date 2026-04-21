"""Repository for the bot_config global key-value table."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


class BotConfigRepository:
    """Read/write access to global bot configuration."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, key: str) -> Any | None:
        """Get a config value by key. Returns parsed JSON or None."""
        row = await self._pool.fetchrow(
            "SELECT value FROM bot_config WHERE key = $1",
            key,
        )
        if row is None:
            return None
        return json.loads(row["value"])

    async def set(self, key: str, value: Any, description: str | None = None) -> None:
        """Set a config value (upsert)."""
        json_value = json.dumps(value)
        await self._pool.execute(
            """
            INSERT INTO bot_config (key, value, description)
            VALUES ($1, $2::jsonb, $3)
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    description = COALESCE(EXCLUDED.description, bot_config.description)
            """,
            key,
            json_value,
            description,
        )

    async def get_all(self) -> dict[str, Any]:
        """Get all config entries as {key: parsed_value}."""
        rows = await self._pool.fetch("SELECT key, value FROM bot_config")
        return {row["key"]: json.loads(row["value"]) for row in rows}

    async def get_defaults(self) -> dict[str, Any]:
        """Get all default_* entries, stripping the 'default_' prefix.

        Returns e.g. {"trigger_words": [...], "language": "ru", ...}
        """
        rows = await self._pool.fetch(
            "SELECT key, value FROM bot_config WHERE key LIKE 'default_%'"
        )
        prefix_len = len("default_")
        return {row["key"][prefix_len:]: json.loads(row["value"]) for row in rows}
