"""
Integration tests: migration 021 (chat_messages.quote_text / quote_is_manual)
against real Postgres, plus confirmation that the online-upgrade guard
(``tests/integration/test_alembic_online_upgrade.py``) actually walks 021.

Q-3's unit tests (``tests/unit/test_message_saver.py``,
``tests/unit/test_message_repository.py``) mock ``MessageRepository`` / the
asyncpg pool -- they pin *what gets passed*, never that it lands correctly in
a real row. This is the round-trip complement: a real aiogram-shaped
``Message`` mock goes through ``MessageSaverMiddleware._save_message`` into a
``MessageRepository`` backed by a real connection, and the assertions read
the row back out of Postgres.

Also pins a pre-existing (not Q-3-introduced) behaviour that matters for this
feature: ``MessageRepository.save()``'s ``ON CONFLICT ... DO UPDATE`` clause
does not mention ``quote_text``/``quote_is_manual`` in its ``SET`` list (same
as it omits ``user_id``/``username``/etc.), so a later re-save on the same
``(chat_id, message_id)`` -- e.g. ``media.py::_update_message_content``
patching in a vision description, or an edited-message re-save -- must not
silently wipe a quote that was captured on the first insert.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio
from aiogram.types import Message

from src.bot.middleware.message_saver import MessageSaverMiddleware
from src.database.repositories.messages import MessageRepository

# ---------------------------------------------------------------------------
# Schema shape (migration 021 itself)
# ---------------------------------------------------------------------------


async def _column(conn: asyncpg.Connection, name: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'chat_messages'
          AND column_name = $1
        """,
        name,
    )
    assert row is not None, f"{name} is missing from chat_messages"
    return row


class TestQuoteColumnsShape:
    @pytest.mark.asyncio
    async def test_quote_text_is_nullable_text_without_default(
        self, db_conn: asyncpg.Connection
    ) -> None:
        record = await _column(db_conn, "quote_text")
        assert record["data_type"] == "text"
        assert record["is_nullable"] == "YES"
        assert record["column_default"] is None

    @pytest.mark.asyncio
    async def test_quote_is_manual_is_nullable_boolean_without_default(
        self, db_conn: asyncpg.Connection
    ) -> None:
        record = await _column(db_conn, "quote_is_manual")
        assert record["data_type"] == "boolean"
        assert record["is_nullable"] == "YES"
        assert record["column_default"] is None


# ---------------------------------------------------------------------------
# message_saver -> real MessageRepository -> real Postgres round trip
# ---------------------------------------------------------------------------


def _make_event(*, quote: MagicMock | None, chat_id: int, message_id: int) -> MagicMock:
    """Mirrors tests/unit/test_message_saver.py::_make_event.

    `spec=Message` still returns a truthy generic MagicMock for unset
    attributes, so every field `_save_message` reads must be pinned
    explicitly -- an unset `message.sticker` would be truthy and misclassify
    the message type.
    """
    event = MagicMock(spec=Message)
    event.chat = MagicMock()
    event.chat.id = chat_id
    event.message_id = message_id
    event.from_user = MagicMock()
    event.from_user.id = 555
    event.from_user.username = "alice"
    event.from_user.first_name = "Alice"
    event.from_user.is_bot = False
    event.text = "reply text"
    event.caption = None
    event.reply_to_message = None
    event.sticker = None
    event.voice = None
    event.video_note = None
    event.photo = None
    event.quote = quote
    return event


def _quote(text: str = "highlighted fragment", is_manual: bool | None = True) -> MagicMock:
    quote = MagicMock()
    quote.text = text
    quote.is_manual = is_manual
    return quote


@pytest_asyncio.fixture
async def repo(db_conn: asyncpg.Connection) -> MessageRepository:
    return MessageRepository(db_conn)  # type: ignore[arg-type]


async def _save_via_middleware(event: MagicMock, repo: MessageRepository) -> None:
    """Route a mock Message through the real middleware code path.

    `_save_message` pulls the repository out of `data["dishka_container"]` via
    `await container.get(MessageRepository)` -- an AsyncMock with `.get`
    configured to return the real, DB-backed repo reproduces that without
    standing up a full Dishka container.
    """
    container = AsyncMock()
    container.get.return_value = repo
    data = {"dishka_container": container, "chat_config": None}
    await MessageSaverMiddleware._save_message(event, data)


async def _fetch_quote(
    db_conn: asyncpg.Connection, chat_id: int, message_id: int
) -> asyncpg.Record | None:
    return await db_conn.fetchrow(
        "SELECT quote_text, quote_is_manual FROM chat_messages "
        "WHERE chat_id = $1 AND message_id = $2",
        chat_id,
        message_id,
    )


class TestMessageSaverQuoteRoundTrip:
    @pytest.mark.asyncio
    async def test_manual_quote_round_trips_through_real_postgres(
        self, repo: MessageRepository, db_conn: asyncpg.Connection
    ) -> None:
        chat_id, message_id = -900201, 1
        event = _make_event(
            quote=_quote(text="highlighted fragment", is_manual=True),
            chat_id=chat_id,
            message_id=message_id,
        )

        await _save_via_middleware(event, repo)

        row = await _fetch_quote(db_conn, chat_id, message_id)
        assert row is not None
        assert row["quote_text"] == "highlighted fragment"
        assert row["quote_is_manual"] is True

    @pytest.mark.asyncio
    async def test_server_attached_quote_persists_is_manual_false_not_null(
        self, repo: MessageRepository, db_conn: asyncpg.Connection
    ) -> None:
        """`is_manual=None` on the aiogram side normalizes to a concrete
        `False` in message_saver.py -- must land in Postgres as `false`, not
        `NULL`, so Q-5's `quote_is_manual is True` gate can tell "server quote"
        apart from "no quote at all"."""
        chat_id, message_id = -900201, 2
        event = _make_event(
            quote=_quote(text="server quote", is_manual=None),
            chat_id=chat_id,
            message_id=message_id,
        )

        await _save_via_middleware(event, repo)

        row = await _fetch_quote(db_conn, chat_id, message_id)
        assert row is not None
        assert row["quote_text"] == "server quote"
        assert row["quote_is_manual"] is False

    @pytest.mark.asyncio
    async def test_no_quote_persists_null_for_both_columns(
        self, repo: MessageRepository, db_conn: asyncpg.Connection
    ) -> None:
        chat_id, message_id = -900201, 3
        event = _make_event(quote=None, chat_id=chat_id, message_id=message_id)

        await _save_via_middleware(event, repo)

        row = await _fetch_quote(db_conn, chat_id, message_id)
        assert row is not None
        assert row["quote_text"] is None
        assert row["quote_is_manual"] is None

    @pytest.mark.asyncio
    async def test_quote_text_persists_untruncated_in_real_row(
        self, repo: MessageRepository, db_conn: asyncpg.Connection
    ) -> None:
        """Storage keeps the raw quote; the 300-char cap is a prompt-render
        concern applied at read time (Q-1), not enforced by the schema."""
        chat_id, message_id = -900201, 4
        long_text = "y" * 900
        event = _make_event(
            quote=_quote(text=long_text, is_manual=True), chat_id=chat_id, message_id=message_id
        )

        await _save_via_middleware(event, repo)

        row = await _fetch_quote(db_conn, chat_id, message_id)
        assert row is not None
        assert row["quote_text"] == long_text
        assert len(row["quote_text"]) == 900


class TestQuoteFieldsSurviveConflictUpdate:
    """`ON CONFLICT (chat_id, message_id) DO UPDATE` (migration 002's UNIQUE
    constraint) sets only `content`/`edited_at`/`edit_count`/`original_content`
    -- quote_text/quote_is_manual are absent from that SET list, same as
    user_id/username. A later re-save on the same key (e.g.
    `media.py::_update_message_content` patching in a vision description
    after MessageSaverMiddleware already stored the quote) must not silently
    wipe the quote captured on first insert.
    """

    @pytest.mark.asyncio
    async def test_conflict_resave_without_quote_kwargs_preserves_original_quote(
        self, repo: MessageRepository, db_conn: asyncpg.Connection
    ) -> None:
        chat_id, message_id = -900202, 1

        await repo.save(
            chat_id=chat_id,
            message_id=message_id,
            message_type="photo",
            content="[caption]",
            quote_text="original highlighted fragment",
            quote_is_manual=True,
        )

        # Mirrors media.py::_update_message_content: same (chat_id, message_id),
        # no quote_text/quote_is_manual passed at all (defaults to None) --
        # this must hit the ON CONFLICT branch, not overwrite the quote to NULL.
        await repo.save(
            chat_id=chat_id,
            message_id=message_id,
            message_type="photo",
            content="[Image: a cat sitting on a windowsill]",
        )

        row = await _fetch_quote(db_conn, chat_id, message_id)
        assert row is not None
        assert row["quote_text"] == "original highlighted fragment", (
            "second save() on conflict silently wiped the persisted quote"
        )
        assert row["quote_is_manual"] is True

        content = await db_conn.fetchval(
            "SELECT content FROM chat_messages WHERE chat_id = $1 AND message_id = $2",
            chat_id,
            message_id,
        )
        assert content == "[Image: a cat sitting on a windowsill]"

    @pytest.mark.asyncio
    async def test_fresh_row_with_no_quote_stays_null_after_conflict_resave(
        self, repo: MessageRepository, db_conn: asyncpg.Connection
    ) -> None:
        """Symmetric case: a message with no quote must not spontaneously gain
        one just because it went through the ON CONFLICT branch."""
        chat_id, message_id = -900202, 2

        await repo.save(chat_id=chat_id, message_id=message_id, message_type="text", content="hi")
        await repo.save(
            chat_id=chat_id, message_id=message_id, message_type="text", content="hi (edited)"
        )

        row = await _fetch_quote(db_conn, chat_id, message_id)
        assert row is not None
        assert row["quote_text"] is None
        assert row["quote_is_manual"] is None


# ---------------------------------------------------------------------------
# Confirm the online-upgrade guard (test_alembic_online_upgrade.py) covers 021
# ---------------------------------------------------------------------------


class TestOnlineUpgradeGuardCoversMigration021:
    """`test_alembic_online_upgrade.py`'s `_all_revisions()` derives its
    revision list from a filesystem glob (`alembic/versions/[0-9]*.py`), not a
    hand-maintained list -- that's the whole point of that test (it already
    went stale once for 016/017/018). This confirms the *same glob pattern*
    picks up 021 (deliberately not importing the other test module's private
    helper -- test files don't import each other here -- so this is an
    independent confirmation, not a tautology against the thing it's
    checking), and that 021 applies cleanly online, matching what Q-3's own
    run reported (4/4 passed) at the time it was written.
    """

    def test_021_is_discovered_by_the_online_upgrade_revision_glob(self) -> None:
        from pathlib import Path

        project_root = Path(__file__).parent.parent.parent
        revisions = sorted(
            path.name.split("_", 1)[0]
            for path in (project_root / "alembic" / "versions").glob("[0-9]*.py")
        )
        assert "021" in revisions, (
            "migration 021 is invisible to the online-upgrade guard's glob -- "
            "check the filename still matches alembic/versions/[0-9]*.py"
        )
        # 021 must not be the accidental last element of an unsorted list --
        # confirm ordering places it right after 020, matching down_revision.
        assert revisions.index("021") == revisions.index("020") + 1

    @pytest.mark.asyncio
    async def test_upgrade_to_021_succeeds_online_against_a_fresh_database(
        self, pg_url: str
    ) -> None:
        """Direct re-run of the class of check
        `test_no_migration_bundles_multiple_commands_in_one_execute` performs
        for every revision, scoped to 021 so a failure here names it
        immediately without walking the whole chain."""
        import os
        import subprocess
        import sys
        from pathlib import Path

        project_root = Path(__file__).parent.parent.parent
        admin = await asyncpg.connect(pg_url)
        db_name = "alembic_021_probe"
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            await admin.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin.close()

        base, _, _ = pg_url.rpartition("/")
        target_url = f"{base}/{db_name}"
        try:
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "021"],
                cwd=project_root,
                env={**os.environ, "DATABASE_URL": target_url},
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, (
                f"migration 021 cannot be applied online.\nstderr:\n{result.stderr}"
            )

            conn = await asyncpg.connect(target_url)
            try:
                for column in ("quote_text", "quote_is_manual"):
                    exists = await conn.fetchval(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'chat_messages'
                              AND column_name = $1
                        )
                        """,
                        column,
                    )
                    assert exists, f"{column} missing after online upgrade to 021"
            finally:
                await conn.close()
        finally:
            admin = await asyncpg.connect(pg_url)
            try:
                await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            finally:
                await admin.close()
