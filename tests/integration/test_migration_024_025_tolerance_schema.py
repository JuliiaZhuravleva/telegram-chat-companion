"""
Integration tests: migrations 024/025 (ADR-0008 Decision 8) schema shape.

Both `sticker_knowledge.explicitness_score` and `chat_settings.tolerance_level`
must be nullable with **no SQL DEFAULT** -- migration 020's own repair
(`test_migration_020_rules_columns.py`) is the precedent for exactly this bug
class: a column added *with* a DEFAULT materializes a concrete value on every
row the moment ``ensure_exists()``/an INSERT touches it, which permanently
shadows that field's own global-layer default
(``bot_config.default_tolerance_level`` here) for every chat. ADR-0008
Implementation notes for D-4 name this test shape explicitly ("assert schema
directly ... so this specific, previously-real bug class can't silently
recur").
"""

from __future__ import annotations

import asyncpg
import pytest


async def _column(conn: asyncpg.Connection, table: str, column: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        """
        SELECT column_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = $1
          AND column_name = $2
        """,
        table,
        column,
    )
    assert row is not None, f"{table}.{column} is missing"
    return row


class TestExplicitnessAndToleranceColumnsNullableNoDefault:
    @pytest.mark.asyncio
    async def test_explicitness_score_nullable_without_default(
        self, db_conn: asyncpg.Connection
    ) -> None:
        record = await _column(db_conn, "sticker_knowledge", "explicitness_score")
        assert record["is_nullable"] == "YES"
        assert record["column_default"] is None, (
            "explicitness_score carries a DEFAULT -- every sticker_knowledge "
            "row would materialize a value and Decision 3's fail-closed NULL "
            "state (unscored) could never be observed"
        )

    @pytest.mark.asyncio
    async def test_tolerance_level_nullable_without_default(
        self, db_conn: asyncpg.Connection
    ) -> None:
        record = await _column(db_conn, "chat_settings", "tolerance_level")
        assert record["is_nullable"] == "YES"
        assert record["column_default"] is None, (
            "tolerance_level carries a DEFAULT -- every chat_settings row "
            "would materialize a value and permanently shadow "
            "bot_config.default_tolerance_level, the exact bug migration 020 "
            "already had to repair for rules_mode/rules_enabled"
        )

    @pytest.mark.asyncio
    async def test_fresh_chat_settings_row_leaves_tolerance_null(
        self, db_conn: asyncpg.Connection
    ) -> None:
        """The behavior that actually matters: a row created the way
        ensure_exists() creates one must defer to the global/dataclass
        layers, not silently pin 0.5 (or anything else) at row-creation
        time."""
        await db_conn.execute(
            "INSERT INTO chat_settings (chat_id) VALUES (-900024) ON CONFLICT DO NOTHING"
        )
        row = await db_conn.fetchrow(
            "SELECT tolerance_level FROM chat_settings WHERE chat_id = -900024"
        )
        assert row is not None
        assert row["tolerance_level"] is None

    @pytest.mark.asyncio
    async def test_explicit_tolerance_override_still_storable(
        self, db_conn: asyncpg.Connection
    ) -> None:
        """Dropping the DEFAULT must not stop an admin setting a real
        per-chat override via the FSM flow (D-3, Decision 10) -- that is the
        entire point of the column."""
        await db_conn.execute(
            """
            INSERT INTO chat_settings (chat_id, tolerance_level)
            VALUES (-900025, 1.0)
            ON CONFLICT (chat_id) DO UPDATE SET tolerance_level = EXCLUDED.tolerance_level
            """
        )
        row = await db_conn.fetchrow(
            "SELECT tolerance_level FROM chat_settings WHERE chat_id = -900025"
        )
        assert row is not None
        assert row["tolerance_level"] == pytest.approx(1.0)
