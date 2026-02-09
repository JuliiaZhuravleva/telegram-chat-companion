"""Tests for src.bot.middleware.topic — TopicMiddleware."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.middleware.topic import TopicMiddleware, _extract_thread_id


def _make_middleware():
    """Create middleware instance."""
    return TopicMiddleware()


def _make_message_event(*, thread_id=None):
    """Create a mock Message event with optional message_thread_id."""
    from aiogram.types import Message

    event = MagicMock(spec=Message)
    event.chat = MagicMock()
    event.chat.id = 123

    if thread_id is not None:
        event.message_thread_id = thread_id
    else:
        # Simulate attribute not present
        del event.message_thread_id

    return event


def _make_callback_event(*, thread_id=None):
    """Create a mock CallbackQuery event with optional message_thread_id."""
    from aiogram.types import CallbackQuery, Message

    inner_message = MagicMock(spec=Message)
    inner_message.chat = MagicMock()
    inner_message.chat.id = 123

    if thread_id is not None:
        inner_message.message_thread_id = thread_id
    else:
        del inner_message.message_thread_id

    event = MagicMock(spec=CallbackQuery)
    event.message = inner_message

    return event


class TestTopicMiddlewareExtraction:
    """Test _extract_thread_id helper function."""

    def test_extracts_from_message_with_thread_id(self):
        event = _make_message_event(thread_id=42)
        result = _extract_thread_id(event)
        assert result == 42

    def test_returns_none_for_message_without_thread_id(self):
        event = _make_message_event(thread_id=None)
        result = _extract_thread_id(event)
        assert result is None

    def test_extracts_from_callback_query_with_thread_id(self):
        event = _make_callback_event(thread_id=99)
        result = _extract_thread_id(event)
        assert result == 99

    def test_returns_none_for_callback_without_thread_id(self):
        event = _make_callback_event(thread_id=None)
        result = _extract_thread_id(event)
        assert result is None

    def test_returns_none_for_callback_without_message(self):
        from aiogram.types import CallbackQuery

        event = MagicMock(spec=CallbackQuery)
        event.message = None
        result = _extract_thread_id(event)
        assert result is None

    def test_validates_integer_type(self):
        """Reject non-integer values for defense-in-depth."""
        from aiogram.types import Message

        event = MagicMock(spec=Message)
        event.chat = MagicMock()
        event.chat.is_forum = True
        event.message_thread_id = "42"  # String, not int
        result = _extract_thread_id(event)
        assert result is None

    def test_accepts_general_topic_id_one(self):
        """General topic always has ID 1."""
        event = _make_message_event(thread_id=1)
        result = _extract_thread_id(event)
        assert result == 1

    def test_returns_none_for_unknown_event_type(self):
        """Unknown event types return None."""
        event = MagicMock()  # Not Message or CallbackQuery
        result = _extract_thread_id(event)
        assert result is None

    def test_returns_none_for_non_forum_supergroup(self):
        """Non-forum chats with message_thread_id should return None."""
        from aiogram.types import Message

        event = MagicMock(spec=Message)
        event.chat = MagicMock()
        event.chat.is_forum = False
        event.message_thread_id = 42
        assert _extract_thread_id(event) is None

    def test_returns_thread_id_for_forum_supergroup(self):
        """Forum chats should return the thread_id."""
        from aiogram.types import Message

        event = MagicMock(spec=Message)
        event.chat = MagicMock()
        event.chat.is_forum = True
        event.message_thread_id = 42
        assert _extract_thread_id(event) == 42

    def test_returns_none_for_non_forum_callback(self):
        """Non-forum callback queries should return None."""
        from aiogram.types import CallbackQuery, Message

        inner = MagicMock(spec=Message)
        inner.chat = MagicMock()
        inner.chat.is_forum = False
        inner.message_thread_id = 42

        event = MagicMock(spec=CallbackQuery)
        event.message = inner
        assert _extract_thread_id(event) is None


class TestTopicMiddleware:
    """Test TopicMiddleware injection."""

    @pytest.mark.asyncio
    async def test_injects_thread_id_into_data(self):
        middleware = _make_middleware()
        handler = AsyncMock()
        event = _make_message_event(thread_id=42)
        data: dict = {}

        await middleware(handler, event, data)

        assert data["message_thread_id"] == 42
        handler.assert_awaited_once_with(event, data)

    @pytest.mark.asyncio
    async def test_injects_none_for_non_forum_message(self):
        middleware = _make_middleware()
        handler = AsyncMock()
        event = _make_message_event(thread_id=None)
        data: dict = {}

        await middleware(handler, event, data)

        assert data["message_thread_id"] is None
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_extracts_from_callback_query(self):
        middleware = _make_middleware()
        handler = AsyncMock()
        event = _make_callback_event(thread_id=99)
        data: dict = {}

        await middleware(handler, event, data)

        assert data["message_thread_id"] == 99
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_always_calls_handler(self):
        """Middleware always passes through to handler."""
        middleware = _make_middleware()
        handler = AsyncMock()
        event = _make_message_event(thread_id=None)
        data: dict = {}

        await middleware(handler, event, data)

        handler.assert_awaited_once()
