"""Migration 037 and AdviceRepository against a real PostgreSQL.

The worker's unit tests run on an in-memory fake with this repository's
contract; these tests are what makes that fake honest -- which rows count as a
candidate, that a verdict and its check-off are one write, and what the digest
reads. Every id and name below is invented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.database.repositories.advice import AdviceRepository
from src.services.advice.verdict import Verdict

CHAT_ID = -1009999990391
OTHER_CHAT = -1009999990392
MEMBER = 900391
ADMIN = 900392
OTHER_BOT = 900393
TELEGRAM = 777000
GROUP_ANON = 1087968824
T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
SINCE = T0 - timedelta(hours=1)

UNSOLICITED = Verdict(advice="unsolicited", level="hard", support=0, constructive=False)
NOTHING = Verdict(advice="none", level=None, support=0, constructive=True)
SUPPORT = Verdict(advice="none", level=None, support=2, constructive=True)


@pytest.fixture
def repo(db_pool: asyncpg.Pool) -> AdviceRepository:
    return AdviceRepository(db_pool)


@pytest.fixture(autouse=True)
async def _clean(db_pool: asyncpg.Pool):
    async def wipe() -> None:
        for table in (
            "advice_checked",
            "advice_verdicts",
            "advice_post_facts",
            "chat_messages",
            "chat_settings",
        ):
            await db_pool.execute(
                f"DELETE FROM {table} WHERE chat_id = ANY($1::bigint[])",  # noqa: S608 -- local constant
                [CHAT_ID, OTHER_CHAT],
            )
        await db_pool.execute("DELETE FROM response_log WHERE task_type = 'advice_watch'")

    await wipe()
    yield
    await wipe()


async def _msg(
    db_pool: asyncpg.Pool,
    message_id: int,
    *,
    user_id: int | None = MEMBER,
    content: str | None = "попробуй медитацию",
    at: datetime = T0,
    chat_id: int = CHAT_ID,
    sender_kind: str | None = None,
    is_bot: bool = False,
    first_name: str | None = "Аня",
) -> None:
    await db_pool.execute(
        """
        INSERT INTO chat_messages (chat_id, message_id, user_id, first_name, message_type,
                                   content, created_at, sender_kind, is_bot_message)
        VALUES ($1, $2, $3, $4, 'text', $5, $6, $7, $8)
        """,
        chat_id,
        message_id,
        user_id,
        first_name,
        content,
        at,
        sender_kind,
        is_bot,
    )


async def _save(repo: AdviceRepository, ids: list[int], verdict: Verdict = UNSOLICITED) -> bool:
    return await repo.save_verdict(
        CHAT_ID,
        user_id=MEMBER,
        message_ids=ids,
        post_message_id=None,
        facts={"what": "советует медитацию"},
        verdict=verdict,
        model="@cf/test",
    )


class TestSchema:
    async def test_the_chat_flag_is_nullable_with_no_default(self, db_pool):
        row = await db_pool.fetchrow(
            """
            SELECT is_nullable, column_default FROM information_schema.columns
             WHERE table_name = 'chat_settings' AND column_name = 'advice_watch_enabled'
            """
        )
        assert row is not None
        assert (row["is_nullable"], row["column_default"]) == ("YES", None)


class TestCandidates:
    async def test_only_members_unchecked_text_in_the_window(self, db_pool, repo):
        await _msg(db_pool, 1)
        # Placeholder senders carry their placeholder `from` id, as stored in production.
        await _msg(db_pool, 2, user_id=TELEGRAM, sender_kind="channel_post")
        await _msg(db_pool, 3, user_id=GROUP_ANON, sender_kind="anon_admin", is_bot=True)
        await _msg(db_pool, 4, user_id=OTHER_BOT, is_bot=True)
        await _msg(db_pool, 5, user_id=ADMIN)
        await _msg(db_pool, 6, content="   ")
        await _msg(db_pool, 7, content=None)
        await _msg(db_pool, 8, at=SINCE - timedelta(seconds=1))
        await _msg(db_pool, 9, chat_id=OTHER_CHAT)
        await _msg(db_pool, 10, at=T0 + timedelta(seconds=5))
        await repo.mark_checked(CHAT_ID, [10])

        got = await repo.unchecked_candidates(CHAT_ID, SINCE, exclude_user_ids=[ADMIN])

        assert [x.message_id for x in got] == [1]

    async def test_no_admins_excludes_no_one(self, db_pool, repo):
        await _msg(db_pool, 1, user_id=ADMIN)
        got = await repo.unchecked_candidates(CHAT_ID, SINCE)
        assert [x.message_id for x in got] == [1]

    async def test_timeline_keeps_every_kind_of_row_in_time_order(self, db_pool, repo):
        await _msg(db_pool, 2, at=T0 + timedelta(seconds=10))
        await _msg(db_pool, 1, user_id=None, sender_kind="channel_post", content="пост")
        got = await repo.timeline(CHAT_ID, SINCE)
        assert [(x.message_id, x.is_post) for x in got] == [(1, True), (2, False)]


class TestVerdicts:
    async def test_saving_checks_off_the_whole_series(self, db_pool, repo):
        for i in (1, 2, 3):
            await _msg(db_pool, i, at=T0 + timedelta(seconds=i))

        assert await _save(repo, [1, 2, 3]) is True

        assert await repo.unchecked_candidates(CHAT_ID, SINCE) == []
        row = await db_pool.fetchrow(
            "SELECT first_message_id, last_message_id, message_ids, advice, facts "
            "FROM advice_verdicts WHERE chat_id = $1",
            CHAT_ID,
        )
        assert (row["first_message_id"], row["last_message_id"]) == (1, 3)
        assert list(row["message_ids"]) == [1, 2, 3]
        assert row["advice"] == "unsolicited"

    async def test_a_second_save_of_the_same_series_is_a_no_op(self, db_pool, repo):
        await _msg(db_pool, 1)
        assert await _save(repo, [1]) is True
        assert await _save(repo, [1], NOTHING) is False
        assert (
            await db_pool.fetchval("SELECT advice FROM advice_verdicts WHERE chat_id = $1", CHAT_ID)
            == "unsolicited"
        )

    async def test_post_facts_round_trip_and_first_write_wins(self, repo):
        assert await repo.get_post_facts(CHAT_ID, 7) is None
        await repo.put_post_facts(CHAT_ID, 7, {"hard_state": True, "what": "пост"})
        await repo.put_post_facts(CHAT_ID, 7, {"hard_state": False})
        assert await repo.get_post_facts(CHAT_ID, 7) == {"hard_state": True, "what": "пост"}


class TestDigest:
    async def test_findings_only_with_name_quote_and_chat(self, db_pool, repo):
        await db_pool.execute(
            "INSERT INTO chat_settings (chat_id, chat_title, chat_type) VALUES ($1, $2, $3)",
            CHAT_ID,
            "Блог",
            "supergroup",
        )
        await _msg(db_pool, 1, content="раз")
        await _msg(db_pool, 2, content="два", at=T0 + timedelta(seconds=1))
        await _msg(db_pool, 3, content="держись")
        await _msg(db_pool, 4, content="ок")
        await _save(repo, [1, 2])
        await _save(repo, [3], SUPPORT)
        await _save(repo, [4], NOTHING)

        items = await repo.digest_items(datetime.now(UTC) + timedelta(minutes=1))

        assert [(x.first_message_id, x.quote) for x in items] == [(1, "раз\nдва"), (3, "держись")]
        first = items[0]
        assert (first.chat_title, first.chat_type, first.name, first.what) == (
            "Блог",
            "supergroup",
            "Аня",
            "советует медитацию",
        )

    async def test_a_deleted_message_leaves_an_empty_quote_not_a_missing_finding(
        self, db_pool, repo
    ):
        await _msg(db_pool, 1)
        await _save(repo, [1])
        await db_pool.execute("DELETE FROM chat_messages WHERE chat_id = $1", CHAT_ID)

        (item,) = await repo.digest_items(datetime.now(UTC) + timedelta(minutes=1))

        assert (item.quote, item.name, item.chat_title) == ("", None, None)

    async def test_mark_notified_closes_everything_up_to_the_cutoff(self, db_pool, repo):
        await _msg(db_pool, 1)
        await _msg(db_pool, 2)
        await _save(repo, [1])
        await _save(repo, [2], NOTHING)
        before = datetime.now(UTC) - timedelta(minutes=1)
        assert await repo.mark_notified(before) == 0

        cutoff = datetime.now(UTC) + timedelta(minutes=1)
        assert await repo.mark_notified(cutoff) == 2
        assert await repo.digest_items(cutoff) == []


class TestCalls:
    async def test_counts_only_this_task_since_the_moment(self, db_pool, repo):
        now = datetime.now(UTC)
        for task, at in (
            ("advice_watch", now),
            ("advice_watch", now - timedelta(minutes=5)),
            ("advice_watch", now - timedelta(days=2)),
            ("text", now),
        ):
            await db_pool.execute(
                "INSERT INTO response_log (chat_id, task_type, created_at) VALUES ($1, $2, $3)",
                0,
                task,
                at,
            )
        try:
            assert await repo.calls_since(now - timedelta(hours=1)) == 2
        finally:
            await db_pool.execute(
                "DELETE FROM response_log WHERE chat_id = 0 AND task_type = 'text' "
                "AND created_at = $1",
                now,
            )
