"""Tests for MessageRepository with a mocked asyncpg pool.

Scope: unit coverage for the username-resolution methods added for B-1
stage 2 (`find_by_username`, `username_seen_elsewhere`). The rest of
MessageRepository already has coverage via its callers' own test suites;
integration coverage for these two methods against a real Postgres schema
is qa's to add (chat-scoped LOWER() match against `chat_messages`).
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
