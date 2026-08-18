"""Repository for chat_chunks -- the conversation-session RAG index (S4)."""

from __future__ import annotations

from collections.abc import Sequence

import asyncpg

from src.services.rag.models import Chunk


class ChunkRepository:
    """Data access for `chat_chunks` (migration 029).

    Writes only, for now: nothing reads chunks until S5 flips retrieval onto
    them. The read side lands with the hybrid query it exists to serve, rather
    than as a search method with no caller that S5 would then have to rewrite.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert_many(self, chunks: Sequence[Chunk]) -> int:
        """Insert chunks, skipping ones already indexed. Returns how many were new.

        `ON CONFLICT DO NOTHING` on the natural key is the whole idempotency
        story: re-running the indexer over the same closed sessions is a
        no-op, which is what makes both a crashed backfill and an overlapping
        watermark safe to simply repeat.

        One transaction per batch so a mid-batch failure cannot leave a
        session half-indexed -- a partially-indexed session looks exactly like
        a fully-indexed one to the watermark, and the missing tail would never
        be revisited.
        """
        if not chunks:
            return 0
        inserted = 0
        async with self._pool.acquire() as conn, conn.transaction():
            for chunk in chunks:
                row_id = await conn.fetchval(
                    """
                    INSERT INTO chat_chunks
                        (chat_id, thread_id, msg_from, msg_to, part, content,
                         msg_count, senders, started_at, ended_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::bigint[], $9, $10)
                    ON CONFLICT ON CONSTRAINT chat_chunks_natural_key DO NOTHING
                    RETURNING id
                    """,
                    chunk.chat_id,
                    chunk.thread_id,
                    chunk.msg_from,
                    chunk.msg_to,
                    chunk.part,
                    chunk.content,
                    chunk.msg_count,
                    list(chunk.senders),
                    chunk.started_at,
                    chunk.ended_at,
                )
                if row_id is not None:
                    inserted += 1
        return inserted

    async def watermarks(self, chat_id: int) -> dict[int | None, int]:
        """The newest indexed message id per thread, `{thread_id: msg_to}`.

        Per *thread*, not per chat: in a forum, thread B's history can sit
        entirely below thread A's newest message, and a single chat-wide
        watermark would declare it already indexed. Derived from the rows
        themselves rather than kept in a state table -- there is then no way
        for the watermark and the index to disagree, and a manually deleted
        chunk is simply re-created on the next pass.
        """
        rows = await self._pool.fetch(
            """
            SELECT thread_id, MAX(msg_to) AS watermark
            FROM chat_chunks
            WHERE chat_id = $1
            GROUP BY thread_id
            """,
            chat_id,
        )
        return {row["thread_id"]: int(row["watermark"]) for row in rows}

    async def get_pending_embeddings(
        self, limit: int, *, exclude_ids: list[int] | None = None
    ) -> list[asyncpg.Record]:
        """Chunks written but not yet embedded, oldest first.

        Same contract as `MemoryRepository.get_pending_embeddings`, including
        `exclude_ids`: the queue is FIFO, so one row the embedding API always
        rejects would otherwise sit at its head forever and starve everything
        behind it. This table is out of retention, so such a row never ages
        out on its own either.
        """
        result: list[asyncpg.Record] = await self._pool.fetch(
            """
            SELECT id, chat_id, content
            FROM chat_chunks
            WHERE embedding IS NULL
              AND NOT (id = ANY($2::bigint[]))
            ORDER BY id ASC
            LIMIT $1
            """,
            limit,
            exclude_ids or [],
        )
        return result

    async def update_embedding(
        self,
        chunk_id: int,
        embedding: list[float],
        *,
        model: str,
        task_type: str,
    ) -> None:
        """Fill in a pending chunk's vector, recording how it was produced.

        `model` and `task_type` are written together with the vector and never
        separately: a row whose recorded provenance does not match the space
        its vector lives in is worse than a row with no provenance, because a
        future migration would trust it and skip the row.

        `AND embedding IS NULL` guards against two passes racing on the same
        row (the worker is single-threaded today; a second process is not
        prevented by anything but this).
        """
        await self._pool.execute(
            """
            UPDATE chat_chunks
            SET embedding = $2, emb_model = $3, emb_task_type = $4
            WHERE id = $1 AND embedding IS NULL
            """,
            chunk_id,
            embedding,
            model,
            task_type,
        )

    async def counts(self, chat_id: int | None = None) -> dict[str, int]:
        """`{"total": N, "pending": M}` -- for the indexer's log line and for
        the coverage probe that answers "how much of this chat is indexed"."""
        row = await self._pool.fetchrow(
            """
            SELECT count(*)::int AS total,
                   count(*) FILTER (WHERE embedding IS NULL)::int AS pending
            FROM chat_chunks
            WHERE $1::bigint IS NULL OR chat_id = $1
            """,
            chat_id,
        )
        if row is None:  # pragma: no cover - aggregate always returns a row
            return {"total": 0, "pending": 0}
        return {"total": int(row["total"]), "pending": int(row["pending"])}
