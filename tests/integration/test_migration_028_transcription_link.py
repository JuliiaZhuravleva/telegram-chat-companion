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

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from aiogram.types import Message

from src.bot.middleware.message_saver import MessageSaverMiddleware
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


# ── The transcript survives an `edited_message` ────────────────────────
#
# Telegram delivers `edited_message` for a voice note, and `MessageSaverMiddleware`
# is registered on that observer too (src/main.py). It saves
# `message.text or message.caption`, which is None for audio -- so the re-save
# used to take the ON CONFLICT branch and overwrite the transcript with NULL.
#
# These drive the REAL middleware into the REAL repository against real
# PostgreSQL, because that pair is the defect: the middleware is what produces
# the NULL and the SQL is what accepts it. A test at either end alone stays
# green while the chain loses data.


def _edited_media_message(
    chat_id: int, message_id: int, *, kind: str, caption: str | None = None
) -> MagicMock:
    """A Telegram `edited_message` for a voice / video_note / photo.

    Every field `_save_message` reads is pinned explicitly: `spec=Message`
    hands back a truthy MagicMock for anything unset, which would make the
    middleware classify the type off the wrong attribute.
    """
    event = MagicMock(spec=Message)
    event.chat = MagicMock()
    event.chat.id = chat_id
    event.message_id = message_id
    event.from_user = MagicMock()
    event.from_user.id = 555
    event.from_user.username = "ivan"
    event.from_user.first_name = "Иван"
    event.from_user.is_bot = False
    # The whole point: an audio or uncaptioned-photo edit carries neither.
    event.text = None
    event.caption = caption
    event.reply_to_message = None
    event.quote = None
    event.sticker = None
    event.voice = MagicMock() if kind == "voice" else None
    event.video_note = MagicMock() if kind == "video_note" else None
    event.photo = [MagicMock()] if kind == "photo" else None
    return event


def _edited_text_message(chat_id: int, message_id: int, text: str) -> MagicMock:
    event = _edited_media_message(chat_id, message_id, kind="text")
    event.text = text
    return event


async def _save_via_middleware(event: MagicMock, repo: MessageRepository) -> None:
    """Route a mock Message through the real middleware code path.

    `_save_message` pulls the repository out of `data["dishka_container"]`; an
    AsyncMock whose `.get` returns the real DB-backed repo reproduces that
    without standing up a full Dishka container.
    """
    container = AsyncMock()
    container.get.return_value = repo
    await MessageSaverMiddleware._save_message(
        event, {"dishka_container": container, "chat_config": None}
    )


async def _row(db_pool: asyncpg.Pool, message_id: int) -> asyncpg.Record:
    row = await db_pool.fetchrow(
        "SELECT content, original_content, edit_count, edited_at "
        "FROM chat_messages WHERE chat_id = $1 AND message_id = $2",
        CHAT_ID,
        message_id,
    )
    assert row is not None, f"no row for message_id={message_id}"
    return row


class TestEditedMediaMessageDoesNotEraseBotWrittenContent:
    """Regression for the 52 rows emptied in production between 2026-08-03 and
    2026-09-01 (29 voice, 11 video_note, 12 photo)."""

    async def test_an_edited_voice_note_keeps_its_transcript(
        self, repo: MessageRepository, db_pool: asyncpg.Pool
    ):
        """The defect, end to end, in the order production performed it."""
        await repo.save(
            CHAT_ID, VOICE_ID, "voice", user_id=555, first_name="Иван"
        )  # MessageSaverMiddleware on dp.message: no text on a voice note
        await repo.save(
            CHAT_ID,
            VOICE_ID,
            "voice",
            user_id=555,
            first_name="Иван",
            content="давайте в субботу",
        )  # VoiceTranscriptionService.transcribe

        await _save_via_middleware(_edited_media_message(CHAT_ID, VOICE_ID, kind="voice"), repo)

        assert (await _row(db_pool, VOICE_ID))["content"] == "давайте в субботу"

    async def test_an_edited_video_note_keeps_its_transcript(
        self, repo: MessageRepository, db_pool: asyncpg.Pool
    ):
        await repo.save(CHAT_ID, 801, "video_note", user_id=555, content="проверяем кружочки")

        await _save_via_middleware(_edited_media_message(CHAT_ID, 801, kind="video_note"), repo)

        assert (await _row(db_pool, 801))["content"] == "проверяем кружочки"

    async def test_an_edited_photo_keeps_its_vision_description(
        self, repo: MessageRepository, db_pool: asyncpg.Pool
    ):
        """`_update_message_content` owns a photo row's content, not the caption."""
        await repo.save(CHAT_ID, 802, "photo", user_id=555, content="[Image: кот на подоконнике]")

        await _save_via_middleware(_edited_media_message(CHAT_ID, 802, kind="photo"), repo)

        assert (await _row(db_pool, 802))["content"] == "[Image: кот на подоконнике]"

    async def test_a_content_less_resave_records_no_edit_at_all(
        self, repo: MessageRepository, db_pool: asyncpg.Pool
    ):
        """Preserving the text is not enough on its own.

        If the bookkeeping columns still moved, `original_content` would be
        pinned to a value nothing ever edited away from, and every future
        "was this edited / show me the original" reader inherits a false
        positive on every voice note in the chat.
        """
        await repo.save(CHAT_ID, 803, "voice", user_id=555, content="привет")
        before = await _row(db_pool, 803)

        await _save_via_middleware(_edited_media_message(CHAT_ID, 803, kind="voice"), repo)

        after = await _row(db_pool, 803)
        assert after["edit_count"] == before["edit_count"]
        assert after["edited_at"] == before["edited_at"]
        assert after["original_content"] == before["original_content"]

    async def test_resaving_identical_content_records_no_edit_either(
        self, repo: MessageRepository, db_pool: asyncpg.Pool
    ):
        """The `IS DISTINCT FROM` half of the guard, asserted behaviourally.

        Removing it from all three columns and keeping only `IS NOT NULL` left
        every integration test in this file green — the defect's own data never
        exercises "same content saved twice", so only a string-match on the SQL
        caught it. This is that same fact stated as behaviour: a re-save that
        changes nothing is not an edit.

        It is a real path, not a hypothetical: aiogram redelivers an unconfirmed
        update after a restart, and the voice handler then re-transcribes the
        same audio to the same text.
        """
        await repo.save(CHAT_ID, 820, "voice", user_id=555, content="одно и то же")
        before = await _row(db_pool, 820)

        await repo.save(CHAT_ID, 820, "voice", user_id=555, content="одно и то же")

        after = await _row(db_pool, 820)
        assert after["edit_count"] == before["edit_count"]
        assert after["edited_at"] == before["edited_at"]
        assert after["original_content"] is None, (
            "an unchanged re-save must not pin original_content to the live text"
        )

    async def test_the_invariant_the_defect_broke_holds_after_an_edit(
        self, repo: MessageRepository, db_pool: asyncpg.Pool
    ):
        """`test_no_row_reaching_a_prompt_has_null_content`, re-asserted on the
        far side of the edit that used to violate it. A NULL here does not read
        as absent: `_format_message` renders it as the literal word "None"
        attributed to a named participant."""
        await _seed_voice_and_transcription(repo)

        await _save_via_middleware(_edited_media_message(CHAT_ID, VOICE_ID, kind="voice"), repo)

        for row in await repo.get_recent(CHAT_ID, limit=10):
            assert row["content"] is not None, row["message_id"]
        for row in await repo.get_recent_with_topic_context(CHAT_ID, None):
            assert row["content"] is not None, row["message_id"]


class TestTheGuardDoesNotWronglyRefuse:
    """The cases whose expected answer is "yes, overwrite". A fix that froze
    `content` outright would pass every test above and break editing."""

    async def test_a_real_text_edit_still_replaces_the_text(
        self, repo: MessageRepository, db_pool: asyncpg.Pool
    ):
        await repo.save(CHAT_ID, 810, "text", user_id=555, content="первая редакция")

        await _save_via_middleware(_edited_text_message(CHAT_ID, 810, "вторая редакция"), repo)

        row = await _row(db_pool, 810)
        assert row["content"] == "вторая редакция"
        assert row["original_content"] == "первая редакция"
        assert row["edit_count"] == 1
        assert row["edited_at"] is not None

    async def test_an_edited_caption_still_replaces_the_content(
        self, repo: MessageRepository, db_pool: asyncpg.Pool
    ):
        await repo.save(CHAT_ID, 811, "photo", user_id=555, content="старая подпись")

        await _save_via_middleware(
            _edited_media_message(CHAT_ID, 811, kind="photo", caption="новая подпись"), repo
        )

        assert (await _row(db_pool, 811))["content"] == "новая подпись"

    async def test_the_transcription_write_itself_still_lands(
        self, repo: MessageRepository, db_pool: asyncpg.Pool
    ):
        """NULL -> transcript is the write the whole feature depends on, and it
        goes through the same ON CONFLICT branch as the wipe did."""
        await repo.save(CHAT_ID, 812, "voice", user_id=555)

        await repo.save(CHAT_ID, 812, "voice", user_id=555, content="расшифровка")

        assert (await _row(db_pool, 812))["content"] == "расшифровка"

    async def test_removing_a_caption_no_longer_clears_the_content(
        self, repo: MessageRepository, db_pool: asyncpg.Pool
    ):
        """The documented trade, asserted so it is a decision and not a
        discovery: `caption=None` is indistinguishable from "this update
        carries no content", so an emptied caption now survives in history.
        Losing a transcript outright is the worse of the two."""
        await repo.save(CHAT_ID, 813, "photo", user_id=555, content="подпись, которую удалят")

        await _save_via_middleware(
            _edited_media_message(CHAT_ID, 813, kind="photo", caption=None), repo
        )

        assert (await _row(db_pool, 813))["content"] == "подпись, которую удалят"
