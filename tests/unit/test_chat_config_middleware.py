"""Tests for src.bot.middleware.chat_config — ChatConfigMiddleware."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.middleware.chat_config import ChatConfigMiddleware
from src.models.chat_config import ChatConfig


def _make_middleware():
    """Create middleware (no constructor args — uses Dishka container)."""
    return ChatConfigMiddleware()


def _make_event(chat_id=123):
    """Create a mock Message event."""
    from aiogram.types import Message

    event = MagicMock(spec=Message)
    event.chat = MagicMock()
    event.chat.id = chat_id
    return event


def _make_data(config=None):
    """Create handler data dict with a mock Dishka container."""
    config_service = AsyncMock()
    if config is not None:
        config_service.get_config.return_value = config

    container = AsyncMock()
    container.get.return_value = config_service

    return {"dishka_container": container}, config_service


class TestChatConfigMiddleware:
    """Test ChatConfigMiddleware injects config via Dishka container."""

    @pytest.mark.asyncio
    async def test_injects_chat_config_into_data(self):
        config = ChatConfig(chat_id=123, enabled=True)
        middleware = _make_middleware()
        handler = AsyncMock()
        event = _make_event(chat_id=123)
        data, _ = _make_data(config)

        await middleware(handler, event, data)

        assert data["chat_config"] is config
        handler.assert_awaited_once_with(event, data)

    @pytest.mark.asyncio
    async def test_does_not_gate_on_enabled(self):
        """Enabled gating is now in AccessControlMiddleware — middleware always passes through."""
        config = ChatConfig(chat_id=123, enabled=False)
        middleware = _make_middleware()
        handler = AsyncMock()
        event = _make_event(chat_id=123)
        data, _ = _make_data(config)

        await middleware(handler, event, data)

        # Handler IS called even for disabled chats
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_through_non_message_events(self):
        middleware = _make_middleware()
        handler = AsyncMock()
        # Non-Message event (e.g. CallbackQuery)
        event = MagicMock()
        event.__class__ = type("CallbackQuery", (), {})
        data: dict = {"dishka_container": AsyncMock()}

        await middleware(handler, event, data)

        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_through_message_without_chat(self):
        middleware = _make_middleware()
        handler = AsyncMock()
        from aiogram.types import Message

        event = MagicMock(spec=Message)
        event.chat = None
        data: dict = {"dishka_container": AsyncMock()}

        await middleware(handler, event, data)

        handler.assert_awaited_once()
