"""Unit tests for EmbeddingBackfillWorker + MemoryRepository's pending-embedding
methods (S2-10).

Mirrors tests/unit/test_retention_cleaner.py's structure: window/config
tests, a run_once() class driving the worker against a mocked repository,
lifecycle (start/stop) tests, and a repository-level class for the raw SQL
methods against a mocked pool.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.config import EmbeddingBackfillSettings
from src.database.repositories.memory import MemoryRepository
from src.services.ai.base import EmbeddingResult
from src.services.rag.backfill import EmbeddingBackfillWorker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker(**overrides: object) -> EmbeddingBackfillWorker:
    config = EmbeddingBackfillSettings(**overrides)  # type: ignore[arg-type]
    return EmbeddingBackfillWorker(pool=AsyncMock(), ai_router=AsyncMock(), config=config)


def _make_row(memory_id: int = 1, chat_id: int = 100, content: str = "hi") -> dict:
    return {"id": memory_id, "chat_id": chat_id, "content": content}


def _make_embedding_result(dimensions: int = 768) -> EmbeddingResult:
    return EmbeddingResult(
        embedding=[0.1] * dimensions, model="mock-embed", provider="mock", dimensions=dimensions
    )


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_fills_every_pending_row_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = AsyncMock()
        repo.get_pending_embeddings = AsyncMock(return_value=[_make_row(1), _make_row(2)])
        repo.update_embedding = AsyncMock()
        monkeypatch.setattr("src.services.rag.backfill.MemoryRepository", lambda _pool: repo)

        worker = _make_worker()
        worker._ai_router.generate_embedding = AsyncMock(return_value=_make_embedding_result())

        result = await worker.run_once()

        assert result == {"filled": 2, "still_pending": 0}
        assert repo.update_embedding.await_count == 2

    @pytest.mark.asyncio
    async def test_provider_failure_leaves_row_pending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = AsyncMock()
        repo.get_pending_embeddings = AsyncMock(return_value=[_make_row(1)])
        repo.update_embedding = AsyncMock()
        monkeypatch.setattr("src.services.rag.backfill.MemoryRepository", lambda _pool: repo)

        worker = _make_worker()
        worker._ai_router.generate_embedding = AsyncMock(
            side_effect=RuntimeError("all providers failed")
        )

        result = await worker.run_once()

        assert result == {"filled": 0, "still_pending": 1}
        repo.update_embedding.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wrong_dimension_result_leaves_row_pending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = AsyncMock()
        repo.get_pending_embeddings = AsyncMock(return_value=[_make_row(1)])
        repo.update_embedding = AsyncMock()
        monkeypatch.setattr("src.services.rag.backfill.MemoryRepository", lambda _pool: repo)

        worker = _make_worker()
        worker._ai_router.generate_embedding = AsyncMock(
            return_value=_make_embedding_result(dimensions=1536)
        )

        result = await worker.run_once()

        assert result == {"filled": 0, "still_pending": 1}
        repo.update_embedding.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_bad_row_does_not_stop_the_batch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A row that keeps failing must not stall the rest of the batch --
        mirrors RetentionCleaner's per-table isolation."""
        repo = AsyncMock()
        repo.get_pending_embeddings = AsyncMock(
            return_value=[_make_row(1, chat_id=100), _make_row(2, chat_id=200)]
        )
        repo.update_embedding = AsyncMock()
        monkeypatch.setattr("src.services.rag.backfill.MemoryRepository", lambda _pool: repo)

        worker = _make_worker()

        async def _flaky_embed(_content: str, *, chat_id: int) -> EmbeddingResult:
            if chat_id == 100:
                raise RuntimeError("boom")
            return _make_embedding_result()

        worker._ai_router.generate_embedding = AsyncMock(side_effect=_flaky_embed)

        result = await worker.run_once()

        assert result == {"filled": 1, "still_pending": 1}

    @pytest.mark.asyncio
    async def test_no_pending_rows_is_a_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = AsyncMock()
        repo.get_pending_embeddings = AsyncMock(return_value=[])
        monkeypatch.setattr("src.services.rag.backfill.MemoryRepository", lambda _pool: repo)

        worker = _make_worker()

        result = await worker.run_once()

        assert result == {"filled": 0, "still_pending": 0}
        worker._ai_router.generate_embedding.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_limit_is_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = AsyncMock()
        repo.get_pending_embeddings = AsyncMock(return_value=[])
        monkeypatch.setattr("src.services.rag.backfill.MemoryRepository", lambda _pool: repo)

        worker = _make_worker(batch_limit=5)
        await worker.run_once()

        repo.get_pending_embeddings.assert_awaited_once_with(limit=5, exclude_ids=[])

    @pytest.mark.asyncio
    async def test_repository_write_failure_leaves_row_pending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """update_embedding() itself raising (e.g. a transient DB error)
        must be counted as still-pending, not crash the pass."""
        repo = AsyncMock()
        repo.get_pending_embeddings = AsyncMock(return_value=[_make_row(1)])
        repo.update_embedding = AsyncMock(side_effect=RuntimeError("db down"))
        monkeypatch.setattr("src.services.rag.backfill.MemoryRepository", lambda _pool: repo)

        worker = _make_worker()
        worker._ai_router.generate_embedding = AsyncMock(return_value=_make_embedding_result())

        result = await worker.run_once()

        assert result == {"filled": 0, "still_pending": 1}


# ---------------------------------------------------------------------------
# Parking deterministically-failing rows
# ---------------------------------------------------------------------------


class TestParksPoisonRows:
    """The queue is FIFO (`ORDER BY created_at ASC LIMIT n`) and a failure
    leaves the row NULL, so without a cap a row that fails *every* time sits
    at the head of every pass forever. Accumulate `batch_limit` of them and
    nothing written later is ever backfilled — and ADR-0011 keeps this table
    out of retention, so they never age out either.
    """

    @pytest.mark.asyncio
    async def test_row_is_parked_and_excluded_after_repeated_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = AsyncMock()
        repo.get_pending_embeddings = AsyncMock(return_value=[_make_row(7)])
        monkeypatch.setattr("src.services.rag.backfill.MemoryRepository", lambda _pool: repo)

        worker = _make_worker()
        worker._ai_router.generate_embedding = AsyncMock(side_effect=RuntimeError("always fails"))

        for _ in range(3):
            await worker.run_once()

        # Not retried a fourth time, and the exclusion actually reaches the query.
        assert repo.get_pending_embeddings.await_args.kwargs["exclude_ids"] == []
        await worker.run_once()
        assert repo.get_pending_embeddings.await_args.kwargs["exclude_ids"] == [7]

    @pytest.mark.asyncio
    async def test_a_poison_row_does_not_block_a_later_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The behaviour that actually matters: with batch_limit=1 the bad row
        owns the only slot until it is parked, after which the good row is
        reached and filled."""
        repo = AsyncMock()
        repo.update_embedding = AsyncMock()

        def _pending(limit: int, *, exclude_ids: list[int] | None = None) -> list[dict]:
            queue = [_make_row(1, content="poison"), _make_row(2, content="fine")]
            remaining = [r for r in queue if r["id"] not in (exclude_ids or [])]
            return remaining[:limit]

        repo.get_pending_embeddings = AsyncMock(side_effect=_pending)
        monkeypatch.setattr("src.services.rag.backfill.MemoryRepository", lambda _pool: repo)

        worker = _make_worker(batch_limit=1)

        async def _embed(content: str, **_kwargs: object) -> EmbeddingResult:
            if content == "poison":
                raise RuntimeError("always fails")
            return _make_embedding_result()

        worker._ai_router.generate_embedding = AsyncMock(side_effect=_embed)

        for _ in range(3):
            assert (await worker.run_once())["filled"] == 0

        assert (await worker.run_once())["filled"] == 1
        repo.update_embedding.assert_awaited_once()
        assert repo.update_embedding.await_args.args[0] == 2

    @pytest.mark.asyncio
    async def test_transient_failure_then_success_does_not_park(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """False-positive control: an outage that recovers must not retire the
        row. The counter is consecutive failures, so a success clears it."""
        repo = AsyncMock()
        repo.get_pending_embeddings = AsyncMock(return_value=[_make_row(5)])
        repo.update_embedding = AsyncMock()
        monkeypatch.setattr("src.services.rag.backfill.MemoryRepository", lambda _pool: repo)

        worker = _make_worker()
        worker._ai_router.generate_embedding = AsyncMock(
            side_effect=[
                RuntimeError("blip"),
                RuntimeError("blip"),
                _make_embedding_result(),
                RuntimeError("blip"),
            ]
        )

        for _ in range(4):
            await worker.run_once()

        assert worker._parked == set()
        assert repo.get_pending_embeddings.await_args.kwargs["exclude_ids"] == []


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_disabled_config_starts_no_task(self) -> None:
        worker = _make_worker(enabled=False)
        await worker.start()
        assert worker._task is None
        await worker.stop()  # must stay safe with no task

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task(self) -> None:
        worker = _make_worker()
        await worker.start()
        assert worker._task is not None
        await worker.stop()
        assert worker._task.cancelled() or worker._task.done()


# ---------------------------------------------------------------------------
# MemoryRepository — pending-embedding methods (mocked pool, guard-level)
# ---------------------------------------------------------------------------


class TestMemoryRepositoryPendingEmbeddings:
    @pytest.mark.asyncio
    async def test_get_pending_embeddings_passes_limit(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        repo = MemoryRepository(pool)

        await repo.get_pending_embeddings(limit=15)

        # Bind by position, not `args[-1]`: the exclusion array is now the last
        # parameter, so "last argument" silently stopped meaning "the limit".
        args = pool.fetch.call_args.args
        assert args[1] == 15  # $1
        assert args[2] == []  # $2 — no parked rows by default
        assert "embedding IS NULL" in args[0]
        assert "id = ANY($2::bigint[])" in args[0]

    @pytest.mark.asyncio
    async def test_get_pending_embeddings_forwards_exclusions(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        repo = MemoryRepository(pool)

        await repo.get_pending_embeddings(limit=15, exclude_ids=[3, 9])

        assert pool.fetch.call_args.args[2] == [3, 9]

    @pytest.mark.asyncio
    async def test_update_embedding_guards_on_still_null(self) -> None:
        pool = AsyncMock()
        pool.execute = AsyncMock()
        repo = MemoryRepository(pool)

        await repo.update_embedding(1, [0.1] * 768)

        args = pool.execute.call_args.args
        assert "embedding IS NULL" in args[0]
        assert args[1] == 1
        assert args[2] == [0.1] * 768

    @pytest.mark.asyncio
    async def test_store_accepts_none_embedding(self) -> None:
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"id": 42})
        repo = MemoryRepository(pool)

        memory_id = await repo.store(chat_id=1, content="hi", embedding=None)

        assert memory_id == 42
        args = pool.fetchrow.call_args.args
        assert args[3] is None  # embedding positional slot
