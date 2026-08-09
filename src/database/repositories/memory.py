"""Repository for chat_memory (RAG) table."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg


class MemoryRepository:
    """Data access layer for RAG vector memory."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def store(
        self,
        chat_id: int,
        content: str,
        embedding: list[float] | None,
        *,
        source_message_id: int | None = None,
        importance_score: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Store a memory. Returns the new record ID.

        ``embedding=None`` (S2-10) persists the row with a NULL vector --
        the natural "pending" marker for ``EmbeddingBackfillWorker`` to fill
        in later, used when the caller's embedding call failed but the
        content itself must not be lost (S2-11 data-preservation invariant).
        ``search()`` already excludes NULL-embedding rows, so a pending row
        is simply invisible to retrieval until backfilled.
        """
        row = await self._pool.fetchrow(
            """
            INSERT INTO chat_memory
                (chat_id, content, embedding, source_message_id,
                 importance_score, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            RETURNING id
            """,
            chat_id,
            content,
            embedding,
            source_message_id,
            importance_score,
            None if metadata is None else json.dumps(metadata),
        )
        return int(row["id"])

    async def search(
        self,
        chat_id: int,
        query_embedding: list[float],
        *,
        min_similarity: float,
        max_results: int = 5,
        before: datetime | None = None,
    ) -> list[asyncpg.Record]:
        """Search memories by cosine similarity.

        ``min_similarity`` has no default (S2-2): the config YAML is the
        single source of truth for the threshold, and this repository method
        must not be able to silently apply a different one than
        ``RAGMemoryService`` (its only caller) resolves it to.

        ``before`` (S3-3) is an optional time bound: when given, only
        memories created strictly before that moment are eligible. It is
        applied in ``WHERE``, ahead of ``LIMIT`` -- postfiltering after the
        query would silently shrink ``k`` (rows past the cutoff would still
        occupy a LIMIT slot and get dropped afterwards, understating the
        true top-k). Default ``None`` means "no bound", matching current
        production behavior; the only production caller
        (``TextProcessingPipeline._timed_rag_search``) does not pass it.

        ``source_message_id`` is now selected alongside the existing columns
        (S3-2): the eval harness needs it to match a retrieved memory back to
        the case's ``expected_message_id_ranges`` for recall@k. Purely
        additive to the SELECT list -- WHERE/ORDER/LIMIT are unchanged, and
        the only production caller reads results by key, so an extra key is
        invisible to it.
        """
        result: list[asyncpg.Record] = await self._pool.fetch(
            """
            SELECT id, content, metadata, importance_score, source_message_id,
                   1 - (embedding <=> $2) AS similarity,
                   created_at
            FROM chat_memory
            WHERE chat_id = $1
              AND embedding IS NOT NULL
              AND 1 - (embedding <=> $2) >= $3
              AND (expires_at IS NULL OR expires_at > NOW())
              AND ($5::timestamptz IS NULL OR created_at < $5::timestamptz)
            ORDER BY embedding <=> $2 ASC
            LIMIT $4
            """,
            chat_id,
            query_embedding,
            min_similarity,
            max_results,
            before,
        )
        return result

    async def delete(self, memory_id: int, *, chat_id: int) -> None:
        """Delete a specific memory entry (scoped to chat for access control)."""
        await self._pool.execute(
            "DELETE FROM chat_memory WHERE id = $1 AND chat_id = $2",
            memory_id,
            chat_id,
        )

    async def get_pending_embeddings(
        self, limit: int, *, exclude_ids: list[int] | None = None
    ) -> list[asyncpg.Record]:
        """Rows awaiting a backfilled embedding (S2-10).

        Oldest first. On its own that ordering does NOT protect the backlog:
        a row that fails *deterministically* (content the model always
        rejects, a lasting wrong-dimension response) stays NULL and, being
        the oldest, is re-fetched at the head of every pass forever. Once
        `limit` such rows accumulate, nothing written after them is ever
        reached — and ADR-0011 keeps `chat_memory` out of retention, so they
        never age out either. `exclude_ids` is how the caller retires them:
        `EmbeddingBackfillWorker` parks a row after repeated failures and
        passes the parked ids here, which moves the queue on.
        """
        result: list[asyncpg.Record] = await self._pool.fetch(
            """
            SELECT id, chat_id, content
            FROM chat_memory
            WHERE embedding IS NULL
              AND NOT (id = ANY($2::bigint[]))
            ORDER BY created_at ASC
            LIMIT $1
            """,
            limit,
            exclude_ids or [],
        )
        return result

    async def update_embedding(self, memory_id: int, embedding: list[float]) -> None:
        """Fill in a previously-pending row's embedding (S2-10).

        ``AND embedding IS NULL`` guards against clobbering a row that a
        concurrent backfill pass (or a fresh ``store()`` call reusing the
        same id, which cannot happen with ``BIGSERIAL`` but costs nothing to
        guard against) already filled in.
        """
        await self._pool.execute(
            "UPDATE chat_memory SET embedding = $2 WHERE id = $1 AND embedding IS NULL",
            memory_id,
            embedding,
        )
