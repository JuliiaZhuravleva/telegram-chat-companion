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
    msg.chat.title = "Test Chat"
    msg.chat.full_name = "Test Chat"
    msg.chat.username = None
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.first_name = "Tester"
    msg.from_user.last_name = None
    msg.from_user.username = None
    msg.text = "hello"
    msg.caption = None
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


def _make_data_with_services(
    *,
    chat_config: ChatConfig,
    admin_ids_str: str = "",
    has_rejected: bool = False,
    notifier: AsyncMock | None = None,
    admin_repo: AsyncMock | None = None,
) -> dict:
    """Container mock that dispatches container.get(Type) by class.

    Returns a data dict wired up for _notify_unauthorized code path.
    """
    from src.database.repositories.admin import AdminRepository
    from src.database.repositories.bot_config import BotConfigRepository
    from src.services.abuse.notifications import AbuseNotificationService

    bot_config_repo = AsyncMock()
    bot_config_repo.get.return_value = admin_ids_str

    if admin_repo is None:
        admin_repo = AsyncMock()
    admin_repo.has_rejected_attempt = AsyncMock(return_value=has_rejected)
    admin_repo.log_unauthorized = AsyncMock(return_value=123)
    admin_repo.get_admin_language = AsyncMock(return_value="ru")

    if notifier is None:
        notifier = AsyncMock()

    mapping = {
        BotConfigRepository: bot_config_repo,
        AdminRepository: admin_repo,
        AbuseNotificationService: notifier,
    }

    container = AsyncMock()
    container.get.side_effect = lambda cls: mapping[cls]

    return {
        "dishka_container": container,
        "chat_config": chat_config,
        "bot": AsyncMock(),
        "_notifier": notifier,
        "_admin_repo": admin_repo,
    }


class TestBlacklistSuppression:
    async def test_skips_notification_when_chat_has_rejected_attempt(
        self,
        middleware,
        make_chat_config,
    ):
        """Chat with a prior rejection → no new notification, no new DB log."""
        config = make_chat_config(enabled=False)
        handler = AsyncMock()
        msg = _make_message(chat_id=-100, user_id=42, chat_type="supergroup")
        data = _make_data_with_services(
            chat_config=config,
            admin_ids_str="999",
            has_rejected=True,
        )
        notifier = data["_notifier"]
        admin_repo = data["_admin_repo"]

        await middleware(handler, msg, data)

        handler.assert_not_called()
        admin_repo.has_rejected_attempt.assert_awaited_once_with(-100)
        # Blacklist short-circuit: no DB log, no notification
        admin_repo.log_unauthorized.assert_not_awaited()
        notifier.notify_unauthorized.assert_not_awaited()

    async def test_notifies_when_chat_has_no_prior_rejection(
        self,
        middleware,
        make_chat_config,
    ):
        """Fresh chat (no prior reject) → normal log + notify path."""
        config = make_chat_config(enabled=False)
        handler = AsyncMock()
        msg = _make_message(chat_id=-100, user_id=42, chat_type="supergroup")
        data = _make_data_with_services(
            chat_config=config,
            admin_ids_str="999",
            has_rejected=False,
        )
        notifier = data["_notifier"]
        admin_repo = data["_admin_repo"]

        await middleware(handler, msg, data)

        admin_repo.has_rejected_attempt.assert_awaited_once_with(-100)
        admin_repo.log_unauthorized.assert_awaited_once()
        notifier.notify_unauthorized.assert_awaited_once()
