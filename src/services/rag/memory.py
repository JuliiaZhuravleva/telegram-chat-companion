"""RAG memory service — vector search and storage."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from src.database.repositories.memory import MemoryRepository
from src.services.ai.router import AIRouter
from src.services.retrieval_floor import rows_above_floor

logger = structlog.get_logger(__name__)

# S2-1: chat_memory.embedding is vector(768) (alembic/versions/003_rag_memory.py).
# Embeddings has no fallback provider (config/default.yml) after this item, so a
# wrong-length vector should only happen on a provider bug or future config
# drift -- kept as a cheap, explicit guard so a mismatch fails loud in
# application code (with the offending provider/model logged) instead of as a
# raw asyncpg error from pgvector's own dimension check.
EXPECTED_EMBEDDING_DIMENSIONS = 768


class RAGMemoryService:
    """Retrieval-Augmented Generation memory using pgvector.

    Stores and retrieves contextual memories for each chat using
    embedding-based cosine similarity search.
    """

    def __init__(
        self,
        memory_repo: MemoryRepository,
        ai_router: AIRouter,
        *,
        min_similarity: float,
        max_results: int = 5,
    ) -> None:
        """Construct the service.

        ``min_similarity`` has no default here on purpose (S2-2): the config
        YAML (``rag.min_similarity``, wired through ``settings.rag`` in
        ``src/di.py``) is the single source of truth for the threshold. A
        constructor default would silently diverge from it for any caller
        that forgets to pass it explicitly (tests, future call sites).
        """
        self._repo = memory_repo
        self._ai_router = ai_router
        self._min_similarity = min_similarity
        self._max_results = max_results

    @property
    def min_similarity(self) -> float:
        """Effective similarity floor (read by retrieval_log params)."""
        return self._min_similarity

    @property
    def max_results(self) -> int:
        """Effective result cap (read by retrieval_log params)."""
        return self._max_results

    async def search(
        self,
        chat_id: int,
        query: str,
        *,
        query_embedding: list[float] | None = None,
        min_similarity: float | None = None,
        max_results: int | None = None,
        before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Memories relevant to a query -- only those clearing the floor.

        The filtering moved here from the repository's ``WHERE`` in R1;
        callers that want to *see* what was rejected (the pipeline, so it can
        log the near-misses a future re-calibration needs) call
        ``search_unfiltered`` instead.

        The contract is unchanged for every floor the bot runs with, but not
        literally for all of them: at ``min_similarity <= 0`` the old SQL still
        applied ``1 - (embedding <=> $2) >= 0``, which cut negatively-scored
        rows, while ``rows_above_floor`` treats a non-positive floor as *no
        floor* and keeps them. That is the intended reading of "set it to 0.0
        to retrieve everything", and it is the value a floor sweep uses as its
        baseline — but it is a behaviour change, not a refactor, so it is said
        out loud rather than buried.

        ``query_embedding``, if given, is used as-is instead of embedding
        ``query`` again (S2-4): the pipeline computes one shared query
        embedding for RAG + KB per turn and passes it here to avoid a
        second ``generate_embedding()`` call for the same text. ``query``
        is still required in that case (kept for logging) but not
        re-embedded.

        ``before`` (S3-3), if given, bounds results to memories created
        strictly before that moment -- passed straight through to
        ``MemoryRepository.search()``, which applies it in ``WHERE`` ahead
        of ``LIMIT``. Default ``None`` (no bound); the production pipeline
        does not pass it, so this is additive and does not change prod
        behavior. Intended consumer: the eval harness (S3-2), replaying a
        historical question must only see memories that existed at
        ``asked_at`` -- without the bound, the memory of the question itself
        ("X asked: ...") is typically the top hit, and the replay circularly
        measures self-retrieval.
        """
        # `x or default` would silently fall back to the instance default for
        # an explicit falsy override (min_similarity=0.0 is a valid "accept
        # everything" threshold; max_results=0 is a valid "retrieve nothing
        # this turn") — S2-2. Both arguments get the same treatment; applying
        # it to only one of them is what review caught here.
        floor = min_similarity if min_similarity is not None else self._min_similarity
        memories = await self.search_unfiltered(
            chat_id,
            query,
            query_embedding=query_embedding,
            max_results=max_results,
            before=before,
        )
        return rows_above_floor(memories, floor)

    async def search_unfiltered(
        self,
        chat_id: int,
        query: str,
        *,
        query_embedding: list[float] | None = None,
        max_results: int | None = None,
        before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """The top-k nearest memories, floor or no floor (R1).

        Exists so the caller can record what retrieval *considered*, not only
        what it accepted. Until R1 the floor was applied in SQL, so a turn
        that injected nothing wrote an empty ``retrieval_log`` row and the
        near-misses behind it were unrecoverable — including the case that
        motivated this whole revision, where the best match sat at 0.675
        against a 0.7 floor and nothing in the data said so.

        Same argument handling as ``search`` minus the floor; see there.
        """
        if query_embedding is not None:
            embedding = query_embedding
        else:
            try:
                embedding_result = await self._ai_router.generate_embedding(query, chat_id=chat_id)
            except Exception:
                logger.warning("Failed to generate query embedding for RAG search")
                return []
            embedding = embedding_result.embedding

        rows = await self._repo.search(
            chat_id=chat_id,
            query_embedding=embedding,
            max_results=(max_results if max_results is not None else self._max_results),
            # `before` has no instance-level default to fall back to -- `None`
            # unconditionally means "no bound" (S3-3). Written as an explicit
            # pass-through (not `before or None`) to match the S2-2 convention
            # above rather than introduce a second style for a new parameter.
            before=before,
        )

        return [
            {
                "id": row["id"],
                "content": row["content"],
                "similarity": float(row["similarity"]),
                "metadata": row["metadata"],
                "created_at": row["created_at"],
                # S3-2: carried through so the eval harness can match a hit
                # back to a case's expected_message_id_ranges. The pipeline
                # (this dict's other consumer) reads by key and never asked
                # for this one, so its behavior is unaffected.
                "source_message_id": row["source_message_id"],
            }
            for row in rows
        ]

    async def store(
        self,
        chat_id: int,
        content: str,
        *,
        source_message_id: int | None = None,
        importance_score: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        """Store a memory with its embedding. Returns the memory ID.

        S2-10: a failed embedding *call* (provider outage -- Gemini is the
        only provider for embeddings since S2-1's honest no-fallback) no
        longer drops the memory. Previously this meant silent, permanent
        data loss: the content was never written at all. Now the row is
        persisted with ``embedding=None`` (a NULL vector, invisible to
        ``search()`` until filled in) and ``EmbeddingBackfillWorker``
        retries it later -- satisfying the S2-11 data-preservation
        invariant.

        Returns ``None`` only for the wrong-dimensionality guard below
        (S2-1) -- a provider/config bug distinct from an outage, where
        refusing to store is the deliberate behavior, not something S2-10
        changes.
        """
        try:
            embedding_result = await self._ai_router.generate_embedding(content, chat_id=chat_id)
        except Exception:
            logger.warning("Failed to generate embedding for RAG store, storing as pending")
            return await self._repo.store(
                chat_id=chat_id,
                content=content,
                embedding=None,
                source_message_id=source_message_id,
                importance_score=importance_score,
                metadata=metadata,
            )

        actual_dimensions = len(embedding_result.embedding)
        if actual_dimensions != EXPECTED_EMBEDDING_DIMENSIONS:
            logger.warning(
                "Embedding has unexpected dimensionality, refusing to store",
                expected=EXPECTED_EMBEDDING_DIMENSIONS,
                actual=actual_dimensions,
                provider=embedding_result.provider,
                model=embedding_result.model,
            )
            return None

        return await self._repo.store(
            chat_id=chat_id,
            content=content,
            embedding=embedding_result.embedding,
            source_message_id=source_message_id,
            importance_score=importance_score,
            metadata=metadata,
        )

    async def delete(self, memory_id: int, *, chat_id: int) -> None:
        """Delete a specific memory (scoped to chat for access control)."""
        await self._repo.delete(memory_id, chat_id=chat_id)
