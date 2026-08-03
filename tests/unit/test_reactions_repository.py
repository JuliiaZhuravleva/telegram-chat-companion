"""Tests for ReactionRepository (message_reactions) with a mocked asyncpg pool."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.database.repositories.reactions import ReactionRepository
from src.services.modules.reactions.models import ReactionEvent


@pytest.fixture
def pool() -> AsyncMock:
    pool = AsyncMock()
    pool.executemany = AsyncMock()
    return pool


@pytest.fixture
def repo(pool: AsyncMock) -> ReactionRepository:
    return ReactionRepository(pool)


class TestInsertEvents:
    @pytest.mark.asyncio
    async def test_empty_events_is_a_noop(self, repo: ReactionRepository, pool: AsyncMock) -> None:
        await repo.insert_events(chat_id=1, message_id=2, user_id=3, actor_chat_id=None, events=[])
        pool.executemany.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inserts_one_row_per_event(
        self, repo: ReactionRepository, pool: AsyncMock
    ) -> None:
        events = [
            ReactionEvent(action="added", reaction_type="emoji", emoji="👍"),
            ReactionEvent(action="removed", reaction_type="emoji", emoji="🔥"),
        ]

        await repo.insert_events(
            chat_id=100, message_id=200, user_id=300, actor_chat_id=None, events=events
        )

        pool.executemany.assert_awaited_once()
        sql, rows = pool.executemany.await_args.args
        assert "INSERT INTO message_reactions" in sql
        assert rows == [
            (100, 200, 300, None, "added", "emoji", "👍", None),
            (100, 200, 300, None, "removed", "emoji", "🔥", None),
        ]

    @pytest.mark.asyncio
    async def test_anonymous_reactor_carries_actor_chat_id_not_user_id(
        self, repo: ReactionRepository, pool: AsyncMock
    ) -> None:
        """user is None, actor_chat carries the anonymous-reactor chat."""
        events = [ReactionEvent(action="added", reaction_type="emoji", emoji="👍")]

        await repo.insert_events(
            chat_id=1, message_id=2, user_id=None, actor_chat_id=-100999, events=events
        )

        _, rows = pool.executemany.await_args.args
        assert rows == [(1, 2, None, -100999, "added", "emoji", "👍", None)]

    @pytest.mark.asyncio
    async def test_custom_emoji_and_paid_columns(
        self, repo: ReactionRepository, pool: AsyncMock
    ) -> None:
        events = [
            ReactionEvent(action="added", reaction_type="custom_emoji", custom_emoji_id="42"),
            ReactionEvent(action="added", reaction_type="paid"),
        ]

        await repo.insert_events(
            chat_id=1, message_id=2, user_id=9, actor_chat_id=None, events=events
        )

        _, rows = pool.executemany.await_args.args
        assert rows == [
            (1, 2, 9, None, "added", "custom_emoji", None, "42"),
            (1, 2, 9, None, "added", "paid", None, None),
        ]
