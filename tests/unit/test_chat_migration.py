"""Tests for the group→supergroup re-key (plan KB-06).

Telegram issues a NEW chat_id on upgrade. Nothing handled that before, so an
upgrading community silently orphaned its settings row and its entire curated
knowledge base — at the moment it was growing, not shrinking.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from src.bot.handlers.chat_events import handle_chat_migration
from src.database.repositories.chat_migration import (
    ChatMigrationRepository,
    MigrationOutcome,
    _rows_affected,
)

OLD_ID = -1009999990001
NEW_ID = -1009999990002


class _AsyncCM:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
def conn() -> MagicMock:
    connection = MagicMock()
    connection.fetchrow = AsyncMock(return_value={"chat_id": OLD_ID})
    connection.fetchval = AsyncMock(return_value=None)
    connection.execute = AsyncMock(return_value="UPDATE 1")
    connection.transaction = MagicMock(return_value=_AsyncCM(None))
    return connection


@pytest.fixture
def repo(conn: MagicMock) -> ChatMigrationRepository:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return ChatMigrationRepository(pool)


class TestMigrate:
    @pytest.mark.asyncio
    async def test_moves_settings_and_facts(
        self, repo: ChatMigrationRepository, conn: MagicMock
    ) -> None:
        conn.execute.side_effect = ["UPDATE 1", "UPDATE 12", "UPDATE 0", "UPDATE 0"]

        outcome = await repo.migrate(OLD_ID, NEW_ID)

        assert outcome.status == "migrated"
        assert outcome.settings_moved == 1
        assert outcome.facts_moved == 12

        tables = [call.args[0] for call in conn.execute.call_args_list]
        assert any("UPDATE chat_settings" in sql for sql in tables)
        assert any("UPDATE chat_facts" in sql for sql in tables)

    @pytest.mark.asyncio
    async def test_moves_custom_rules_too(
        self, repo: ChatMigrationRepository, conn: MagicMock
    ) -> None:
        """TD-112: curated moderation rules were left on the old id.

        Worse than a plain gap — re-keying `chat_settings` away also removes
        the chat from the rules menu, which enumerates chats by their settings
        row. So the rules stopped firing AND became unreachable.
        """
        await repo.migrate(OLD_ID, NEW_ID)

        tables = [call.args[0] for call in conn.execute.call_args_list]
        assert any("UPDATE custom_rules" in sql for sql in tables), (
            f"custom_rules was never re-keyed; statements were: {tables}"
        )

    @pytest.mark.asyncio
    async def test_moves_participant_aliases_too(
        self, repo: ChatMigrationRepository, conn: MagicMock
    ) -> None:
        """TD-150: the names a chat chose for its people are curated too.

        Same failure shape as the rules above and just as quiet: the bot would
        simply go back to calling everyone by their account handle, in a chat
        that had already told it what to use, with nothing logged to say why.
        """
        await repo.migrate(OLD_ID, NEW_ID)

        tables = [call.args[0] for call in conn.execute.call_args_list]
        assert any("UPDATE chat_user_aliases" in sql for sql in tables), (
            f"chat_user_aliases was never re-keyed; statements were: {tables}"
        )

    @pytest.mark.asyncio
    async def test_reports_how_many_rules_moved(
        self, repo: ChatMigrationRepository, conn: MagicMock
    ) -> None:
        """The count is the only in-prod signal that the third table moved."""
        conn.execute.side_effect = ["UPDATE 1", "UPDATE 12", "UPDATE 4", "UPDATE 2"]

        outcome = await repo.migrate(OLD_ID, NEW_ID)

        assert outcome.rules_moved == 4

    @pytest.mark.asyncio
    async def test_all_four_tables_move_in_one_transaction(
        self, repo: ChatMigrationRepository, conn: MagicMock
    ) -> None:
        """A half-applied move leaves settings, knowledge, rules and names on
        different ids."""
        await repo.migrate(OLD_ID, NEW_ID)

        conn.transaction.assert_called_once()
        assert conn.execute.await_count == 4

    @pytest.mark.asyncio
    async def test_locks_the_source_row_before_deciding(
        self, repo: ChatMigrationRepository, conn: MagicMock
    ) -> None:
        """The middleware writes chat_settings on this very update."""
        await repo.migrate(OLD_ID, NEW_ID)

        assert "FOR UPDATE" in conn.fetchrow.call_args.args[0]

    @pytest.mark.asyncio
    async def test_missing_source_is_a_quiet_noop(
        self, repo: ChatMigrationRepository, conn: MagicMock
    ) -> None:
        """Telegram announces the migration TWICE — the second must not error."""
        conn.fetchrow.return_value = None

        outcome = await repo.migrate(OLD_ID, NEW_ID)

        assert outcome.status == "nothing_to_move"
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_occupied_target_is_refused_and_nothing_is_touched(
        self, repo: ChatMigrationRepository, conn: MagicMock
    ) -> None:
        """ChatConfigMiddleware.ensure_exists() may have created the new row first.

        Merging two settings rows — and resolving the (chat_id, subject,
        predicate) unique collision between two fact sets — is a human
        decision. Refusing must destroy nothing.
        """
        conn.fetchval.return_value = 1

        outcome = await repo.migrate(OLD_ID, NEW_ID)

        assert outcome.status == "target_occupied"
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_identical_ids_short_circuit_before_any_query(
        self, repo: ChatMigrationRepository, conn: MagicMock
    ) -> None:
        outcome = await repo.migrate(OLD_ID, OLD_ID)

        assert outcome.status == "nothing_to_move"
        conn.fetchrow.assert_not_awaited()


class TestRowsAffected:
    @pytest.mark.parametrize(
        ("tag", "expected"), [("UPDATE 3", 3), ("UPDATE 0", 0), ("", 0), (None, 0), ("UPDATE", 0)]
    )
    def test_parses_the_command_tag(self, tag: str | None, expected: int) -> None:
        assert _rows_affected(tag) == expected


def _make_message(*, chat_id: int, to_id: int | None, from_id: int | None) -> MagicMock:
    message = MagicMock()
    message.chat = MagicMock()
    message.chat.id = chat_id
    message.migrate_to_chat_id = to_id
    message.migrate_from_chat_id = from_id
    return message


class TestHandler:
    @pytest.mark.asyncio
    async def test_reads_ids_from_the_old_chats_announcement(self) -> None:
        migration_repo = AsyncMock()
        migration_repo.migrate = AsyncMock(return_value=MagicMock(status="migrated"))
        config_service = MagicMock()

        await handle_chat_migration(
            _make_message(chat_id=OLD_ID, to_id=NEW_ID, from_id=None),
            migration_repo,
            config_service,
        )

        migration_repo.migrate.assert_awaited_once_with(OLD_ID, NEW_ID)

    @pytest.mark.asyncio
    async def test_reads_ids_from_the_new_chats_announcement(self) -> None:
        """The same migration is announced from the other side, ids reversed."""
        migration_repo = AsyncMock()
        migration_repo.migrate = AsyncMock(return_value=MagicMock(status="nothing_to_move"))
        config_service = MagicMock()

        await handle_chat_migration(
            _make_message(chat_id=NEW_ID, to_id=None, from_id=OLD_ID),
            migration_repo,
            config_service,
        )

        migration_repo.migrate.assert_awaited_once_with(OLD_ID, NEW_ID)

    @pytest.mark.asyncio
    async def test_invalidates_the_cached_config_for_the_old_id(self) -> None:
        migration_repo = AsyncMock()
        migration_repo.migrate = AsyncMock(return_value=MagicMock(status="migrated"))
        config_service = MagicMock()

        await handle_chat_migration(
            _make_message(chat_id=OLD_ID, to_id=NEW_ID, from_id=None),
            migration_repo,
            config_service,
        )

        config_service.invalidate.assert_called_once_with(OLD_ID)

    @pytest.mark.asyncio
    async def test_does_not_invalidate_when_nothing_moved(self) -> None:
        migration_repo = AsyncMock()
        migration_repo.migrate = AsyncMock(return_value=MagicMock(status="nothing_to_move"))
        config_service = MagicMock()

        await handle_chat_migration(
            _make_message(chat_id=OLD_ID, to_id=NEW_ID, from_id=None),
            migration_repo,
            config_service,
        )

        config_service.invalidate.assert_not_called()


class TestMigrationLogTellsTheTruth:
    """The log line is the only in-prod evidence of what a re-key touched.

    Every test here builds a REAL ``MigrationOutcome``. The handler tests above
    pass ``MagicMock(status="migrated")``, and a Mock's ``.rules_moved`` is a
    truthy Mock — asserting on that would pass with the third UPDATE deleted.
    """

    @pytest.mark.asyncio
    async def test_reports_the_rules_it_moved(self) -> None:
        migration_repo = AsyncMock()
        migration_repo.migrate = AsyncMock(
            return_value=MigrationOutcome(
                status="migrated", settings_moved=1, facts_moved=12, rules_moved=4
            )
        )

        with capture_logs() as logs:
            await handle_chat_migration(
                _make_message(chat_id=OLD_ID, to_id=NEW_ID, from_id=None),
                migration_repo,
                MagicMock(),
            )

        moved = [entry for entry in logs if entry.get("rules_moved") is not None]
        assert moved, f"the re-key log never reported rules_moved; logs were: {logs}"
        assert moved[0]["rules_moved"] == 4

    @pytest.mark.asyncio
    async def test_names_exactly_what_stayed_on_the_old_id(self) -> None:
        """Spelled out as a literal, not imported from the module under test.

        Importing ``NOT_MOVED`` would mirror the implementation and pass for
        any value. Written out, the assertion goes red on drift in EITHER
        direction — a table quietly dropped from the list, or a table that
        started moving without the list being updated.
        """
        migration_repo = AsyncMock()
        migration_repo.migrate = AsyncMock(
            return_value=MigrationOutcome(status="migrated", settings_moved=1)
        )

        with capture_logs() as logs:
            await handle_chat_migration(
                _make_message(chat_id=OLD_ID, to_id=NEW_ID, from_id=None),
                migration_repo,
                MagicMock(),
            )

        left_behind = [entry["not_moved"] for entry in logs if "not_moved" in entry]
        assert left_behind == [
            "chat_memory, chat_messages, chat_chunks, unauthorized_attempts, observability logs"
        ]

    @pytest.mark.asyncio
    async def test_a_refused_migration_says_the_rules_stayed_too(self) -> None:
        """`target_occupied` is the one outcome where TD-112's symptom survives.

        Nothing moves, so the rules are still stranded — the warning has to
        say so, or the operator reads "settings already exist" as cosmetic.
        """
        migration_repo = AsyncMock()
        migration_repo.migrate = AsyncMock(return_value=MigrationOutcome(status="target_occupied"))

        with capture_logs() as logs:
            await handle_chat_migration(
                _make_message(chat_id=OLD_ID, to_id=NEW_ID, from_id=None),
                migration_repo,
                MagicMock(),
            )

        warnings = [e for e in logs if e.get("log_level") == "warning"]
        assert warnings, f"a refused migration must warn; logs were: {logs}"
        assert "rules" in warnings[0]["event"].lower(), (
            f"the refusal warning does not mention rules: {warnings[0]['event']!r}"
        )


class TestHandlerIsFilteredNotBodyGuarded:
    """aiogram consumes an update as soon as a handler's filters match.

    A field check inside the body would swallow every ordinary message in the
    chat instead of letting the message pipeline run — the exact failure
    CLAUDE.md records. So the filter must live on the decorator.
    """

    def test_handler_is_registered_with_a_migration_filter(self) -> None:
        from src.bot.handlers.chat_events import router

        registered = [
            handler
            for handler in router.message.handlers
            if handler.callback is handle_chat_migration
        ]
        assert len(registered) == 1, "migration handler must be registered on dp.message"
        assert registered[0].filters, "must be filtered at registration, not in the body"
