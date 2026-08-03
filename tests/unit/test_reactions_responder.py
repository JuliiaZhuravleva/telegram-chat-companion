"""Tests for src.services.modules.reactions.responder — set_reaction (R-5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
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
    async def test_other_exceptions_are_not_swallowed(self) -> None:
        """Only TelegramBadRequest is this function's concern; anything else
        propagates so the caller's own safety net (if any) decides."""
        bot = MagicMock()
        bot.set_message_reaction = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await set_reaction(bot, chat_id=-100, message_id=42, emoji="🔥")
