"""Migration 035: who really speaks, against a real PostgreSQL.

Every unit test of this feature mocks the repositories, so none of them runs a
character of the SQL: the backfill's CASE, the COALESCE that keeps a re-save
from erasing the sender, the "never overwrite a different channel" UPDATE and
the person-only filters are all exercised here and nowhere else.

Every id below is invented; the three placeholder accounts are Telegram's own
and the same in every chat.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.messages import MessageRepository

CHAT_ID = -1009990035
CHANNEL_ID = -1009990036
OTHER_CHANNEL_ID = -1009990037

TELEGRAM_SERVICE_ID = 777000
GROUP_ANONYMOUS_BOT_ID = 1087968824
CHANNEL_BOT_ID = 136817688

_MIGRATION = Path(__file__).parents[2] / "alembic" / "versions" / "035_sender_kind.py"


@pytest.fixture
def messages(db_pool: asyncpg.Pool) -> MessageRepository:
    return MessageRepository(db_pool)


@pytest.fixture
def settings_repo(db_pool: asyncpg.Pool) -> ChatSettingsRepository:
    return ChatSettingsRepository(db_pool)


@pytest.fixture(autouse=True)
async def _clean(db_pool: asyncpg.Pool):
    async def wipe() -> None:
        await db_pool.execute("DELETE FROM chat_messages WHERE chat_id = $1", CHAT_ID)
        await db_pool.execute("DELETE FROM chat_settings WHERE chat_id = $1", CHAT_ID)

    await wipe()
    yield
    await wipe()


def _backfill_sql() -> str:
    """The migration's own UPDATE, captured rather than copied."""
    spec = importlib.util.spec_from_file_location("migration_035", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements: list[str] = []
    module.op = SimpleNamespace(execute=statements.append)
    module.upgrade()
    updates = [s for s in statements if s.lstrip().startswith("UPDATE")]
    assert len(updates) == 1, statements
    return updates[0]


class TestSchema:
    async def test_per_chat_columns_are_nullable_with_no_default(self, db_pool: asyncpg.Pool):
        # A DEFAULT would be materialized by ensure_exists() and shadow the
        # global layer (CLAUDE.md, migrations 014/015).
        rows = await db_pool.fetch(
            """
            SELECT column_name, is_nullable, column_default
              FROM information_schema.columns
             WHERE table_name = 'chat_settings'
               AND column_name IN ('linked_channel_id', 'linked_channel_title',
                                   'transcribe_expandable')
            """
        )
        assert {r["column_name"]: (r["is_nullable"], r["column_default"]) for r in rows} == {
            "linked_channel_id": ("YES", None),
            "linked_channel_title": ("YES", None),
            "transcribe_expandable": ("YES", None),
        }


class TestBackfill:
    async def test_placeholders_are_classified_and_people_are_not(
        self, db_pool: asyncpg.Pool, messages: MessageRepository
    ):
        for message_id, user_id, is_bot in (
            (1, TELEGRAM_SERVICE_ID, False),
            (2, GROUP_ANONYMOUS_BOT_ID, True),
            (3, CHANNEL_BOT_ID, True),
            (4, 900001, False),  # a person
            (5, 900002, True),  # another bot
            (6, None, True),  # this bot
        ):
            await messages.save(
                CHAT_ID, message_id, "text", user_id=user_id, is_bot_message=is_bot, content="x"
            )

        await db_pool.execute(_backfill_sql())

        rows = await db_pool.fetch(
            "SELECT message_id, sender_kind FROM chat_messages WHERE chat_id = $1 "
            "ORDER BY message_id",
            CHAT_ID,
        )
        assert [r["sender_kind"] for r in rows] == [
            "channel_post",
            "anon_admin",
            "channel",
            None,
            None,
            None,
        ]

    async def test_it_fills_and_never_overwrites(
        self, db_pool: asyncpg.Pool, messages: MessageRepository
    ):
        # A row the new MessageSaver already classified from the live message.
        await messages.save(
            CHAT_ID,
            1,
            "text",
            user_id=CHANNEL_BOT_ID,
            is_bot_message=True,
            content="x",
            sender_kind="channel",
            sender_chat_id=CHANNEL_ID,
            sender_chat_title="Блог",
        )
        await db_pool.execute(
            "UPDATE chat_messages SET sender_kind = 'anon_admin' WHERE chat_id = $1", CHAT_ID
        )

        await db_pool.execute(_backfill_sql())

        assert (
            await db_pool.fetchval(
                "SELECT sender_kind FROM chat_messages WHERE chat_id = $1", CHAT_ID
            )
            == "anon_admin"
        )


class TestSaveKeepsTheSender:
    async def test_a_re_save_without_the_sender_does_not_erase_it(
        self, db_pool: asyncpg.Pool, messages: MessageRepository
    ):
        await messages.save(
            CHAT_ID,
            1,
            "voice",
            user_id=GROUP_ANONYMOUS_BOT_ID,
            is_bot_message=True,
            sender_kind="anon_admin",
            sender_chat_id=CHAT_ID,
            author_signature="Юля",
        )
        # What the transcription writes back onto the same row, and what an
        # edited_message of a caption-less voice note carries: no sender at all.
        await messages.save(CHAT_ID, 1, "voice", content="расшифровка")

        row = await db_pool.fetchrow(
            "SELECT sender_kind, sender_chat_id, author_signature, content "
            "FROM chat_messages WHERE chat_id = $1 AND message_id = 1",
            CHAT_ID,
        )
        assert dict(row) == {
            "sender_kind": "anon_admin",
            "sender_chat_id": CHAT_ID,
            "author_signature": "Юля",
            "content": "расшифровка",
        }

    async def test_the_transcription_source_reports_who_spoke(self, messages: MessageRepository):
        await messages.save(
            CHAT_ID,
            1,
            "voice",
            user_id=TELEGRAM_SERVICE_ID,
            content="текст поста",
            sender_kind="channel_post",
            sender_chat_id=CHANNEL_ID,
            sender_chat_title="Блог",
        )
        await messages.save(
            CHAT_ID,
            2,
            "transcription",
            is_bot_message=True,
            reply_to_message_id=1,
            transcribed_message_id=1,
        )

        source = await messages.get_transcription_source(CHAT_ID, 2)

        assert source is not None
        assert (source["source_sender_kind"], source["source_sender_chat_title"]) == (
            "channel_post",
            "Блог",
        )


class TestPeopleOnly:
    """A channel's posts are not a participant and not a reply-length sample."""

    async def _seed(self, messages: MessageRepository) -> None:
        await messages.save(
            CHAT_ID,
            1,
            "text",
            user_id=TELEGRAM_SERVICE_ID,
            first_name="Telegram",
            content="очень длинный пост канала " * 40,
            sender_kind="channel_post",
        )
        await messages.save(
            CHAT_ID, 2, "text", user_id=900001, first_name="Аня", content="короткий ответ"
        )

    async def test_reply_length_samples(self, messages: MessageRepository):
        await self._seed(messages)

        assert await messages.get_recent_lengths(CHAT_ID) == [len("короткий ответ")]

    async def test_participant_picker(self, messages: MessageRepository):
        await self._seed(messages)

        candidates, total = await messages.get_top_active_users(CHAT_ID, page=0)

        assert [c["user_id"] for c in candidates] == [900001]
        assert total == 1


class TestSetLinkedChannel:
    async def test_no_row_yet_writes_nothing(self, settings_repo: ChatSettingsRepository):
        assert await settings_repo.set_linked_channel(CHAT_ID, CHANNEL_ID, "Блог") is None

    async def test_an_empty_slot_is_filled(
        self, db_pool: asyncpg.Pool, settings_repo: ChatSettingsRepository
    ):
        await settings_repo.ensure_exists(CHAT_ID, "Обсуждение", "supergroup")

        assert await settings_repo.set_linked_channel(CHAT_ID, CHANNEL_ID, "Блог") == CHANNEL_ID
        row = await db_pool.fetchrow(
            "SELECT linked_channel_id, linked_channel_title FROM chat_settings WHERE chat_id = $1",
            CHAT_ID,
        )
        assert tuple(row) == (CHANNEL_ID, "Блог")

    async def test_the_same_channel_refreshes_its_title(
        self, db_pool: asyncpg.Pool, settings_repo: ChatSettingsRepository
    ):
        await settings_repo.ensure_exists(CHAT_ID, "Обсуждение", "supergroup")
        await settings_repo.set_linked_channel(CHAT_ID, CHANNEL_ID, "Блог")

        await settings_repo.set_linked_channel(CHAT_ID, CHANNEL_ID, "Блог 2")

        assert (
            await db_pool.fetchval(
                "SELECT linked_channel_title FROM chat_settings WHERE chat_id = $1", CHAT_ID
            )
            == "Блог 2"
        )

    async def test_a_different_channel_is_never_written(
        self, db_pool: asyncpg.Pool, settings_repo: ChatSettingsRepository
    ):
        await settings_repo.ensure_exists(CHAT_ID, "Обсуждение", "supergroup")
        await settings_repo.set_linked_channel(CHAT_ID, CHANNEL_ID, "Блог")

        stored = await settings_repo.set_linked_channel(CHAT_ID, OTHER_CHANNEL_ID, "Чужой")

        assert stored == CHANNEL_ID
        row = await db_pool.fetchrow(
            "SELECT linked_channel_id, linked_channel_title FROM chat_settings WHERE chat_id = $1",
            CHAT_ID,
        )
        assert tuple(row) == (CHANNEL_ID, "Блог")
