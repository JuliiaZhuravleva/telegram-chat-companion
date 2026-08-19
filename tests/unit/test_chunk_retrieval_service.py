"""Tests for `ChunkRetrievalService` -- the thin half of chunk retrieval (S5).

The SQL is tested against a real PostgreSQL in
`tests/integration/test_chunk_search.py`; what is left here is everything the
service decides *around* it, and each item is something that has already gone
wrong once in this codebase's history:

* falsy overrides swallowed by `x or default` (S2-2, fixed twice already);
* a floor applied in two places that drift apart (R1, and why
  `rows_above_floor` is one shared module);
* an embedding failure taking the whole turn down rather than the vector leg;
* a `task_type` that silently changes the embedding space (S4's measurement).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from src.database.repositories.chunks import ChunkRepository
from src.services.ai.base import AIProviderError, EmbeddingResult
from src.services.rag.chunk_retrieval import QUERY_TASK_TYPE, ChunkRetrievalService

CHAT_ID = -100555000111


def _row(chunk_id: int, similarity: float | None) -> dict[str, object]:
    """One repository row, with every field the service copies out."""
    return {
        "id": chunk_id,
        "content": f"chunk {chunk_id}",
        "similarity": similarity,
        "rrf_score": 0.03,
        "vec_rank": 1,
        "fts_rank": None,
        "fts_relaxed": False,
        "msg_from": chunk_id * 10,
        "msg_to": chunk_id * 10 + 9,
        "msg_count": 5,
        "senders": [1, 2],
        "started_at": None,
        "ended_at": None,
    }


def _make_service(**kwargs: object) -> tuple[ChunkRetrievalService, AsyncMock, AsyncMock]:
    repo = AsyncMock(spec=ChunkRepository)
    repo.search.return_value = []
    router = AsyncMock()
    router.generate_embedding.return_value = EmbeddingResult(
        embedding=[0.1] * 768, model="mock-embed", provider="mock", dimensions=768
    )
    service = ChunkRetrievalService(repo, router, **kwargs)  # type: ignore[arg-type]
    return service, repo, router


class TestEmbedding:
    async def test_a_supplied_embedding_is_not_recomputed(self) -> None:
        """The pipeline computes one query vector per turn for RAG + KB and
        passes it down (TD-009/S2-4). Embedding again here would double the
        cost of every addressed message for no change in result."""
        service, repo, router = _make_service()
        vector = [0.5] * 768
        await service.search(CHAT_ID, "вопрос", query_embedding=vector)
        router.generate_embedding.assert_not_awaited()
        assert repo.search.await_args.kwargs["query_embedding"] is vector

    async def test_it_embeds_with_the_query_half_of_the_asymmetric_pair(self) -> None:
        """The index writes `RETRIEVAL_DOCUMENT`; the query side must ask for
        `RETRIEVAL_QUERY` or the two land in different spaces. Measured on
        gemini-embedding-001 the two are byte-identical to omitting it, so
        nothing observable breaks today -- which is exactly why an assertion
        is the only thing that will notice when that stops being true."""
        service, repo, router = _make_service()
        await service.search(CHAT_ID, "вопрос")
        assert router.generate_embedding.await_args.kwargs["task_type"] == QUERY_TASK_TYPE
        assert QUERY_TASK_TYPE == "RETRIEVAL_QUERY"

    async def test_an_embedding_failure_degrades_to_the_lexical_leg(self) -> None:
        """The Q&A path returns `[]` here and the turn answers blind. Chunks
        have a second leg that needs no vector, so the failure costs ranking
        quality, not the retrieval."""
        service, repo, router = _make_service()
        router.generate_embedding.side_effect = AIProviderError("provider down", "mock")
        repo.search.return_value = [_row(1, None)]
        hits = await service.search(CHAT_ID, "пароль от вайфая")
        assert repo.search.await_count == 1
        assert repo.search.await_args.kwargs["query_embedding"] is None
        assert [h["id"] for h in hits] == [1]

    async def test_a_floor_rejects_everything_when_there_was_no_embedding(self) -> None:
        """Degrading to FTS-only means no similarity, and a row that cannot be
        *shown* to clear a floor must not be treated as clearing it. The caller
        gets an honest empty result rather than unscored rows."""
        service, repo, router = _make_service(min_similarity=0.5)
        router.generate_embedding.side_effect = AIProviderError("provider down", "mock")
        repo.search.return_value = [_row(1, None), _row(2, None)]
        assert await service.search(CHAT_ID, "пароль") == []


class TestOverrides:
    async def test_max_results_zero_is_honoured_not_replaced(self) -> None:
        """`x or default` turns an explicit 0 back into the instance default.
        That has been the bug twice in this subsystem (S2-2), so it is asserted
        rather than assumed."""
        service, repo, _ = _make_service(max_results=5)
        await service.search(CHAT_ID, "q", query_embedding=[0.1] * 768, max_results=0)
        assert repo.search.await_args.kwargs["limit"] == 0

    async def test_min_similarity_zero_is_honoured_not_replaced(self) -> None:
        service, repo, _ = _make_service(min_similarity=0.8)
        repo.search.return_value = [_row(1, 0.2), _row(2, 0.1)]
        hits = await service.search(CHAT_ID, "q", query_embedding=[0.1] * 768, min_similarity=0.0)
        assert [h["id"] for h in hits] == [1, 2]

    async def test_the_instance_floor_applies_when_no_override_is_given(self) -> None:
        service, repo, _ = _make_service(min_similarity=0.5)
        repo.search.return_value = [_row(1, 0.9), _row(2, 0.4)]
        hits = await service.search(CHAT_ID, "q", query_embedding=[0.1] * 768)
        assert [h["id"] for h in hits] == [1]

    async def test_the_default_floor_is_no_floor(self) -> None:
        """Unlike `RAGMemoryService`, this service defaults to 0.0 on purpose:
        no calibrated floor exists for this store yet, and importing the 0.7
        derived on `chat_memory` would carry a number across the scale shift
        `docs/rag-eval-baseline.md` measured between the two."""
        service, repo, _ = _make_service()
        assert service.min_similarity == 0.0
        repo.search.return_value = [_row(1, 0.01)]
        hits = await service.search(CHAT_ID, "q", query_embedding=[0.1] * 768)
        assert [h["id"] for h in hits] == [1]


class TestDepth:
    async def test_each_leg_goes_deeper_than_the_caller_asked_for(self) -> None:
        """Fusing two top-k lists can only return rows some leg already had in
        its top k, which discards the one thing RRF is for."""
        service, repo, _ = _make_service(max_results=5, depth_multiplier=2)
        await service.search(CHAT_ID, "q", query_embedding=[0.1] * 768)
        kwargs = repo.search.await_args.kwargs
        assert kwargs["limit"] == 5
        assert kwargs["depth"] == 10

    async def test_depth_follows_a_per_call_max_results(self) -> None:
        """Depth is derived from the effective limit, not the instance one --
        otherwise a per-call `max_results` silently changes the ratio."""
        service, repo, _ = _make_service(max_results=5, depth_multiplier=3)
        await service.search(CHAT_ID, "q", query_embedding=[0.1] * 768, max_results=2)
        assert repo.search.await_args.kwargs["depth"] == 6


class TestLoggingParams:
    def test_params_name_every_knob_that_shaped_the_result(self) -> None:
        """`retrieval_log.params` is what a later calibration reads. A log that
        recorded only "chunks" could not tell a floor change from a weight
        change months afterwards, and both will have happened."""
        service, _, _ = _make_service(
            max_results=7, min_similarity=0.3, rrf_k=42, vector_weight=2.0, fts_weight=0.5
        )
        assert service.params == {
            "backend": "chunks",
            "max_results": 7,
            "min_similarity": 0.3,
            "rrf_k": 42,
            "vector_weight": 2.0,
            "fts_weight": 0.5,
            "depth_multiplier": 2,
        }

    async def test_the_weights_reach_the_query(self) -> None:
        """A knob wired to nothing is worse than no knob: an S6 sweep over it
        reports 'no effect found' and the conclusion is drawn anyway."""
        service, repo, _ = _make_service(rrf_k=42, vector_weight=2.0, fts_weight=0.5)
        await service.search(CHAT_ID, "q", query_embedding=[0.1] * 768)
        kwargs = repo.search.await_args.kwargs
        assert kwargs["rrf_k"] == 42
        assert kwargs["vector_weight"] == 2.0
        assert kwargs["fts_weight"] == 0.5


class TestSignatureCompatibility:
    def test_it_accepts_everything_the_harness_passes_to_either_store(self) -> None:
        """`scripts/eval_rag.py` swaps the two services by name, so a missing
        keyword here would surface as a TypeError mid-run, after the provider
        calls for earlier cases are already paid for."""
        import inspect

        from src.services.rag.memory import RAGMemoryService

        chunk_params = inspect.signature(ChunkRetrievalService.search).parameters
        memory_params = inspect.signature(RAGMemoryService.search).parameters
        assert set(memory_params) <= set(chunk_params), (
            "ChunkRetrievalService.search must accept every argument "
            f"RAGMemoryService.search does; missing: {set(memory_params) - set(chunk_params)}"
        )
