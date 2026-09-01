"""The one-off repair for content an ``edited_message`` erased.

Driven against real PostgreSQL rather than a mocked pool, because everything
this script is has to be true of the SQL: the predicate that decides which
production rows get written, the ``UPDATE`` command tag it counts, and the
watermark subquery that tells an operator whether a repaired row will ever
reach the chunk archive. A mocked pool would assert the strings back at me.

The negative cases matter as much as the positive one. This script's whole risk
is writing to rows it was not meant to touch, in a table where the column it
reads from is the only surviving copy of the text.
"""

from __future__ import annotations

import asyncpg
import pytest

from scripts.repair_wiped_media_content import find_wiped, repair
from src.database.repositories.messages import MessageRepository

CHAT_ID = -100999000222


@pytest.fixture
def repo(db_pool: asyncpg.Pool) -> MessageRepository:
    return MessageRepository(db_pool)


@pytest.fixture(autouse=True)
async def _clean(db_pool: asyncpg.Pool):
    await db_pool.execute("DELETE FROM chat_messages WHERE chat_id = $1", CHAT_ID)
    await db_pool.execute("DELETE FROM chat_chunks WHERE chat_id = $1", CHAT_ID)
    yield
    await db_pool.execute("DELETE FROM chat_messages WHERE chat_id = $1", CHAT_ID)
    await db_pool.execute("DELETE FROM chat_chunks WHERE chat_id = $1", CHAT_ID)


async def _wiped_row(
    db_pool: asyncpg.Pool, message_id: int, text: str, *, message_type: str = "voice"
) -> None:
    """A row in exactly the state production was left in.

    Written with direct SQL on purpose: `MessageRepository.save()` can no longer
    produce this state, which is the entire point of the fix. Reproducing the
    damage through the fixed code is impossible, so the fixture states the
    damaged shape outright.
    """
    await db_pool.execute(
        """
        INSERT INTO chat_messages
            (chat_id, message_id, message_type, user_id, first_name,
             content, original_content, edit_count, edited_at)
        VALUES ($1, $2, $3, 555, 'Иван', NULL, $4, 2, NOW())
        """,
        CHAT_ID,
        message_id,
        message_type,
        text,
    )


async def _content(db_pool: asyncpg.Pool, message_id: int) -> str | None:
    return await db_pool.fetchval(
        "SELECT content FROM chat_messages WHERE chat_id = $1 AND message_id = $2",
        CHAT_ID,
        message_id,
    )


class TestRepair:
    async def test_a_wiped_transcript_comes_back(self, db_pool: asyncpg.Pool):
        await _wiped_row(db_pool, 1, "давайте в субботу")

        assert await repair(db_pool, apply=True) == 1

        assert await _content(db_pool, 1) == "давайте в субботу"

    async def test_a_dry_run_writes_nothing(self, db_pool: asyncpg.Pool):
        await _wiped_row(db_pool, 1, "давайте в субботу")

        assert await repair(db_pool, apply=False) == 0

        assert await _content(db_pool, 1) is None
        assert len(await find_wiped(db_pool)) == 1, "the dry run must still report the row"

    async def test_running_twice_changes_nothing_the_second_time(self, db_pool: asyncpg.Pool):
        await _wiped_row(db_pool, 1, "давайте в субботу")

        assert await repair(db_pool, apply=True) == 1
        assert await repair(db_pool, apply=True) == 0

        assert await _content(db_pool, 1) == "давайте в субботу"

    async def test_original_content_is_left_in_place(self, db_pool: asyncpg.Pool):
        """It is the only trace that a row was ever damaged, and nothing reads
        it, so clearing it would buy nothing and cost the audit trail."""
        await _wiped_row(db_pool, 1, "давайте в субботу")

        await repair(db_pool, apply=True)

        assert (
            await db_pool.fetchval(
                "SELECT original_content FROM chat_messages WHERE chat_id = $1 AND message_id = $2",
                CHAT_ID,
                1,
            )
            == "давайте в субботу"
        )

    async def test_edit_bookkeeping_is_not_rewritten(self, db_pool: asyncpg.Pool):
        """An edited_message really did arrive. Hiding that would be a second
        falsification on top of the first."""
        await _wiped_row(db_pool, 1, "давайте в субботу")
        before = await db_pool.fetchrow(
            "SELECT edit_count, edited_at FROM chat_messages "
            "WHERE chat_id = $1 AND message_id = $2",
            CHAT_ID,
            1,
        )

        await repair(db_pool, apply=True)

        after = await db_pool.fetchrow(
            "SELECT edit_count, edited_at FROM chat_messages "
            "WHERE chat_id = $1 AND message_id = $2",
            CHAT_ID,
            1,
        )
        assert after["edit_count"] == before["edit_count"] == 2
        assert after["edited_at"] == before["edited_at"]

    async def test_every_damaged_type_is_covered(self, db_pool: asyncpg.Pool):
        """Production held three: 29 voice, 11 video_note, 12 photo."""
        await _wiped_row(db_pool, 1, "расшифровка", message_type="voice")
        await _wiped_row(db_pool, 2, "расшифровка кружочка", message_type="video_note")
        await _wiped_row(db_pool, 3, "[Image: кот на подоконнике]", message_type="photo")

        assert await repair(db_pool, apply=True) == 3

        assert await _content(db_pool, 3) == "[Image: кот на подоконнике]"


class TestRepairLeavesEverythingElseAlone:
    """The cases whose expected answer is "do not touch this row"."""

    async def test_a_healthy_row_is_not_rewritten(
        self, db_pool: asyncpg.Pool, repo: MessageRepository
    ):
        """A row that was edited normally has BOTH columns populated. Restoring
        it would undo a real edit — reverting the user's own correction."""
        await repo.save(CHAT_ID, 10, "text", user_id=555, content="первая редакция")
        await repo.save(CHAT_ID, 10, "text", user_id=555, content="вторая редакция")

        assert await repair(db_pool, apply=True) == 0

        assert await _content(db_pool, 10) == "вторая редакция"

    async def test_a_row_that_never_had_content_is_not_invented(self, db_pool: asyncpg.Pool):
        """A sticker, or a voice note whose download failed before Whisper ever
        ran, has NULL in both columns. There is nothing to restore and the
        script must not pretend otherwise."""
        await db_pool.execute(
            "INSERT INTO chat_messages (chat_id, message_id, message_type, user_id) "
            "VALUES ($1, 11, 'sticker', 555)",
            CHAT_ID,
        )

        assert await repair(db_pool, apply=True) == 0

        assert await _content(db_pool, 11) is None

    async def test_another_chat_is_not_scoped_out_but_is_reported_separately(
        self, db_pool: asyncpg.Pool
    ):
        """The repair is deliberately global — the damage was, too. The report
        carries chat_id so an operator can still see the split."""
        await _wiped_row(db_pool, 1, "расшифровка")

        rows = await find_wiped(db_pool)

        assert [row["chat_id"] for row in rows if row["chat_id"] == CHAT_ID] == [CHAT_ID]


class TestArchiveProspects:
    """`above_watermark` is what tells an operator whether a repaired row will
    ever reach `chat_chunks`. It drives the decision to run this sooner rather
    than later, so a wrong answer here is worse than no answer."""

    async def _chunk(self, db_pool: asyncpg.Pool, msg_from: int, msg_to: int) -> None:
        await db_pool.execute(
            """
            INSERT INTO chat_chunks
                (chat_id, thread_id, msg_from, msg_to, part, content, msg_count,
                 senders, started_at, ended_at)
            VALUES ($1, NULL, $2, $3, 0, 'chunk text', 1, ARRAY[555]::bigint[], NOW(), NOW())
            """,
            CHAT_ID,
            msg_from,
            msg_to,
        )

    async def test_a_row_ahead_of_the_watermark_still_reaches_the_archive(
        self, db_pool: asyncpg.Pool
    ):
        await self._chunk(db_pool, 1, 100)
        await _wiped_row(db_pool, 200, "ещё не заархивировано")

        rows = await find_wiped(db_pool)

        assert [row["above_watermark"] for row in rows] == [True]

    async def test_a_row_behind_the_watermark_is_reported_as_lost_to_the_archive(
        self, db_pool: asyncpg.Pool
    ):
        """The watermark is `MAX(msg_to)` and the chunker fetches
        `message_id > watermark`, so a maximum never moves backwards and this
        row is never re-read — restoring its content repairs the prompt history
        and `/summary`, but not the archive."""
        await self._chunk(db_pool, 1, 100)
        await _wiped_row(db_pool, 50, "архив это уже не увидит")

        rows = await find_wiped(db_pool)

        assert [row["above_watermark"] for row in rows] == [False]

    async def test_the_watermark_is_the_highest_chunk_not_the_lowest(self, db_pool: asyncpg.Pool):
        """With one chunk in the fixture, `max(msg_to)` and `min(msg_to)` are
        the same number and every assertion above passes either way — a
        mutation from max to min survived the whole class. Two chunks with a
        damaged row between them is what makes the aggregate falsifiable.
        """
        await self._chunk(db_pool, 1, 100)
        await self._chunk(db_pool, 101, 200)
        await _wiped_row(db_pool, 150, "внутри уже покрытого диапазона")

        rows = await find_wiped(db_pool)

        assert [row["above_watermark"] for row in rows] == [False], (
            "150 is behind max(msg_to)=200; reading the watermark as min(msg_to)=100 "
            "would wrongly promise this row reaches the archive"
        )

    async def test_a_chat_with_no_chunks_at_all_counts_as_ahead(self, db_pool: asyncpg.Pool):
        """COALESCE(..., 0) — an unindexed chat has everything still to come,
        not nothing."""
        await _wiped_row(db_pool, 1, "чат ещё не индексировался")

        rows = await find_wiped(db_pool)

        assert [row["above_watermark"] for row in rows] == [True]
