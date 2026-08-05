"""
Integration tests: migration 020 (rules_mode / rules_enabled DEFAULT removal).

Migration 008 added both columns WITH a DEFAULT. The three-layer merge reads
NULL in ``chat_settings`` as "not overridden", so a DEFAULT materializes a value
on every ``ensure_exists()`` and permanently shadows the column's own
``bot_config.default_*`` global layer.

Migration 015 repaired six columns with exactly this problem; these two were
never in its list. Measured on the dev database before 020: all 9 chat_settings
rows carried both values, against 1 of 9 for the correctly-nullable
``kb_enabled`` -- i.e. ``default_rules_mode`` and ``default_rules_enabled`` could
not affect any chat.
"""

from __future__ import annotations

import asyncpg
import pytest

_RULES_COLUMNS = ("rules_mode", "rules_enabled")


async def _column(conn: asyncpg.Connection, name: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        """
        SELECT column_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'chat_settings'
          AND column_name = $1
        """,
        name,
    )
    assert row is not None, f"{name} is missing from chat_settings"
    return row


class TestRulesColumnsNoLongerShadowGlobals:
    @pytest.mark.asyncio
    async def test_columns_are_nullable_without_default(self, db_conn: asyncpg.Connection) -> None:
        for name in _RULES_COLUMNS:
            record = await _column(db_conn, name)
            assert record["is_nullable"] == "YES", f"{name} must be nullable"
            assert record["column_default"] is None, (
                f"{name} still carries a DEFAULT, so every new chat_settings row "
                f"materializes a value and shadows bot_config.default_{name}"
            )

    @pytest.mark.asyncio
    async def test_fresh_row_leaves_them_null(self, db_conn: asyncpg.Connection) -> None:
        """The behaviour that actually matters: a row created the way
        ensure_exists() creates one must defer to the global layer."""
        await db_conn.execute(
            "INSERT INTO chat_settings (chat_id) VALUES (-900020) ON CONFLICT DO NOTHING"
        )
        row = await db_conn.fetchrow(
            "SELECT rules_mode, rules_enabled FROM chat_settings WHERE chat_id = -900020"
        )

        assert row is not None
        assert row["rules_mode"] is None
        assert row["rules_enabled"] is None

    @pytest.mark.asyncio
    async def test_explicit_override_still_storable(self, db_conn: asyncpg.Connection) -> None:
        """Dropping the DEFAULT must not stop an admin setting a real per-chat
        override -- that is the whole point of the column."""
        await db_conn.execute(
            """
            INSERT INTO chat_settings (chat_id, rules_mode, rules_enabled)
            VALUES (-900021, 'highest_weight', true)
            ON CONFLICT (chat_id) DO UPDATE
            SET rules_mode = EXCLUDED.rules_mode, rules_enabled = EXCLUDED.rules_enabled
            """
        )
        row = await db_conn.fetchrow(
            "SELECT rules_mode, rules_enabled FROM chat_settings WHERE chat_id = -900021"
        )

        assert row is not None
        assert row["rules_mode"] == "highest_weight"
        assert row["rules_enabled"] is True
