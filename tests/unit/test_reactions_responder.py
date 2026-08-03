"""Tests for src.services.modules.reactions.responder — set_reaction (R-5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import ReactionTypeEmoji

from src.services.modules.reactions.responder import set_reaction


class TestSetReaction:
    @pytest.mark.asyncio
    async def test_calls_bot_set_message_reaction_with_single_emoji(self) -> None:
        bot = MagicMock()
        bot.set_message_reaction = AsyncMock(return_value=True)

        result = await set_reaction(bot, chat_id=-100, message_id=42, emoji="🔥")

        assert result is True
        bot.set_message_reaction.assert_awaited_once()
        call_kwargs = bot.set_message_reaction.call_args.kwargs
        assert call_kwargs["chat_id"] == -100
        assert call_kwargs["message_id"] == 42
        assert len(call_kwargs["reaction"]) == 1
        reaction = call_kwargs["reaction"][0]
        assert isinstance(reaction, ReactionTypeEmoji)
        assert reaction.emoji == "🔥"

    @pytest.mark.asyncio
    async def test_telegram_bad_request_is_swallowed(self) -> None:
        """Restricted available_reactions / non-reactable service message ->
        logged warning, not a crash into the caller (ADR-0004 Decision 4)."""
        bot = MagicMock()
        bot.set_message_reaction = AsyncMock(
            side_effect=TelegramBadRequest(
                method=MagicMock(), message="Bad Request: REACTION_INVALID"
            )
        )

        result = await set_reaction(bot, chat_id=-100, message_id=42, emoji="🔥")

        assert result is False

    @pytest.mark.asyncio
    async def test_forbidden_is_swallowed(self) -> None:
        """The bot being kicked/blocked is exactly what R-D1 exists to surface,
        so it must degrade to False rather than escape.

        Contract change (was: only TelegramBadRequest caught). These classes are
        siblings under TelegramAPIError, not subclasses of TelegramBadRequest,
        so the narrower catch let them through while the docstring promised
        "never raises into the caller".
        """
        bot = MagicMock()
        bot.set_message_reaction = AsyncMock(
            side_effect=TelegramForbiddenError(
                method=MagicMock(), message="Forbidden: bot was kicked"
            )
        )

        assert await set_reaction(bot, chat_id=-100, message_id=42, emoji="🔥") is False

    @pytest.mark.asyncio
    async def test_retry_after_is_swallowed(self) -> None:
        bot = MagicMock()
        bot.set_message_reaction = AsyncMock(
            side_effect=TelegramRetryAfter(
                method=MagicMock(), message="Too Many Requests", retry_after=5
            )
        )

        assert await set_reaction(bot, chat_id=-100, message_id=42, emoji="🔥") is False

    @pytest.mark.asyncio
    async def test_non_telegram_exceptions_are_not_swallowed(self) -> None:
        """A programming error is not an API failure -- it must stay visible
        rather than be reported as a benign `False`."""
        bot = MagicMock()
        bot.set_message_reaction = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await set_reaction(bot, chat_id=-100, message_id=42, emoji="🔥")
