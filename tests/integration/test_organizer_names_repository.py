"""Integration tests: knowledge-base organizer names against real Postgres (migration 038).

What lives in the database and cannot be shown against a mock: the table's shape,
the upsert, the lateral "latest name in the chat history" read with its NULL-dated
rows, and "the stored name beats the history".

Fixture discipline is the alias tests' one: ``db_pool`` (the repository acquires
from a pool), isolation by a chat-id band nothing else uses (``-96xxxx``: checked
with ``grep -rnE '\\-96[0-9_]{3,}' tests/integration/*.py``, which also finds the
aliases' ``-95`` band as a control), and invented ids and names only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest_asyncio

from src.database.repositories.organizer_names import KnownName, OrganizerNameRepository

CHAT = -960_001
OTHER_CHAT = -960_002
ANNA = 112_001
BORIS = 112_002
VERA = 112_003
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def repo(db_pool: asyncpg.Pool) -> OrganizerNameRepository:
    return OrganizerNameRepository(db_pool)


@pytest_asyncio.fixture(autouse=True)
async def _clean(db_pool: asyncpg.Pool):
    async def wipe() -> None:
        for table in ("kb_organizer_names", "chat_messages"):
            await db_pool.execute(
                f"DELETE FROM {table} WHERE chat_id = ANY($1::bigint[])",  # noqa: S608 -- local constant
                [CHAT, OTHER_CHAT],
            )

    await wipe()
    yield
    await wipe()


async def _posted(
    pool: asyncpg.Pool,
    message_id: int,
    user_id: int,
    *,
    first_name: str | None,
    username: str | None = None,
    at: datetime | None = T0,
    chat_id: int = CHAT,
) -> None:
    await pool.execute(
        """
        INSERT INTO chat_messages (chat_id, message_id, user_id, username, first_name,
                                   message_type, content, created_at)
        VALUES ($1, $2, $3, $4, $5, 'text', 'привет', $6)
        """,
        chat_id,
        message_id,
        user_id,
        username,
        first_name,
        at,
    )


class TestTheTable:
    async def test_shape(self, db_pool: asyncpg.Pool) -> None:
        columns = {
            row["column_name"]: (row["is_nullable"], row["column_default"])
            for row in await db_pool.fetch(
                """
                SELECT column_name, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name = 'kb_organizer_names'
                """
            )
        }
        assert set(columns) == {
            "chat_id",
            "user_id",
            "first_name",
            "last_name",
            "username",
            "updated_at",
        }
        assert columns["chat_id"][0] == columns["user_id"][0] == "NO"
        for part in ("first_name", "last_name", "username"):
            assert columns[part] == ("YES", None), part
        assert columns["updated_at"][0] == "NO"
        assert "now()" in (columns["updated_at"][1] or "")

        key = await db_pool.fetchval(
            """
            SELECT array_agg(a.attname ORDER BY k.ord)
            FROM pg_index i
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
            WHERE i.indrelid = 'kb_organizer_names'::regclass AND i.indisprimary
            """
        )
        assert key == ["chat_id", "user_id"]

    async def test_it_is_not_a_chat_settings_column(self, db_pool: asyncpg.Pool) -> None:
        """Kept out of the ChatConfig merge: nothing about names lives in chat_settings."""
        names = await db_pool.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'chat_settings' AND column_name LIKE 'kb_organizer%'
            """
        )
        assert [row["column_name"] for row in names] == ["kb_organizer_ids"]


class TestRemember:
    async def test_the_picked_name_is_read_back(self, repo: OrganizerNameRepository) -> None:
        assert await repo.remember(CHAT, ANNA, first_name="Аня", last_name="Ким", username="anya")

        assert await repo.names(CHAT, [ANNA]) == {ANNA: KnownName("Аня", "Ким", "anya")}

    async def test_a_newer_add_replaces_the_name(
        self, repo: OrganizerNameRepository, db_pool: asyncpg.Pool
    ) -> None:
        await repo.remember(CHAT, ANNA, first_name="Аня", last_name="Ким", username="anya")
        await db_pool.execute(
            "UPDATE kb_organizer_names SET updated_at = $3 WHERE chat_id = $1 AND user_id = $2",
            CHAT,
            ANNA,
            T0 - timedelta(days=30),
        )

        await repo.remember(CHAT, ANNA, first_name="Анна", username=None)

        assert await repo.names(CHAT, [ANNA]) == {ANNA: KnownName("Анна", None, None)}
        moved = await db_pool.fetchval(
            "SELECT updated_at FROM kb_organizer_names WHERE chat_id = $1 AND user_id = $2",
            CHAT,
            ANNA,
        )
        assert moved > T0, "updated_at did not move on the replace"

    async def test_nothing_to_remember_writes_nothing(
        self, repo: OrganizerNameRepository, db_pool: asyncpg.Pool
    ) -> None:
        await repo.remember(CHAT, ANNA, first_name="Аня")

        assert not await repo.remember(CHAT, ANNA, first_name="  ", last_name=None, username="")

        assert await repo.names(CHAT, [ANNA]) == {ANNA: KnownName("Аня", None, None)}
        rows = await db_pool.fetchval(
            "SELECT count(*) FROM kb_organizer_names WHERE chat_id = ANY($1::bigint[])",
            [CHAT, OTHER_CHAT],
        )
        assert rows == 1

    async def test_names_are_per_chat(self, repo: OrganizerNameRepository) -> None:
        await repo.remember(CHAT, ANNA, first_name="Аня")

        assert await repo.names(OTHER_CHAT, [ANNA]) == {}


class TestRememberSeen:
    async def test_the_latest_named_message_is_copied(
        self, repo: OrganizerNameRepository, db_pool: asyncpg.Pool
    ) -> None:
        await _posted(db_pool, 1, BORIS, first_name="Боря", username="old_nick")
        await _posted(
            db_pool, 2, BORIS, first_name="Борис", username="boris_k", at=T0 + timedelta(1)
        )
        # A NULL-dated row (the n8n-era import) is the OLDEST, not the newest.
        await _posted(db_pool, 3, BORIS, first_name="Не тот", username="wrong", at=None)

        assert await repo.remember_seen(CHAT, BORIS)

        assert await repo.names(CHAT, [BORIS]) == {BORIS: KnownName("Борис", None, "boris_k")}

    async def test_no_history_writes_nothing(
        self, repo: OrganizerNameRepository, db_pool: asyncpg.Pool
    ) -> None:
        await _posted(db_pool, 1, BORIS, first_name="Борис", chat_id=OTHER_CHAT)

        assert not await repo.remember_seen(CHAT, BORIS)
        assert await repo.names(CHAT, [BORIS]) == {}


class TestNames:
    async def test_the_chat_history_names_someone_never_stored(
        self, repo: OrganizerNameRepository, db_pool: asyncpg.Pool
    ) -> None:
        await _posted(db_pool, 1, VERA, first_name="Вера", username=None)
        await _posted(db_pool, 2, VERA, first_name=None, username=None, at=T0 + timedelta(1))
        await _posted(db_pool, 3, VERA, first_name="Не та", at=None)

        assert await repo.names(CHAT, [VERA]) == {VERA: KnownName("Вера", None, None)}, (
            "a newer message without a name must not hide the older name, "
            "and a NULL-dated row is the oldest, not the newest"
        )

    async def test_the_stored_name_beats_a_newer_message(
        self, repo: OrganizerNameRepository, db_pool: asyncpg.Pool
    ) -> None:
        await repo.remember(CHAT, ANNA, first_name="Аня", username="anya")
        await _posted(db_pool, 1, ANNA, first_name="Другое имя", at=T0 + timedelta(days=5))

        assert await repo.names(CHAT, [ANNA]) == {ANNA: KnownName("Аня", None, "anya")}

    async def test_someone_never_seen_is_left_out(
        self, repo: OrganizerNameRepository, db_pool: asyncpg.Pool
    ) -> None:
        await repo.remember(CHAT, ANNA, first_name="Аня")
        await _posted(db_pool, 1, VERA, first_name="Вера")

        found = await repo.names(CHAT, [ANNA, BORIS, VERA, ANNA])

        assert found == {ANNA: KnownName("Аня", None, None), VERA: KnownName("Вера", None, None)}

    async def test_no_ids_is_no_query(self, repo: OrganizerNameRepository) -> None:
        assert await repo.names(CHAT, []) == {}
