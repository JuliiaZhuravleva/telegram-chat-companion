"""Retrieval over `chat_chunks` -- the read half of the chunk index (S5).

Sibling of `RAGMemoryService`, deliberately shaped the same way so that both
can be handed to the same caller: the eval harness swaps one for the other by
name, and the pipeline will do the same behind the `rag_backend` flag. What
differs is underneath -- `chat_memory` holds Q&A pairs from turns where the
bot replied (measured 4-8% of a live chat, and precisely the part that is
about the bot), while `chat_chunks` holds sessions of the actual conversation
over the whole of it.

The retrieval is hybrid: a vector leg and a full-text leg, fused by RRF inside
one SQL statement (`ChunkRepository.search`). This module is the thin part --
resolving defaults, embedding the query when the caller did not, and turning
records into dicts. Anything that decides *ranking* lives in the SQL, where it
can be tested against a real PostgreSQL rather than mocked.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from src.database.repositories.chunks import ChunkRepository
from src.services.ai.router import AIRouter
from src.services.retrieval_floor import rows_above_floor

logger = structlog.get_logger(__name__)

QUERY_TASK_TYPE = "RETRIEVAL_QUERY"
"""The query half of the asymmetric pair; the index side writes
`RETRIEVAL_DOCUMENT` (`indexer.INDEX_TASK_TYPE`).

Passed explicitly even though it changes nothing today: measured 2026-08-19,
`gemini-embedding-001` returns a **byte-identical** vector for an omitted
`task_type` and for `RETRIEVAL_QUERY`. So the shared per-turn embedding the
pipeline computes without a task type already lands in this space, and a
caller may pass it straight in. Naming it here is what keeps that true on
purpose rather than by luck -- the day the default changes, this line is the
one that has to be reconsidered, and it is findable.
"""

_RESULT_FIELDS = (
    "id",
    "content",
    "similarity",
    "rrf_score",
    "vec_rank",
    "fts_rank",
    "fts_relaxed",
    "msg_from",
    "msg_to",
    "msg_count",
    "senders",
    "started_at",
    "ended_at",
)
"""The repository columns copied onto every returned dict.

`vector_leg_skipped` is added by `search()` afterwards -- it describes the
call, not the row, and there is no column for it.
"""


class ChunkRetrievalService:
    """Hybrid search over one chat's conversation chunks."""

    def __init__(
        self,
        chunk_repo: ChunkRepository,
        ai_router: AIRouter,
        *,
        max_results: int = 5,
        min_similarity: float = 0.0,
        rrf_k: int = 60,
        vector_weight: float = 1.0,
        fts_weight: float = 1.0,
        depth_multiplier: int = 2,
    ) -> None:
        """Construct the service.

        `min_similarity` defaults to **0.0, i.e. no floor**, which is the
        opposite of `RAGMemoryService`'s deliberate no-default. The reason is
        that no honest floor exists for this store yet: the 0.7 both RAG and KB
        use was derived on `chat_memory`, whose documents are built out of the
        raw exchange and therefore share the query's boilerplate. Chunks are
        ordinary conversation and sit on a differently-offset scale --
        measured, see `docs/rag-eval-baseline.md` -- so importing 0.7 here
        would be carrying a number across the exact discontinuity that
        document warns about. S6 calibrates one from `retrieval_log`; until
        then the caller decides what to inject and everything is logged.

        `depth_multiplier` sets how deep each leg goes before fusion, as a
        multiple of `max_results`. The plan requires at least 2: fusing two
        top-`k` lists can only return rows some leg already had in its top `k`,
        which discards the one thing RRF is for.
        """
        self._repo = chunk_repo
        self._ai_router = ai_router
        self._max_results = max_results
        self._min_similarity = min_similarity
        self._rrf_k = rrf_k
        self._vector_weight = vector_weight
        self._fts_weight = fts_weight
        self._depth_multiplier = depth_multiplier

    @property
    def max_results(self) -> int:
        """Effective result cap (read by `retrieval_log` params)."""
        return self._max_results

    @property
    def min_similarity(self) -> float:
        """Effective similarity floor; 0.0 means none (read by `retrieval_log`)."""
        return self._min_similarity

    @property
    def params(self) -> dict[str, Any]:
        """Everything that shaped a result, for `retrieval_log.params`.

        Recorded per turn rather than read from config at analysis time,
        because the numbers are the tuning surface S6 sweeps: a log that says
        only "chunks" cannot tell a floor change from a weight change months
        later, and both will have happened.
        """
        return {
            "backend": "chunks",
            "max_results": self._max_results,
            "min_similarity": self._min_similarity,
            "rrf_k": self._rrf_k,
            "vector_weight": self._vector_weight,
            "fts_weight": self._fts_weight,
            "depth_multiplier": self._depth_multiplier,
        }

    async def search(
        self,
        chat_id: int,
        query: str,
        *,
        query_embedding: list[float] | None = None,
        min_similarity: float | None = None,
        max_results: int | None = None,
        before: datetime | None = None,
        after: datetime | None = None,
        senders: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Chunks relevant to `query`, best first.

        Signature matches `RAGMemoryService.search` on every argument that
        `scripts/eval_rag.py` and the pipeline pass, so the two are
        interchangeable at the call site -- that is what makes an A/B between
        the stores a flag rather than a fork.

        `query_embedding`, if given, is used as-is: the pipeline computes one
        shared query vector per turn for RAG + KB and must not pay for a
        second (TD-009/S2-4).

        Explicit falsy overrides are honoured rather than swallowed by
        `x or default`: `max_results=0` means "retrieve nothing this turn" and
        `min_similarity=0.0` means "no floor", both of which `or` would turn
        back into the instance default (S2-2).
        """
        floor = min_similarity if min_similarity is not None else self._min_similarity
        limit = max_results if max_results is not None else self._max_results

        degraded = False
        embedding = query_embedding
        if embedding is None:
            try:
                result = await self._ai_router.generate_embedding(
                    query, chat_id=chat_id, task_type=QUERY_TASK_TYPE
                )
                embedding = result.embedding
            except Exception as exc:
                # Degrade to the lexical leg rather than losing the turn. The
                # Q&A path returns nothing at all in this state; here the FTS
                # leg still answers.
                #
                # Deliberately broad, matching `RAGMemoryService`: narrowing to
                # `AIProviderError` looks tidier and does not hold. The Gemini
                # provider parses its response body outside its own try, so a
                # 200 with a malformed payload raises a bare `ValueError`
                # (`json.JSONDecodeError`) that the router's fallback loop does
                # not catch either -- and it would come straight out of here,
                # taking down the very turn this fallback exists to save.
                logger.warning(
                    "Chunk retrieval: query embedding failed, falling back to the FTS leg",
                    chat_id=chat_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                degraded = True

        rows = await self._repo.search(
            chat_id,
            query_text=query,
            query_embedding=embedding,
            limit=limit,
            depth=limit * self._depth_multiplier,
            rrf_k=self._rrf_k,
            vector_weight=self._vector_weight,
            fts_weight=self._fts_weight,
            after=after,
            before=before,
            senders=senders,
        )
        chunks = [{field: row[field] for field in _RESULT_FIELDS} for row in rows]
        # Say it on every row rather than leaving the caller to infer it from
        # `similarity is None`. A caller checking one field per row is easy to
        # get right; a caller that has to notice the *absence* of a score on
        # every row, and understand why, is not -- and `retrieval_log` needs a
        # field it can filter degraded turns out by when S6 calibrates a floor
        # on that data.
        for chunk in chunks:
            chunk["vector_leg_skipped"] = degraded
        return rows_above_floor(chunks, floor)
