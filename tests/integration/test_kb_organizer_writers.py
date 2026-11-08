"""Integration tests: the organizer list's writers and its name store against real Postgres.

What a mock cannot show: that one ``UPDATE ... RETURNING`` is what makes two
writes that race both land, that a chat without a row answers ``None`` instead
of a list nobody stored, and what ``jsonb_insert`` does with a position past the
end. Plus the names side of the same add: a name taken from the chat history
only fills a gap, it never replaces one an add path was given.

Fixture discipline is the organizer-names tests' one: ``db_pool``, isolation by
a chat-id band nothing else uses (``-97xxxx``: checked with
``find tests -name '*.py' -exec /usr/bin/grep -lE '\\-97[0-9_]{3,}' {} +``, which
finds the ``-96`` band of that file as a control), and invented ids and names only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.organizer_names import KnownName, OrganizerNameRepository
from src.utils import parse_user_id_list

CHAT = -970_001
GONE_CHAT = -970_002
ANNA = 113_001
BORIS = 113_002
VERA = 113_003
GLEB = 113_004
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def repo(db_pool: asyncpg.Pool) -> ChatSettingsRepository:
    return ChatSettingsRepository(db_pool)


@pytest_asyncio.fixture(autouse=True)
async def _clean(db_pool: asyncpg.Pool):
    async def wipe() -> None:
        for table in ("kb_organizer_names", "chat_messages", "chat_settings"):
            await db_pool.execute(
                f"DELETE FROM {table} WHERE chat_id = ANY($1::bigint[])",  # noqa: S608 -- local constant
                [CHAT, GONE_CHAT],
            )

    await wipe()
    yield
    await wipe()


async def _chat(pool: asyncpg.Pool, stored: str = "[]") -> None:
    """A chat_settings row whose organizers are the JSON text ``stored``."""
    await pool.execute(
        "INSERT INTO chat_settings (chat_id, kb_organizer_ids) VALUES ($1, $2::jsonb)",
        CHAT,
        stored,
    )


async def _stored(pool: asyncpg.Pool) -> str:
    """The column as Postgres prints it, not as the repository parses it."""
    value: str = await pool.fetchval(
        "SELECT kb_organizer_ids::text FROM chat_settings WHERE chat_id = $1", CHAT
    )
    return value


class TestAdd:
    async def test_appends_and_returns_the_stored_list(
        self, repo: ChatSettingsRepository, db_pool: asyncpg.Pool
    ) -> None:
        await _chat(db_pool, f"[{ANNA}]")

        assert await repo.add_kb_organizer(CHAT, BORIS) == [ANNA, BORIS]
        assert await _stored(db_pool) == f"[{ANNA}, {BORIS}]"

    async def test_someone_already_there_is_not_added_twice(
        self, repo: ChatSettingsRepository, db_pool: asyncpg.Pool
    ) -> None:
        await _chat(db_pool, f"[{ANNA}, {BORIS}]")

        assert await repo.add_kb_organizer(CHAT, ANNA) == [ANNA, BORIS]
        assert await _stored(db_pool) == f"[{ANNA}, {BORIS}]"

    async def test_a_chat_without_a_row_is_none_and_gets_no_row(
        self, repo: ChatSettingsRepository, db_pool: asyncpg.Pool
    ) -> None:
        assert await repo.add_kb_organizer(GONE_CHAT, ANNA) is None
        rows = await db_pool.fetchval(
            "SELECT count(*) FROM chat_settings WHERE chat_id = $1", GONE_CHAT
        )
        assert rows == 0

    @pytest.mark.parametrize(
        "malformed", ['{"a": 1}', '"[1, 2]"', '["x"]', f'[{ANNA}, "x"]', "null"]
    )
    async def test_a_malformed_value_starts_from_an_empty_list(
        self, repo: ChatSettingsRepository, db_pool: asyncpg.Pool, malformed: str
    ) -> None:
        """`parse_user_id_list` reads all of these as nobody; an add on top of
        them would store a list that still reads as nobody."""
        await _chat(db_pool, malformed)

        assert await repo.add_kb_organizer(CHAT, BORIS) == [BORIS]
        assert await _stored(db_pool) == f"[{BORIS}]"


class TestRemove:
    async def test_takes_the_person_out_and_keeps_the_order(
        self, repo: ChatSettingsRepository, db_pool: asyncpg.Pool
    ) -> None:
        await _chat(db_pool, f"[{ANNA}, {BORIS}, {VERA}]")

        assert await repo.remove_kb_organizer(CHAT, BORIS) == [ANNA, VERA]
        assert await _stored(db_pool) == f"[{ANNA}, {VERA}]"

    async def test_the_last_one_leaves_an_empty_list_not_null(
        self, repo: ChatSettingsRepository, db_pool: asyncpg.Pool
    ) -> None:
        await _chat(db_pool, f"[{ANNA}]")

        assert await repo.remove_kb_organizer(CHAT, ANNA) == []
        assert await _stored(db_pool) == "[]"

    async def test_someone_not_there_changes_nothing(
        self, repo: ChatSettingsRepository, db_pool: asyncpg.Pool
    ) -> None:
        await _chat(db_pool, f"[{ANNA}]")

        assert await repo.remove_kb_organizer(CHAT, BORIS) == [ANNA]

    async def test_a_chat_without_a_row_is_none(self, repo: ChatSettingsRepository) -> None:
        assert await repo.remove_kb_organizer(GONE_CHAT, ANNA) is None


class TestRestore:
    @pytest.mark.parametrize(
        ("position", "expected"),
        [
            (0, [GLEB, ANNA, BORIS, VERA]),
            (1, [ANNA, GLEB, BORIS, VERA]),
            (3, [ANNA, BORIS, VERA, GLEB]),
            (40, [ANNA, BORIS, VERA, GLEB]),
            (10**12, [ANNA, BORIS, VERA, GLEB]),
            (None, [ANNA, BORIS, VERA, GLEB]),
            (-5, [GLEB, ANNA, BORIS, VERA]),
        ],
        ids=["first", "middle", "at-the-end", "past-the-end", "past-int4", "none", "negative"],
    )
    async def test_puts_the_person_back_at_the_position(
        self,
        repo: ChatSettingsRepository,
        db_pool: asyncpg.Pool,
        position: int | None,
        expected: list[int],
    ) -> None:
        await _chat(db_pool, f"[{ANNA}, {BORIS}, {VERA}]")

        assert await repo.restore_kb_organizer(CHAT, GLEB, position) == expected
        assert await _stored(db_pool) == "[" + ", ".join(map(str, expected)) + "]"

    async def test_into_an_empty_list(
        self, repo: ChatSettingsRepository, db_pool: asyncpg.Pool
    ) -> None:
        await _chat(db_pool)

        assert await repo.restore_kb_organizer(CHAT, ANNA, 3) == [ANNA]

    async def test_someone_already_back_stays_where_they_are(
        self, repo: ChatSettingsRepository, db_pool: asyncpg.Pool
    ) -> None:
        await _chat(db_pool, f"[{ANNA}, {BORIS}]")

        assert await repo.restore_kb_organizer(CHAT, BORIS, 0) == [ANNA, BORIS]

    async def test_a_chat_without_a_row_is_none(self, repo: ChatSettingsRepository) -> None:
        assert await repo.restore_kb_organizer(GONE_CHAT, ANNA, 0) is None


async def _waiting_on_a_lock(conn: asyncpg.Connection, count: int) -> None:
    """Until ``count`` backends wait on a lock: the racing writes are parked.

    ``conn`` is outside the pool, as the holder is: the pool has three
    connections, the parked writes hold two, and a poll queued for a pool
    connection behind them would never run (the first version of this test hung).
    """
    waiting = 0
    for _ in range(200):
        waiting = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE wait_event_type = 'Lock' AND datname = current_database()"
        )
        if waiting >= count:
            return
        await asyncio.sleep(0.025)
    raise AssertionError(f"{count} writes never queued behind the lock (saw {waiting})")


Write = Callable[[ChatSettingsRepository], Awaitable[object]]


class TestRacingWrites:
    """Two writes on the same row at the same time, as two quick taps make them.

    The row is held by ``SELECT ... FOR UPDATE`` on a connection outside the
    pool until both writes are queued behind it, then let go. A read in Python
    followed by a write would read the same list in both and the second write
    would erase the first; one UPDATE reads the row it locked, i.e. the
    first write's result.
    """

    @pytest.mark.parametrize(
        ("first", "second", "expected"),
        [
            (
                lambda r: r.add_kb_organizer(CHAT, BORIS),
                lambda r: r.add_kb_organizer(CHAT, VERA),
                {ANNA, BORIS, VERA},
            ),
            (
                lambda r: r.remove_kb_organizer(CHAT, ANNA),
                lambda r: r.add_kb_organizer(CHAT, VERA),
                {VERA},
            ),
            (
                lambda r: r.restore_kb_organizer(CHAT, GLEB, 0),
                lambda r: r.add_kb_organizer(CHAT, VERA),
                {GLEB, ANNA, VERA},
            ),
        ],
        ids=["add-add", "remove-add", "restore-add"],
    )
    async def test_both_writes_survive(
        self,
        repo: ChatSettingsRepository,
        db_pool: asyncpg.Pool,
        pg_url: str,
        first: Write,
        second: Write,
        expected: set[int],
    ) -> None:
        await _chat(db_pool, f"[{ANNA}]")
        holder = await asyncpg.connect(pg_url)
        poller = await asyncpg.connect(pg_url)
        try:
            tx = holder.transaction()
            await tx.start()
            await holder.execute("SELECT 1 FROM chat_settings WHERE chat_id = $1 FOR UPDATE", CHAT)
            racing = [asyncio.create_task(first(repo)), asyncio.create_task(second(repo))]
            try:
                await _waiting_on_a_lock(poller, 2)
            finally:
                await tx.commit()
            await asyncio.wait_for(asyncio.gather(*racing), timeout=10)
        finally:
            await holder.close()
            await poller.close()

        final = await repo.get(CHAT)
        assert final is not None
        stored = parse_user_id_list(final["kb_organizer_ids"])
        assert set(stored) == expected, f"a racing write was lost: stored {stored}"
        assert len(stored) == len(expected)


async def _posted(pool: asyncpg.Pool, message_id: int, user_id: int, **name: str | None) -> None:
    await pool.execute(
        """
        INSERT INTO chat_messages (chat_id, message_id, user_id, username, first_name,
                                   message_type, content, created_at)
        VALUES ($1, $2, $3, $4, $5, 'text', 'привет', $6)
        """,
        CHAT,
        message_id,
        user_id,
        name.get("username"),
        name.get("first_name"),
        T0,
    )


class TestRememberSeenOnlyFillsAGap:
    """`OrganizerNameRepository.remember_seen`, the name the list pick stores.

    Here and not in ``test_organizer_names_repository.py`` because this file is
    the one run for this change; the store's other tests live there.
    """

    async def test_a_stored_full_name_is_kept(self, db_pool: asyncpg.Pool) -> None:
        names = OrganizerNameRepository(db_pool)
        await names.remember(CHAT, ANNA, first_name="Аня", last_name="Ким", username="anya")
        await _posted(db_pool, 1, ANNA, first_name="Анна", username="anya_old")

        assert not await names.remember_seen(CHAT, ANNA), "it reported a write"

        assert await names.names(CHAT, [ANNA]) == {ANNA: KnownName("Аня", "Ким", "anya")}

    async def test_with_no_stored_name_the_history_one_is_stored(
        self, db_pool: asyncpg.Pool
    ) -> None:
        names = OrganizerNameRepository(db_pool)
        await _posted(db_pool, 1, BORIS, first_name="Борис", username="boris_k")

        assert await names.remember_seen(CHAT, BORIS)

        stored = await db_pool.fetchrow(
            "SELECT first_name, last_name, username FROM kb_organizer_names "
            "WHERE chat_id = $1 AND user_id = $2",
            CHAT,
            BORIS,
        )
        assert stored is not None
        assert tuple(stored) == ("Борис", None, "boris_k")
