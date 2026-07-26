"""
Integration tests: migration 014 (chat_facts + chat_settings.kb_* columns) against
real Postgres+pgvector.

A1's unit test (``tests/unit/test_migration_014_chat_facts.py``) monkeypatches
``op.execute`` and only checks the *rendered SQL* (chain integrity, idempotency
guards) -- it never actually runs the DDL against a database. This file is the
A6-owned complement: the session-scoped ``run_migrations`` fixture in
``tests/integration/conftest.py`` applies every migration (including 014) against
a real pgvector container once per test session, so these tests verify the DDL
*actually lands correctly*: the table/columns/defaults/indexes/trigger/FK exist
and behave as ADR-0003 specifies.
"""

from __future__ import annotations

import json

import asyncpg
import pytest

# ---------------------------------------------------------------------------
# chat_facts table shape
# ---------------------------------------------------------------------------


class TestChatFactsTableShape:
    @pytest.mark.asyncio
    async def test_table_exists(self, db_conn: asyncpg.Connection) -> None:
        exists = await db_conn.fetchval("SELECT to_regclass('public.chat_facts') IS NOT NULL")
        assert exists is True

    @pytest.mark.asyncio
    async def test_expected_columns_present(self, db_conn: asyncpg.Connection) -> None:
        rows = await db_conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'chat_facts'
            """
        )
        columns = {r["column_name"] for r in rows}
        expected = {
            "id",
            "chat_id",
            "topic",
            "subject",
            "predicate",
            "value",
            "fact_text",
            "embedding",
            "status",
            "valid_from",
            "valid_to",
            "superseded_by",
            "source",
            "source_message_id",
            "source_user_id",
            "authority_level",
            "confidence",
            "salience",
            "created_at",
            "updated_at",
        }
        assert expected <= columns

    @pytest.mark.asyncio
    async def test_default_status_is_pending(self, db_conn: asyncpg.Connection) -> None:
        """DDL default is 'pending' -- callers (KnowledgeRepository.upsert_fact) always
        pass status='active' explicitly, but a bare INSERT without a status column
        must still land as 'pending' per ADR-0003's lifecycle contract."""
        row = await db_conn.fetchrow(
            """
            INSERT INTO chat_facts (chat_id, subject, predicate, value, fact_text, source)
            VALUES (-900001, 'test-subject', 'test-predicate', 'v', 'fact text', 'manual')
            RETURNING status, valid_from, valid_to, salience
            """
        )
        assert row is not None
        assert row["status"] == "pending"
        assert row["valid_from"] is not None
        assert row["valid_to"] is None
        assert row["salience"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_superseded_by_self_references_chat_facts(
        self, db_conn: asyncpg.Connection
    ) -> None:
        first = await db_conn.fetchval(
            """
            INSERT INTO chat_facts (chat_id, subject, predicate, value, fact_text, source)
            VALUES (-900002, 's', 'p', 'v1', 'fact 1', 'manual')
            RETURNING id
            """
        )
        # NB different subject: the UNIQUE partial index forbids a second
        # active row at the same (chat_id, subject, predicate) key.
        second = await db_conn.fetchval(
            """
            INSERT INTO chat_facts (chat_id, subject, predicate, value, fact_text, source, superseded_by)
            VALUES (-900002, 's2', 'p', 'v2', 'fact 2', 'manual', NULL)
            RETURNING id
            """
        )
        # superseded_by is a self-FK; pointing it at a real chat_facts.id must succeed.
        await db_conn.execute(
            "UPDATE chat_facts SET superseded_by = $1 WHERE id = $2", second, first
        )
        row = await db_conn.fetchrow("SELECT superseded_by FROM chat_facts WHERE id = $1", first)
        assert row is not None
        assert row["superseded_by"] == second

    @pytest.mark.asyncio
    async def test_unique_partial_index_rejects_second_active_row_same_key(
        self, db_conn: asyncpg.Connection
    ) -> None:
        """DB-level backstop for ADR-0003's 'one active row per key' (review fix)."""
        await db_conn.execute(
            """
            INSERT INTO chat_facts (chat_id, subject, predicate, value, fact_text, source)
            VALUES (-900012, 'uniq', 'p', 'v1', 'fact 1', 'manual')
            """
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            # Savepoint so the violation doesn't poison the test connection.
            async with db_conn.transaction():
                await db_conn.execute(
                    """
                    INSERT INTO chat_facts (chat_id, subject, predicate, value, fact_text, source)
                    VALUES (-900012, 'uniq', 'p', 'v2', 'fact 2', 'manual')
                    """
                )
        # A superseded (closed) row at the same key is fine.
        await db_conn.execute(
            "UPDATE chat_facts SET valid_to = NOW() WHERE chat_id = -900012 AND subject = 'uniq'"
        )
        await db_conn.execute(
            """
            INSERT INTO chat_facts (chat_id, subject, predicate, value, fact_text, source)
            VALUES (-900012, 'uniq', 'p', 'v3', 'fact 3', 'manual')
            """
        )

    @pytest.mark.asyncio
    async def test_superseded_by_rejects_nonexistent_id(self, db_conn: asyncpg.Connection) -> None:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await db_conn.execute(
                """
                INSERT INTO chat_facts (
                    chat_id, subject, predicate, value, fact_text, source, superseded_by
                )
                VALUES (-900003, 's', 'p', 'v', 'fact', 'manual', 999999999)
                """
            )


# ---------------------------------------------------------------------------
# Indexes + trigger
# ---------------------------------------------------------------------------


class TestChatFactsIndexesAndTrigger:
    @pytest.mark.asyncio
    async def test_expected_indexes_exist(self, db_conn: asyncpg.Connection) -> None:
        rows = await db_conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'chat_facts'"
        )
        names = {r["indexname"] for r in rows}
        assert "idx_chat_facts_status" in names
        assert "idx_chat_facts_active_key" in names
        assert "idx_chat_facts_embedding" in names

    @pytest.mark.asyncio
    async def test_active_key_index_is_partial_on_valid_to_null(
        self, db_conn: asyncpg.Connection
    ) -> None:
        row = await db_conn.fetchrow(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_chat_facts_active_key'"
        )
        assert row is not None
        assert "valid_to IS NULL" in row["indexdef"]

    @pytest.mark.asyncio
    async def test_embedding_index_is_ivfflat(self, db_conn: asyncpg.Connection) -> None:
        row = await db_conn.fetchrow(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_chat_facts_embedding'"
        )
        assert row is not None
        assert "ivfflat" in row["indexdef"]
        assert "vector_cosine_ops" in row["indexdef"]

    @pytest.mark.asyncio
    async def test_updated_at_trigger_fires_on_update(self, db_conn: asyncpg.Connection) -> None:
        """NB: can't compare a "before" vs "after" `now()` reading here --
        `db_conn` wraps the whole test in one transaction, and Postgres's
        `now()` (what the trigger uses) is the *transaction* timestamp, frozen
        for the transaction's duration -- an insert-then-update within the same
        transaction would see identical `now()` regardless of whether the
        trigger fired. Instead, seed an explicit sentinel `updated_at` far in
        the past (bypassing the column DEFAULT, which the `BEFORE UPDATE`-only
        trigger never touches on INSERT) and assert the trigger overwrites it
        away from that sentinel on UPDATE.
        """
        fact_id = await db_conn.fetchval(
            """
            INSERT INTO chat_facts (chat_id, subject, predicate, value, fact_text, source, updated_at)
            VALUES (-900004, 's', 'p', 'v', 'fact', 'manual', '2020-01-01T00:00:00Z')
            RETURNING id
            """
        )
        await db_conn.execute("UPDATE chat_facts SET status = 'rejected' WHERE id = $1", fact_id)
        after = await db_conn.fetchval("SELECT updated_at FROM chat_facts WHERE id = $1", fact_id)
        assert after.year > 2020


# ---------------------------------------------------------------------------
# chat_settings.kb_organizer_ids / kb_enabled
# ---------------------------------------------------------------------------


class TestChatSettingsKbColumns:
    @pytest.mark.asyncio
    async def test_kb_enabled_defaults_null_deferring_to_global(
        self, db_conn: asyncpg.Connection
    ) -> None:
        """Review fix: no column DEFAULT — a fresh row leaves kb_enabled NULL
        so the bot_config default_kb_enabled layer stays effective until the
        chat explicitly opts in/out."""
        await db_conn.execute(
            "INSERT INTO chat_settings (chat_id) VALUES (-900005) ON CONFLICT DO NOTHING"
        )
        row = await db_conn.fetchrow("SELECT kb_enabled FROM chat_settings WHERE chat_id = -900005")
        assert row is not None
        assert row["kb_enabled"] is None

    @pytest.mark.asyncio
    async def test_kb_organizer_ids_defaults_empty_array(self, db_conn: asyncpg.Connection) -> None:
        await db_conn.execute(
            "INSERT INTO chat_settings (chat_id) VALUES (-900006) ON CONFLICT DO NOTHING"
        )
        row = await db_conn.fetchrow(
            "SELECT kb_organizer_ids FROM chat_settings WHERE chat_id = -900006"
        )
        assert row is not None
        assert json.loads(row["kb_organizer_ids"]) == []

    @pytest.mark.asyncio
    async def test_kb_organizer_ids_stores_id_list(self, db_conn: asyncpg.Connection) -> None:
        await db_conn.execute(
            """
            INSERT INTO chat_settings (chat_id, kb_organizer_ids)
            VALUES (-900007, $1::jsonb)
            ON CONFLICT (chat_id) DO UPDATE SET kb_organizer_ids = EXCLUDED.kb_organizer_ids
            """,
            json.dumps([111, 222]),
        )
        row = await db_conn.fetchrow(
            "SELECT kb_organizer_ids FROM chat_settings WHERE chat_id = -900007"
        )
        assert row is not None
        assert json.loads(row["kb_organizer_ids"]) == [111, 222]
