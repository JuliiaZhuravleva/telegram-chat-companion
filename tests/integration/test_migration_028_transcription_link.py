"""Migration 028: `chat_messages.transcribed_message_id` and what reads it.

Every unit test around this feature mocks `MessageRepository`, so none of them
executes a single character of the SQL. A wrong column name, a broken join, or
a `message_type` filter that excludes the wrong rows would pass the entire unit
suite. These tests drive the real repository against a real PostgreSQL.

They also pin the two side effects that are easy to get wrong and impossible to
notice from the outside: the bookkeeping row must not reach the prompt history,
and it must not count towards the relevancy gate's bot-to-human ratio (which
would make the bot quietly mute itself in any chat with voice traffic).
"""

from __future__ import annotations

import asyncpg
import pytest

from src.database.repositories.messages import MessageRepository

CHAT_ID = -100999000111
VOICE_ID = 777
TRANSCRIPTION_ID = 778


@pytest.fixture
def repo(db_pool: asyncpg.Pool) -> MessageRepository:
    return MessageRepository(db_pool)


@pytest.fixture(autouse=True)
async def _clean(db_pool: asyncpg.Pool):
    await db_pool.execute("DELETE FROM chat_messages WHERE chat_id = $1", CHAT_ID)
    yield
    await db_pool.execute("DELETE FROM chat_messages WHERE chat_id = $1", CHAT_ID)


async def _seed_voice_and_transcription(repo: MessageRepository) -> None:
    """The two rows the voice handler writes, in the order it writes them."""
    await repo.save(
        CHAT_ID,
        VOICE_ID,
        "voice",
        user_id=555,
        first_name="Иван",
        content="давайте в субботу",
    )
    await repo.save(
        CHAT_ID,
        TRANSCRIPTION_ID,
        "transcription",
        is_bot_message=True,
        reply_to_message_id=VOICE_ID,
        transcribed_message_id=VOICE_ID,
    )


class TestColumnExists:
    async def test_column_and_partial_index_are_present(self, db_pool: asyncpg.Pool):
        column = await db_pool.fetchval(
            """
            SELECT data_type FROM information_schema.columns
             WHERE table_name = 'chat_messages'
               AND column_name = 'transcribed_message_id'
            """
        )
        assert column == "bigint"

        index = await db_pool.fetchval(
            "SELECT indexname FROM pg_indexes WHERE indexname = $1",
            "idx_chat_messages_transcribed_source",
        )
        assert index == "idx_chat_messages_transcribed_source"

    async def test_column_is_nullable_with_no_default(self, db_pool: asyncpg.Pool):
        """A DEFAULT here would mark every message a transcription of something."""
        row = await db_pool.fetchrow(
            """
            SELECT is_nullable, column_default FROM information_schema.columns
             WHERE table_name = 'chat_messages'
               AND column_name = 'transcribed_message_id'
            """
        )
        assert row["is_nullable"] == "YES"
        assert row["column_default"] is None


class TestGetTranscriptionSource:
    async def test_resolves_the_speaker_and_what_they_said(self, repo: MessageRepository):
        await _seed_voice_and_transcription(repo)

        found = await repo.get_transcription_source(CHAT_ID, TRANSCRIPTION_ID)

        assert found is not None
        assert found["source_message_id"] == VOICE_ID
        assert found["source_first_name"] == "Иван"
        assert found["source_user_id"] == 555
        assert found["transcript"] == "давайте в субботу"

    async def test_an_ordinary_bot_message_is_not_a_transcription(self, repo: MessageRepository):
        """The control that matters: this is what keeps a normal AI reply
        counting as addressed to the bot."""
        await repo.save(CHAT_ID, 900, "text", content="как скажешь", is_bot_message=True)

        assert await repo.get_transcription_source(CHAT_ID, 900) is None

    async def test_the_voice_message_itself_is_not_a_transcription(self, repo: MessageRepository):
        await _seed_voice_and_transcription(repo)

        assert await repo.get_transcription_source(CHAT_ID, VOICE_ID) is None

    async def test_unknown_message_id(self, repo: MessageRepository):
        await _seed_voice_and_transcription(repo)

        assert await repo.get_transcription_source(CHAT_ID, 999999) is None

    async def test_the_lookup_is_chat_scoped(self, repo: MessageRepository, db_pool: asyncpg.Pool):
        """Message ids are only unique per chat; a cross-chat hit would attach
        one chat's speaker to another chat's message."""
        await _seed_voice_and_transcription(repo)

        assert await repo.get_transcription_source(CHAT_ID + 1, TRANSCRIPTION_ID) is None

    async def test_survives_the_source_row_being_pruned(
        self, repo: MessageRepository, db_pool: asyncpg.Pool
    ):
        """Retention can delete the audio row while the link row remains.

        "This is a transcription" must still hold — that is what suppresses the
        REPLY trigger — even though the speaker and the words are now unknown.
        """
        await _seed_voice_and_transcription(repo)
        await db_pool.execute(
            "DELETE FROM chat_messages WHERE chat_id = $1 AND message_id = $2",
            CHAT_ID,
            VOICE_ID,
        )

        found = await repo.get_transcription_source(CHAT_ID, TRANSCRIPTION_ID)

        assert found is not None
        assert found["source_message_id"] == VOICE_ID
        assert found["source_first_name"] is None
        assert found["transcript"] is None


class TestBookkeepingRowStaysOutOfTheWay:
    async def test_history_excludes_the_transcription_row(self, repo: MessageRepository):
        """It carries no content, so it would render as an empty "Bot:" turn,
        and the transcript is already on the voice row — including it would put
        the same utterance in the prompt twice."""
        await _seed_voice_and_transcription(repo)

        history = await repo.get_recent_with_topic_context(CHAT_ID, None)

        ids = [row["message_id"] for row in history]
        assert VOICE_ID in ids
        assert TRANSCRIPTION_ID not in ids

    async def test_bot_ratio_ignores_the_transcription_row(self, repo: MessageRepository):
        """The relevancy gate mutes the bot when it judges the bot is dominating.
        Counting bookkeeping rows as bot turns would make voice-heavy chats
        gradually silence it for reasons no one could see."""
        await _seed_voice_and_transcription(repo)

        stats = await repo.get_bot_message_stats(CHAT_ID)

        assert stats["total_count"] == 1
        assert stats["bot_count"] == 0

    async def test_a_real_bot_reply_still_counts(self, repo: MessageRepository):
        """Control for the test above — the exclusion must be narrow."""
        await _seed_voice_and_transcription(repo)
        await repo.save(CHAT_ID, 900, "text", content="и тебе привет", is_bot_message=True)

        stats = await repo.get_bot_message_stats(CHAT_ID)

        assert stats["bot_count"] == 1


class TestEveryReaderExcludesTheBookkeepingRow:
    """The exclusion has to be complete, and it was not.

    `get_recent_with_topic_context` and `get_bot_message_stats` were filtered on
    the first pass; `get_recent` was missed. That one feeds the relevancy gate's
    tier-3 judge, which renders each row as `f"{prefix}: {content}"` — so a
    content-free bot row became a bare "Bot: " line in the prompt that decides
    whether the bot speaks, and displaced a real turn out of a 5-message window
    every time someone sent a voice note.
    """

    async def test_get_recent_excludes_it(self, repo: MessageRepository):
        await _seed_voice_and_transcription(repo)

        recent = await repo.get_recent(CHAT_ID, limit=10)

        ids = [row["message_id"] for row in recent]
        assert VOICE_ID in ids
        assert TRANSCRIPTION_ID not in ids

    async def test_get_recent_still_returns_real_bot_replies(self, repo: MessageRepository):
        """Control, and the reason `exclude_bot=True` is the wrong fix here:
        the judge needs to see what the bot actually said."""
        await _seed_voice_and_transcription(repo)
        await repo.save(CHAT_ID, 900, "text", content="и тебе привет", is_bot_message=True)

        recent = await repo.get_recent(CHAT_ID, limit=10)

        ids = [row["message_id"] for row in recent]
        assert 900 in ids
        assert TRANSCRIPTION_ID not in ids

    async def test_no_row_reaching_a_prompt_has_null_content(self, repo: MessageRepository):
        """The property behind all of this, asserted directly rather than per
        call site: anything the prompt renders must have something to render."""
        await _seed_voice_and_transcription(repo)

        for row in await repo.get_recent(CHAT_ID, limit=10):
            assert row["content"] is not None, row["message_id"]
        for row in await repo.get_recent_with_topic_context(CHAT_ID, None):
            assert row["content"] is not None, row["message_id"]
