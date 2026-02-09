"""Tests for the RulesMiddleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message

from src.bot.middleware.rules import RulesMiddleware
from src.models.chat_config import ChatConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    chat_id: int = -1001,
    user_id: int = 100,
    is_message: bool = True,
) -> MagicMock:
    if not is_message:
        return MagicMock()  # Not a Message instance

    event = MagicMock(spec=Message)
    event.chat = MagicMock()
    event.chat.id = chat_id
    event.from_user = MagicMock()
    event.from_user.id = user_id
    return event


def _make_data(
    rules_enabled: bool = True,
    chat_id: int = -1001,
) -> dict:
    chat_config = ChatConfig(chat_id=chat_id, enabled=True, rules_enabled=rules_enabled)
    container = AsyncMock()
    return {
        "chat_config": chat_config,
        "dishka_container": container,
        "bot": AsyncMock(),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRulesMiddleware:
    @pytest.mark.asyncio
    async def test_skips_non_message_events(self) -> None:
        mw = RulesMiddleware()
        handler = AsyncMock(return_value="ok")
        event = MagicMock()  # Not a Message

        result = await mw(handler, event, {})
        assert result == "ok"
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_rules_disabled(self) -> None:
        mw = RulesMiddleware()
        handler = AsyncMock(return_value="ok")
        event = _make_event()
        data = _make_data(rules_enabled=False)

        result = await mw(handler, event, data)
        assert result == "ok"
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_no_chat_config(self) -> None:
        mw = RulesMiddleware()
        handler = AsyncMock(return_value="ok")
        event = _make_event()

        result = await mw(handler, event, {})
        assert result == "ok"
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_no_user(self) -> None:
        mw = RulesMiddleware()
        handler = AsyncMock(return_value="ok")
        event = _make_event()
        event.from_user = None
        data = _make_data(rules_enabled=True)

        result = await mw(handler, event, data)
        assert result == "ok"
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluates_and_executes_rules(self) -> None:
        mw = RulesMiddleware()
        handler = AsyncMock(return_value="ok")
        event = _make_event()
        data = _make_data(rules_enabled=True)

        mock_engine = AsyncMock()
        mock_executor = AsyncMock()
        mock_action = MagicMock()
        mock_engine.evaluate = AsyncMock(return_value=[mock_action])

        container = data["dishka_container"]
        container.get = AsyncMock(side_effect=lambda cls: {
            type(mock_engine): mock_engine,
            type(mock_executor): mock_executor,
        }.get(cls, MagicMock()))

        # Patch to return our mocks from dishka
        from src.services.rules.engine import RuleEngine
        from src.services.rules.executor import RuleActionExecutor

        async def get_side_effect(cls):
            if cls is RuleEngine:
                return mock_engine
            if cls is RuleActionExecutor:
                return mock_executor
            return MagicMock()

        container.get = AsyncMock(side_effect=get_side_effect)

        result = await mw(handler, event, data)

        assert result == "ok"
        handler.assert_called_once()
        mock_engine.evaluate.assert_called_once()
        mock_executor.execute.assert_called_once_with(
            mock_action, message=event, bot=data["bot"]
        )

    @pytest.mark.asyncio
    async def test_always_calls_handler_on_error(self) -> None:
        mw = RulesMiddleware()
        handler = AsyncMock(return_value="ok")
        event = _make_event()
        data = _make_data(rules_enabled=True)

        container = data["dishka_container"]
        container.get = AsyncMock(side_effect=RuntimeError("DI broken"))

        result = await mw(handler, event, data)

        assert result == "ok"
        handler.assert_called_once()
