"""Migration 036: joins, leaves and returns, against a real PostgreSQL.

The transitions are single statements whose correctness lives entirely in their
WHERE clauses and COALESCEs, so no mock can exercise them. Each ordering rule has
a test that fails when that rule is removed (see the module docstring of
`member_state.py`); the concurrency tests run the two paths a join really takes
at the same moment.

Every id below is invented.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.database.repositories.member_state import ChatMemberStateRepository
from src.database.repositories.messages import MessageRepository

CHAT_ID = -1009990361
USER = 900361
OTHER = 900362

T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def repo(db_pool: asyncpg.Pool) -> ChatMemberStateRepository:
    return ChatMemberStateRepository(db_pool)


@pytest.fixture(autouse=True)
async def _clean(db_pool: asyncpg.Pool):
    async def wipe() -> None:
        await db_pool.execute("DELETE FROM chat_member_state WHERE chat_id = $1", CHAT_ID)
        await db_pool.execute("DELETE FROM chat_messages WHERE chat_id = $1", CHAT_ID)

    await wipe()
    yield
    await wipe()


async def _row(db_pool: asyncpg.Pool, user_id: int = USER) -> dict:
    row = await db_pool.fetchrow(
        "SELECT status, join_count, leave_count, pending_greeting "
        "FROM chat_member_state WHERE chat_id = $1 AND user_id = $2",
        CHAT_ID,
        user_id,
    )
    return dict(row) if row else {}


class TestSchema:
    async def test_per_chat_columns_are_nullable_with_no_default(self, db_pool: asyncpg.Pool):
        rows = await db_pool.fetch(
            """
            SELECT column_name, is_nullable, column_default
              FROM information_schema.columns
             WHERE table_name = 'chat_settings'
               AND column_name IN ('community_enabled', 'greet_on_first_message',
                                   'welcome_back_enabled', 'farewell_public', 'farewell_dm',
                                   'returner_joke_enabled', 'community_templates')
            """
        )
        assert len(rows) == 7
        assert {(r["is_nullable"], r["column_default"]) for r in rows} == {("YES", None)}


class TestJoin:
    async def test_first_join_is_a_newcomer(self, db_pool, repo):
        outcome = await repo.record_join(CHAT_ID, USER, T0, greet=True)

        assert outcome is not None and outcome.kind == "new"
        assert await _row(db_pool) == {
            "status": "member",
            "join_count": 1,
            "leave_count": 0,
            "pending_greeting": "new",
        }

    async def test_a_duplicate_join_changes_nothing(self, repo):
        await repo.record_join(CHAT_ID, USER, T0, greet=True)

        assert await repo.record_join(CHAT_ID, USER, T0, greet=True) is None

    async def test_join_after_leave_is_a_return(self, db_pool, repo):
        await repo.record_join(CHAT_ID, USER, T0, greet=True)
        await repo.claim_greeting(CHAT_ID, USER)
        await repo.record_leave(CHAT_ID, USER, T0 + timedelta(days=1), status="left")

        outcome = await repo.record_join(CHAT_ID, USER, T0 + timedelta(days=2), greet=True)

        assert outcome is not None and outcome.kind == "back"
        assert (outcome.join_count, outcome.leave_count) == (2, 1)
        assert (await _row(db_pool))["pending_greeting"] == "back"

    async def test_greet_false_records_without_a_pending_greeting(self, db_pool, repo):
        # Community mode off: the state is kept, but nothing is owed.
        await repo.record_join(CHAT_ID, USER, T0, greet=False)

        assert (await _row(db_pool))["pending_greeting"] is None
        assert await repo.claim_greeting(CHAT_ID, USER) is None


class TestOrdering:
    async def test_a_join_duplicate_in_the_same_second_as_the_leave_stays_gone(self, db_pool, repo):
        # A spam account banned the moment it joined: the chat_member update of
        # the join can arrive after the ban. It must not resurrect the account
        # or produce "welcome back".
        await repo.record_join(CHAT_ID, USER, T0, greet=True)
        await repo.record_leave(CHAT_ID, USER, T0, status="kicked")

        late_duplicate = await repo.record_join(CHAT_ID, USER, T0, greet=True)

        assert late_duplicate is None, "same-second join after a leave counted as a return"
        assert (await _row(db_pool))["status"] == "kicked"

    async def test_a_leave_in_the_same_second_as_the_join_wins(self, db_pool, repo):
        await repo.record_join(CHAT_ID, USER, T0, greet=True)

        outcome = await repo.record_leave(CHAT_ID, USER, T0, status="kicked")

        assert outcome is not None, "same-second leave ignored -- the spammer stays a member"
        assert await _row(db_pool) == {
            "status": "kicked",
            "join_count": 1,
            "leave_count": 1,
            "pending_greeting": None,
        }

    async def test_a_stale_leave_does_not_undo_a_later_join(self, db_pool, repo):
        await repo.record_leave(CHAT_ID, USER, T0 + timedelta(hours=1), status="left")
        await repo.record_join(CHAT_ID, USER, T0 + timedelta(hours=2), greet=True)

        assert await repo.record_leave(CHAT_ID, USER, T0, status="left") is None
        assert (await _row(db_pool))["status"] == "member"

    async def test_a_stale_join_does_not_undo_a_later_leave(self, db_pool, repo):
        await repo.record_join(CHAT_ID, USER, T0, greet=True)
        await repo.record_leave(CHAT_ID, USER, T0 + timedelta(hours=2), status="left")

        assert await repo.record_join(CHAT_ID, USER, T0 + timedelta(hours=1), greet=True) is None
        assert (await _row(db_pool))["status"] == "left"


class TestUnknownAndSeeded:
    async def test_a_silent_member_who_leaves_and_returns_is_welcomed_back(self, db_pool, repo):
        # Present before the bot arrived, never wrote: no row at all.
        leave = await repo.record_leave(CHAT_ID, USER, T0, status="left")

        assert leave is not None and leave.joined_at is None
        assert (await _row(db_pool))["status"] == "left"
        join = await repo.record_join(CHAT_ID, USER, T0 + timedelta(days=3), greet=True)
        assert join is not None and join.kind == "back"

    async def test_a_seeded_member_who_joins_is_a_return(self, db_pool, repo):
        # `seen` rows carry NULL join/leave times; a bare comparison against
        # NULL would make every transition below silently not happen.
        await db_pool.execute(
            "INSERT INTO chat_member_state (chat_id, user_id, status, first_seen_at) "
            "VALUES ($1, $2, 'seen', $3)",
            CHAT_ID,
            USER,
            T0,
        )

        join = await repo.record_join(CHAT_ID, USER, T0 + timedelta(days=1), greet=True)

        assert join is not None and join.kind == "back"

    async def test_a_seeded_member_who_leaves_reports_the_first_message(self, repo, db_pool):
        await db_pool.execute(
            "INSERT INTO chat_member_state (chat_id, user_id, status, first_seen_at) "
            "VALUES ($1, $2, 'seen', $3)",
            CHAT_ID,
            USER,
            T0,
        )

        leave = await repo.record_leave(CHAT_ID, USER, T0 + timedelta(days=5), status="left")

        assert leave is not None and leave.joined_at == T0

    async def test_leave_rejects_a_status_that_is_not_a_leave(self, repo):
        with pytest.raises(ValueError, match="not a leave status"):
            await repo.record_leave(CHAT_ID, USER, T0, status="member")


class TestClaim:
    async def test_the_greeting_is_claimed_once(self, repo):
        await repo.record_join(CHAT_ID, USER, datetime.now(UTC), greet=True)

        assert await repo.claim_greeting(CHAT_ID, USER) == "new"
        assert await repo.claim_greeting(CHAT_ID, USER) is None

    async def test_an_old_join_is_not_greeted(self, repo):
        await repo.record_join(CHAT_ID, USER, datetime.now(UTC) - timedelta(days=60), greet=True)

        assert await repo.claim_greeting(CHAT_ID, USER) is None

    async def test_nobody_else_is_claimed(self, repo):
        await repo.record_join(CHAT_ID, USER, datetime.now(UTC), greet=True)

        assert await repo.claim_greeting(CHAT_ID, OTHER) is None
        assert await repo.claim_greeting(CHAT_ID, USER) == "new"


class TestConcurrency:
    """The two ways a join arrives, at the same moment."""

    async def test_two_concurrent_joins_make_one_newcomer(self, repo):
        at = datetime.now(UTC)

        outcomes = await asyncio.gather(
            *(repo.record_join(CHAT_ID, USER, at, greet=True) for _ in range(8))
        )

        assert [o.kind for o in outcomes if o is not None] == ["new"], outcomes

    async def test_a_claim_that_waited_on_another_finds_nothing(self, db_pool, repo):
        # Deterministic, not a gather: a race the scheduler decides passes
        # whether or not the claim is safe. Here the second claim is made to
        # take its snapshot while the first holds the row, so it starts out
        # seeing 'new' and must re-check after the first commits.
        await repo.record_join(CHAT_ID, USER, datetime.now(UTC), greet=True)

        async with db_pool.acquire() as first:
            tx = first.transaction()
            await tx.start()
            await first.execute(
                "UPDATE chat_member_state SET pending_greeting = NULL, greeted_at = now() "
                "WHERE chat_id = $1 AND user_id = $2",
                CHAT_ID,
                USER,
            )
            second = asyncio.create_task(repo.claim_greeting(CHAT_ID, USER))
            for _ in range(200):
                waiting = await db_pool.fetchval(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND query LIKE '%pending_greeting%'"
                )
                if waiting:
                    break
                await asyncio.sleep(0.01)
            assert waiting, "the second claim never blocked on the first -- the test proves nothing"
            await tx.commit()

        assert await second is None, "a greeting already taken was claimed a second time"


class TestPriorMessages:
    """No row, but the archive knows them: the bot cannot list members."""

    async def test_someone_who_wrote_before_is_a_return(self, db_pool, repo):
        messages = MessageRepository(db_pool)
        await messages.save(CHAT_ID, 1, "text", user_id=USER, content="a")

        join = await repo.record_join(
            CHAT_ID, USER, datetime.now(UTC) + timedelta(minutes=1), greet=True
        )

        assert join is not None and join.kind == "back"
        assert (await _row(db_pool))["pending_greeting"] == "back"

    async def test_a_message_after_the_join_does_not_make_a_return(self, db_pool, repo):
        # The join update arrived late, after their first message was saved.
        messages = MessageRepository(db_pool)
        await messages.save(CHAT_ID, 1, "text", user_id=USER, content="hi")

        join = await repo.record_join(
            CHAT_ID, USER, datetime.now(UTC) - timedelta(minutes=1), greet=True
        )

        assert join is not None and join.kind == "new"

    async def test_someone_elses_messages_do_not_count(self, db_pool, repo):
        messages = MessageRepository(db_pool)
        await messages.save(CHAT_ID, 1, "text", user_id=OTHER, content="a")

        join = await repo.record_join(
            CHAT_ID, USER, datetime.now(UTC) + timedelta(minutes=1), greet=True
        )

        assert join is not None and join.kind == "new"

    async def test_the_kind_is_known_even_when_nothing_is_owed(self, db_pool, repo):
        messages = MessageRepository(db_pool)
        await messages.save(CHAT_ID, 1, "text", user_id=USER, content="a")

        join = await repo.record_join(
            CHAT_ID, USER, datetime.now(UTC) + timedelta(minutes=1), greet=False
        )

        assert join is not None and join.kind == "back"
        assert (await _row(db_pool))["pending_greeting"] is None

    async def test_message_span(self, db_pool, repo):
        messages = MessageRepository(db_pool)
        assert await repo.message_span(CHAT_ID, USER) == (None, None)
        await messages.save(CHAT_ID, 1, "text", user_id=USER, content="a")
        await messages.save(CHAT_ID, 2, "text", user_id=USER, content="b")

        first, last = await repo.message_span(CHAT_ID, USER)

        assert first is not None and last is not None and first <= last
