"""Tests for src/bot/utils.py's shared bot-layer helpers.

`is_bot_chat_admin` (R-D1, ADR-0004 Decision 5) and `check_admin_direct`, which
replaced three byte-identical private copies across the admin sub-routers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.client.default import Default
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest

from src.bot.utils import (
    check_admin_direct,
    is_bot_chat_admin,
    safe_edit_reply_markup,
    safe_edit_text,
)

# Distinguishes "passed None" from "not passed at all" — the whole point here.
_ABSENT = object()

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


class TestSafeEditTextParseMode:
    """`parse_mode=None` must disable HTML, not silently re-enable it.

    The three states are distinct and the middle one used to be unreachable:

    * omitted           → inherit the bot-wide default (HTML). Nine call sites
                          rely on this and their text contains real markup, so
                          this is the state that must not change.
    * `parse_mode=None` → genuinely no parse mode. Previously indistinguishable
                          from "omitted", because `None` *was* the default and the
                          helper then dropped the argument — so a caller asking
                          for plain text got HTML, which is this project's
                          documented escaping trap one step removed.
    * explicit mode     → that mode.

    Asserted on what reaches `edit_text`, because that is where the substitution
    happened; a test on the helper's return value could not see it.
    """

    @pytest.mark.asyncio
    async def test_explicit_none_disables_parsing(self) -> None:
        message = MagicMock()
        message.edit_text = AsyncMock()

        await safe_edit_text(message, "a < b & c", parse_mode=None)

        passed = message.edit_text.await_args.kwargs.get("parse_mode", _ABSENT)
        assert passed is None, (
            "parse_mode must be passed through as None; "
            f"got {passed!r} (absent means the helper dropped the argument, "
            "so aiogram substitutes the bot default and HTML is re-enabled)"
        )

    @pytest.mark.asyncio
    async def test_omitted_inherits_the_bot_default(self) -> None:
        message = MagicMock()
        message.edit_text = AsyncMock()

        await safe_edit_text(message, "<b>bold</b>")

        passed = message.edit_text.await_args.kwargs.get("parse_mode", _ABSENT)
        assert isinstance(passed, Default), (
            f"expected aiogram's Default sentinel so the bot-wide HTML applies, got {passed!r}"
        )

    @pytest.mark.asyncio
    async def test_explicit_mode_is_passed_through(self) -> None:
        message = MagicMock()
        message.edit_text = AsyncMock()

        await safe_edit_text(message, "<b>bold</b>", parse_mode="HTML")

        assert message.edit_text.await_args.kwargs["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_not_modified_is_still_suppressed(self) -> None:
        """The helper's original reason for existing must survive the change."""
        message = MagicMock()
        message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(
                method=MagicMock(), message="message is not modified: nothing changed"
            )
        )

        await safe_edit_text(message, "same", parse_mode=None)  # must not raise

    @pytest.mark.asyncio
    async def test_other_bad_requests_still_propagate(self) -> None:
        message = MagicMock()
        message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="message can't be edited")
        )

        with pytest.raises(TelegramBadRequest):
            await safe_edit_text(message, "x", parse_mode=None)


class TestSafeEditTextLinkPreview:
    """The link-preview parameter follows the same three-state rule as parse_mode.

    It exists because three real call sites pass `disable_web_page_preview=True`
    and could not have been converted otherwise (TD-048).
    """

    @pytest.mark.asyncio
    async def test_omitted_inherits_the_bot_default(self) -> None:
        """Not `None`. Defaulting to None would silently flip link-preview
        behaviour for every caller that never asked about it — the same trap
        the parse_mode note records, one parameter over."""
        message = MagicMock()
        message.edit_text = AsyncMock()

        await safe_edit_text(message, "https://example.com")

        passed = message.edit_text.await_args.kwargs.get("disable_web_page_preview", _ABSENT)
        assert isinstance(passed, Default), f"expected aiogram's Default sentinel, got {passed!r}"
        assert passed.name == "link_preview_is_disabled", (
            "aiogram resolves a Default BY NAME (bot.default[value.name]), so the "
            f"wrong name silently resolves to the wrong setting; got {passed.name!r}"
        )

    @pytest.mark.asyncio
    async def test_explicit_true_is_passed_through(self) -> None:
        message = MagicMock()
        message.edit_text = AsyncMock()

        await safe_edit_text(message, "https://example.com", disable_web_page_preview=True)

        assert message.edit_text.await_args.kwargs["disable_web_page_preview"] is True


class TestSafeEditReplyMarkup:
    """Its own Bot API method, and the one a double-tapped approve/reject hits."""

    @pytest.mark.asyncio
    async def test_not_modified_is_suppressed(self) -> None:
        message = MagicMock()
        message.edit_reply_markup = AsyncMock(
            side_effect=TelegramBadRequest(
                method=MagicMock(), message="message is not modified: nothing changed"
            )
        )

        await safe_edit_reply_markup(message, reply_markup=None)  # must not raise

    @pytest.mark.asyncio
    async def test_other_bad_requests_still_propagate(self) -> None:
        """`method=` is a REQUIRED positional on TelegramBadRequest.

        Omit it and the test dies with TypeError in its own body — red either
        way, which makes the control unfalsifiable rather than passing.
        """
        message = MagicMock()
        message.edit_reply_markup = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="message can't be edited")
        )

        with pytest.raises(TelegramBadRequest):
            await safe_edit_reply_markup(message, reply_markup=None)

    @pytest.mark.asyncio
    async def test_markup_is_passed_by_keyword(self) -> None:
        """The first positional of Message.edit_reply_markup is
        `inline_message_id`, not the markup — passing it positionally would
        send the keyboard as an inline message id."""
        message = MagicMock()
        message.edit_reply_markup = AsyncMock()
        markup = MagicMock()

        await safe_edit_reply_markup(message, reply_markup=markup)

        assert message.edit_reply_markup.await_args.args == ()
        assert message.edit_reply_markup.await_args.kwargs["reply_markup"] is markup
