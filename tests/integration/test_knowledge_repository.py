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
from datetime import date, datetime, timedelta

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.knowledge import KnowledgeRepository
from src.services.knowledge import capture

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

    @pytest.mark.asyncio
    async def test_concurrent_first_writers_new_key_resolve_to_single_active_row(
        self, repo: KnowledgeRepository
    ) -> None:
        """Create-create race (review finding): with NO pre-existing row for
        the key, FOR UPDATE has nothing to lock -- serialization must come
        from the advisory lock, with the UNIQUE partial index as backstop.
        Two concurrent first writes must end as exactly one active row and
        one superseded row, never two active."""
        chat_id = -910016
        results = await asyncio.gather(
            repo.upsert_fact(
                chat_id=chat_id,
                subject="новый-ключ",
                predicate="p",
                value="vA",
                fact_text="vA",
                source="manual",
            ),
            repo.upsert_fact(
                chat_id=chat_id,
                subject="новый-ключ",
                predicate="p",
                value="vB",
                fact_text="vB",
                source="manual",
            ),
        )
        assert len(set(results)) == 2

        active = await repo.get_active_facts(chat_id)
        assert len(active) == 1
        assert active[0]["id"] in results

        loser_id = next(r for r in results if r != active[0]["id"])
        loser = await repo.get_by_id(loser_id, chat_id=chat_id)
        assert loser is not None
        assert loser["status"] == "superseded"
        assert loser["valid_to"] is not None
        assert loser["superseded_by"] == active[0]["id"]


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
    async def test_similarity_wins_over_salience(self, repo: KnowledgeRepository) -> None:
        """ADR-0009 contract: ORDER BY similarity DESC, salience DESC (tiebreak) --
        a more-similar, lower-salience fact must rank first even against a much
        higher-salience competitor. Supersedes ADR-0003 Part 2's opposite
        contract (formerly `test_salience_wins_over_similarity`); the old
        scenario's intent (salience deciding a survivor) now lives at the
        budget-trim layer -- see
        `tests/unit/test_prompt_builder.py::TestTrimFactsToBudget`."""
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
        assert results[0]["subject"] == "very-similar-low-salience"
        assert results[1]["subject"] == "dissimilar-high-salience"

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


# ---------------------------------------------------------------------------
# append_fact — the append-only write path (S2 / KB-07)
# ---------------------------------------------------------------------------


async def _row(pool: asyncpg.Pool, fact_id: int) -> asyncpg.Record:
    """Read a row straight from the table, bypassing every repository filter.

    A repository read cannot show that an "invisible" row is genuinely
    untouched: `get_active_facts` hides a superseded row exactly as it hides an
    expired one, so a test asserting only through the repository cannot tell
    "still active" from "quietly retired".
    """
    row = await pool.fetchrow(
        "SELECT id, status, valid_to, superseded_by, value, expires_at,"
        " embedding IS NULL AS no_embedding FROM chat_facts WHERE id = $1",
        fact_id,
    )
    assert row is not None, f"fact {fact_id} does not exist"
    return row


async def _count(pool: asyncpg.Pool, chat_id: int) -> int:
    """Every row for the chat, whatever its status."""
    return int(await pool.fetchval("SELECT count(*) FROM chat_facts WHERE chat_id = $1", chat_id))


class TestAppendFactIsAppendOnly:
    @pytest.mark.asyncio
    async def test_two_captures_about_one_subject_both_stay_live(
        self, repo: KnowledgeRepository, db_pool: asyncpg.Pool
    ) -> None:
        """The bug S2 exists to fix, against the real UNIQUE partial index.

        Phase 1 wrote `/remember` through `upsert_fact` with the constant
        predicate `"факт"`, which collapsed the designed key
        `(chat_id, subject, predicate)` to `(chat_id, subject)`: a second
        `/remember` about the same subject **superseded** the first, so adding a
        detail silently deleted a fact. With the predicate carrying the capture's
        own identity (`capture.fact_predicate`), the second write is an ordinary
        INSERT: `idx_chat_facts_active_key` does not fire and both facts live.
        """
        chat_id = -910101
        subject = "мероприятие"
        # "The index does not fire" is only a claim about this schema if the
        # index is there at all -- on a database missing it, every assertion
        # below would pass while proving nothing.
        assert (
            await db_pool.fetchval(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_chat_facts_active_key'"
            )
            is not None
        ), "migration 014's UNIQUE partial index is missing from the test database"

        first_id, first_created = await repo.append_fact(
            chat_id=chat_id,
            subject=subject,
            predicate=capture.fact_predicate(999),
            value="Лофт №3",
            fact_text="место — Лофт №3",
            source="manual",
        )
        second_id, second_created = await repo.append_fact(
            chat_id=chat_id,
            subject=subject,
            predicate=capture.fact_predicate(1001),
            value="сбор в 19:00",
            fact_text="сбор в 19:00",
            source="manual",
        )
        assert (first_created, second_created) == (True, True)
        assert first_id != second_id

        active = await repo.get_active_facts(chat_id)
        assert len(active) == 2, "the second capture must not have retired the first"
        assert {f["id"] for f in active} == {first_id, second_id}
        for fact in active:
            assert fact["status"] == "active"
            assert fact["valid_to"] is None
            assert fact["superseded_by"] is None

        # Capture order, not predicate order: as text, "m1001" < "m999", so the
        # pre-S2 `ORDER BY ... predicate` listed the newer fact first.
        assert [f["id"] for f in active] == [first_id, second_id]

        # Two rows in the table, not three: no supersession revision was written
        # for an event that never happened.
        assert await _count(db_pool, chat_id) == 2

    @pytest.mark.asyncio
    async def test_upsert_still_supersedes_at_a_stable_key(self, repo: KnowledgeRepository) -> None:
        """Append-only is `/remember`'s rule, not the table's.

        The reconciler and S3's "rewrite this fact" genuinely replace the value
        at a stable key, and that behaviour must survive the S2 change --
        otherwise the fix trades a silent delete for an ever-growing pile of
        contradictory revisions.
        """
        chat_id = -910102
        old_id = await repo.upsert_fact(
            chat_id=chat_id,
            subject="мероприятие",
            predicate="место",
            value="Лофт №3",
            fact_text="место — Лофт №3",
            source="reconciler",
        )
        new_id = await repo.upsert_fact(
            chat_id=chat_id,
            subject="мероприятие",
            predicate="место",
            value="Артплей",
            fact_text="место — Артплей",
            source="reconciler",
        )
        active = await repo.get_active_facts(chat_id)
        assert [f["id"] for f in active] == [new_id]
        old = await repo.get_by_id(old_id, chat_id=chat_id)
        assert old is not None
        assert old["status"] == "superseded"
        assert old["superseded_by"] == new_id

    @pytest.mark.asyncio
    async def test_redelivered_capture_returns_the_existing_row_untouched(
        self, repo: KnowledgeRepository, db_pool: asyncpg.Pool
    ) -> None:
        """The same command arriving twice: one row, reported as "already saved".

        Telegram redelivers updates, and a user can double-tap send. Because the
        predicate is the *command's* identity, the retry collides on the real
        UNIQUE partial index -- and the correct answer is the id of the row that
        exists with `created=False`. The dangerous alternative is what
        `upsert_fact` would do here: retire the first row and insert a
        near-identical second one, i.e. a supersession record for an event that
        never happened. So the first row's own status is asserted, not just the
        count.
        """
        chat_id = -910103
        predicate = capture.fact_predicate(2002)
        first_id, first_created = await repo.append_fact(
            chat_id=chat_id,
            subject="правила",
            predicate=predicate,
            value="не флудить",
            fact_text="правила: не флудить",
            source="manual",
        )
        # A redelivery re-parses the same message; pass a different value so a
        # silent in-place UPDATE would be visible too.
        second_id, second_created = await repo.append_fact(
            chat_id=chat_id,
            subject="правила",
            predicate=predicate,
            value="ПЕРЕЗАПИСЬ",
            fact_text="ПЕРЕЗАПИСЬ",
            source="manual",
        )
        assert (second_id, second_created) == (first_id, False)
        assert await _count(db_pool, chat_id) == 1

        row = await _row(db_pool, first_id)
        assert row["status"] == "active", "the first row must not have been retired"
        assert row["valid_to"] is None
        assert row["superseded_by"] is None
        assert row["value"] == "не флудить", "an append must never rewrite the row it found"

    @pytest.mark.asyncio
    async def test_same_predicate_in_another_chat_is_a_different_fact(
        self, repo: KnowledgeRepository
    ) -> None:
        """Message ids are per-chat, so the predicate alone is not the key.

        Two chats can produce `/remember` commands with the same message id; the
        unique index is `(chat_id, subject, predicate)` and both rows must live.
        """
        chat_id_a, chat_id_b = -910104, -910105
        predicate = capture.fact_predicate(3003)
        for chat_id in (chat_id_a, chat_id_b):
            _, created = await repo.append_fact(
                chat_id=chat_id,
                subject="s",
                predicate=predicate,
                value="v",
                fact_text="ft",
                source="manual",
            )
            assert created is True
        assert len(await repo.get_active_facts(chat_id_a)) == 1
        assert len(await repo.get_active_facts(chat_id_b)) == 1

    @pytest.mark.asyncio
    async def test_two_simultaneous_deliveries_of_one_capture_leave_one_row(
        self, repo: KnowledgeRepository, db_pool: asyncpg.Pool
    ) -> None:
        """A *real* unique violation, produced by two writers on two connections.

        The unit test injects `UniqueViolationError` into a mock, which proves the
        handling but not that Postgres ever raises it here -- and this path has no
        advisory lock and no transaction, deliberately. `db_pool` is created with
        `max_size=3`, so `gather` really does run these on separate connections
        (the loser blocks on the index entry until the winner commits, then gets
        the violation and looks the row up).
        """
        chat_id = -910111
        predicate = capture.fact_predicate(4004)

        async def _capture_once() -> tuple[int, bool]:
            return await repo.append_fact(
                chat_id=chat_id,
                subject="s",
                predicate=predicate,
                value="v",
                fact_text="ft",
                source="manual",
            )

        first, second = await asyncio.gather(_capture_once(), _capture_once())

        assert first[0] == second[0], "both deliveries must name the same row"
        assert {first[1], second[1]} == {True, False}, "exactly one of them created it"
        assert await _count(db_pool, chat_id) == 1
        row = await _row(db_pool, first[0])
        assert row["status"] == "active"
        assert row["valid_to"] is None


# ---------------------------------------------------------------------------
# expiry boundary — real Postgres clock, runner timezone irrelevant
# ---------------------------------------------------------------------------


async def _db_clock(pool: asyncpg.Pool) -> tuple[datetime, date]:
    """`NOW()` as the DATABASE sees it, plus today's date in Asia/Tbilisi.

    Both come from the server on purpose. The runner's clock and `TZ` decide
    nothing here: `expires_at` is compared against `NOW()` inside Postgres, and
    the calendar day the user meant by "до 5 сентября" is a Tbilisi day
    (`capture.CAPTURE_TZ`), not the runner's. A test that computed either side
    locally would pass or fail according to the machine it ran on.
    """
    row = await pool.fetchrow(
        "SELECT NOW() AS db_now, (NOW() AT TIME ZONE 'Asia/Tbilisi')::date AS tbilisi_today"
    )
    assert row is not None
    return row["db_now"], row["tbilisi_today"]


class TestExpiryBoundary:
    @pytest.mark.asyncio
    async def test_final_day_in_tbilisi_is_inclusive(
        self, repo: KnowledgeRepository, db_pool: asyncpg.Pool
    ) -> None:
        """`до 5 сентября` means "live all through the 5th, gone on the 6th".

        The instant under test is the one `capture.end_of_day()` actually
        produces, encoded by asyncpg and compared by Postgres -- the whole path,
        not a hand-written timestamp that happens to agree with it. The
        yesterday row is the other half: the same construction one day earlier
        must already be past.
        """
        chat_id = -910106
        _, tbilisi_today = await _db_clock(db_pool)

        today_id, _ = await repo.append_fact(
            chat_id=chat_id,
            subject="сегодня",
            predicate=capture.fact_predicate(1),
            value="v",
            fact_text="истекает сегодня",
            source="manual",
            embedding=_one_hot(0),
            expires_at=capture.end_of_day(tbilisi_today),
        )
        yesterday_id, _ = await repo.append_fact(
            chat_id=chat_id,
            subject="вчера",
            predicate=capture.fact_predicate(2),
            value="v",
            fact_text="истекло вчера",
            source="manual",
            embedding=_one_hot(0),
            expires_at=capture.end_of_day(tbilisi_today - timedelta(days=1)),
        )

        active = [f["id"] for f in await repo.get_active_facts(chat_id)]
        assert active == [today_id], "a fact must stay live through its last Tbilisi day"

        found = [f["id"] for f in await repo.search_by_similarity(chat_id, _one_hot(0), limit=5)]
        assert found == [today_id], "retrieval must agree with the list about what is live"

        expired = [f["id"] for f in await repo.get_expired_facts(chat_id)]
        assert expired == [yesterday_id], "yesterday's deadline has passed, today's has not"

        # The stored instant is the inclusive end of the day, not its midnight.
        stored = await _row(db_pool, today_id)
        assert stored["expires_at"] == capture.end_of_day(tbilisi_today)

    @pytest.mark.asyncio
    async def test_boundary_is_measured_against_the_database_clock(
        self, repo: KnowledgeRepository, db_pool: asyncpg.Pool
    ) -> None:
        """Just-past vs just-future, both derived from `NOW()` in the server.

        Seconds either side of the same instant, so nothing here can pass by
        accident of a wide margin: the past row must be gone from both live
        reads and present in the expired read, the future row the reverse.
        """
        chat_id = -910107
        row = await db_pool.fetchrow(
            "SELECT NOW() - interval '3 seconds' AS just_past,"
            " NOW() + interval '2 hours' AS just_future"
        )
        assert row is not None
        assert row["just_past"].tzinfo is not None  # asyncpg gives aware datetimes

        past_id, _ = await repo.append_fact(
            chat_id=chat_id,
            subject="past",
            predicate=capture.fact_predicate(11),
            value="v",
            fact_text="просрочено",
            source="manual",
            embedding=_one_hot(0),
            expires_at=row["just_past"],
        )
        future_id, _ = await repo.append_fact(
            chat_id=chat_id,
            subject="future",
            predicate=capture.fact_predicate(12),
            value="v",
            fact_text="ещё живо",
            source="manual",
            embedding=_one_hot(0),
            expires_at=row["just_future"],
        )
        never_id, _ = await repo.append_fact(
            chat_id=chat_id,
            subject="never",
            predicate=capture.fact_predicate(13),
            value="v",
            fact_text="без срока",
            source="manual",
            embedding=_one_hot(0),
        )

        active = {f["id"] for f in await repo.get_active_facts(chat_id)}
        assert active == {future_id, never_id}

        found = {f["id"] for f in await repo.search_by_similarity(chat_id, _one_hot(0), limit=5)}
        assert found == {future_id, never_id}, "an aged-out fact must not reach the prompt"

        expired = {f["id"] for f in await repo.get_expired_facts(chat_id)}
        assert expired == {past_id}, "no-expiry rows are not expired rows"

        # Gone from every read path, but not superseded and not deleted: the
        # management action it needs ("the event moved, clear the expiry") has
        # to be able to find it.
        stored = await _row(db_pool, past_id)
        assert stored["status"] == "active"
        assert stored["valid_to"] is None

    @pytest.mark.asyncio
    async def test_expired_fact_is_excluded_from_the_topic_filtered_list(
        self, repo: KnowledgeRepository, db_pool: asyncpg.Pool
    ) -> None:
        """`get_active_facts` duplicates its query for the topic filter, and one
        branch keeping the old predicate is exactly how expiry regresses."""
        chat_id = -910108
        _, tbilisi_today = await _db_clock(db_pool)
        await repo.append_fact(
            chat_id=chat_id,
            subject="вчера",
            predicate=capture.fact_predicate(21),
            value="v",
            fact_text="истекло вчера",
            source="manual",
            topic="event:встреча",
            expires_at=capture.end_of_day(tbilisi_today - timedelta(days=1)),
        )
        live_id, _ = await repo.append_fact(
            chat_id=chat_id,
            subject="сегодня",
            predicate=capture.fact_predicate(22),
            value="v",
            fact_text="истекает сегодня",
            source="manual",
            topic="event:встреча",
            expires_at=capture.end_of_day(tbilisi_today),
        )
        facts = await repo.get_active_facts(chat_id, topic="event:встреча")
        assert [f["id"] for f in facts] == [live_id]


# ---------------------------------------------------------------------------
# embedding backfill eligibility
# ---------------------------------------------------------------------------


class TestPendingEmbeddings:
    @pytest.mark.asyncio
    async def test_appended_row_without_embedding_is_pending_unless_expired(
        self, repo: KnowledgeRepository, db_pool: asyncpg.Pool
    ) -> None:
        """`/remember` stores the fact even when the embedding call fails, so the
        row has to be reachable by the backfill worker -- otherwise a provider
        blip makes a fact permanently unretrievable while `/kb` still shows it.

        And the mirror of that: a fact whose deadline has already passed must
        NOT be embedded. Nothing will ever return it, so the call is spend with
        no possible reader.

        Membership assertions, not equality: `get_pending_embeddings` is
        deliberately not chat-scoped (the worker drains one global queue), so
        rows from other tests in this session are legitimately in the result.
        """
        chat_id = -910109
        _, tbilisi_today = await _db_clock(db_pool)

        pending_id, _ = await repo.append_fact(
            chat_id=chat_id,
            subject="без вектора",
            predicate=capture.fact_predicate(31),
            value="v",
            fact_text="эмбеддинг не получился",
            source="manual",
            embedding=None,
        )
        expired_id, _ = await repo.append_fact(
            chat_id=chat_id,
            subject="просрочено",
            predicate=capture.fact_predicate(32),
            value="v",
            fact_text="срок вышел",
            source="manual",
            embedding=None,
            expires_at=capture.end_of_day(tbilisi_today - timedelta(days=1)),
        )
        embedded_id, _ = await repo.append_fact(
            chat_id=chat_id,
            subject="с вектором",
            predicate=capture.fact_predicate(33),
            value="v",
            fact_text="уже есть вектор",
            source="manual",
            embedding=_one_hot(0),
        )
        # The fixtures are what the assertions below depend on -- assert their
        # state before reading anything into the result.
        assert (await _row(db_pool, pending_id))["no_embedding"] is True
        assert (await _row(db_pool, expired_id))["no_embedding"] is True
        assert (await _row(db_pool, embedded_id))["no_embedding"] is False

        pending = {r["id"] for r in await repo.get_pending_embeddings(1000)}
        assert pending_id in pending
        assert expired_id not in pending, "an unretrievable row must not cost an embedding call"
        assert embedded_id not in pending

    @pytest.mark.asyncio
    async def test_exclude_ids_parks_a_row_the_worker_already_holds(
        self, repo: KnowledgeRepository
    ) -> None:
        """The worker's FIFO queue re-reads the same head until it succeeds; a
        parked row must drop out or the queue never advances."""
        chat_id = -910110
        fact_id, _ = await repo.append_fact(
            chat_id=chat_id,
            subject="s",
            predicate=capture.fact_predicate(41),
            value="v",
            fact_text="ft",
            source="manual",
            embedding=None,
        )
        assert fact_id in {r["id"] for r in await repo.get_pending_embeddings(1000)}
        assert fact_id not in {
            r["id"] for r in await repo.get_pending_embeddings(1000, exclude_ids=[fact_id])
        }
