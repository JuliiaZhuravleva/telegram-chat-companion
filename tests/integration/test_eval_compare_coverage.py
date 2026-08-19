"""Integration test: `scripts/eval_compare.coverage()` (S5).

This one query produces the headline of the S5 decision -- "the Q&A store can
reach 8% of the conversation, the chunk index reaches all of it". A number that
carries that much weight has to be checked against a fixture whose answer is
known by counting, not by trusting the SQL.

Two specific ways it could be wrong and still look right:

* summing `msg_count` instead of counting messages would overstate chunk
  coverage, because consecutive chunks overlap by up to two messages by design
  (measured on production: the sum runs ~20% above the real message count);
* counting `chat_memory` rows instead of distinct `source_message_id` would
  overstate the Q&A store, since several memories can point at one message and
  many point at none.

Both inflate in the direction of the conclusion the author already expects,
which is exactly when a check is worth having.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest_asyncio

from scripts.eval_compare import coverage

CHAT = -100888000111
OTHER = -100888000222
_T0 = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
_EMBED_DIM = 768


@pytest_asyncio.fixture(autouse=True)
async def _clean(db_pool: asyncpg.Pool):
    for table in ("chat_chunks", "chat_memory", "chat_messages"):
        await db_pool.execute(
            f"DELETE FROM {table} WHERE chat_id = ANY($1::bigint[])",  # noqa: S608
            [CHAT, OTHER],
        )
    yield
    for table in ("chat_chunks", "chat_memory", "chat_messages"):
        await db_pool.execute(
            f"DELETE FROM {table} WHERE chat_id = ANY($1::bigint[])",  # noqa: S608
            [CHAT, OTHER],
        )


async def _messages(pool: asyncpg.Pool, chat_id: int, ids: range) -> None:
    for message_id in ids:
        await pool.execute(
            """
            INSERT INTO chat_messages
                (chat_id, message_id, user_id, message_type, content, created_at)
            VALUES ($1, $2, 111, 'text', 'сообщение', $3)
            """,
            chat_id,
            message_id,
            _T0 + timedelta(minutes=message_id),
        )


async def _chunk(
    pool: asyncpg.Pool, chat_id: int, msg_from: int, msg_to: int, part: int = 0
) -> None:
    await pool.execute(
        """
        INSERT INTO chat_chunks
            (chat_id, thread_id, msg_from, msg_to, part, content, msg_count,
             senders, started_at, ended_at)
        VALUES ($1, NULL, $2, $3, $4, 'кусок', $5, '{111}'::bigint[], $6, $6)
        """,
        chat_id,
        msg_from,
        msg_to,
        part,
        msg_to - msg_from + 1,
        _T0,
    )


async def _memory(pool: asyncpg.Pool, chat_id: int, source_message_id: int | None) -> None:
    await pool.execute(
        """
        INSERT INTO chat_memory (chat_id, content, embedding, source_message_id, created_at)
        VALUES ($1, 'память', $2, $3, $4)
        """,
        chat_id,
        [0.1] * _EMBED_DIM,
        source_message_id,
        _T0,
    )


class TestCoverage:
    async def test_overlapping_chunks_do_not_inflate_the_count(self, db_pool: asyncpg.Pool) -> None:
        """Ten messages, two chunks that overlap by two. `sum(msg_count)` is 12
        -- 120% of the chat -- while the honest answer is 10 of 10."""
        await _messages(db_pool, CHAT, range(1, 11))
        await _chunk(db_pool, CHAT, 1, 6)
        await _chunk(db_pool, CHAT, 5, 10, part=1)

        row = next(r for r in await coverage(db_pool) if r["chat_id"] == CHAT)

        assert row["total"] == 10
        assert row["chunk_rows"] == 2
        assert row["chunk_covered"] == 10

    async def test_a_gap_between_chunks_is_reported_as_a_gap(self, db_pool: asyncpg.Pool) -> None:
        """The control for the test above: if the query counted spans rather
        than real messages, an unchunked middle would vanish."""
        await _messages(db_pool, CHAT, range(1, 11))
        await _chunk(db_pool, CHAT, 1, 3)
        await _chunk(db_pool, CHAT, 8, 10, part=1)

        row = next(r for r in await coverage(db_pool) if r["chat_id"] == CHAT)

        assert row["chunk_covered"] == 6

    async def test_memory_is_counted_by_distinct_source_message(
        self, db_pool: asyncpg.Pool
    ) -> None:
        """Four rows, three of which are useless as coverage: two point at the
        same message and one points nowhere."""
        await _messages(db_pool, CHAT, range(1, 11))
        await _memory(db_pool, CHAT, 3)
        await _memory(db_pool, CHAT, 3)
        await _memory(db_pool, CHAT, 7)
        await _memory(db_pool, CHAT, None)

        row = next(r for r in await coverage(db_pool) if r["chat_id"] == CHAT)

        assert row["memory_rows"] == 4
        assert row["memory_covered"] == 2

    async def test_a_chunk_from_another_chat_never_counts(self, db_pool: asyncpg.Pool) -> None:
        """The join is on `chat_id` as well as the id range, and message ids
        are only unique per chat -- without the chat predicate another chat's
        chunk covering ids 1..10 would count here."""
        await _messages(db_pool, CHAT, range(1, 11))
        await _messages(db_pool, OTHER, range(1, 11))
        await _chunk(db_pool, OTHER, 1, 10)

        row = next(r for r in await coverage(db_pool) if r["chat_id"] == CHAT)
        other = next(r for r in await coverage(db_pool) if r["chat_id"] == OTHER)

        assert row["chunk_covered"] == 0
        assert other["chunk_covered"] == 10

    async def test_a_chat_with_no_rows_in_either_store_still_appears(
        self, db_pool: asyncpg.Pool
    ) -> None:
        """A chat that is entirely unreachable is the most important row in the
        table; an inner join would have dropped it and the average would look
        better for it."""
        await _messages(db_pool, CHAT, range(1, 6))

        row = next(r for r in await coverage(db_pool) if r["chat_id"] == CHAT)

        assert row == {
            "chat_id": CHAT,
            "total": 5,
            "memory_rows": 0,
            "memory_covered": 0,
            "chunk_rows": 0,
            "chunk_covered": 0,
        }

    async def test_an_unembedded_chunk_still_counts_as_coverage(
        self, db_pool: asyncpg.Pool
    ) -> None:
        """Coverage is about what the store *holds*, not what it can currently
        rank. Conflating the two would make a mid-backfill run report a
        shrinking corpus."""
        await _messages(db_pool, CHAT, range(1, 6))
        await _chunk(db_pool, CHAT, 1, 5)

        row = next(r for r in await coverage(db_pool) if r["chat_id"] == CHAT)

        assert row["chunk_covered"] == 5


class TestFormatting:
    def test_the_table_never_prints_a_chat_id(self) -> None:
        """The report is written under `internal/`, but section 1 is quoted
        into `docs/` by hand. A chat id is a real Telegram identifier and this
        repo is public, so the rows are numbered instead of named.

        Asserted as "no long run of digits survives" rather than "this one
        string is absent". The narrow form only proves the id it was handed
        did not leak, and the id it was handed has to look realistic to be
        convincing -- which is how the first version of this test came to
        carry a **real** production chat id into a public repository, inside
        the very check written to stop that. Neither gitleaks (no credential
        shape) nor `scripts/check_plan_artifacts.py` (scoped to `docs/`) looks
        at this file."""
        import re

        from scripts.eval_compare import _format_coverage

        text = _format_coverage(
            [
                {
                    "chat_id": -100888000333,
                    "total": 100,
                    "memory_rows": 8,
                    "memory_covered": 8,
                    "chunk_rows": 9,
                    "chunk_covered": 100,
                }
            ]
        )

        assert not re.search(r"\d{9,}", text), f"a long numeric id leaked into the table: {text}"
        assert "888000333" not in text
        assert "| #1 |" in text
        assert "8.0%" in text
        assert "100.0%" in text

    def test_it_does_not_divide_by_zero_on_an_empty_chat(self) -> None:
        from scripts.eval_compare import _format_coverage

        text = _format_coverage(
            [
                {
                    "chat_id": -1,
                    "total": 0,
                    "memory_rows": 0,
                    "memory_covered": 0,
                    "chunk_rows": 0,
                    "chunk_covered": 0,
                }
            ]
        )

        assert "0.0%" in text
