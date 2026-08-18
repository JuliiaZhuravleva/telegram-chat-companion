"""Repository for the chat_settings per-chat configuration table."""

from __future__ import annotations

from typing import Any

import asyncpg

# Columns that can be written via upsert / set_field.
# Maps Python field names to SQL column names (they happen to match).
_WRITABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "chat_title",
        "chat_type",
        "enabled",
        "trigger_words",
        "random_response_chance",
        "random_response_min_interval",
        "system_prompt",
        "language",
        "rag_enabled",
        "transcribe_voice",
        "transcribe_video_notes",
        "abuse_filter_enabled",
        "sticker_learning_enabled",
        "sticker_response_chance",
        "image_analysis_enabled",
        "save_messages",
        "rules_enabled",
        "rules_mode",
        "sticker_reply_to_sticker_enabled",
        "sticker_reply_to_sticker_chance",
        "image_comment_sticker_enabled",
        "image_comment_sticker_chance",
        "tolerance_level",
        "link_comments_enabled",
        "relevancy_gate_enabled",
        "kb_enabled",
        "kb_organizer_ids",
        "last_activity_at",
        "reactions_enabled",
        "reactions_history_enabled",
    }
)


class ChatSettingsRepository:
    """Read/write access to per-chat settings."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, chat_id: int) -> dict[str, Any] | None:
        """Get settings for a chat. Returns column dict or None."""
        row = await self._pool.fetchrow(
            "SELECT * FROM chat_settings WHERE chat_id = $1",
            chat_id,
        )
        if row is None:
            return None
        return dict(row)

    async def upsert(self, chat_id: int, **fields: Any) -> None:
        """Insert or update chat settings.

        Only columns in _WRITABLE_COLUMNS are accepted; unknown keys raise ValueError.
        """
        bad_keys = set(fields) - _WRITABLE_COLUMNS
        if bad_keys:
            raise ValueError(f"Unknown chat_settings columns: {bad_keys}")

        if not fields:
            # Just ensure the row exists with defaults
            await self._pool.execute(
                """
                INSERT INTO chat_settings (chat_id)
                VALUES ($1)
                ON CONFLICT (chat_id) DO NOTHING
                """,
                chat_id,
            )
            return

        columns = list(fields.keys())
        values = list(fields.values())

        # Build SET clause: "col1 = $2, col2 = $3, ..."
        set_clause = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(columns))
        # Build INSERT columns and value placeholders
        insert_cols = "chat_id, " + ", ".join(columns)
        insert_vals = "$1, " + ", ".join(f"${i + 2}" for i in range(len(columns)))

        await self._pool.execute(
            f"""
            INSERT INTO chat_settings ({insert_cols})
            VALUES ({insert_vals})
            ON CONFLICT (chat_id) DO UPDATE SET {set_clause}
            """,
            chat_id,
            *values,
        )

    async def set_field(self, chat_id: int, field: str, value: Any) -> None:
        """Update a single field for a chat (row must exist)."""
        if field not in _WRITABLE_COLUMNS:
            raise ValueError(f"Unknown chat_settings column: {field}")

        await self._pool.execute(
            f"UPDATE chat_settings SET {field} = $2 WHERE chat_id = $1",  # noqa: S608
            chat_id,
            value,
        )

    async def toggle_bool_field(self, chat_id: int, field: str, effective: bool) -> bool | None:
        """Flip a bool column atomically, returning the value now stored.

        ``None`` means no row matched — the caller must not report success.

        Read-modify-write in Python loses a flip when an admin double-taps a
        toggle: aiogram runs the two updates concurrently, both read the same
        value across their awaits, and both write the same negation, so two
        taps produce one flip. Negating inside the UPDATE removes the window.

        ``effective`` supplies the merged value (YAML/global layers) used only
        when the column is NULL, i.e. "inherited": flipping must start from
        what the chat actually behaves like, not from SQL NULL. When the column
        is non-NULL, COALESCE keeps the stored value and ``effective`` equals
        it anyway, so the negation is the same either way.
        """
        if field not in _WRITABLE_COLUMNS:
            raise ValueError(f"Unknown chat_settings column: {field}")

        stored = await self._pool.fetchval(
            f"UPDATE chat_settings SET {field} = NOT COALESCE({field}, $2) "  # noqa: S608
            f"WHERE chat_id = $1 RETURNING {field}",
            chat_id,
            effective,
        )
        return None if stored is None else bool(stored)

    async def ensure_exists(
        self,
        chat_id: int,
        chat_title: str | None = None,
        chat_type: str = "group",
    ) -> dict[str, Any]:
        """Ensure a chat_settings row exists, return it."""
        await self._pool.execute(
            """
            INSERT INTO chat_settings (chat_id, chat_title, chat_type)
            VALUES ($1, $2, $3)
            ON CONFLICT (chat_id) DO UPDATE
                SET chat_title = COALESCE(EXCLUDED.chat_title, chat_settings.chat_title),
                    chat_type = COALESCE(EXCLUDED.chat_type, chat_settings.chat_type)
            """,
            chat_id,
            chat_title,
            chat_type,
        )
        row = await self.get(chat_id)
        assert row is not None  # just inserted
        return row

    async def list_enabled(self) -> list[int]:
        """Get all enabled (whitelisted) chat IDs."""
        rows = await self._pool.fetch("SELECT chat_id FROM chat_settings WHERE enabled = true")
        return [row["chat_id"] for row in rows]

    async def list_all(self) -> list[asyncpg.Record]:
        """Every chat the bot has a settings row for, with its title.

        Deliberately not `list_enabled()`: the chunk indexer's gate is
        `save_messages`, not the whitelist. A chat that was disabled after
        years of saved history still owns that history, and freezing its index
        at the moment of a whitelist change would be a silent, unrecoverable
        edit to the bot's memory.
        """
        rows: list[asyncpg.Record] = await self._pool.fetch(
            "SELECT chat_id, chat_title FROM chat_settings ORDER BY chat_id"
        )
        return rows
