"""Repository for chat_chunks -- the conversation-session RAG index (S4)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import asyncpg

from src.services.rag.models import Chunk


class ChunkRepository:
    """Data access for `chat_chunks` (migration 029).

    Write side from S4 (indexer, backfill); `search` is the S5 read side --
    hybrid vector + FTS fused by RRF. It landed together with the eval run
    that judged it rather than as a search method with no caller, which is
    why its knobs (`depth`, `rrf_k`, both weights) are arguments: the harness
    sweeps them, the pipeline passes the configured values.
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

    async def watermark(self, chat_id: int) -> int:
        """The newest message id this chat is indexed up to, 0 when empty.

        Chat-wide, because chunks are chat-wide: `message_thread_id` turned
        out to identify reply chains rather than forum topics (see
        `MessageRepository.get_for_chunking`), so there is no per-thread index
        to keep a per-thread watermark for. If forum-aware chunking ever
        lands, this becomes a `GROUP BY thread_id` and the column is already
        in the table.

        Derived from the rows rather than kept in a state table, so there is
        nothing that can disagree with what was actually written: a crash
        mid-backfill resumes exactly where the rows stop. Note what that does
        *not* give you -- deleting the newest chunk makes the indexer rebuild
        it, but deleting one from the middle does not, because the watermark
        is a maximum and never moved. Re-indexing a range means deleting
        everything from that point on.
        """
        value = await self._pool.fetchval(
            "SELECT coalesce(max(msg_to), 0) FROM chat_chunks WHERE chat_id = $1",
            chat_id,
        )
        return int(value or 0)

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

    # Repeated verbatim in three places below (the strict-match probe and both
    # retrieval legs) and composed by f-string per the "SQL composition via
    # local constants" ADR: hardcoded, no user input, so the S608 suppression
    # on the query is the documented form. Writing them out three times by hand
    # is the failure mode this avoids -- a bound applied to the vector leg but
    # not the FTS leg silently makes the two legs answer different questions,
    # and the fusion hides it by rank rather than raising.
    _CHUNK_FILTERS = """
              AND ($4::timestamptz IS NULL OR c.started_at >= $4)
              AND ($5::timestamptz IS NULL OR c.ended_at < $5)
              AND ($6::bigint[] IS NULL OR c.senders @> $6::bigint[])
    """

    async def search(
        self,
        chat_id: int,
        *,
        query_text: str,
        query_embedding: list[float] | None,
        limit: int = 5,
        depth: int | None = None,
        rrf_k: int = 60,
        vector_weight: float = 1.0,
        fts_weight: float = 1.0,
        after: datetime | None = None,
        before: datetime | None = None,
        senders: Sequence[int] | None = None,
    ) -> list[asyncpg.Record]:
        """Hybrid retrieval over one chat's chunks: vector + FTS, fused by RRF (S5).

        Two independent legs rank the same rows and Reciprocal Rank Fusion
        combines them by *rank*, never by score -- cosine distance and
        `ts_rank_cd` are differently-scaled quantities and any attempt to add
        them directly is a hidden weighting that changes whenever either scale
        moves. RRF's contribution is `weight / (rrf_k + rank)`, so the knobs
        that matter are the two weights, and they are arguments rather than
        constants because S6 tunes them against the eval set.

        **Why both legs.** The vector leg finds a paraphrase the group never
        typed; the FTS leg finds the rare literal token an embedding smooths
        away -- names, in-jokes, model numbers, misspellings. On this corpus
        that second class is most of what people actually ask about, which is
        why the plan (§4.2) rejected a vector-only cutover.

        **Both legs are `MATERIALIZED`**, and the honest reason is narrower
        than the plan's. Measured against the real 2841-chunk corpus, removing
        the keyword returns *the same rows in the same order* and costs the
        same (medians 3.7ms vs 3.1ms over five alternating runs; a single cold
        run showed 23ms vs 3.6ms and was cache, not the query). It cannot
        change the ranking: `row_number() OVER (ORDER BY ...)` computes over
        its own subquery's rows whether PostgreSQL inlines the CTE or scans it,
        so there is no ordering for inlining to disturb.

        What it does buy is a pinned plan shape. Each leg is referenced exactly
        once, so PG12+ is free to inline both, and the choice it makes is a
        function of table size -- which here is a few thousand rows today and
        an unknown number later. The keyword costs nothing measurable and
        removes one thing that can silently change under the query.

        Said plainly because a mutation run deleting it breaks no test, and a
        guard whose justification is stronger than its evidence is how the
        migration's ё-normalisation claim got written.

        `depth` is how deep each leg goes before fusion, defaulting to
        `2 * limit` (plan §4.2 requires >= 2x): fusing two top-`limit` lists can
        only ever return rows some leg already had in its top `limit`, which
        throws away RRF's main trick -- a row ranked 7th by both legs beating a
        row ranked 1st by one and 400th by the other.

        `query_embedding=None` is legal and means "FTS only". That is the
        state after an embedding-API failure, and answering from the lexical
        leg alone is strictly better than the alternative the Q&A path takes
        (retrieve nothing at all). Callers can tell which happened: `similarity`
        comes back NULL on every row.

        A **weight of zero switches its leg off entirely** rather than merely
        scoring it zero. The difference is not cosmetic: a zero-weighted leg
        that still contributes candidates pads the result with `rrf_score = 0`
        rows, which the caller has no reason to distrust and would inject. It
        also makes an ablation sweep meaningless -- "retrieval without the FTS
        leg" would still be answering out of the FTS leg's candidate set.

        The returned `similarity` is plain cosine against the query vector,
        computed for the whole fused set -- including rows only the FTS leg
        found. RRF score ranks; cosine is what a *floor* can be calibrated on
        (S6), because it means the same thing across queries while an RRF score
        does not: a row no second leg corroborates caps at `1 / (rrf_k + 1)`
        no matter how good it is.

        The `translate($7, 'ёЁ', 'еЕ')` mirrors the generated column's own
        expression, and mirroring it is the whole reason it is there: on the
        `russian` configuration both are no-ops (PostgreSQL folds ё→е itself --
        measured, see migration 029), so what this line buys is that the two
        sides stay one edit apart rather than two.

        `senders` filters with `@>`, i.e. **contains all of** -- a two-name
        list returns only chunks where both people spoke, not either. That is
        the plan's choice (§4.2) and it is the useful one for "what did A and B
        decide", but it is the opposite of what `&&` would do, so it is said
        here rather than left to be discovered from a query that quietly
        returns too little.

        `before` bounds by `ended_at`, i.e. only chunks that had finished by
        that moment -- the eval harness replaying a historical question must not
        be handed the chunk the question itself sits in, which is the chunk that
        most often contains its answer too (S3-3's self-retrieval trap, one
        table over).
        """
        if senders is not None and not senders:
            # `senders @> '{}'::bigint[]` is TRUE for every row -- the empty
            # array is contained in everything -- so an empty list would read
            # as "no filter" while the caller meant "from nobody". A retrieval
            # filter that silently widens is the fail-open shape worth
            # refusing: the caller gets every chunk in the chat and no
            # indication that its filter did nothing. `None` is how you say
            # "no filter", and it is a different value.
            raise ValueError(
                "senders=[] would match every chunk (an empty array is contained "
                "in every array). Pass None for 'no sender filter'."
            )
        depth = depth if depth is not None else limit * 2
        sql = f"""
            WITH tsq AS MATERIALIZED (
                SELECT
                    CASE WHEN strict_matches OR NOT relaxable
                         THEN q_strict ELSE q_relaxed END AS q,
                    (NOT strict_matches AND relaxable
                        AND q_relaxed <> q_strict) AS relaxed
                FROM (
                    SELECT
                        q_strict,
                        replace(q_strict::text, '&', '|')::tsquery AS q_relaxed,
                        position('!' in q_strict::text) = 0 AS relaxable,
                        EXISTS (
                            SELECT 1 FROM chat_chunks c
                            WHERE c.chat_id = $1
                              AND c.tsv @@ q_strict
                              {self._CHUNK_FILTERS}
                        ) AS strict_matches
                    FROM (
                        SELECT websearch_to_tsquery(
                            'russian', translate($7, 'ёЁ', 'еЕ')
                        ) AS q_strict
                    ) s
                ) t
            ),
            vec AS MATERIALIZED (
                SELECT c.id,
                       row_number() OVER (
                           ORDER BY c.embedding <=> $2::vector, c.id
                       ) AS rank
                FROM chat_chunks c
                WHERE c.chat_id = $1
                  AND $8::float > 0
                  AND $2::vector IS NOT NULL
                  AND c.embedding IS NOT NULL
                  {self._CHUNK_FILTERS}
                ORDER BY c.embedding <=> $2::vector, c.id
                LIMIT $3
            ),
            fts AS MATERIALIZED (
                SELECT c.id,
                       row_number() OVER (
                           ORDER BY ts_rank_cd(c.tsv, tsq.q) DESC, c.id
                       ) AS rank
                FROM chat_chunks c, tsq
                WHERE c.chat_id = $1
                  AND $9::float > 0
                  AND c.tsv @@ tsq.q
                  {self._CHUNK_FILTERS}
                ORDER BY ts_rank_cd(c.tsv, tsq.q) DESC, c.id
                LIMIT $3
            ),
            fused AS (
                SELECT id,
                       sum(score) AS rrf_score,
                       max(vec_rank) AS vec_rank,
                       max(fts_rank) AS fts_rank
                FROM (
                    SELECT id, $8::float / ($10::float + rank) AS score,
                           rank AS vec_rank, NULL::bigint AS fts_rank
                    FROM vec
                    UNION ALL
                    SELECT id, $9::float / ($10::float + rank) AS score,
                           NULL::bigint AS vec_rank, rank AS fts_rank
                    FROM fts
                ) legs
                GROUP BY id
            )
            SELECT c.id, c.chat_id, c.content, c.msg_from, c.msg_to,
                   c.msg_count, c.senders, c.started_at, c.ended_at,
                   f.rrf_score, f.vec_rank, f.fts_rank,
                   CASE
                       WHEN $2::vector IS NOT NULL AND c.embedding IS NOT NULL
                       THEN 1 - (c.embedding <=> $2::vector)
                   END AS similarity,
                   (SELECT relaxed FROM tsq) AS fts_relaxed
            FROM fused f
            JOIN chat_chunks c ON c.id = f.id
            ORDER BY f.rrf_score DESC, c.id
            LIMIT $11
        """  # noqa: S608
        rows: list[asyncpg.Record] = await self._pool.fetch(
            sql,
            chat_id,
            query_embedding,
            depth,
            after,
            before,
            list(senders) if senders is not None else None,
            query_text,
            vector_weight,
            fts_weight,
            rrf_k,
            limit,
        )
        return rows
