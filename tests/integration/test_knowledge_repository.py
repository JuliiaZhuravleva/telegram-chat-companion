"""
Integration tests: KnowledgeRepository against real Postgres+pgvector.

A2's unit tests (``tests/unit/test_knowledge_repository.py``) mock the asyncpg
pool/connection/transaction entirely -- including the ``FOR UPDATE`` row lock
that makes ``upsert_fact``'s supersession safe under concurrent writers, and the
pgvector ``<=>`` ranking that ``search_by_similarity`` relies on. Neither can be
meaningfully verified against a mock. This file is A6's real-DB complement,
flagged explicitly by A2's routing hint.

NB: unlike the other integration test files in this directory,
``KnowledgeRepository`` manages its own transactions via ``pool.acquire()`` in
``upsert_fact`` (needed for ``FOR UPDATE`` + insert-then-close-old in one
transaction) -- it cannot be constructed with the rolled-back ``db_conn``
fixture (a bare ``asyncpg.Connection`` has no ``.acquire()``). Tests here use
the real ``db_pool`` fixture instead and rely on per-test unique ``chat_id``
values (no shared mutable state) rather than transaction rollback for isolation.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.knowledge import KnowledgeRepository

_EMBED_DIM = 768


def _one_hot(index: int, *, dim: int = _EMBED_DIM) -> list[float]:
    """A deterministic unit vector with a single 1.0 at `index`.

    Cosine similarity between two one-hot vectors is 1.0 if same index, 0.0 if
    different -- gives fully deterministic, discrete control over pgvector
    ranking without depending on real embedding semantics.
    """
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


@pytest_asyncio.fixture
async def repo(db_pool: asyncpg.Pool) -> KnowledgeRepository:
    return KnowledgeRepository(db_pool)


# ---------------------------------------------------------------------------
# upsert_fact — plain insert
# ---------------------------------------------------------------------------


class TestUpsertFactPlainInsert:
    @pytest.mark.asyncio
    async def test_first_write_creates_active_row(self, repo: KnowledgeRepository) -> None:
        fact_id = await repo.upsert_fact(
            chat_id=-910001,
            subject="мероприятие",
            predicate="место",
            value="Лофт №3",
            fact_text="Место мероприятия — Лофт №3",
            source="manual",
        )
        assert isinstance(fact_id, int)
        row = await repo.get_by_id(fact_id, chat_id=-910001)
        assert row is not None
        assert row["status"] == "active"
        assert row["valid_to"] is None
        assert row["superseded_by"] is None


# ---------------------------------------------------------------------------
# upsert_fact — supersession (commit path)
# ---------------------------------------------------------------------------


class TestUpsertFactSupersession:
    @pytest.mark.asyncio
    async def test_second_write_same_key_supersedes_first(self, repo: KnowledgeRepository) -> None:
        chat_id = -910002
        old_id = await repo.upsert_fact(
            chat_id=chat_id,
            subject="мероприятие",
            predicate="место",
            value="Лофт №3",
            fact_text="было Лофт №3",
            source="manual",
        )
        new_id = await repo.upsert_fact(
            chat_id=chat_id,
            subject="мероприятие",
            predicate="место",
            value="Артплей",
            fact_text="стало Артплей",
            source="manual",
        )
        assert new_id != old_id

        old_row = await repo.get_by_id(old_id, chat_id=chat_id)
        assert old_row is not None
        assert old_row["status"] == "superseded"
        assert old_row["valid_to"] is not None
        assert old_row["superseded_by"] == new_id

        new_row = await repo.get_by_id(new_id, chat_id=chat_id)
        assert new_row is not None
        assert new_row["status"] == "active"
        assert new_row["valid_to"] is None

    @pytest.mark.asyncio
    async def test_superseded_row_excluded_from_active_facts(
        self, repo: KnowledgeRepository
    ) -> None:
        chat_id = -910003
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="s",
            predicate="p",
            value="v1",
            fact_text="v1",
            source="manual",
        )
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="s",
            predicate="p",
            value="v2",
            fact_text="v2",
            source="manual",
        )
        active = await repo.get_active_facts(chat_id)
        assert len(active) == 1
        assert active[0]["value"] == "v2"

    @pytest.mark.asyncio
    async def test_different_predicate_does_not_supersede(self, repo: KnowledgeRepository) -> None:
        """Supersession key is (chat_id, subject, predicate) -- a different
        predicate under the same subject is an independent fact, never closed."""
        chat_id = -910004
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="мероприятие",
            predicate="место",
            value="Лофт №3",
            fact_text="место — Лофт №3",
            source="manual",
        )
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="мероприятие",
            predicate="дата",
            value="14.09",
            fact_text="дата — 14.09",
            source="manual",
        )
        active = await repo.get_active_facts(chat_id)
        assert len(active) == 2

    @pytest.mark.asyncio
    async def test_failed_write_leaves_existing_active_row_untouched(
        self, repo: KnowledgeRepository
    ) -> None:
        """Atomicity: if the new row's INSERT fails (NOT NULL violation on
        `value`), the whole transaction rolls back -- the previously-active row
        for the key must NOT have been closed. Guards against a supersession
        implementation that closes the old row before/independently of
        inserting the new one."""
        chat_id = -910005
        old_id = await repo.upsert_fact(
            chat_id=chat_id,
            subject="s",
            predicate="p",
            value="v1",
            fact_text="v1",
            source="manual",
        )

        with pytest.raises(asyncpg.PostgresError):
            await repo.upsert_fact(
                chat_id=chat_id,
                subject="s",
                predicate="p",
                value=None,  # type: ignore[arg-type]  # forces a NOT NULL violation
                fact_text="v2",
                source="manual",
            )

        old_row = await repo.get_by_id(old_id, chat_id=chat_id)
        assert old_row is not None
        assert old_row["status"] == "active"
        assert old_row["valid_to"] is None
        assert old_row["superseded_by"] is None

    @pytest.mark.asyncio
    async def test_concurrent_writers_same_key_resolve_to_single_active_row(
        self, repo: KnowledgeRepository
    ) -> None:
        """The FOR UPDATE lock in upsert_fact's existing-row lookup must
        serialize two concurrent writers targeting the same
        (chat_id, subject, predicate) key -- the reconciler (Phase 2) and a
        concurrent manual /remember (Phase 1) are the real-world instance of
        this race. Regardless of which writer's transaction commits first,
        the end state must be exactly one active row for the key and a fully
        consistent supersession chain (no double-active, no dangling
        superseded_by)."""
        chat_id = -910006
        # Seed an existing active row so both concurrent writers hit the
        # supersession path (not two independent plain inserts).
        seed_id = await repo.upsert_fact(
            chat_id=chat_id,
            subject="s",
            predicate="p",
            value="v0",
            fact_text="v0",
            source="manual",
        )

        results = await asyncio.gather(
            repo.upsert_fact(
                chat_id=chat_id,
                subject="s",
                predicate="p",
                value="vA",
                fact_text="vA",
                source="manual",
            ),
            repo.upsert_fact(
                chat_id=chat_id,
                subject="s",
                predicate="p",
                value="vB",
                fact_text="vB",
                source="manual",
            ),
        )
        assert len(set(results)) == 2  # two distinct new rows, no crash/deadlock

        active = await repo.get_active_facts(chat_id)
        assert len(active) == 1
        winner_id = active[0]["id"]
        assert winner_id in results

        # The seed row and the losing writer's row must both be superseded,
        # each pointing forward correctly, never both/neither active.
        all_rows = {
            row_id: await repo.get_by_id(row_id, chat_id=chat_id) for row_id in [seed_id, *results]
        }
        active_rows = [r for r in all_rows.values() if r is not None and r["status"] == "active"]
        assert len(active_rows) == 1
        assert active_rows[0]["id"] == winner_id
        superseded_rows = [
            r for r in all_rows.values() if r is not None and r["status"] == "superseded"
        ]
        assert len(superseded_rows) == 2
        for row in superseded_rows:
            assert row["valid_to"] is not None
            assert row["superseded_by"] is not None


# ---------------------------------------------------------------------------
# get_by_id / get_active_facts
# ---------------------------------------------------------------------------


class TestReads:
    @pytest.mark.asyncio
    async def test_get_by_id_scoped_to_chat(self, repo: KnowledgeRepository) -> None:
        fact_id = await repo.upsert_fact(
            chat_id=-910007,
            subject="s",
            predicate="p",
            value="v",
            fact_text="v",
            source="manual",
        )
        # Wrong chat_id must not leak the row.
        assert await repo.get_by_id(fact_id, chat_id=-999999) is None
        assert await repo.get_by_id(fact_id, chat_id=-910007) is not None

    @pytest.mark.asyncio
    async def test_get_active_facts_filters_by_topic(self, repo: KnowledgeRepository) -> None:
        chat_id = -910008
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="s1",
            predicate="p",
            value="v",
            fact_text="v",
            source="manual",
            topic="event:лето",
        )
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="s2",
            predicate="p",
            value="v",
            fact_text="v",
            source="manual",
            topic="general",
        )
        event_facts = await repo.get_active_facts(chat_id, topic="event:лето")
        assert len(event_facts) == 1
        assert event_facts[0]["subject"] == "s1"

        all_facts = await repo.get_active_facts(chat_id)
        assert len(all_facts) == 2


# ---------------------------------------------------------------------------
# search_by_similarity — real pgvector ranking
# ---------------------------------------------------------------------------


class TestSearchBySimilarity:
    @pytest.mark.asyncio
    async def test_orders_by_similarity_when_salience_tied(self, repo: KnowledgeRepository) -> None:
        chat_id = -910009
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="far",
            predicate="p",
            value="v",
            fact_text="far fact",
            source="manual",
            embedding=_one_hot(1),
            salience=0.5,
        )
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="near",
            predicate="p",
            value="v",
            fact_text="near fact",
            source="manual",
            embedding=_one_hot(0),
            salience=0.5,
        )
        results = await repo.search_by_similarity(chat_id, _one_hot(0), limit=5)
        assert [r["subject"] for r in results] == ["near", "far"]
        assert results[0]["similarity"] == pytest.approx(1.0)
        assert results[1]["similarity"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_salience_wins_over_similarity(self, repo: KnowledgeRepository) -> None:
        """ADR-0003 Part 2 contract: ORDER BY salience DESC, similarity DESC --
        a lower-similarity, higher-salience fact must rank first."""
        chat_id = -910010
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="very-similar-low-salience",
            predicate="p",
            value="v",
            fact_text="v",
            source="manual",
            embedding=_one_hot(0),  # identical to query -> similarity 1.0
            salience=0.1,
        )
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="dissimilar-high-salience",
            predicate="p",
            value="v",
            fact_text="v",
            source="manual",
            embedding=_one_hot(1),  # orthogonal to query -> similarity 0.0
            salience=0.9,
        )
        results = await repo.search_by_similarity(chat_id, _one_hot(0), limit=5)
        assert results[0]["subject"] == "dissimilar-high-salience"
        assert results[1]["subject"] == "very-similar-low-salience"

    @pytest.mark.asyncio
    async def test_excludes_facts_without_embedding(self, repo: KnowledgeRepository) -> None:
        chat_id = -910011
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="no-embedding",
            predicate="p",
            value="v",
            fact_text="v",
            source="manual",
            embedding=None,
        )
        results = await repo.search_by_similarity(chat_id, _one_hot(0), limit=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_excludes_superseded_facts(self, repo: KnowledgeRepository) -> None:
        chat_id = -910012
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="s",
            predicate="p",
            value="old",
            fact_text="old",
            source="manual",
            embedding=_one_hot(0),
        )
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="s",
            predicate="p",
            value="new",
            fact_text="new",
            source="manual",
            embedding=_one_hot(0),
        )
        results = await repo.search_by_similarity(chat_id, _one_hot(0), limit=5)
        assert len(results) == 1
        assert results[0]["value"] == "new"

    @pytest.mark.asyncio
    async def test_respects_limit(self, repo: KnowledgeRepository) -> None:
        chat_id = -910013
        for i in range(3):
            await repo.upsert_fact(
                chat_id=chat_id,
                subject=f"s{i}",
                predicate="p",
                value="v",
                fact_text="v",
                source="manual",
                embedding=_one_hot(0),
            )
        results = await repo.search_by_similarity(chat_id, _one_hot(0), limit=2)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# reject_fact
# ---------------------------------------------------------------------------


class TestRejectFact:
    @pytest.mark.asyncio
    async def test_rejects_active_fact(self, repo: KnowledgeRepository) -> None:
        chat_id = -910014
        fact_id = await repo.upsert_fact(
            chat_id=chat_id,
            subject="s",
            predicate="p",
            value="v",
            fact_text="v",
            source="manual",
        )
        ok = await repo.reject_fact(fact_id, chat_id=chat_id)
        assert ok is True

        row = await repo.get_by_id(fact_id, chat_id=chat_id)
        assert row is not None
        assert row["status"] == "rejected"
        assert row["valid_to"] is not None

        active = await repo.get_active_facts(chat_id)
        assert active == []

    @pytest.mark.asyncio
    async def test_returns_false_for_nonexistent_fact(self, repo: KnowledgeRepository) -> None:
        assert await repo.reject_fact(999999999, chat_id=-910015) is False

    @pytest.mark.asyncio
    async def test_returns_false_for_wrong_chat(self, repo: KnowledgeRepository) -> None:
        chat_id = -910016
        fact_id = await repo.upsert_fact(
            chat_id=chat_id,
            subject="s",
            predicate="p",
            value="v",
            fact_text="v",
            source="manual",
        )
        assert await repo.reject_fact(fact_id, chat_id=-999999) is False
        # Untouched by the failed attempt.
        row = await repo.get_by_id(fact_id, chat_id=chat_id)
        assert row is not None
        assert row["status"] == "active"

    @pytest.mark.asyncio
    async def test_returns_false_for_already_closed_fact(self, repo: KnowledgeRepository) -> None:
        """A fact already closed (valid_to set, via supersession) is not
        re-rejectable -- reject_fact's WHERE guards on valid_to IS NULL."""
        chat_id = -910017
        old_id = await repo.upsert_fact(
            chat_id=chat_id,
            subject="s",
            predicate="p",
            value="v1",
            fact_text="v1",
            source="manual",
        )
        await repo.upsert_fact(
            chat_id=chat_id,
            subject="s",
            predicate="p",
            value="v2",
            fact_text="v2",
            source="manual",
        )
        assert await repo.reject_fact(old_id, chat_id=chat_id) is False
