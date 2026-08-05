"""
Integration tests: migration 015 (the six drifted chat_settings columns).

These six per-chat toggles lived in ``ChatConfig`` and were read by
``_CHAT_CONFIG_FIELDS`` for a long time, but no migration ever created them --
they only existed in hand-patched dev databases.  ``alembic upgrade head`` on a
fresh database therefore produced a schema without them, which silently
downgraded every per-chat override to the global layer and made
``ChatSettingsRepository.upsert(link_comments_enabled=...)`` raise.

The last class here is the important one: rather than pinning today's six
columns, it asserts the *general* invariant -- every field the code expects to
read from or write to ``chat_settings`` exists as a real column.  That is the
guard that turns "someone hand-patched the dev DB again" into a red test.
"""

from __future__ import annotations

import asyncpg
import pytest

from src.database.repositories.chat_settings import _WRITABLE_COLUMNS
from src.services.chat_config import _CHAT_CONFIG_FIELDS

# Columns introduced by migration 015.
_DRIFTED_COLUMNS: frozenset[str] = frozenset(
    {
        "sticker_reply_to_sticker_enabled",
        "sticker_reply_to_sticker_chance",
        "image_comment_sticker_enabled",
        "image_comment_sticker_chance",
        "link_comments_enabled",
        "relevancy_gate_enabled",
    }
)

# Fields that are not per-chat columns and so are exempt from the drift guard.
# chat_id is the primary key, supplied separately by every caller.
_NOT_COLUMNS: frozenset[str] = frozenset({"chat_id"})


async def _chat_settings_columns(conn: asyncpg.Connection) -> dict[str, asyncpg.Record]:
    rows = await conn.fetch(
        """
        SELECT column_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'chat_settings'
        """
    )
    return {r["column_name"]: r for r in rows}


class TestDriftedColumnsExist:
    @pytest.mark.asyncio
    async def test_all_six_columns_present(self, db_conn: asyncpg.Connection) -> None:
        columns = await _chat_settings_columns(db_conn)
        assert set(columns) >= _DRIFTED_COLUMNS

    @pytest.mark.asyncio
    async def test_columns_are_nullable_without_default(self, db_conn: asyncpg.Connection) -> None:
        """Same contract as kb_enabled in 014: the three-layer merge reads NULL as
        'not overridden', so a column DEFAULT would materialize a value on
        ensure_exists() and silently shadow its own bot_config.default_* layer."""
        columns = await _chat_settings_columns(db_conn)
        for name in sorted(_DRIFTED_COLUMNS):
            record = columns[name]
            assert record["is_nullable"] == "YES", f"{name} must be nullable"
            assert record["column_default"] is None, f"{name} must not carry a DEFAULT"

    @pytest.mark.asyncio
    async def test_fresh_row_leaves_them_null(self, db_conn: asyncpg.Connection) -> None:
        await db_conn.execute(
            "INSERT INTO chat_settings (chat_id) VALUES (-900015) ON CONFLICT DO NOTHING"
        )
        row = await db_conn.fetchrow(
            """
            SELECT sticker_reply_to_sticker_enabled, sticker_reply_to_sticker_chance,
                   image_comment_sticker_enabled, image_comment_sticker_chance,
                   link_comments_enabled, relevancy_gate_enabled
            FROM chat_settings WHERE chat_id = -900015
            """
        )
        assert row is not None
        assert all(value is None for value in row.values())

    @pytest.mark.asyncio
    async def test_values_round_trip(self, db_conn: asyncpg.Connection) -> None:
        """The n8n migration writes explicit per-chat values into these columns
        (8 of 9 live chats have link_comments_enabled = true)."""
        await db_conn.execute(
            """
            INSERT INTO chat_settings (chat_id, link_comments_enabled, image_comment_sticker_chance)
            VALUES (-900016, true, 0.42)
            ON CONFLICT (chat_id) DO UPDATE SET
                link_comments_enabled = EXCLUDED.link_comments_enabled,
                image_comment_sticker_chance = EXCLUDED.image_comment_sticker_chance
            """
        )
        row = await db_conn.fetchrow(
            """
            SELECT link_comments_enabled, image_comment_sticker_chance
            FROM chat_settings WHERE chat_id = -900016
            """
        )
        assert row is not None
        assert row["link_comments_enabled"] is True
        assert row["image_comment_sticker_chance"] == pytest.approx(0.42)


class TestNoSchemaDrift:
    """The general guard -- keep this passing and 015 can never happen again."""

    @pytest.mark.asyncio
    async def test_every_readable_field_is_a_real_column(self, db_conn: asyncpg.Connection) -> None:
        """_CHAT_CONFIG_FIELDS drives the per-chat layer of the merge; a name
        missing from the table degrades that override to the global layer with
        no error anywhere."""
        columns = set(await _chat_settings_columns(db_conn))
        expected = _CHAT_CONFIG_FIELDS - _NOT_COLUMNS
        assert expected <= columns, f"missing from chat_settings: {sorted(expected - columns)}"

    @pytest.mark.asyncio
    async def test_every_writable_column_is_a_real_column(
        self, db_conn: asyncpg.Connection
    ) -> None:
        """_WRITABLE_COLUMNS is the upsert allow-list; a name missing from the
        table passes validation and then fails in Postgres at runtime."""
        columns = set(await _chat_settings_columns(db_conn))
        expected = _WRITABLE_COLUMNS - _NOT_COLUMNS
        assert expected <= columns, f"missing from chat_settings: {sorted(expected - columns)}"

    @pytest.mark.asyncio
    async def test_every_writable_column_actually_accepts_a_write(
        self, db_conn: asyncpg.Connection
    ) -> None:
        """Stronger than the column-name check: proves each allow-listed name can
        really be written, which is what upsert() promises its callers.

        Self-assignment (``SET col = col``) exercises the column reference and
        its type without inventing a value -- writing NULL would trip the
        NOT NULL columns (enabled, kb_organizer_ids)."""
        await db_conn.execute(
            "INSERT INTO chat_settings (chat_id) VALUES (-900017) ON CONFLICT DO NOTHING"
        )
        for name in sorted(_WRITABLE_COLUMNS - _NOT_COLUMNS):
            await db_conn.execute(  # noqa: S608 — name comes from a frozenset constant
                f"UPDATE chat_settings SET {name} = {name} WHERE chat_id = -900017"
            )
