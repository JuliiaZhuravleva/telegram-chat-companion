"""Repository for chat_memory (RAG) table."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg

# pgvector's ivfflat index is APPROXIMATE: it scans `probes` of its `lists`
# partitions. Migration 003 built this one with `lists = 100` and nothing ever
# set `probes`, whose default is 1.
#
# Two things make that dangerous here specifically, and R1 is what brings them
# together. First, `idx_chat_memory_embedding` carries no `chat_id`: an index
# scan on it takes the k globally-nearest rows and only then filters to the
# chat, so a chat whose memories all sit in unscanned partitions comes back
# empty while a strong match is in the table. Second, the floor predicate R1
# removed was also a planner input -- Postgres estimates that opaque float
# comparison at its default 1/3 selectivity, so dropping it makes the LIMIT-ed
# ivfflat path look ~3x cheaper and can flip a plan that used to be exact.
# Measured on a throwaway pgvector at ~5k rows / 10 chats: a 0.91-similarity
# memory was injected on 200/200 turns before the change and 110/200 after, and
# recovered to 200/200 with `enable_indexscan=off` -- i.e. the plan, not the
# code. Production is at ~3k rows today and still takes the exact per-chat
# bitmap path (EXPLAIN ANALYZE against the live database, 1.3 ms), so nothing
# regresses now; `chat_memory` is exempt from retention (ADR-0011), so it only
# grows toward the crossover.
#
# probes == lists is an exact scan, which removes the plan dependency rather
# than hoping the planner keeps choosing well. The cost argument differs from
# `chat_facts` (knowledge.py), where the table is tens of curated rows per
# chat; here it is thousands, so this is a real trade. It is worth making
# because the whole point of R1 is that the recorded near-miss distribution
# feeds the S6 floor calibration, and a calibration computed from an
# approximate candidate set is not reproducible. Revisit with `lists` if this
# table grows past the ~100k the migration sized it for, or when S4's chunk
# store replaces it.
_IVFFLAT_PROBES = 100


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
        max_results: int = 5,
        before: datetime | None = None,
    ) -> list[asyncpg.Record]:
        """The top ``max_results`` memories by cosine similarity, unfiltered.

        **No similarity floor here (R1).** It used to live in this ``WHERE``
        as ``1 - (embedding <=> $2) >= $3``, which meant a sub-floor row was
        never returned, never logged, and therefore never existed as far as
        any later analysis was concerned. On a turn that retrieved nothing,
        the data could not say whether the best match missed by 0.001 or by
        0.3 -- and `docs/plans/rag-revision-2026-08.md` §4.2 plans to
        "re-calibrate the floor from `retrieval_log` distributions", a
        calibration this implementation made impossible. The floor now lives
        in ``RAGMemoryService`` (see ``memories_above_floor``), which returns
        the filtered set to callers while the pipeline logs everything.

        The rows that reach a prompt are unchanged by the move, **given an
        exact scan** -- which is what ``_IVFFLAT_PROBES`` buys, and the
        qualifier is not decoration. Both orders then read the same
        ``ORDER BY similarity DESC`` sequence: filtering first and taking ``k``
        yields the same above-floor rows as taking ``k`` and filtering, and
        where fewer than ``k`` clear the floor the extra rows are sub-floor
        ones the caller drops. Stated unconditionally, as a first draft did,
        the claim is false -- an approximate scan produces a different
        sequence, and removing the floor predicate is itself one of the things
        that can provoke one. Fetching ``k`` unfiltered is also what makes the
        blind case measurable: when nothing clears the floor, all ``k``
        near-misses land in the log, which is exactly the population a
        re-tuning needs.

        ``before`` (S3-3) is an optional time bound: when given, only
        memories created strictly before that moment are eligible. It is
        applied in ``WHERE``, ahead of ``LIMIT`` -- postfiltering after the
        query would silently shrink ``k`` (rows past the cutoff would still
        occupy a LIMIT slot and get dropped afterwards, understating the
        true top-k). Default ``None`` means "no bound", matching current
        production behavior; the only production caller
        (``TextProcessingPipeline._timed_rag_search``) does not pass it.

        Undated rows stay eligible under ``before``. ``chat_memory.created_at``
        is nullable (migration 003 declares ``TIMESTAMPTZ DEFAULT NOW()``, no
        ``NOT NULL``) and ``prompt_builder._rag_section`` already treats such
        rows as reachable. A bare ``created_at < $4`` would evaluate to NULL
        for them -- not TRUE -- so they would drop out silently, and since the
        eval harness passes ``before`` on *every* case (``EvalCase.asked_at``
        is required), the measured recall would sag for a reason that has
        nothing to do with retrieval quality: exactly the distortion this
        parameter exists to prevent. Keeping them visible is also what matches
        production, which applies no bound at all. The self-retrieval the
        bound guards against cannot hide among them -- ``store()`` never
        writes ``created_at`` explicitly, so every row the bot itself creates
        is dated; undated rows can only arrive via bulk import.

        ``source_message_id`` is now selected alongside the existing columns
        (S3-2): the eval harness needs it to match a retrieved memory back to
        the case's ``expected_message_id_ranges`` for recall@k. Purely
        additive to the SELECT list -- WHERE/ORDER/LIMIT are unchanged, and
        the only production caller reads results by key, so an extra key is
        invisible to it.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            # Transaction-local (`is_local=true`), so it never leaks onto a
            # pooled connection some later query will reuse. Set through
            # set_config() rather than `SET LOCAL` because only the function
            # form accepts a bind parameter.
            await conn.execute(
                "SELECT set_config('ivfflat.probes', $1, true)", str(_IVFFLAT_PROBES)
            )
            result: list[asyncpg.Record] = await conn.fetch(
                """
                SELECT id, content, metadata, importance_score, source_message_id,
                       1 - (embedding <=> $2) AS similarity,
                       created_at
                FROM chat_memory
                WHERE chat_id = $1
                  AND embedding IS NOT NULL
                  AND (expires_at IS NULL OR expires_at > NOW())
                  AND (
                        $4::timestamptz IS NULL
                        OR created_at IS NULL
                        OR created_at < $4::timestamptz
                  )
                ORDER BY embedding <=> $2 ASC
                LIMIT $3
                """,
                chat_id,
                query_embedding,
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
