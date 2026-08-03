"""Tests for src/bot/utils.py's is_bot_chat_admin (R-D1, ADR-0004 Decision 5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ChatMemberStatus

from src.bot.utils import is_bot_chat_admin

CHAT_ID = -1001234567890
BOT_ID = 999


def _make_bot(status: ChatMemberStatus | None, *, raises: Exception | None = None) -> MagicMock:
    bot = MagicMock()
    if raises is not None:
        bot.get_chat_member = AsyncMock(side_effect=raises)
    else:
        member = MagicMock()
        member.status = status
        bot.get_chat_member = AsyncMock(return_value=member)
    return bot


class TestIsBotChatAdmin:
    @pytest.mark.asyncio
    async def test_true_when_administrator(self) -> None:
        bot = _make_bot(ChatMemberStatus.ADMINISTRATOR)
        assert await is_bot_chat_admin(bot, CHAT_ID, BOT_ID) is True
        bot.get_chat_member.assert_awaited_once_with(CHAT_ID, BOT_ID)

    @pytest.mark.asyncio
    async def test_true_when_creator(self) -> None:
        bot = _make_bot(ChatMemberStatus.CREATOR)
        assert await is_bot_chat_admin(bot, CHAT_ID, BOT_ID) is True

    @pytest.mark.asyncio
    async def test_false_when_plain_member(self) -> None:
        bot = _make_bot(ChatMemberStatus.MEMBER)
        assert await is_bot_chat_admin(bot, CHAT_ID, BOT_ID) is False

    @pytest.mark.asyncio
    async def test_false_when_left(self) -> None:
        bot = _make_bot(ChatMemberStatus.LEFT)
        assert await is_bot_chat_admin(bot, CHAT_ID, BOT_ID) is False

    @pytest.mark.asyncio
    async def test_degrades_to_false_on_api_error(self) -> None:
        """A failed Bot API call (e.g. bot not in chat) must not crash -- it's
        a diagnostic, not a hot-path call, and must fail closed."""
        bot = _make_bot(None, raises=RuntimeError("boom"))
        assert await is_bot_chat_admin(bot, CHAT_ID, BOT_ID) is False
