"""Tests for src/bot/utils.py's shared bot-layer helpers.

`is_bot_chat_admin` (R-D1, ADR-0004 Decision 5) and `check_admin_direct`, which
replaced three byte-identical private copies across the admin sub-routers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ChatMemberStatus

from src.bot.utils import check_admin_direct, is_bot_chat_admin

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


class TestCheckAdminDirect:
    """Extracted from admin_kb / admin_sticker / admin_reactions, where the
    same body was copied three times across 17 call sites."""

    @staticmethod
    def _repo(admin_ids) -> MagicMock:
        repo = MagicMock()
        repo.get = AsyncMock(return_value=admin_ids)
        return repo

    @pytest.mark.asyncio
    async def test_admin_id_is_accepted(self) -> None:
        assert await check_admin_direct(self._repo([7, 8]), 7) is True

    @pytest.mark.asyncio
    async def test_non_admin_is_rejected(self) -> None:
        assert await check_admin_direct(self._repo([7, 8]), 9) is False

    @pytest.mark.asyncio
    async def test_missing_user_id_is_rejected_without_a_lookup(self) -> None:
        """A callback with no from_user must fail closed, and must not spend a
        DB round-trip deciding that."""
        repo = self._repo([7])
        assert await check_admin_direct(repo, None) is False
        repo.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_comma_separated_string_form_is_parsed(self) -> None:
        """bot_config values arrive as either a JSON array or a comma-separated
        string; parse_admin_ids handles both and this must not regress."""
        assert await check_admin_direct(self._repo("7,8"), 8) is True

    @pytest.mark.asyncio
    async def test_unset_admin_ids_rejects_everyone(self) -> None:
        assert await check_admin_direct(self._repo(None), 7) is False
