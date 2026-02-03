"""Tests for src.bot.middleware.chat_config — ChatConfigMiddleware."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.middleware.chat_config import ChatConfigMiddleware
from src.models.chat_config import ChatConfig


def _make_middleware(config=None):
    """Create middleware with a mocked config service."""
    config_service = AsyncMock()
    if config is not None:
        config_service.get_config.return_value = config
    return ChatConfigMiddleware(config_service), config_service


def _make_event(chat_id=123):
    """Create a mock Message event."""
    from aiogram.types import Message

    event = MagicMock(spec=Message)
    event.chat = MagicMock()
    event.chat.id = chat_id
    return event


class TestChatConfigMiddleware:
    """Test ChatConfigMiddleware injects config and gates on enabled."""

    @pytest.mark.asyncio
    async def test_injects_chat_config_into_data(self):
        config = ChatConfig(chat_id=123, enabled=True)
        middleware, service = _make_middleware(config)
        handler = AsyncMock()
        event = _make_event(chat_id=123)
        data: dict = {}

        await middleware(handler, event, data)

        assert data["chat_config"] is config
        handler.assert_awaited_once_with(event, data)

    @pytest.mark.asyncio
    async def test_blocks_disabled_chats(self):
        config = ChatConfig(chat_id=123, enabled=False)
        middleware, service = _make_middleware(config)
        handler = AsyncMock()
        event = _make_event(chat_id=123)

        result = await middleware(handler, event, {})

        assert result is None
        handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passes_through_non_message_events(self):
        middleware, service = _make_middleware()
        handler = AsyncMock()
        # Non-Message event (e.g. CallbackQuery)
        event = MagicMock()
        event.__class__ = type("CallbackQuery", (), {})
        data: dict = {}

        await middleware(handler, event, data)

        handler.assert_awaited_once()
        service.get_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passes_through_message_without_chat(self):
        middleware, service = _make_middleware()
        handler = AsyncMock()
        from aiogram.types import Message

        event = MagicMock(spec=Message)
        event.chat = None
        data: dict = {}

        await middleware(handler, event, data)

        handler.assert_awaited_once()
        service.get_config.assert_not_awaited()
