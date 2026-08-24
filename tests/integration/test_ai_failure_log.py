"""Integration tests: ai_failure_log (migration 031) against a real database.

The point of this file is that the SQL is only a string until PostgreSQL
parses it. The write, the grouped read and the retention DELETE all run here
against the real schema, because a green unit test over a mocked pool proves
the Python around the query, never the query.

Why the table exists: `AIRouter._log_usage` writes to `response_log` only when
a call SUCCEEDS, so a failed AI call left no row anywhere, and HealthChecker's
only AI signal reads that same table -- blind to failure by construction.
Video-note transcription was dead in every chat for five days behind exactly
that blindness (ba8ce2c, 2026-08-19..24).
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest

from src.database.repositories.health import HealthRepository
from src.database.repositories.maintenance import RETENTION_TABLES, MaintenanceRepository
from src.database.repositories.response_log import ResponseLogRepository

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _clean(db_pool: asyncpg.Pool):
    await db_pool.execute("DELETE FROM ai_failure_log")
    yield
    await db_pool.execute("DELETE FROM ai_failure_log")


class TestSchema:
    async def test_migration_created_the_table_with_the_expected_columns(
        self, db_pool: asyncpg.Pool
    ):
        rows = await db_pool.fetch(
            """
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'ai_failure_log'
            """
        )
        columns = {row["column_name"]: row["is_nullable"] for row in rows}

        assert set(columns) == {
            "id",
            "task_type",
            "provider",
            "model",
            "error_type",
            "error_message",
            "created_at",
        }

    async def test_created_at_is_not_nullable(self, db_pool: asyncpg.Pool):
        """A nullable timestamp is a known trap in this schema.

        `created_at < NOW() - interval` is three-valued: a NULL row is neither
        older nor newer, so it survives every retention pass for ever and is
        invisible to the health window that is this table's whole purpose.
        """
        nullable = await db_pool.fetchval(
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name = 'ai_failure_log' AND column_name = 'created_at'
            """
        )
        assert nullable == "NO"

        # And the default must supply it, since no writer passes one.
        await db_pool.execute("INSERT INTO ai_failure_log (task_type) VALUES ('transcription')")
        assert await db_pool.fetchval("SELECT created_at FROM ai_failure_log") is not None


class TestWriteAndRead:
    async def test_failure_is_written_and_counted_per_task(self, db_pool: asyncpg.Pool):
        writer = ResponseLogRepository(db_pool)
        reader = HealthRepository(db_pool)

        for _ in range(3):
            await writer.log_failure(
                task_type="transcription",
                provider="openai",
                model="gpt-4o-mini-transcribe",
                error_type="AIProviderError",
                error_message="Audio file might be corrupted or unsupported",
            )
        await writer.log_failure(task_type="vision", provider="openai")

        counts = await reader.get_ai_failure_counts(timedelta(minutes=15))

        assert counts == {"transcription": 3, "vision": 1}

    async def test_rows_outside_the_window_are_not_counted(self, db_pool: asyncpg.Pool):
        await ResponseLogRepository(db_pool).log_failure(task_type="transcription")
        await db_pool.execute("UPDATE ai_failure_log SET created_at = NOW() - interval '2 hours'")

        counts = await HealthRepository(db_pool).get_ai_failure_counts(timedelta(minutes=15))

        assert counts == {}

    async def test_no_failures_reads_as_an_empty_mapping(self, db_pool: asyncpg.Pool):
        assert await HealthRepository(db_pool).get_ai_failure_counts(timedelta(minutes=15)) == {}

    async def test_oversized_error_body_is_truncated_not_rejected(self, db_pool: asyncpg.Pool):
        """Provider error bodies are unbounded and occasionally large.

        Losing the failure record because its own description was too long
        would defeat the table on exactly the incidents worth recording.
        """
        await ResponseLogRepository(db_pool).log_failure(
            task_type="transcription",
            error_message="x" * 10_000,
        )

        stored = await db_pool.fetchval("SELECT error_message FROM ai_failure_log")
        assert len(stored) == 2000

    async def test_optional_columns_accept_nothing_at_all(self, db_pool: asyncpg.Pool):
        """Only task_type is knowable in every failure mode."""
        await ResponseLogRepository(db_pool).log_failure(task_type="embeddings")

        row = await db_pool.fetchrow("SELECT * FROM ai_failure_log")
        assert row["task_type"] == "embeddings"
        assert row["provider"] is None
        assert row["error_message"] is None


class TestRetention:
    async def test_table_is_registered_for_retention(self):
        """An append-only table absent from this map grows for ever, and the
        cleaner refuses it by name rather than failing loudly at write time."""
        assert RETENTION_TABLES.get("ai_failure_log") == "created_at"

    async def test_cleaner_actually_deletes_from_it(self, db_pool: asyncpg.Pool):
        """Drive the real cleaner, not the allowlist.

        Being listed proves the name is spelled right; only running the DELETE
        proves the table and its age column exist as the query assumes.
        """
        writer = ResponseLogRepository(db_pool)
        await writer.log_failure(task_type="transcription")
        await db_pool.execute("UPDATE ai_failure_log SET created_at = NOW() - interval '100 days'")
        await writer.log_failure(task_type="vision")  # recent, must survive

        deleted = await MaintenanceRepository(db_pool).delete_older_than(
            "ai_failure_log", timedelta(days=90)
        )

        assert deleted == 1
        remaining = await db_pool.fetch("SELECT task_type FROM ai_failure_log")
        assert [row["task_type"] for row in remaining] == ["vision"]
