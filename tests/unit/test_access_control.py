"""Tests for AccessControlMiddleware."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.middleware.access_control import AccessControlMiddleware
from src.models.chat_config import ChatConfig


@pytest.fixture
def middleware():
    return AccessControlMiddleware()


def _make_message(chat_id: int = -100123, user_id: int = 42, chat_type: str = "group"):
    """Create a mock that passes isinstance(event, Message) check."""
    from aiogram.types import Message

    msg = MagicMock(spec=Message)
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    return msg


def _make_data(chat_config: ChatConfig | None = None, admin_ids_str: str = ""):
    """Create mock handler data with Dishka container."""
    bot_config_repo = AsyncMock()
    bot_config_repo.get.return_value = admin_ids_str

    container = AsyncMock()
    container.get.return_value = bot_config_repo

    data: dict = {"dishka_container": container}
    if chat_config is not None:
        data["chat_config"] = chat_config

    return data


class TestAccessControl:
    async def test_passes_enabled_chat(self, middleware, make_chat_config):
        config = make_chat_config(enabled=True)
        handler = AsyncMock()
        msg = _make_message()
        data = _make_data(config)

        await middleware(handler, msg, data)

        handler.assert_called_once()
        assert data["is_admin"] is False

    async def test_blocks_disabled_chat(self, middleware, make_chat_config):
        config = make_chat_config(enabled=False)
        handler = AsyncMock()
        msg = _make_message()
        data = _make_data(config)

        result = await middleware(handler, msg, data)

        handler.assert_not_called()
        assert result is None

    async def test_admin_dm_bypasses_disabled(self, middleware, make_chat_config):
        config = make_chat_config(enabled=False)
        handler = AsyncMock()
        msg = _make_message(user_id=999, chat_type="private")
        data = _make_data(config, admin_ids_str="999")

        await middleware(handler, msg, data)

        handler.assert_called_once()
        assert data["is_admin"] is True

    async def test_admin_detected_from_config(self, middleware, make_chat_config):
        config = make_chat_config(enabled=True)
        handler = AsyncMock()
        msg = _make_message(user_id=42)
        data = _make_data(config, admin_ids_str="42, 100, 200")

        await middleware(handler, msg, data)

        assert data["is_admin"] is True

    async def test_non_admin_user(self, middleware, make_chat_config):
        config = make_chat_config(enabled=True)
        handler = AsyncMock()
        msg = _make_message(user_id=42)
        data = _make_data(config, admin_ids_str="100, 200")

        await middleware(handler, msg, data)

        assert data["is_admin"] is False

    async def test_no_admin_ids_configured(self, middleware, make_chat_config):
        config = make_chat_config(enabled=True)
        handler = AsyncMock()
        msg = _make_message(user_id=42)
        data = _make_data(config, admin_ids_str="")

        await middleware(handler, msg, data)

        assert data["is_admin"] is False

    async def test_non_message_events_pass_through(self, middleware):
        handler = AsyncMock()
        event = MagicMock()  # Not a Message
        event.__class__ = type("CallbackQuery", (), {})
        data: dict = {}

        await middleware(handler, event, data)

        handler.assert_called_once()

    async def test_no_chat_config_passes_through(self, middleware):
        handler = AsyncMock()
        msg = _make_message()
        data = _make_data(chat_config=None)

        await middleware(handler, msg, data)

        handler.assert_called_once()

    async def test_disabled_group_chat_blocked_even_for_admin(self, middleware, make_chat_config):
        """Admin in a group chat should NOT bypass the enabled check."""
        config = make_chat_config(enabled=False)
        handler = AsyncMock()
        msg = _make_message(user_id=999, chat_type="supergroup")
        data = _make_data(config, admin_ids_str="999")

        result = await middleware(handler, msg, data)

        handler.assert_not_called()
        assert result is None
