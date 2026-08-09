"""Tests for src.services.rag.memory — RAGMemoryService (S2-2, S2-7b).

S2-2 classes cover the single-source-of-truth consolidation for the RAG
similarity threshold: the constructor/repository ``min_similarity``
defaults (0.65, diverging from the 0.7 that's actually live via
``src/di.py``) are removed, and the ``x or default`` -> ``x if x is not
None else default`` bug in the per-call override is fixed.

S2-7b classes cover the rest of ``RAGMemoryService``'s own behavior that
S2-2 explicitly left out: what ``search()``/``store()`` do when embedding
generation fails (currently: log a warning and return ``[]``/``None``
without ever reaching the repository), the ``max_results`` passthrough
(mirroring the ``min_similarity`` override tests), and the S2-4
``query_embedding`` passthrough (a shared embedding skips a second
``generate_embedding()`` call). Repository-level chat-scoping (privacy
invariant) is S2-7a's, in ``tests/integration/``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.database.repositories.memory import MemoryRepository
from src.di import ServiceProvider
from src.services.ai.base import EmbeddingResult
from src.services.rag.memory import RAGMemoryService


def _make_embedding_result() -> EmbeddingResult:
    return EmbeddingResult(
        embedding=[0.1] * 768, model="mock-embed", provider="mock", dimensions=768
    )


def _make_service(
    *, min_similarity: float = 0.7, repo: AsyncMock | None = None, router: AsyncMock | None = None
) -> tuple[RAGMemoryService, AsyncMock, AsyncMock]:
    repo = repo if repo is not None else AsyncMock(spec=MemoryRepository)
    router = router if router is not None else AsyncMock()
    service = RAGMemoryService(repo, router, min_similarity=min_similarity)
    return service, repo, router


class TestConstructorRequiresThreshold:
    """S2-2: ``min_similarity`` must not carry its own default.

    config/default.yml is the single source of truth (via
    ``settings.rag.min_similarity`` / ``src/di.py``); a constructor default
    was a dead branch reachable only by construction outside DI (tests,
    future call sites) and it disagreed with the live value (0.65 vs 0.7).
    """

    def test_missing_min_similarity_raises(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        router = AsyncMock()
        with pytest.raises(TypeError):
            RAGMemoryService(repo, router)  # type: ignore[call-arg]

    def test_explicit_value_is_used(self) -> None:
        service, _, _ = _make_service(min_similarity=0.42)
        assert service.min_similarity == 0.42


class TestDIWiringMatchesConfig:
    """The production path must read one threshold, and it must be config's.

    ``ServiceProvider.rag_memory_service`` (src/di.py) is the one place that
    constructs ``RAGMemoryService`` in production; assert the effective
    threshold it produces equals ``settings.rag.min_similarity``.
    """

    def test_service_threshold_equals_settings(self, make_settings) -> None:
        settings = make_settings()
        repo = AsyncMock(spec=MemoryRepository)
        router = AsyncMock()

        service = ServiceProvider().rag_memory_service(settings, repo, router)

        assert settings.rag.min_similarity == 0.7  # config/default.yml:76
        assert service.min_similarity == settings.rag.min_similarity


class TestSearchThresholdOverride:
    """S2-2: fix for ``min_similarity or self._min_similarity``.

    That pattern silently discarded an explicit ``0.0`` override (a valid
    "accept everything" threshold) because ``0.0`` is falsy in Python.
    """

    @pytest.mark.asyncio
    async def test_explicit_zero_override_is_honored(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = []
        router = AsyncMock()
        router.generate_embedding.return_value = _make_embedding_result()
        service, _, _ = _make_service(min_similarity=0.9, repo=repo, router=router)

        await service.search(chat_id=1, query="hi", min_similarity=0.0)

        repo.search.assert_awaited_once()
        assert repo.search.call_args.kwargs["min_similarity"] == 0.0

    @pytest.mark.asyncio
    async def test_no_override_falls_back_to_instance_default(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = []
        router = AsyncMock()
        router.generate_embedding.return_value = _make_embedding_result()
        service, _, _ = _make_service(min_similarity=0.9, repo=repo, router=router)

        await service.search(chat_id=1, query="hi")

        assert repo.search.call_args.kwargs["min_similarity"] == 0.9


class TestRepositorySearchRequiresThreshold:
    """S2-2: ``MemoryRepository.search()``'s ``min_similarity`` default
    (0.65) removed -- omitting it is now a hard ``TypeError`` instead of a
    silent, wrong value that disagrees with the configured 0.7.
    """

    @pytest.mark.asyncio
    async def test_missing_min_similarity_raises(self) -> None:
        pool = AsyncMock()
        repo = MemoryRepository(pool)
        with pytest.raises(TypeError):
            await repo.search(1, [0.1] * 768)  # type: ignore[call-arg]


class TestEmbeddingDimensionGuard:
    """S2-1: cheap length guard before write.

    ``chat_memory.embedding`` is ``vector(768)``
    (``alembic/versions/003_rag_memory.py``). With the OpenAI fallback gone
    (S2-1), a dimension mismatch should only happen on a provider bug or
    future config drift, but ``RAGMemoryService.store()`` must still refuse
    to hand a wrong-length vector to the repository -- fail loud in
    application code (logged) rather than reach asyncpg/pgvector's own
    dimension check.
    """

    @pytest.mark.asyncio
    async def test_wrong_dimension_embedding_is_not_stored(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        router = AsyncMock()
        router.generate_embedding.return_value = EmbeddingResult(
            embedding=[0.1] * 1536,  # e.g. an OpenAI-text-embedding-3-small shape
            model="mock-embed",
            provider="mock",
            dimensions=1536,
        )
        service, _, _ = _make_service(repo=repo, router=router)

        memory_id = await service.store(chat_id=1, content="hi")

        assert memory_id is None
        repo.store.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_correct_dimension_embedding_is_stored(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        repo.store.return_value = 42
        router = AsyncMock()
        router.generate_embedding.return_value = _make_embedding_result()  # 768-dim
        service, _, _ = _make_service(repo=repo, router=router)

        memory_id = await service.store(chat_id=1, content="hi")

        assert memory_id == 42
        repo.store.assert_awaited_once()


class TestSearchEmbeddingFailure:
    """S2-7b: ``search()`` when query embedding generation fails.

    ``generate_embedding()`` raises whenever the provider chain is
    exhausted (e.g. Gemini down, no fallback since S2-1). ``search()``
    degrades to "no memories found" rather than propagating -- a single
    provider outage must not take down the whole turn.
    """

    @pytest.mark.asyncio
    async def test_embedding_failure_returns_empty_list(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        router = AsyncMock()
        router.generate_embedding.side_effect = RuntimeError("all providers failed")
        service, _, _ = _make_service(repo=repo, router=router)

        result = await service.search(chat_id=1, query="hi")

        assert result == []

    @pytest.mark.asyncio
    async def test_embedding_failure_does_not_reach_repository(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        router = AsyncMock()
        router.generate_embedding.side_effect = RuntimeError("all providers failed")
        service, _, _ = _make_service(repo=repo, router=router)

        await service.search(chat_id=1, query="hi")

        repo.search.assert_not_awaited()


class TestSearchQueryEmbeddingPassthrough:
    """S2-4: an already-computed ``query_embedding`` skips re-embedding.

    The pipeline computes one shared query embedding for RAG + KB per
    turn and passes it to ``search()`` instead of letting it embed the
    query a second time.
    """

    @pytest.mark.asyncio
    async def test_provided_embedding_skips_generation(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = []
        router = AsyncMock()
        service, _, _ = _make_service(repo=repo, router=router)
        shared_embedding = [0.2] * 768

        await service.search(chat_id=1, query="hi", query_embedding=shared_embedding)

        router.generate_embedding.assert_not_awaited()
        assert repo.search.call_args.kwargs["query_embedding"] == shared_embedding

    @pytest.mark.asyncio
    async def test_missing_embedding_falls_back_to_generation(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = []
        router = AsyncMock()
        router.generate_embedding.return_value = _make_embedding_result()
        service, _, _ = _make_service(repo=repo, router=router)

        await service.search(chat_id=1, query="hi")

        router.generate_embedding.assert_awaited_once()


class TestSearchMaxResultsOverride:
    """S2-7b: ``max_results`` passthrough, mirroring the ``min_similarity``
    override tests above -- same ``x or default`` pitfall shape (an
    explicit ``0`` would be swallowed by ``or``), so cover both the
    override and the no-override default explicitly.
    """

    @pytest.mark.asyncio
    async def test_explicit_max_results_override_is_honored(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = []
        router = AsyncMock()
        router.generate_embedding.return_value = _make_embedding_result()
        service, _, _ = _make_service(repo=repo, router=router)

        await service.search(chat_id=1, query="hi", max_results=1)

        assert repo.search.call_args.kwargs["max_results"] == 1

    @pytest.mark.asyncio
    async def test_no_override_falls_back_to_instance_default(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = []
        router = AsyncMock()
        router.generate_embedding.return_value = _make_embedding_result()
        service = RAGMemoryService(repo, router, min_similarity=0.7, max_results=3)

        await service.search(chat_id=1, query="hi")

        assert repo.search.call_args.kwargs["max_results"] == 3


class TestStoreEmbeddingFailure:
    """S2-7b: ``store()`` when embedding generation fails.

    Mirrors ``TestSearchEmbeddingFailure`` -- a failed embedding must not
    reach the repository, and the memory is simply not persisted (the
    caller, ``_safe_rag_store``, already ignores the return value).
    """

    @pytest.mark.asyncio
    async def test_embedding_failure_returns_none(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        router = AsyncMock()
        router.generate_embedding.side_effect = RuntimeError("all providers failed")
        service, _, _ = _make_service(repo=repo, router=router)

        memory_id = await service.store(chat_id=1, content="hi")

        assert memory_id is None

    @pytest.mark.asyncio
    async def test_embedding_failure_does_not_reach_repository(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        router = AsyncMock()
        router.generate_embedding.side_effect = RuntimeError("all providers failed")
        service, _, _ = _make_service(repo=repo, router=router)

        await service.store(chat_id=1, content="hi")

        repo.store.assert_not_awaited()
