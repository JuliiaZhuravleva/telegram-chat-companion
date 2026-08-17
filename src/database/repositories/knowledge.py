"""Repository for the chat_facts table (per-chat Knowledge Base, ADR-0003).

Phase 1 (manual MVP) scope: CRUD + supersession-in-transaction + pgvector
similarity retrieval. Extraction/reconciliation land on top of this in later
phases -- this repository is the generic supersession primitive both manual
writes (A4's `/remember`) and the Phase 2+ reconciler will use.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# Shared by both write paths (`upsert_fact`'s supersession and `append_fact`'s
# plain insert) so the two can never drift on which columns a new fact carries
# -- a fact written by one path and missing a column the other sets is a bug
# nothing in the schema would catch.
_INSERT_FACT = """
    INSERT INTO chat_facts (
        chat_id, topic, subject, predicate, value, fact_text,
        embedding, status, source, source_message_id,
        source_user_id, authority_level, confidence, salience, expires_at
    ) VALUES (
        $1, $2, $3, $4, $5, $6, $7, 'active', $8, $9, $10, $11, $12, $13, $14
    )
    RETURNING id
"""

# Everything a reader needs, minus `embedding`. `SELECT *` shipped a 768-float
# vector per row to the bot process on every `/kb` press, for a list that
# renders none of it -- and `get_active_facts` is unbounded, so the cost grew
# with the corpus S2 exists to create.
_FACT_COLUMNS = """
    id, chat_id, topic, subject, predicate, value, fact_text, status,
    source, source_message_id, source_user_id, authority_level, confidence,
    salience, valid_from, valid_to, superseded_by, expires_at,
    rejected_by, rejected_at, created_at, updated_at
"""

# The one definition of "a fact that should influence the bot right now".
#
# `status='active' AND valid_to IS NULL` alone was the Phase-1 predicate;
# migration 027 adds `expires_at`, and an expired fact must stop being
# retrievable without becoming a *superseded revision* of anything (see that
# migration's docstring). Interpolated as a local constant only -- never a
# user value -- per the SQL-composition ADR.
_LIVE_FACTS = """
    status = 'active'
      AND valid_to IS NULL
      AND (expires_at IS NULL OR expires_at > NOW())
"""

# pgvector's ivfflat index is APPROXIMATE: it scans `probes` of its `lists`
# partitions and returns whatever it finds there. Migration 014 built the
# index with `lists = 10` and nothing ever set `probes`, whose default is 1 --
# i.e. every KB lookup has been reading ~1/10th of the index and silently
# missing the best fact whenever it lived in another partition.
#
# probes == lists means an exact scan. That is the right trade here and not a
# general one: `chat_facts` holds a curated, hand-written set (tens of rows
# per chat, not the thousands `chat_memory` carries), the whole point of the
# revision is to let a fact decide whether the bot speaks, and a decision made
# on an approximate result is not reproducible. Raise `lists` and revisit this
# together, per migration 014's own note.
_IVFFLAT_PROBES = 10


class KnowledgeRepository:
    """Data access layer for `chat_facts`."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ── write (create + supersession) ────────────────────────────────

    async def upsert_fact(
        self,
        *,
        chat_id: int,
        subject: str,
        predicate: str,
        value: str,
        fact_text: str,
        source: str,
        topic: str | None = None,
        embedding: list[float] | None = None,
        source_message_id: int | None = None,
        source_user_id: int | None = None,
        authority_level: int = 0,
        confidence: float | None = None,
        salience: float = 0.5,
        expires_at: datetime | None = None,
    ) -> int:
        """Write a fact, superseding any existing active fact at the same key.

        MemStrata bi-temporal lifecycle (ADR-0003): if an active row already
        exists for `(chat_id, subject, predicate)`, it is closed
        (`valid_to = NOW()`, `status = 'superseded'`, `superseded_by` set to
        the new row's id) and the new row is inserted -- **never** `DELETE`,
        **never** a bare `UPDATE` of the old row's `value`. Both statements
        run in one transaction so a reader never observes zero or two active
        rows for the same key.

        Concurrency: writers on the same key are serialized by a
        transaction-scoped advisory lock (covers the create-create race,
        where there is no row for `FOR UPDATE` to lock, and the READ
        COMMITTED re-check path, where a superseded row silently drops out
        of the locking SELECT). The UNIQUE partial index
        `idx_chat_facts_active_key` backstops the invariant at the DB level;
        a unique-violation is retried once, by which point the winning
        writer's row is visible and gets superseded normally.

        If no active row exists for the key, this is a plain insert.

        Manual capture does **not** come through here any more: `/remember` is
        append-only since S2/KB-07 and uses `append_fact`. This path stays for
        the writers that genuinely replace a value at a stable key -- the
        Phase-2 reconciler, and S3's "rewrite this fact" action.

        Returns the new row's id.
        """
        for attempt in (1, 2):
            try:
                return await self._upsert_fact_once(
                    chat_id=chat_id,
                    subject=subject,
                    predicate=predicate,
                    value=value,
                    fact_text=fact_text,
                    source=source,
                    topic=topic,
                    embedding=embedding,
                    source_message_id=source_message_id,
                    source_user_id=source_user_id,
                    authority_level=authority_level,
                    confidence=confidence,
                    salience=salience,
                    expires_at=expires_at,
                )
            except asyncpg.UniqueViolationError:
                if attempt == 2:
                    raise
                logger.warning(
                    "kb_upsert_unique_race_retry",
                    chat_id=chat_id,
                    subject=subject,
                    predicate=predicate,
                )
        raise AssertionError("unreachable")  # pragma: no cover

    async def _upsert_fact_once(
        self,
        *,
        chat_id: int,
        subject: str,
        predicate: str,
        value: str,
        fact_text: str,
        source: str,
        topic: str | None,
        embedding: list[float] | None,
        source_message_id: int | None,
        source_user_id: int | None,
        authority_level: int,
        confidence: float | None,
        salience: float,
        expires_at: datetime | None = None,
    ) -> int:
        async with self._pool.acquire() as conn, conn.transaction():
            # Serialize all writers of this key for the transaction's duration
            # (released automatically at commit/rollback).
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1 || ':' || $2 || ':' || $3, 0))",
                str(chat_id),
                subject,
                predicate,
            )

            existing = await conn.fetchrow(
                """
                SELECT id FROM chat_facts
                WHERE chat_id = $1 AND subject = $2 AND predicate = $3
                  AND valid_to IS NULL
                FOR UPDATE
                """,
                chat_id,
                subject,
                predicate,
            )

            # Close the old row BEFORE inserting the new one: the UNIQUE
            # partial index checks per-statement, so two active rows may
            # never coexist even transiently inside the transaction.
            old_id: int | None = None
            if existing is not None:
                old_id = int(existing["id"])
                await conn.execute(
                    """
                    UPDATE chat_facts
                    SET valid_to = NOW(), status = 'superseded'
                    WHERE id = $1
                    """,
                    old_id,
                )

            new_row = await conn.fetchrow(
                _INSERT_FACT,
                chat_id,
                topic,
                subject,
                predicate,
                value,
                fact_text,
                embedding,
                source,
                source_message_id,
                source_user_id,
                authority_level,
                confidence,
                salience,
                expires_at,
            )
            assert new_row is not None
            new_id = int(new_row["id"])

            if old_id is not None:
                await conn.execute(
                    "UPDATE chat_facts SET superseded_by = $2 WHERE id = $1",
                    old_id,
                    new_id,
                )
                logger.info(
                    "kb_fact_superseded",
                    chat_id=chat_id,
                    old_id=old_id,
                    new_id=new_id,
                    subject=subject,
                    predicate=predicate,
                )

            return new_id

    async def append_fact(
        self,
        *,
        chat_id: int,
        subject: str,
        predicate: str,
        value: str,
        fact_text: str,
        source: str,
        topic: str | None = None,
        embedding: list[float] | None = None,
        source_message_id: int | None = None,
        source_user_id: int | None = None,
        authority_level: int = 0,
        confidence: float | None = None,
        salience: float = 0.5,
        expires_at: datetime | None = None,
    ) -> tuple[int, bool]:
        """Add a fact **without** retiring anything. Returns ``(id, created)``.

        The append-only half of D-3/KB-07, and a separate method rather than a
        flag on `upsert_fact` on purpose: the two have opposite invariants, and
        a boolean that silently switches between "replace the value at this key"
        and "never touch another row" is the kind of parameter a later caller
        gets wrong in one direction only.

        `created=False` means an active row already existed at this exact key.
        That is not an error and not a supersession -- it is the same capture
        arriving twice (the user double-tapped send, or Telegram redelivered the
        update), and the caller reports "already saved" pointing at the row that
        exists. The alternative, which `upsert_fact` would have done here, is to
        retire the first row and insert an identical second one: a duplicate in
        the prompt, two entries in `/kb`, and a supersession record for an event
        that never happened.

        Idempotency rests on the predicate carrying the *capture's* identity
        (`capture.fact_predicate` derives it from the command's message id), so
        the same command can only ever own one row. A predicate built from a
        clock or a random token would make this branch unreachable and every
        redelivery a duplicate.

        The pre-check below is not redundant with the unique index, because the
        index is **partial** (`WHERE valid_to IS NULL`) and `reject_fact` sets
        `valid_to = NOW()`. An undone fact therefore *leaves* the index: without
        this check, a redelivered update after an undo inserted a second row and
        silently resurrected the fact the user had just removed. The index still
        backstops the concurrent case, where two writers race past this SELECT.
        """
        existing_any = await self._pool.fetchrow(
            """
            SELECT id, status FROM chat_facts
            WHERE chat_id = $1 AND subject = $2 AND predicate = $3
            ORDER BY id DESC
            LIMIT 1
            """,
            chat_id,
            subject,
            predicate,
        )
        if existing_any is not None:
            logger.info(
                "kb_fact_append_already_exists",
                chat_id=chat_id,
                fact_id=int(existing_any["id"]),
                status=existing_any["status"],
                subject=subject,
                predicate=predicate,
            )
            return int(existing_any["id"]), False

        try:
            row = await self._pool.fetchrow(
                _INSERT_FACT,
                chat_id,
                topic,
                subject,
                predicate,
                value,
                fact_text,
                embedding,
                source,
                source_message_id,
                source_user_id,
                authority_level,
                confidence,
                salience,
                expires_at,
            )
        except asyncpg.UniqueViolationError:
            existing = await self._pool.fetchrow(
                """
                SELECT id FROM chat_facts
                WHERE chat_id = $1 AND subject = $2 AND predicate = $3
                  AND valid_to IS NULL
                """,
                chat_id,
                subject,
                predicate,
            )
            if existing is None:
                # The unique violation was not this key's -- re-raise rather
                # than reporting a save that did not happen.
                raise
            logger.info(
                "kb_fact_append_already_exists",
                chat_id=chat_id,
                fact_id=int(existing["id"]),
                subject=subject,
                predicate=predicate,
            )
            return int(existing["id"]), False

        assert row is not None
        return int(row["id"]), True

    # ── read ──────────────────────────────────────────────────────────

    async def get_by_id(self, fact_id: int, *, chat_id: int) -> dict[str, Any] | None:
        """Get a single fact by id, scoped to chat for access control."""
        row = await self._pool.fetchrow(
            "SELECT * FROM chat_facts WHERE id = $1 AND chat_id = $2",
            fact_id,
            chat_id,
        )
        return dict(row) if row else None

    async def get_active_facts(
        self, chat_id: int, *, topic: str | None = None
    ) -> list[dict[str, Any]]:
        """List all currently-active facts for a chat, optionally filtered by topic.

        Ordered by topic then subject for stable, grouped rendering (A4's
        `/kb` view groups by topic).

        The final tiebreak is `created_at, id`, not `predicate`: append-only
        capture (KB-07) gives each fact a predicate derived from its command's
        message id, and sorting those as text puts `m1001` before `m999`, so two
        facts about one subject would list in an order that looks arbitrary and
        changes as ids grow. `id` is needed after `created_at` because rows
        written inside one transaction share `NOW()` to the microsecond.
        """
        if topic is not None:
            rows = await self._pool.fetch(
                f"""
                SELECT {_FACT_COLUMNS} FROM chat_facts
                WHERE chat_id = $1 AND {_LIVE_FACTS}
                  AND topic = $2
                ORDER BY topic NULLS LAST, subject, created_at, id
                """,  # noqa: S608 -- local constants, no user input
                chat_id,
                topic,
            )
        else:
            rows = await self._pool.fetch(
                f"""
                SELECT {_FACT_COLUMNS} FROM chat_facts
                WHERE chat_id = $1 AND {_LIVE_FACTS}
                ORDER BY topic NULLS LAST, subject, created_at, id
                """,  # noqa: S608 -- local constants, no user input
                chat_id,
            )
        return [dict(r) for r in rows]

    async def get_expired_facts(self, chat_id: int) -> list[dict[str, Any]]:
        """Facts that were live and have aged out. Reachable, not vanished.

        `_LIVE_FACTS` hides expired rows from every read path, which would
        otherwise make the management action they need ("the event moved --
        clear the expiry") inapplicable to exactly the facts that need it.
        The S3 fact list surfaces these behind their own segment.
        """
        rows = await self._pool.fetch(
            f"""
            SELECT {_FACT_COLUMNS} FROM chat_facts
            WHERE chat_id = $1
              AND status = 'active'
              AND valid_to IS NULL
              AND expires_at IS NOT NULL
              AND expires_at <= NOW()
            ORDER BY expires_at DESC, subject
            """,  # noqa: S608 -- local constant, no user input
            chat_id,
        )
        return [dict(r) for r in rows]

    async def search_by_similarity(
        self,
        chat_id: int,
        query_embedding: list[float],
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """pgvector cosine-similarity search over active facts.

        Ordered by **similarity DESC, then salience DESC as a tiebreak** (ADR-0009
        -- retrieval relevance decides what this round trip returns at all;
        salience-driven budget-trim priority moved to `trim_facts_to_budget()`
        in `prompt_builder.py`, which now stable-sorts by salience before
        applying the token budget). Only live rows (`_LIVE_FACTS` -- active,
        not superseded, not past `expires_at`) with a stored embedding are
        eligible.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            # Transaction-local (`is_local=true`), so this never leaks onto a
            # pooled connection that some other query will reuse. Set through
            # set_config() rather than `SET LOCAL` because only the function
            # form accepts a bind parameter.
            await conn.execute(
                "SELECT set_config('ivfflat.probes', $1, true)", str(_IVFFLAT_PROBES)
            )
            rows = await conn.fetch(
                f"""
                SELECT *,
                       1 - (embedding <=> $2) AS similarity
                FROM chat_facts
                WHERE chat_id = $1
                  AND {_LIVE_FACTS}
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> $2 ASC, salience DESC
                LIMIT $3
                """,  # noqa: S608 -- local constant, no user input
                chat_id,
                query_embedding,
                limit,
            )
        return [dict(r) for r in rows]

    # ── embedding backfill (mirrors MemoryRepository's contract) ─────

    async def get_pending_embeddings(
        self, limit: int, *, exclude_ids: list[int] | None = None
    ) -> list[asyncpg.Record]:
        """Live facts stored without a vector, oldest first.

        `/remember` persists the fact even when `generate_embedding()` raises,
        so a provider blip leaves a row the user can see in `/kb` and
        `search_by_similarity()` can never return (it filters
        `embedding IS NOT NULL`). Nothing retried it, which made a transient
        outage permanent for that fact. `EmbeddingBackfillWorker` is what
        repairs it, using the same shape it already uses for `chat_memory`.

        Only live facts are eligible: re-embedding a superseded, rejected or
        expired row spends a call on something no read path will return.
        `exclude_ids` carries the worker's parked rows, same as the memory
        repository -- see its docstring for why a FIFO queue needs it.
        """
        result: list[asyncpg.Record] = await self._pool.fetch(
            f"""
            SELECT id, chat_id, fact_text
            FROM chat_facts
            WHERE embedding IS NULL
              AND {_LIVE_FACTS}
              AND NOT (id = ANY($2::bigint[]))
            ORDER BY created_at ASC
            LIMIT $1
            """,  # noqa: S608 -- local constant, no user input
            limit,
            exclude_ids or [],
        )
        return result

    async def update_embedding(self, fact_id: int, embedding: list[float]) -> None:
        """Fill in a previously-pending fact's embedding.

        ``AND embedding IS NULL`` guards against clobbering a row a concurrent
        pass already filled, mirroring ``MemoryRepository.update_embedding``.
        """
        await self._pool.execute(
            "UPDATE chat_facts SET embedding = $2 WHERE id = $1 AND embedding IS NULL",
            fact_id,
            embedding,
        )

    # ── update (terminal, non-supersession) ──────────────────────────

    async def reject_fact(
        self, fact_id: int, *, chat_id: int, rejected_by: int | None = None
    ) -> bool:
        """Mark a fact as rejected (terminal; not superseded, not deleted).

        Used by organizer/admin removal (A4) -- per ADR-0003, `chat_facts`
        rows are never hard-deleted so `/kb history` (Phase 4) stays free.
        Returns True if a row was updated.

        `rejected_by` is the user who retired it (migration 027). It is
        recorded because the revision lets every Telegram chat administrator
        remove facts, not only the operator's hand-picked organizers -- so
        "a fact disappeared" needs an answer that is not "ask everyone".
        Defaulting to None keeps a system-initiated removal expressible and
        distinct from an unattributed one.
        """
        result = await self._pool.execute(
            """
            UPDATE chat_facts
            SET status = 'rejected',
                valid_to = COALESCE(valid_to, NOW()),
                rejected_by = $3,
                rejected_at = NOW()
            WHERE id = $1 AND chat_id = $2 AND valid_to IS NULL
            """,
            fact_id,
            chat_id,
            rejected_by,
        )
        return result.endswith(" 1") if result else False
