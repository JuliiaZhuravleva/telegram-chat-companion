"""Tests for MessageRepository with a mocked asyncpg pool.

Scope: unit coverage for the username-resolution methods added for B-1
stage 2 (`find_by_username`, `username_seen_elsewhere`), the
activity-ranking method added for B-2 (`get_top_active_users`), the
quote-persistence columns added for Q-3 (`save`'s `quote_text` /
`quote_is_manual` params, migration 021), and the quote-columns projection
added for Q-5 (`get_recent_with_topic_context`'s `quote_text` /
`quote_is_manual` in both the non-forum and forum-mode queries). The rest of
MessageRepository already has coverage via its callers' own test suites;
integration coverage for these methods against a real Postgres schema is
qa's to add (chat-scoped LOWER() match / GROUP BY aggregation against
`chat_messages`; round-trip persistence of the quote columns is Q-4's;
UNION ALL type-check + injection/gating/truncation regression for the
Q-5 projection is Q-6's).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.database.repositories.messages import MessageRepository

CHAT_ID = -1001234567890


@pytest.fixture
def repo():
    """MessageRepository with mocked pool."""
    pool = AsyncMock()
    return MessageRepository(pool), pool


# Positional order of save()'s $1..$N, mirroring the INSERT's column list.
_SAVE_PARAMS = (
    "chat_id",
    "message_id",
    "user_id",
    "username",
    "first_name",
    "message_type",
    "content",
    "raw_data",
    "reply_to_message_id",
    "is_bot_message",
    "sticker_file_id",
    "sticker_file_unique_id",
    "sticker_set_name",
    "sticker_emoji",
    "message_thread_id",
    "quote_text",
    "quote_is_manual",
    "transcribed_message_id",
)


def _saved(pool) -> dict:
    """save()'s bound parameters, by name.

    These assertions used to index from the end (`call_args[-2]`), which rotted
    the moment migration 028 appended a parameter: two of them silently started
    reading the wrong column, and a third kept passing only because every value
    it compared happened to be None. The length check below turns the next such
    append into an immediate, obvious failure instead of a quiet mis-read.
    """
    args = pool.execute.call_args[0][1:]
    assert len(args) == len(_SAVE_PARAMS), (
        f"save() now binds {len(args)} parameters, _SAVE_PARAMS lists {len(_SAVE_PARAMS)}"
    )
    return dict(zip(_SAVE_PARAMS, args, strict=True))


class TestSaveQuoteColumns:
    """`save()`'s quote_text/quote_is_manual params (Q-3, migration 021)."""

    @pytest.mark.asyncio
    async def test_passes_quote_fields_through_to_insert(self, repo):
        repo_, pool = repo

        await repo_.save(
            CHAT_ID,
            message_id=1,
            message_type="text",
            content="reply",
            quote_text="highlighted fragment",
            quote_is_manual=True,
        )

        pool.execute.assert_awaited_once()
        saved = _saved(pool)
        assert saved["quote_text"] == "highlighted fragment"
        assert saved["quote_is_manual"] is True

    @pytest.mark.asyncio
    async def test_defaults_quote_fields_to_none_when_no_quote(self, repo):
        repo_, pool = repo

        await repo_.save(CHAT_ID, message_id=2, message_type="text", content="plain message")

        saved = _saved(pool)
        assert saved["quote_text"] is None
        assert saved["quote_is_manual"] is None

    @pytest.mark.asyncio
    async def test_non_manual_quote_persists_false_not_none(self, repo):
        """A quote that exists but wasn't hand-selected: text is set, flag is False.

        Distinct from "no quote at all" (both NULL) -- Q-5's consumer gates on
        `quote_is_manual is True`, so this must not collapse into the same
        NULL as the no-quote case.
        """
        repo_, pool = repo

        await repo_.save(
            CHAT_ID,
            message_id=3,
            message_type="text",
            content="reply",
            quote_text="server-attached quote",
            quote_is_manual=False,
        )

        saved = _saved(pool)
        assert saved["quote_text"] == "server-attached quote"
        assert saved["quote_is_manual"] is False


class TestSaveTranscriptionLink:
    """`transcribed_message_id` (migration 028) — the sole marker of "this bot
    message is a relayed voice transcription"."""

    @pytest.mark.asyncio
    async def test_link_is_bound_when_given(self, repo):
        repo_, pool = repo

        await repo_.save(
            CHAT_ID,
            message_id=778,
            message_type="transcription",
            is_bot_message=True,
            transcribed_message_id=777,
        )

        assert _saved(pool)["transcribed_message_id"] == 777

    @pytest.mark.asyncio
    async def test_ordinary_messages_bind_null(self, repo):
        """Every other save path must leave the column NULL — a stray value
        would make an ordinary bot message read as a transcription and go
        silent on replies."""
        repo_, pool = repo

        await repo_.save(CHAT_ID, message_id=5, message_type="text", content="hi")

        assert _saved(pool)["transcribed_message_id"] is None


class TestGetRecentWithTopicContext:
    """quote_text/quote_is_manual projection added for Q-5 (migration 021).

    `get_recent_with_topic_context()` had zero unit coverage before this
    item. These tests are a thin regression guard (query text actually
    projects the two new columns; rows carrying them pass through
    unchanged) -- they cannot verify the UNION ALL type-checks against a
    real schema or that Postgres accepts the query; that's qa's
    (Q-6, testcontainers).
    """

    @pytest.mark.asyncio
    async def test_non_forum_query_projects_quote_columns(self, repo):
        repo_, pool = repo
        pool.fetch.return_value = []

        await repo_.get_recent_with_topic_context(CHAT_ID, None)

        query = pool.fetch.call_args[0][0]
        assert "quote_text" in query
        assert "quote_is_manual" in query

    @pytest.mark.asyncio
    async def test_forum_mode_query_projects_quote_columns_in_both_branches(self, repo):
        repo_, pool = repo
        pool.fetch.return_value = []

        await repo_.get_recent_with_topic_context(CHAT_ID, message_thread_id=5)

        query = pool.fetch.call_args[0][0]
        # Both the 'current' and 'other' SELECT branches of the UNION ALL
        # must project the columns -- a regression could easily add them
        # to only one side.
        assert query.count("quote_text") == 2
        assert query.count("quote_is_manual") == 2

    @pytest.mark.asyncio
    async def test_non_forum_rows_carry_quote_fields_through(self, repo):
        repo_, pool = repo
        pool.fetch.return_value = [
            {
                "id": 1,
                "chat_id": CHAT_ID,
                "message_id": 10,
                "user_id": 1,
                "username": "alice",
                "first_name": "Alice",
                "message_type": "text",
                "content": "hi",
                "is_bot_message": False,
                "created_at": None,
                "quote_text": "important bit",
                "quote_is_manual": True,
                "topic_scope": None,
            }
        ]

        result = await repo_.get_recent_with_topic_context(CHAT_ID, None)

        assert result[0]["quote_text"] == "important bit"
        assert result[0]["quote_is_manual"] is True


class TestFindByUsername:
    @pytest.mark.asyncio
    async def test_returns_row_when_found(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"user_id": 888, "first_name": "New"}

        result = await repo_.find_by_username(CHAT_ID, "newbie")

        assert result == {"user_id": 888, "first_name": "New"}
        pool.fetchrow.assert_awaited_once()
        call_args = pool.fetchrow.call_args[0]
        assert call_args[1] == CHAT_ID
        assert call_args[2] == "newbie"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = None

        result = await repo_.find_by_username(CHAT_ID, "ghost")

        assert result is None


class TestUsernameSeenElsewhere:
    @pytest.mark.asyncio
    async def test_true_when_seen_in_another_chat(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"?column?": 1}

        result = await repo_.username_seen_elsewhere(CHAT_ID, "elsewhere_user")

        assert result is True

    @pytest.mark.asyncio
    async def test_false_when_never_seen(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = None

        result = await repo_.username_seen_elsewhere(CHAT_ID, "totally_unknown")

        assert result is False


class TestGetTopActiveUsers:
    @pytest.mark.asyncio
    async def test_returns_candidates_and_total(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 12
        pool.fetch.return_value = [
            {"user_id": 111, "username": "alice", "first_name": "Alice", "message_count": 40},
            {"user_id": 222, "username": None, "first_name": "Bob", "message_count": 10},
        ]

        candidates, total = await repo_.get_top_active_users(CHAT_ID, page=0, per_page=5)

        assert total == 12
        assert candidates == [
            {"user_id": 111, "username": "alice", "first_name": "Alice", "message_count": 40},
            {"user_id": 222, "username": None, "first_name": "Bob", "message_count": 10},
        ]

    @pytest.mark.asyncio
    async def test_pagination_offset_uses_page_times_per_page(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 12
        pool.fetch.return_value = []

        await repo_.get_top_active_users(CHAT_ID, page=2, per_page=5)

        call_args = pool.fetch.call_args[0]
        assert call_args[1] == CHAT_ID
        assert call_args[2] == 5  # per_page (LIMIT)
        assert call_args[3] == 10  # page * per_page (OFFSET)

    @pytest.mark.asyncio
    async def test_zero_total_when_no_rows(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = None
        pool.fetch.return_value = []

        candidates, total = await repo_.get_top_active_users(CHAT_ID, page=0, per_page=5)

        assert total == 0
        assert candidates == []


class TestSaveNeverErasesContentOnConflict:
    """Shape-level guard on the ON CONFLICT branch.

    Weak on its own — it asserts SQL text, not what PostgreSQL does with it,
    and the behavioural coverage lives in
    tests/integration/test_migration_028_transcription_link.py. It earns its
    place as a tripwire: the defect it guards was a single unconditional
    assignment that looked entirely reasonable in review for six months, and
    this is the assertion that makes reintroducing it loud.
    """

    def _set_clause(self, pool) -> str:
        sql = pool.execute.call_args[0][0]
        return sql[sql.index("DO UPDATE") :]

    async def test_content_is_coalesced_not_overwritten(self, repo):
        repository, pool = repo
        await repository.save(CHAT_ID, 1, "voice")

        clause = self._set_clause(pool)
        assert "content = COALESCE(EXCLUDED.content, chat_messages.content)" in clause
        assert "content = EXCLUDED.content" not in clause

    async def test_edit_bookkeeping_is_conditional_on_a_real_content_change(self, repo):
        repository, pool = repo
        await repository.save(CHAT_ID, 1, "voice")

        clause = self._set_clause(pool)
        # An unconditional NOW() / +1 would record a phantom edit for every
        # content-less re-save, pinning original_content to a value nothing
        # ever edited away from.
        assert "edited_at = NOW()," not in clause
        assert "edit_count = chat_messages.edit_count + 1," not in clause
        assert clause.count("EXCLUDED.content IS DISTINCT FROM chat_messages.content") == 3
