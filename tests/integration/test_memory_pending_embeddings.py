"""The pending-embedding SQL, against a real PostgreSQL + pgvector.

`tests/unit/test_embedding_backfill.py` drives these methods through a mocked
pool and asserts on substrings of the query text. That proves what the SQL
*says*, not what it *does* — and the exclusion clause added after review
(`AND NOT (id = ANY($2::bigint[]))`) is a real cast against a real column
type. These cases run it for real: NULL-embedding rows are found, excluded
ids are skipped, ordering is oldest-first, and the update fills the row in.
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.memory import MemoryRepository

_EMBED_DIM = 768

CHAT = -940001


def _vec(index: int) -> list[float]:
    vec = [0.0] * _EMBED_DIM
    vec[index] = 1.0
    return vec


@pytest_asyncio.fixture
async def repo(db_pool: asyncpg.Pool) -> MemoryRepository:
    return MemoryRepository(db_pool)


@pytest_asyncio.fixture
async def pending_rows(db_pool: asyncpg.Pool, repo: MemoryRepository) -> list[int]:
    """Three rows stored without an embedding, oldest first."""
    await db_pool.execute("DELETE FROM chat_memory WHERE chat_id = $1", CHAT)
    ids = [await repo.store(chat_id=CHAT, content=f"pending {n}", embedding=None) for n in range(3)]
    # created_at defaults to NOW() for all three within the same statement
    # batch, so make the intended order explicit rather than relying on clock
    # resolution — otherwise "oldest first" is untestable.
    for offset, memory_id in enumerate(ids):
        await db_pool.execute(
            "UPDATE chat_memory SET created_at = NOW() - ($2 || ' minutes')::interval WHERE id = $1",
            memory_id,
            str(10 - offset),
        )
    return ids


class TestPendingEmbeddings:
    @pytest.mark.asyncio
    async def test_finds_null_embedding_rows_oldest_first(
        self, repo: MemoryRepository, pending_rows: list[int]
    ) -> None:
        rows = await repo.get_pending_embeddings(limit=10)

        found = [r["id"] for r in rows if r["id"] in pending_rows]
        assert found == pending_rows

    @pytest.mark.asyncio
    async def test_excluded_ids_are_skipped(
        self, repo: MemoryRepository, pending_rows: list[int]
    ) -> None:
        """The clause that lets the worker retire a poison row. Runs the real
        `= ANY($2::bigint[])` cast, which the mocked-pool tests cannot."""
        rows = await repo.get_pending_embeddings(limit=10, exclude_ids=[pending_rows[0]])

        found = [r["id"] for r in rows if r["id"] in pending_rows]
        assert pending_rows[0] not in found
        assert found == pending_rows[1:]

    @pytest.mark.asyncio
    async def test_empty_exclusion_list_is_valid_sql(
        self, repo: MemoryRepository, pending_rows: list[int]
    ) -> None:
        """The default path: an empty array must not error or filter everything."""
        rows = await repo.get_pending_embeddings(limit=10, exclude_ids=[])

        assert [r["id"] for r in rows if r["id"] in pending_rows] == pending_rows

    @pytest.mark.asyncio
    async def test_row_leaves_the_queue_once_filled(
        self, repo: MemoryRepository, pending_rows: list[int]
    ) -> None:
        await repo.update_embedding(pending_rows[0], _vec(1))

        rows = await repo.get_pending_embeddings(limit=10)

        assert pending_rows[0] not in [r["id"] for r in rows]

    @pytest.mark.asyncio
    async def test_update_does_not_clobber_an_already_filled_row(
        self, db_pool: asyncpg.Pool, repo: MemoryRepository, pending_rows: list[int]
    ) -> None:
        """`AND embedding IS NULL` in the UPDATE — a second backfill pass must
        not overwrite a vector another pass already wrote."""
        await repo.update_embedding(pending_rows[0], _vec(1))
        await repo.update_embedding(pending_rows[0], _vec(2))

        # Cast in SQL rather than reading the client-side pgvector type: the
        # Python `Vector` repr varies by pgvector release (an unpinned 0.5.0
        # once returned a non-iterable one and broke tests), while
        # `embedding::text` is stable.
        stored = await db_pool.fetchval(
            "SELECT embedding::text FROM chat_memory WHERE id = $1", pending_rows[0]
        )
        assert stored is not None
        assert stored.startswith("[0,1,0")  # first write survived, second was a no-op
