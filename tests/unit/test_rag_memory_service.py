"""Tests for src.services.rag.memory — RAGMemoryService threshold wiring (S2-2).

Scope: the single-source-of-truth consolidation for the RAG similarity
threshold (S2-2) only -- the constructor/repository ``min_similarity``
defaults (0.65, diverging from the 0.7 that's actually live via
``src/di.py``) are removed, and the ``x or default`` -> ``x if x is not
None else default`` bug in the per-call override is fixed. Broader
RAGMemoryService coverage (embedding-failure behavior, limit passthrough)
is S2-7b's, not this item's.
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
