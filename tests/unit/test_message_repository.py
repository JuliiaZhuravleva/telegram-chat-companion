"""Tests for MessageRepository with a mocked asyncpg pool.

Scope: unit coverage for the username-resolution methods added for B-1
stage 2 (`find_by_username`, `username_seen_elsewhere`), the
activity-ranking method added for B-2 (`get_top_active_users`), and the
quote-persistence columns added for Q-3 (`save`'s `quote_text` /
`quote_is_manual` params, migration 021). The rest of MessageRepository
already has coverage via its callers' own test suites; integration coverage
for these methods against a real Postgres schema is qa's to add (chat-scoped
LOWER() match / GROUP BY aggregation against `chat_messages`; round-trip
persistence of the quote columns is Q-4's).
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
        call_args = pool.execute.call_args[0]
        # Last two positional params, matching $16/$17 in the INSERT.
        assert call_args[-2] == "highlighted fragment"
        assert call_args[-1] is True

    @pytest.mark.asyncio
    async def test_defaults_quote_fields_to_none_when_no_quote(self, repo):
        repo_, pool = repo

        await repo_.save(CHAT_ID, message_id=2, message_type="text", content="plain message")

        call_args = pool.execute.call_args[0]
        assert call_args[-2] is None
        assert call_args[-1] is None

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

        call_args = pool.execute.call_args[0]
        assert call_args[-2] == "server-attached quote"
        assert call_args[-1] is False


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
