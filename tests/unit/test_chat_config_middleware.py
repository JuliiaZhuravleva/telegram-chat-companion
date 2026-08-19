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

    @pytest.mark.asyncio
    async def test_injects_chat_config_for_message_reaction_event(self):
        """R-1: message_reaction handler needs chat_config (reactions_enabled /
        reactions_history_enabled) too."""
        from aiogram.types import MessageReactionUpdated

        config = ChatConfig(chat_id=-1001, reactions_enabled=True)
        middleware = _make_middleware()
        handler = AsyncMock()
        event = MagicMock(spec=MessageReactionUpdated)
        event.chat = MagicMock()
        event.chat.id = -1001
        data, _ = _make_data(config)

        await middleware(handler, event, data)

        assert data["chat_config"] is config
        handler.assert_awaited_once_with(event, data)


class TestExtractChatInfo:
    """TD-102: is_forum must come out of the event as a definite bool.

    Telegram omits the field for non-forum chats, so on an event carrying a
    real Chat object None *means* "not a forum" — passing it through as None
    would let a chat that turned forum mode off keep a stale True in the DB
    forever (ensure_exists COALESCEs None away).
    """

    def _message_event(self, is_forum):
        from aiogram.types import Message

        event = MagicMock(spec=Message)
        event.chat = MagicMock()
        event.chat.title = "Test Chat"
        event.chat.type = "supergroup"
        event.chat.is_forum = is_forum
        return event

    def test_forum_chat_yields_true(self):
        from src.bot.middleware.chat_config import _extract_chat_info

        title, chat_type, is_forum = _extract_chat_info(self._message_event(True))
        assert (title, chat_type, is_forum) == ("Test Chat", "supergroup", True)

    def test_omitted_field_yields_false_not_none(self):
        from src.bot.middleware.chat_config import _extract_chat_info

        _, _, is_forum = _extract_chat_info(self._message_event(None))
        assert is_forum is False

    def test_chatless_event_yields_none(self):
        from src.bot.middleware.chat_config import _extract_chat_info

        _, _, is_forum = _extract_chat_info(MagicMock(spec=[]))
        assert is_forum is None

    @pytest.mark.asyncio
    async def test_cache_miss_writes_is_forum(self):
        """The middleware must hand is_forum to ensure_exists — the column is
        written opportunistically, there is no other writer."""
        from src.database.repositories.chat_settings import ChatSettingsRepository
        from src.services.chat_config import ChatConfigService

        middleware = _make_middleware()
        handler = AsyncMock()
        event = self._message_event(True)
        event.chat.id = -1005

        config_service = AsyncMock()
        # Keyword-faithful stub (accepts the call however the middleware
        # spells it) — a signature mismatch here would raise inside code that
        # logs-and-continues, and the miss would render as a pass.
        config_service.is_cached = lambda *args, **kwargs: False  # noqa: ARG005
        config_service.get_config.return_value = ChatConfig(chat_id=-1005)
        repo = AsyncMock()

        deps = {ChatConfigService: config_service, ChatSettingsRepository: repo}

        async def container_get(dep):
            return deps[dep]

        container = MagicMock()
        container.get = container_get
        data = {"dishka_container": container}

        await middleware(handler, event, data)

        repo.ensure_exists.assert_awaited_once_with(-1005, "Test Chat", "supergroup", True)
