"""Tests for src.services.rag.memory — RAGMemoryService (S2-2, S2-7b, S2-10).

S2-2 classes cover the single-source-of-truth consolidation for the RAG
similarity threshold: the constructor/repository ``min_similarity``
defaults (0.65, diverging from the 0.7 that's actually live via
``src/di.py``) are removed, and the ``x or default`` -> ``x if x is not
None else default`` bug in the per-call override is fixed.

S2-7b classes cover the rest of ``RAGMemoryService``'s own behavior that
S2-2 explicitly left out: what ``search()`` does when embedding generation
fails (log a warning and return ``[]`` without ever reaching the
repository), the ``max_results`` passthrough (mirroring the
``min_similarity`` override tests), and the S2-4 ``query_embedding``
passthrough (a shared embedding skips a second ``generate_embedding()``
call). Repository-level chat-scoping (privacy invariant) is S2-7a's, in
``tests/integration/``.

``TestStoreEmbeddingFailure`` was originally an S2-7b class asserting
``store()`` returned ``None`` and never reached the repository on embedding
failure; S2-10 changed that behavior (persist with ``embedding=None``
instead of dropping the memory) so the class was rewritten in place --
see ``tests/unit/test_embedding_backfill.py`` for the worker that fills
those NULLs back in.

S3-3 adds ``TestSearchBeforePassthrough`` / ``TestRepositorySearchBeforeDefault``:
the optional ``before: datetime | None`` time bound for the real search path
(needed by the S3-2 eval harness so a replayed question can't retrieve the
memory of asking it) -- additive, default ``None``, and applied in the
repository's ``WHERE`` ahead of ``LIMIT`` rather than postfiltered.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.database.repositories.memory import _IVFFLAT_PROBES, MemoryRepository
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


def _row(memory_id: int, similarity: float) -> dict[str, object]:
    """One repository row, in the shape ``MemoryRepository.search`` returns."""
    return {
        "id": memory_id,
        "content": f"memory {memory_id}",
        "similarity": similarity,
        "metadata": None,
        "created_at": None,
        "source_message_id": None,
    }


class TestSearchThresholdOverride:
    """S2-2's concern, at the layer R1 moved the floor to.

    The original pattern ``min_similarity or self._min_similarity`` silently
    discarded an explicit ``0.0`` override (a valid "accept everything"
    threshold) because ``0.0`` is falsy. Until R1 that was observable as an
    argument handed to the repository; now the floor never leaves the
    service, so the same property has to be asserted on what ``search()``
    *returns*. Asserting the old call argument would now pass vacuously —
    the key is simply gone.
    """

    @staticmethod
    def _service_with_rows(floor: float):
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = [_row(1, 0.95), _row(2, 0.50)]
        router = AsyncMock()
        router.generate_embedding.return_value = _make_embedding_result()
        return _make_service(min_similarity=floor, repo=repo, router=router)

    @pytest.mark.asyncio
    async def test_explicit_zero_override_is_honored(self) -> None:
        service, _, _ = self._service_with_rows(0.9)

        results = await service.search(chat_id=1, query="hi", min_similarity=0.0)

        assert [r["id"] for r in results] == [1, 2]

    @pytest.mark.asyncio
    async def test_no_override_falls_back_to_instance_default(self) -> None:
        service, _, _ = self._service_with_rows(0.9)

        results = await service.search(chat_id=1, query="hi")

        assert [r["id"] for r in results] == [1]

    @pytest.mark.asyncio
    async def test_unfiltered_search_ignores_the_floor_entirely(self) -> None:
        """The whole point of R1: the caller can see what was rejected."""
        service, _, _ = self._service_with_rows(0.9)

        results = await service.search_unfiltered(chat_id=1, query="hi")

        assert [r["id"] for r in results] == [1, 2]
        assert [r["similarity"] for r in results] == [0.95, 0.50]


class _AsyncCM:
    """Minimal async context manager wrapper (pattern: test_knowledge_repository.py)."""

    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _pool_with_connection() -> tuple[MagicMock, MagicMock]:
    """A pool whose ``acquire()`` yields a mocked connection.

    ``MemoryRepository.search`` runs inside ``acquire() + transaction()`` since
    the ivfflat-probes guard, so a bare ``pool.fetch`` mock no longer sees the
    query at all — it would make every assertion below pass vacuously against
    an empty call list rather than fail.
    """
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool, conn


class TestRepositoryScanIsExact:
    """The ivfflat index is approximate and carries no ``chat_id``.

    ``idx_chat_memory_embedding`` indexes the vector alone, so an index scan
    takes the k globally-nearest rows and only then filters to the chat: with
    the default ``probes = 1`` of migration 003's ``lists = 100``, a chat whose
    memories sit in unscanned partitions comes back empty while a strong match
    is in the table. R1 makes this reachable — removing the floor predicate
    also removed a planner input, which is what can flip the plan onto that
    index. Asserting the call site, not the constant.
    """

    @pytest.mark.asyncio
    async def test_probes_are_set_on_the_same_connection_before_the_query(self) -> None:
        pool, conn = _pool_with_connection()
        repo = MemoryRepository(pool)

        await repo.search(1, [0.1] * 768)

        set_calls = [c for c in conn.execute.call_args_list if "set_config" in str(c.args[0])]
        assert len(set_calls) == 1, "probes must be set exactly once per search"
        stmt, value = set_calls[0].args[0], set_calls[0].args[1]
        assert "ivfflat.probes" in stmt
        # is_local=true — must not leak onto a pooled connection.
        assert stmt.strip().endswith("true)")
        assert value == "100", "probes must equal the index's `lists` for an exact scan"
        # The query runs on that same connection, inside that transaction.
        assert conn.fetch.call_count == 1
        assert conn.transaction.call_count == 1

    @pytest.mark.asyncio
    async def test_probes_match_the_lists_the_migration_built(self) -> None:
        """An exact scan is probes == lists; drift between them is silent."""
        migration = pathlib.Path("alembic/versions/003_rag_memory.py").read_text()

        assert "WITH (lists = 100)" in migration
        assert _IVFFLAT_PROBES == 100


class TestRepositoryDoesNotOwnTheThreshold:
    """R1: the floor left the repository, and must not drift back.

    It used to be applied in ``WHERE`` as ``1 - (embedding <=> $2) >= $3``,
    which is why sub-floor rows never reached ``retrieval_log``. If a future
    change re-adds the parameter, two floors exist — one in SQL and one in
    ``RAGMemoryService`` — and the log starts describing a different
    selection than the prompt received. A ``TypeError`` here is the cheapest
    place to notice.
    """

    @pytest.mark.asyncio
    async def test_repository_rejects_a_similarity_floor(self) -> None:
        pool, _conn = _pool_with_connection()
        repo = MemoryRepository(pool)

        with pytest.raises(TypeError):
            await repo.search(1, [0.1] * 768, min_similarity=0.7)  # type: ignore[call-arg]

    @pytest.mark.asyncio
    async def test_the_sql_carries_no_similarity_predicate(self) -> None:
        """Asserting the SQL, not just the signature.

        A floor could be re-introduced as a literal rather than a bind
        parameter, which the signature check above cannot see.
        """
        pool, conn = _pool_with_connection()
        repo = MemoryRepository(pool)

        await repo.search(1, [0.1] * 768)

        sql = conn.fetch.call_args.args[0]
        # The similarity expression must appear exactly once — in the SELECT
        # list. A floor, whether bound or hardcoded, needs a second occurrence
        # in WHERE. Counting is deliberate: a keyword search for ">=" trips
        # over `expires_at > NOW()` and says nothing about similarity.
        assert "AS similarity" in sql, "the column itself must still be selected"
        assert sql.count("1 - (embedding <=> $2)") == 1


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


class TestSearchResultIncludesSourceMessageId:
    """S3-2: ``source_message_id`` is carried through from the repository
    row into the dict ``search()`` returns.

    Needed so the eval harness can match a retrieved memory back to a
    case's ``expected_message_id_ranges`` for recall@k (S3-4) -- purely
    additive, the production pipeline reads this dict by key and never
    asked for this one.
    """

    @pytest.mark.asyncio
    async def test_source_message_id_is_passed_through(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = [
            {
                "id": 1,
                "content": "we meet at 5pm",
                "similarity": 0.9,
                "metadata": None,
                "created_at": datetime(2026, 5, 9, tzinfo=UTC),
                "source_message_id": 42,
            }
        ]
        service, _, _ = _make_service(repo=repo)

        results = await service.search(chat_id=1, query="hi", query_embedding=[0.1] * 768)

        assert results[0]["source_message_id"] == 42

    @pytest.mark.asyncio
    async def test_none_source_message_id_is_passed_through_as_none(self) -> None:
        """A memory stored with no ``source_message_id`` (S2-10 pending-embedding
        rows, or any store() call that omits it) must not be coerced into
        something else here -- ``None`` means "no linked message", not
        "unknown, guess"."""
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = [
            {
                "id": 1,
                "content": "we meet at 5pm",
                "similarity": 0.9,
                "metadata": None,
                "created_at": datetime(2026, 5, 9, tzinfo=UTC),
                "source_message_id": None,
            }
        ]
        service, _, _ = _make_service(repo=repo)

        results = await service.search(chat_id=1, query="hi", query_embedding=[0.1] * 768)

        assert results[0]["source_message_id"] is None


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

    @pytest.mark.asyncio
    async def test_explicit_zero_max_results_is_not_swallowed(self) -> None:
        """The case this class's docstring always claimed to cover but did not.

        `max_results or self._max_results` replaces an explicit 0 with the
        instance default, so a caller asking for "retrieve nothing this turn"
        silently gets the configured 5 and memories land in the prompt anyway.
        Verified to fail against the `or` form.
        """
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = []
        router = AsyncMock()
        router.generate_embedding.return_value = _make_embedding_result()
        service = RAGMemoryService(repo, router, min_similarity=0.7, max_results=5)

        await service.search(chat_id=1, query="hi", max_results=0)

        assert repo.search.call_args.kwargs["max_results"] == 0


class TestSearchBeforePassthrough:
    """S3-3: optional ``before`` time bound, passed straight through to the
    repository -- in ``WHERE`` ahead of ``LIMIT`` there (see
    ``MemoryRepository.search()``), not postfiltered here.

    No instance-level default exists to fall back to (unlike
    ``min_similarity``/``max_results``): omitting ``before`` must reach the
    repository as ``None`` (no bound), and does not change the production
    call path (``TextProcessingPipeline`` never passes it).
    """

    @pytest.mark.asyncio
    async def test_explicit_before_is_forwarded_to_repository(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = []
        router = AsyncMock()
        router.generate_embedding.return_value = _make_embedding_result()
        service, _, _ = _make_service(repo=repo, router=router)
        cutoff = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

        await service.search(chat_id=1, query="hi", before=cutoff)

        assert repo.search.call_args.kwargs["before"] == cutoff

    @pytest.mark.asyncio
    async def test_no_before_forwards_none(self) -> None:
        """Omitting ``before`` must reach the repository as ``None``, not be
        dropped from the call -- the repository's own default only helps
        callers that don't pass the kwarg at all; the service must not
        substitute anything else."""
        repo = AsyncMock(spec=MemoryRepository)
        repo.search.return_value = []
        router = AsyncMock()
        router.generate_embedding.return_value = _make_embedding_result()
        service, _, _ = _make_service(repo=repo, router=router)

        await service.search(chat_id=1, query="hi")

        assert repo.search.call_args.kwargs["before"] is None


class TestRepositorySearchBeforeDefault:
    """S3-3: ``MemoryRepository.search()``'s ``before`` is optional and
    additive -- omitting it must not change the SQL sent for existing
    callers (asserted at the call-args level; the WHERE-before-LIMIT
    ordering itself needs a real database, see
    ``tests/integration/test_memory_repository_chat_scoping.py`` for the
    established pattern of testing this repository's SQL against real rows).
    """

    @pytest.mark.asyncio
    async def test_omitting_before_still_executes(self) -> None:
        pool, conn = _pool_with_connection()
        repo = MemoryRepository(pool)

        result = await repo.search(1, [0.1] * 768)

        assert result == []
        # `before` positional arg (last bind param, $4 since R1) must be None.
        assert conn.fetch.call_args.args[-1] is None

    @pytest.mark.asyncio
    async def test_explicit_before_is_passed_as_last_bind_param(self) -> None:
        pool, conn = _pool_with_connection()
        repo = MemoryRepository(pool)
        cutoff = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

        await repo.search(1, [0.1] * 768, before=cutoff)

        assert conn.fetch.call_args.args[-1] == cutoff


class TestStoreEmbeddingFailure:
    """S2-10: ``store()`` when embedding generation fails.

    Supersedes the old S2-7b behavior (returned ``None`` and never touched
    the repository, permanently losing the memory on a provider outage).
    S2-1's honest no-fallback made Gemini the only embeddings provider, so
    an outage now persists the row with ``embedding=None`` (a "pending"
    marker) instead of dropping it -- ``EmbeddingBackfillWorker`` fills it
    in later (S2-11 data-preservation invariant).
    """

    @pytest.mark.asyncio
    async def test_embedding_failure_still_persists_with_null_embedding(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        repo.store.return_value = 7
        router = AsyncMock()
        router.generate_embedding.side_effect = RuntimeError("all providers failed")
        service, _, _ = _make_service(repo=repo, router=router)

        memory_id = await service.store(chat_id=1, content="hi")

        assert memory_id == 7
        repo.store.assert_awaited_once()
        assert repo.store.call_args.kwargs["embedding"] is None
        assert repo.store.call_args.kwargs["chat_id"] == 1
        assert repo.store.call_args.kwargs["content"] == "hi"

    @pytest.mark.asyncio
    async def test_embedding_failure_forwards_optional_fields_to_repository(self) -> None:
        repo = AsyncMock(spec=MemoryRepository)
        repo.store.return_value = 8
        router = AsyncMock()
        router.generate_embedding.side_effect = RuntimeError("all providers failed")
        service, _, _ = _make_service(repo=repo, router=router)

        await service.store(
            chat_id=1,
            content="hi",
            source_message_id=99,
            importance_score=0.9,
            metadata={"k": "v"},
        )

        call_kwargs = repo.store.call_args.kwargs
        assert call_kwargs["source_message_id"] == 99
        assert call_kwargs["importance_score"] == 0.9
        assert call_kwargs["metadata"] == {"k": "v"}
