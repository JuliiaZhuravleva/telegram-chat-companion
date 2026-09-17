"""Integration tests: `get_ai_failure_details` against a real database.

This query is the one that tells the admin "holding since 16.09 18:42 (23h
4m)", and it is the part of the alerting change that a mocked pool cannot test
at all: over `AsyncMock` the SQL is a string nobody parses, and the semantics
of `since` live entirely inside it.

The first version defined the failure streak by a gap between FAILURES -- more
than an hour apart started a new streak. A review measured what that does to
any task whose requests are sparse: `transcription` broken continuously for
two days and 22 hours reported `holding since 0m`, because voice notes arrive
further apart than the gap. The tests below pin the corrected semantics
("nothing has SUCCEEDED since then") for both shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.database.repositories.health import HealthRepository

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _clean(db_pool: asyncpg.Pool):
    await db_pool.execute("DELETE FROM ai_failure_log")
    await db_pool.execute("DELETE FROM response_log")
    yield
    await db_pool.execute("DELETE FROM ai_failure_log")
    await db_pool.execute("DELETE FROM response_log")


async def _failure(pool: asyncpg.Pool, task: str, when: datetime, *, message: str = "429") -> None:
    await pool.execute(
        """
        INSERT INTO ai_failure_log (task_type, provider, error_type, error_message, created_at)
        VALUES ($1, 'gemini', 'RateLimitError', $2, $3)
        """,
        task,
        message,
        when,
    )


async def _success(pool: asyncpg.Pool, task: str, when: datetime) -> None:
    await pool.execute(
        """
        INSERT INTO response_log (chat_id, task_type, provider, model, created_at)
        VALUES (-1000000000001, $1, 'gemini', 'gemini-embedding-001', $2)
        """,
        task,
        when,
    )


class TestSince:
    async def test_a_sparse_task_reports_the_whole_outage(self, db_pool: asyncpg.Pool):
        """Failures hours apart are ONE outage while nothing succeeds between them.

        The measured regression: with a one-hour gap rule this returned the
        most recent failure, so a three-day transcription outage rendered as
        "holding since 0m" -- in the very field added to tell a fresh incident
        from an old one.
        """
        now = datetime.now(UTC)
        await _success(db_pool, "transcription", now - timedelta(days=4))
        for hours_ago in (70, 40, 12, 0.1):
            await _failure(db_pool, "transcription", now - timedelta(hours=hours_ago))

        rows = await HealthRepository(db_pool).get_ai_failure_details(timedelta(minutes=15))

        assert len(rows) == 1
        assert rows[0]["since"] is not None
        held_for = now - rows[0]["since"]
        assert held_for > timedelta(hours=69)

    async def test_a_success_in_between_starts_a_new_streak(self, db_pool: asyncpg.Pool):
        """Control for the test above, and the reason the gap rule existed.

        Without this, `since` would fuse last week's unrelated incident onto
        today's -- the opposite error, and just as misleading.
        """
        now = datetime.now(UTC)
        await _failure(db_pool, "embeddings", now - timedelta(days=3))
        await _success(db_pool, "embedding", now - timedelta(hours=2))  # note: singular
        await _failure(db_pool, "embeddings", now - timedelta(hours=1))
        await _failure(db_pool, "embeddings", now - timedelta(minutes=1))

        rows = await HealthRepository(db_pool).get_ai_failure_details(timedelta(minutes=15))

        held_for = now - rows[0]["since"]
        assert timedelta(minutes=50) < held_for < timedelta(hours=1, minutes=10)

    async def test_the_two_tables_disagree_on_task_names(self, db_pool: asyncpg.Pool):
        """`ai_failure_log` says "embeddings", `response_log` says "embedding".

        Mapping them wrongly is not a theoretical risk: querying the plural
        name against the success table during the live incident produced
        "zero successes in 30 hours", which read as a totally dead provider.
        Here it would silently disable the success side and bring the fused
        streak back.
        """
        now = datetime.now(UTC)
        await _failure(db_pool, "embeddings", now - timedelta(hours=5))
        await _success(db_pool, "embeddings", now - timedelta(hours=1))  # WRONG name on purpose
        await _failure(db_pool, "embeddings", now - timedelta(minutes=2))

        rows = await HealthRepository(db_pool).get_ai_failure_details(timedelta(minutes=15))

        # The mis-named success must NOT end the streak: it is not a success
        # of this task by either table's vocabulary.
        assert now - rows[0]["since"] > timedelta(hours=4)

    async def test_no_success_ever_recorded(self, db_pool: asyncpg.Pool):
        now = datetime.now(UTC)
        await _failure(db_pool, "vision", now - timedelta(hours=8))
        await _failure(db_pool, "vision", now - timedelta(minutes=3))

        rows = await HealthRepository(db_pool).get_ai_failure_details(timedelta(minutes=15))

        assert now - rows[0]["since"] > timedelta(hours=7)


class TestCountsAndDetails:
    async def test_count_covers_the_window_only(self, db_pool: asyncpg.Pool):
        now = datetime.now(UTC)
        for minutes_ago in (1, 2, 3, 400):
            await _failure(db_pool, "embeddings", now - timedelta(minutes=minutes_ago))

        rows = await HealthRepository(db_pool).get_ai_failure_details(timedelta(minutes=15))

        assert rows[0]["count"] == 3

    async def test_the_latest_error_message_is_the_one_returned(self, db_pool: asyncpg.Pool):
        """A stale diagnosis describes a situation that may have changed."""
        now = datetime.now(UTC)
        await _failure(db_pool, "embeddings", now - timedelta(minutes=9), message="older quota")
        await _failure(
            db_pool, "embeddings", now - timedelta(minutes=1), message="credits are depleted"
        )

        rows = await HealthRepository(db_pool).get_ai_failure_details(timedelta(minutes=15))

        assert rows[0]["error_message"] == "credits are depleted"

    async def test_tasks_are_reported_separately_and_busiest_first(self, db_pool: asyncpg.Pool):
        now = datetime.now(UTC)
        await _failure(db_pool, "transcription", now - timedelta(minutes=1))
        for _ in range(3):
            await _failure(db_pool, "embeddings", now - timedelta(minutes=1))

        rows = await HealthRepository(db_pool).get_ai_failure_details(timedelta(minutes=15))

        assert [r["task_type"] for r in rows] == ["embeddings", "transcription"]

    async def test_a_quiet_window_returns_nothing(self, db_pool: asyncpg.Pool):
        """Positive control lives in the tests above: this one must be empty.

        An empty result is what "healthy" is built on, so it needs its own
        assertion rather than being inferred from the others passing.
        """
        await _failure(db_pool, "embeddings", datetime.now(UTC) - timedelta(hours=3))

        rows = await HealthRepository(db_pool).get_ai_failure_details(timedelta(minutes=15))

        assert rows == []


class TestIndexExists:
    async def test_migration_034_indexed_the_columns_the_queries_filter_on(
        self, db_pool: asyncpg.Pool
    ):
        """Two scans of this table run every five minutes for ever.

        Measured on a 112k-row copy before the index: ~2768 buffer hits and
        16-19 ms per cycle, both sequential scans.
        """
        rows = await db_pool.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'ai_failure_log'"
        )
        names = {r["indexname"] for r in rows}

        assert "idx_ai_failure_log_task_time" in names
