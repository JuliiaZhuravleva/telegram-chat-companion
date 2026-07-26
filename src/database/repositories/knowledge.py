"""Repository for the chat_facts table (per-chat Knowledge Base, ADR-0003).

Phase 1 (manual MVP) scope: CRUD + supersession-in-transaction + pgvector
similarity retrieval. Extraction/reconciliation land on top of this in later
phases -- this repository is the generic supersession primitive both manual
writes (A4's `/remember`) and the Phase 2+ reconciler will use.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


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
                """
                INSERT INTO chat_facts (
                    chat_id, topic, subject, predicate, value, fact_text,
                    embedding, status, source, source_message_id,
                    source_user_id, authority_level, confidence, salience
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, 'active', $8, $9, $10, $11, $12, $13
                )
                RETURNING id
                """,
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
        """
        if topic is not None:
            rows = await self._pool.fetch(
                """
                SELECT * FROM chat_facts
                WHERE chat_id = $1 AND status = 'active' AND valid_to IS NULL
                  AND topic = $2
                ORDER BY topic NULLS LAST, subject, predicate
                """,
                chat_id,
                topic,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT * FROM chat_facts
                WHERE chat_id = $1 AND status = 'active' AND valid_to IS NULL
                ORDER BY topic NULLS LAST, subject, predicate
                """,
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

        Ordered by **salience DESC, then similarity DESC** (ADR-0003 Part 2 --
        retrieval order is this repository's concern; `trim_facts_to_budget()`
        in `prompt_builder.py` (A5) does not re-sort). Only `status='active'
        AND valid_to IS NULL` rows with a stored embedding are eligible.
        """
        rows = await self._pool.fetch(
            """
            SELECT *,
                   1 - (embedding <=> $2) AS similarity
            FROM chat_facts
            WHERE chat_id = $1
              AND status = 'active'
              AND valid_to IS NULL
              AND embedding IS NOT NULL
            ORDER BY salience DESC, embedding <=> $2 ASC
            LIMIT $3
            """,
            chat_id,
            query_embedding,
            limit,
        )
        return [dict(r) for r in rows]

    # ── update (terminal, non-supersession) ──────────────────────────

    async def reject_fact(self, fact_id: int, *, chat_id: int) -> bool:
        """Mark a fact as rejected (terminal; not superseded, not deleted).

        Used by organizer/admin removal (A4) -- per ADR-0003, `chat_facts`
        rows are never hard-deleted so `/kb history` (Phase 4) stays free.
        Returns True if a row was updated.
        """
        result = await self._pool.execute(
            """
            UPDATE chat_facts
            SET status = 'rejected', valid_to = COALESCE(valid_to, NOW())
            WHERE id = $1 AND chat_id = $2 AND valid_to IS NULL
            """,
            fact_id,
            chat_id,
        )
        return result.endswith(" 1") if result else False
