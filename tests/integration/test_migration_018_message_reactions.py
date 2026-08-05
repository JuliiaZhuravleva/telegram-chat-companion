"""
Integration tests: migration 018 (message_reactions + chat_settings reaction
toggles) against real Postgres+pgvector.

The unit test (``tests/unit/test_migration_018_message_reactions.py``, written by
R-1) monkeypatches ``op.execute`` and only checks the *rendered SQL* -- it never
actually runs the DDL. This is QA-1's complement: the session-scoped
``run_migrations`` fixture in ``tests/integration/conftest.py`` applies every
migration (including 018) against a real pgvector container once per test
session, so these tests verify the DDL *actually lands* -- table/columns/indexes
exist and behave as ADR-0004 specifies -- and that ``ReactionRepository`` really
inserts rows through asyncpg, not just through a mocked pool
(``tests/unit/test_reactions_repository.py`` only asserts the SQL text/params
passed to a mock; it never touches a database).
"""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.reactions import ReactionRepository
from src.services.modules.reactions.models import ReactionEvent

# ---------------------------------------------------------------------------
# message_reactions table shape
# ---------------------------------------------------------------------------


class TestMessageReactionsTableShape:
    @pytest.mark.asyncio
    async def test_table_exists(self, db_conn: asyncpg.Connection) -> None:
        exists = await db_conn.fetchval(
            "SELECT to_regclass('public.message_reactions') IS NOT NULL"
        )
        assert exists is True

    @pytest.mark.asyncio
    async def test_expected_columns_present(self, db_conn: asyncpg.Connection) -> None:
        rows = await db_conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'message_reactions'
            """
        )
        columns = {r["column_name"] for r in rows}
        expected = {
            "id",
            "chat_id",
            "message_id",
            "user_id",
            "actor_chat_id",
            "action",
            "reaction_type",
            "emoji",
            "custom_emoji_id",
            "created_at",
        }
        assert expected <= columns

    @pytest.mark.asyncio
    async def test_no_foreign_key_to_chat_messages(self, db_conn: asyncpg.Connection) -> None:
        """ADR-0004 Decision 1: (chat_id, message_id) is a soft join key -- a
        reacted-to message can be older than retention or never saved at all,
        so an FK here would make legitimate inserts fail."""
        rows = await db_conn.fetch(
            """
            SELECT tc.constraint_type
            FROM information_schema.table_constraints tc
            WHERE tc.table_name = 'message_reactions'
              AND tc.constraint_type = 'FOREIGN KEY'
            """
        )
        assert rows == []

    @pytest.mark.asyncio
    async def test_insert_with_no_matching_chat_message_row_succeeds(
        self, db_conn: asyncpg.Connection
    ) -> None:
        """The message being reacted to need not exist in chat_messages at all
        (save_messages off, or the message aged out of retention) -- the soft
        join must not be enforced at the DB level."""
        row_id = await db_conn.fetchval(
            """
            INSERT INTO message_reactions
                (chat_id, message_id, user_id, action, reaction_type, emoji)
            VALUES (-900101, 999999999, 42, 'added', 'emoji', '👍')
            RETURNING id
            """
        )
        assert row_id is not None

    @pytest.mark.asyncio
    async def test_expected_indexes_exist(self, db_conn: asyncpg.Connection) -> None:
        rows = await db_conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'message_reactions'"
        )
        names = {r["indexname"] for r in rows}
        assert "idx_message_reactions_chat_message" in names
        assert "idx_message_reactions_chat_created" in names


# ---------------------------------------------------------------------------
# chat_settings.reactions_enabled / reactions_history_enabled
# ---------------------------------------------------------------------------


class TestChatSettingsReactionColumns:
    @pytest.mark.asyncio
    async def test_reactions_enabled_defaults_null_deferring_to_global(
        self, db_conn: asyncpg.Connection
    ) -> None:
        """No column DEFAULT: a fresh row leaves reactions_enabled NULL so the
        three-layer merge's global layer stays effective until the chat
        explicitly opts in/out (ADR-0004 Decision 3, mirrors kb_enabled)."""
        await db_conn.execute(
            "INSERT INTO chat_settings (chat_id) VALUES (-900102) ON CONFLICT DO NOTHING"
        )
        row = await db_conn.fetchrow(
            "SELECT reactions_enabled, reactions_history_enabled "
            "FROM chat_settings WHERE chat_id = -900102"
        )
        assert row is not None
        assert row["reactions_enabled"] is None
        assert row["reactions_history_enabled"] is None

    @pytest.mark.asyncio
    async def test_both_toggles_are_independently_settable(
        self, db_conn: asyncpg.Connection
    ) -> None:
        """An owner can keep bot-initiated reactions (R-5) while opting out of
        behavioral history logging -- the two columns must not be coupled."""
        await db_conn.execute(
            """
            INSERT INTO chat_settings (chat_id, reactions_enabled, reactions_history_enabled)
            VALUES (-900103, TRUE, FALSE)
            ON CONFLICT (chat_id) DO UPDATE SET
                reactions_enabled = EXCLUDED.reactions_enabled,
                reactions_history_enabled = EXCLUDED.reactions_history_enabled
            """
        )
        row = await db_conn.fetchrow(
            "SELECT reactions_enabled, reactions_history_enabled "
            "FROM chat_settings WHERE chat_id = -900103"
        )
        assert row is not None
        assert row["reactions_enabled"] is True
        assert row["reactions_history_enabled"] is False


# ---------------------------------------------------------------------------
# ReactionRepository against a real connection (not a mocked pool)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def repo(db_conn: asyncpg.Connection) -> ReactionRepository:
    return ReactionRepository(db_conn)  # type: ignore[arg-type]


class TestReactionRepositoryRealInsert:
    @pytest.mark.asyncio
    async def test_inserts_are_persisted_and_readable(
        self, repo: ReactionRepository, db_conn: asyncpg.Connection
    ) -> None:
        events = [
            ReactionEvent(action="added", reaction_type="emoji", emoji="🔥"),
            ReactionEvent(action="removed", reaction_type="emoji", emoji="👍"),
        ]
        await repo.insert_events(
            chat_id=-900104, message_id=555, user_id=777, actor_chat_id=None, events=events
        )

        rows = await db_conn.fetch(
            "SELECT action, reaction_type, emoji FROM message_reactions "
            "WHERE chat_id = -900104 AND message_id = 555 ORDER BY action"
        )
        assert [(r["action"], r["reaction_type"], r["emoji"]) for r in rows] == [
            ("added", "emoji", "🔥"),
            ("removed", "emoji", "👍"),
        ]

    @pytest.mark.asyncio
    async def test_anonymous_reactor_row_has_null_user_id(
        self, repo: ReactionRepository, db_conn: asyncpg.Connection
    ) -> None:
        events = [ReactionEvent(action="added", reaction_type="emoji", emoji="❤")]
        await repo.insert_events(
            chat_id=-900105, message_id=1, user_id=None, actor_chat_id=-900105, events=events
        )

        row = await db_conn.fetchrow(
            "SELECT user_id, actor_chat_id FROM message_reactions "
            "WHERE chat_id = -900105 AND message_id = 1"
        )
        assert row is not None
        assert row["user_id"] is None
        assert row["actor_chat_id"] == -900105

    @pytest.mark.asyncio
    async def test_custom_emoji_row_stores_raw_id_unresolved(
        self, repo: ReactionRepository, db_conn: asyncpg.Connection
    ) -> None:
        """Phase 1 stores the raw custom_emoji_id and never resolves it to a
        rendered emoji (ADR-0004)."""
        events = [
            ReactionEvent(
                action="added", reaction_type="custom_emoji", custom_emoji_id="5368324170671202873"
            )
        ]
        await repo.insert_events(
            chat_id=-900106, message_id=2, user_id=8, actor_chat_id=None, events=events
        )

        row = await db_conn.fetchrow(
            "SELECT reaction_type, emoji, custom_emoji_id FROM message_reactions "
            "WHERE chat_id = -900106 AND message_id = 2"
        )
        assert row is not None
        assert row["reaction_type"] == "custom_emoji"
        assert row["emoji"] is None
        assert row["custom_emoji_id"] == "5368324170671202873"

    @pytest.mark.asyncio
    async def test_empty_events_inserts_nothing(
        self, repo: ReactionRepository, db_conn: asyncpg.Connection
    ) -> None:
        await repo.insert_events(
            chat_id=-900107, message_id=3, user_id=1, actor_chat_id=None, events=[]
        )
        count = await db_conn.fetchval(
            "SELECT count(*) FROM message_reactions WHERE chat_id = -900107 AND message_id = 3"
        )
        assert count == 0


class TestRedeliveryDeduplication:
    """Migration 019. Telegram redelivers an update whenever the polling offset
    was not confirmed -- a restart mid-batch is enough -- and the redelivery
    carries the identical old/new pair, so the handler's diff produces the same
    events again.

    Asserting "ON CONFLICT DO NOTHING is in the SQL" proves only what the string
    says; these drive the real index in Postgres.
    """

    @staticmethod
    def _events() -> list[ReactionEvent]:
        return [ReactionEvent(action="added", reaction_type="emoji", emoji="👍")]

    @pytest.mark.asyncio
    async def test_same_update_twice_writes_one_row(
        self, repo: ReactionRepository, db_conn: asyncpg.Connection
    ) -> None:
        stamp = datetime(2026, 8, 3, 18, 24, 3, tzinfo=UTC)
        for _ in range(2):
            await repo.insert_events(
                chat_id=-900108,
                message_id=10,
                user_id=42,
                actor_chat_id=None,
                events=self._events(),
                event_date=stamp,
            )

        count = await db_conn.fetchval(
            "SELECT count(*) FROM message_reactions WHERE chat_id = -900108 AND message_id = 10"
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_genuine_repeat_later_is_still_recorded(
        self, repo: ReactionRepository, db_conn: asyncpg.Connection
    ) -> None:
        """Re-adding the same emoji later is a real second event, not a
        redelivery -- deduplication must not swallow it."""
        for minute in (0, 5):
            await repo.insert_events(
                chat_id=-900109,
                message_id=11,
                user_id=42,
                actor_chat_id=None,
                events=self._events(),
                event_date=datetime(2026, 8, 3, 18, minute, 0, tzinfo=UTC),
            )

        count = await db_conn.fetchval(
            "SELECT count(*) FROM message_reactions WHERE chat_id = -900109 AND message_id = 11"
        )
        assert count == 2

    @pytest.mark.asyncio
    async def test_anonymous_reactor_duplicates_are_caught(
        self, repo: ReactionRepository, db_conn: asyncpg.Connection
    ) -> None:
        """NULL never equals NULL in a plain unique index, so without the
        COALESCE sentinels anonymous-reactor rows (user_id NULL) would duplicate
        freely -- precisely the rows the index is there to protect."""
        stamp = datetime(2026, 8, 3, 18, 30, 0, tzinfo=UTC)
        for _ in range(2):
            await repo.insert_events(
                chat_id=-900110,
                message_id=12,
                user_id=None,
                actor_chat_id=-900110,
                events=self._events(),
                event_date=stamp,
            )

        count = await db_conn.fetchval(
            "SELECT count(*) FROM message_reactions WHERE chat_id = -900110 AND message_id = 12"
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_custom_emoji_duplicates_are_caught(
        self, repo: ReactionRepository, db_conn: asyncpg.Connection
    ) -> None:
        """Same NULL-inequality trap on the other side: a custom-emoji row has
        emoji NULL."""
        stamp = datetime(2026, 8, 3, 18, 35, 0, tzinfo=UTC)
        events = [
            ReactionEvent(action="added", reaction_type="custom_emoji", custom_emoji_id="7788")
        ]
        for _ in range(2):
            await repo.insert_events(
                chat_id=-900111,
                message_id=13,
                user_id=42,
                actor_chat_id=None,
                events=events,
                event_date=stamp,
            )

        count = await db_conn.fetchval(
            "SELECT count(*) FROM message_reactions WHERE chat_id = -900111 AND message_id = 13"
        )
        assert count == 1
