"""Once part of an answer is on screen, a later failure must degrade, not raise.

`TelegramRetryAfter` is a *sibling* of `TelegramBadRequest`, not a subclass, so
`except TelegramBadRequest` steps straight past it. A throttled part two
therefore unwound the whole handler with part one already visible in the chat —
and because the caller's post-send bookkeeping never ran, a bot transcription
sat in the chat with no migration-028 routing row behind it. Per CLAUDE.md,
"without it the bot answers every such reply": a reply to those words is then
answered as if the bot itself had spoken them.

Raising bought nothing. The global error handler answers only CallbackQuery, so
the user sees the same silence either way; the only difference is whether the
bookkeeping ran.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from src.bot.reply_flow import send_html_parts


def _message() -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock(id=-100123)
    msg.message_id = 42
    return msg


def _throttled() -> TelegramRetryAfter:
    return TelegramRetryAfter(
        method=SendMessage(chat_id=-100123, text="x"),
        message="Too Many Requests: retry after 45",
        retry_after=45,
    )


class TestPartialDelivery:
    @pytest.mark.asyncio
    async def test_a_failure_after_part_one_keeps_what_landed(self) -> None:
        message = _message()
        message.answer = AsyncMock(side_effect=[MagicMock(message_id=43), _throttled()])

        sent = await send_html_parts(
            message=message,
            parts=["part one", "part two", "part three"],
            reply_to_message_id=42,
            language="ru",
        )

        assert [m.message_id for m in sent] == [43], (
            "the caller must be told what actually landed, not handed an exception"
        )

    @pytest.mark.asyncio
    async def test_a_failure_on_the_very_first_part_still_raises(self) -> None:
        """Control: with nothing on screen there is no half-finished state to save,
        and the caller's own error handling is still the right place for it."""
        message = _message()
        message.answer = AsyncMock(side_effect=_throttled())

        with pytest.raises(TelegramRetryAfter):
            await send_html_parts(
                message=message,
                parts=["only part"],
                reply_to_message_id=42,
                language="ru",
            )

    @pytest.mark.asyncio
    async def test_already_delivered_covers_the_progress_placeholder_case(self) -> None:
        """`ProgressNotice.finish` edits part one into the placeholder and passes
        `parts[1:]` here — so a failure on THIS call's first element is still a
        partial delivery. Keying on `index == 0` alone would miss exactly that.
        """
        message = _message()
        message.answer = AsyncMock(side_effect=TelegramNetworkError(method=None, message="x"))

        sent = await send_html_parts(
            message=message,
            parts=["part two", "part three"],
            reply_to_message_id=None,
            language="ru",
            already_delivered=True,
        )

        assert sent == []

    @pytest.mark.asyncio
    async def test_the_whole_answer_still_arrives_when_nothing_fails(self) -> None:
        """Control: the degrade path must not have truncated the happy path."""
        message = _message()
        message.answer = AsyncMock(side_effect=[MagicMock(message_id=i) for i in (43, 44, 45)])

        sent = await send_html_parts(
            message=message,
            parts=["a", "b", "c"],
            reply_to_message_id=42,
            language="ru",
        )

        assert [m.message_id for m in sent] == [43, 44, 45]
        # Only the first quotes the source; the rest read as continuations.
        quotes = [c.kwargs.get("reply_to_message_id") for c in message.answer.await_args_list]
        assert quotes == [42, None, None]
