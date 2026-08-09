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

        repo.get_pending_embeddings.assert_awaited_once_with(limit=5)

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

        args = pool.fetch.call_args.args
        assert args[-1] == 15
        assert "embedding IS NULL" in args[0]

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
