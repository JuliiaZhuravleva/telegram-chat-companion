"""
Integration tests: migration 026 (ADR-0009 Decision 3) schema shape.

``sticker_knowledge.explicitness_is_manual`` is deliberately the OPPOSITE
polarity from migration 024/025's ``explicitness_score`` /
``tolerance_level`` (see ``test_migration_024_025_tolerance_schema.py``):
those two are nullable with **no** SQL DEFAULT because they participate in
``ChatConfigService``'s three-layer merge, where a materialized default would
permanently shadow a higher layer (migration 020's own bug class). This
column has no merge semantics -- it is a flat, always-meaningful per-row
fact ("was this score hand-set or not") -- so **NOT NULL DEFAULT false** is
correct here and must NOT be "fixed" to match the nullable-no-default shape
of its sibling columns (ADR-0009 Decision 3; "Implementation notes for Q-1"
explicitly flags this deliberate difference).
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


class TestExplicitnessIsManualNotNullDefaultFalse:
    @pytest.mark.asyncio
    async def test_column_is_not_null_with_default_false(self, db_conn: asyncpg.Connection) -> None:
        record = await _column(db_conn, "sticker_knowledge", "explicitness_is_manual")
        assert record["is_nullable"] == "NO", (
            "explicitness_is_manual must be NOT NULL -- unlike "
            "explicitness_score/tolerance_level, it has no three-layer merge "
            "semantics to protect, and NULL would be a third, meaningless state "
            "for a flat per-row boolean fact"
        )
        assert (
            record["column_default"] is not None and "false" in record["column_default"].lower()
        ), (
            "explicitness_is_manual must carry a SQL DEFAULT of false -- every "
            "pre-existing row predates the manual-override feature and is "
            "unambiguously not hand-set"
        )

    @pytest.mark.asyncio
    async def test_row_inserted_without_specifying_it_reads_false_not_null(
        self, db_conn: asyncpg.Connection
    ) -> None:
        """The behaviour that actually matters: a bare INSERT (the schema-level
        equivalent of a pre-migration row that never named this column) must
        read ``false``, never ``NULL`` -- the opposite assertion from
        ``test_migration_024_025_tolerance_schema.py``'s nullable columns,
        which must read ``NULL`` in the same situation. Do not unify these two
        tests' expectations; the polarity difference is deliberate (Decision 3)."""
        await db_conn.execute(
            """
            INSERT INTO sticker_knowledge (file_unique_id, file_id)
            VALUES ('mig026-schema-001', 'f-mig026-schema-001')
            ON CONFLICT (file_unique_id) DO NOTHING
            """
        )
        row = await db_conn.fetchrow(
            "SELECT explicitness_is_manual FROM sticker_knowledge WHERE file_unique_id = 'mig026-schema-001'"
        )
        assert row is not None
        assert row["explicitness_is_manual"] is False

    @pytest.mark.asyncio
    async def test_explicit_manual_flag_still_storable(self, db_conn: asyncpg.Connection) -> None:
        """A NOT NULL DEFAULT must not stop a real value being written -- that
        is the entire point of the column (A-4's set_manual_explicitness_score)."""
        await db_conn.execute(
            """
            INSERT INTO sticker_knowledge (file_unique_id, file_id, explicitness_is_manual)
            VALUES ('mig026-schema-002', 'f-mig026-schema-002', true)
            ON CONFLICT (file_unique_id) DO UPDATE
            SET explicitness_is_manual = EXCLUDED.explicitness_is_manual
            """
        )
        row = await db_conn.fetchrow(
            "SELECT explicitness_is_manual FROM sticker_knowledge WHERE file_unique_id = 'mig026-schema-002'"
        )
        assert row is not None
        assert row["explicitness_is_manual"] is True
