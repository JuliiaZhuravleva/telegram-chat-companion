"""Tests for src.bot.middleware.message_saver — MessageSaverMiddleware.

Scope: the quote-extraction logic added for Q-3 (persist `message.quote` ->
`quote_text`/`quote_is_manual` via `MessageRepository.save()`). The rest of
`_save_message`'s field mapping predates this item and isn't re-covered here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from src.bot.middleware.message_saver import MessageSaverMiddleware


def _make_event(*, quote: MagicMock | None, chat_id: int = -1001234567890) -> MagicMock:
    """Mock aiogram Message with every field `_save_message` reads set explicitly.

    `spec=Message` still returns a generic (truthy) MagicMock for unset
    attributes, so sticker/voice/video_note/photo/quote must all be pinned
    to a concrete value -- an unset `message.sticker` would be truthy and
    misclassify the message type.
    """
    event = MagicMock(spec=Message)
    event.chat = MagicMock()
    event.chat.id = chat_id
    event.message_id = 42
    event.from_user = MagicMock()
    event.from_user.id = 555
    event.from_user.username = "alice"
    event.from_user.first_name = "Alice"
    event.from_user.is_bot = False
    event.text = "reply text"
    event.caption = None
    event.reply_to_message = None
    event.sticker = None
    event.voice = None
    event.video_note = None
    event.photo = None
    event.quote = quote
    return event


def _quote(text: str = "highlighted fragment", is_manual: bool | None = True) -> MagicMock:
    quote = MagicMock()
    quote.text = text
    quote.is_manual = is_manual
    return quote


def _make_data() -> tuple[dict, AsyncMock]:
    msg_repo = AsyncMock()
    container = AsyncMock()
    container.get.return_value = msg_repo
    return {"dishka_container": container, "chat_config": None}, msg_repo


class TestMessageSaverQuotePersistence:
    """`message.quote` -> `msg_repo.save(quote_text=..., quote_is_manual=...)`."""

    @pytest.mark.asyncio
    async def test_manual_quote_is_persisted(self):
        event = _make_event(quote=_quote(text="highlighted fragment", is_manual=True))
        data, msg_repo = _make_data()

        await MessageSaverMiddleware._save_message(event, data)

        msg_repo.save.assert_awaited_once()
        kwargs = msg_repo.save.call_args.kwargs
        assert kwargs["quote_text"] == "highlighted fragment"
        assert kwargs["quote_is_manual"] is True

    @pytest.mark.asyncio
    async def test_no_quote_persists_none_for_both_fields(self):
        event = _make_event(quote=None)
        data, msg_repo = _make_data()

        await MessageSaverMiddleware._save_message(event, data)

        kwargs = msg_repo.save.call_args.kwargs
        assert kwargs["quote_text"] is None
        assert kwargs["quote_is_manual"] is None

    @pytest.mark.asyncio
    async def test_server_attached_quote_normalizes_is_manual_to_false(self):
        """`is_manual=None` (server-attached quote) -> a concrete `False`, not None.

        Must not collapse into the same NULL as "no quote at all" -- Q-5's
        consumer needs to tell the two apart.
        """
        event = _make_event(quote=_quote(text="server quote", is_manual=None))
        data, msg_repo = _make_data()

        await MessageSaverMiddleware._save_message(event, data)

        kwargs = msg_repo.save.call_args.kwargs
        assert kwargs["quote_text"] == "server quote"
        assert kwargs["quote_is_manual"] is False

    @pytest.mark.asyncio
    async def test_quote_text_persisted_untruncated(self):
        """Persistence stores the raw quote; the 300-char cap is a prompt-render concern (Q-1)."""
        long_text = "y" * 900
        event = _make_event(quote=_quote(text=long_text, is_manual=True))
        data, msg_repo = _make_data()

        await MessageSaverMiddleware._save_message(event, data)

        kwargs = msg_repo.save.call_args.kwargs
        assert kwargs["quote_text"] == long_text


class TestMediaEditsProduceNoContent:
    """The producing half of the transcript-wipe defect.

    `_save_message` maps content from `text or caption`, so for a voice note,
    a video note or an uncaptioned photo it hands `save()` a None -- on the
    `dp.message` delivery, where the row does not exist yet and None is
    correct, and identically on the `dp.edited_message` delivery, where the
    row now holds a transcript the bot wrote. The middleware cannot tell those
    apart and is not asked to: the repository's ON CONFLICT branch is what
    refuses to write the None over existing text (see
    `MessageRepository.save`, and the round-trip in
    tests/integration/test_migration_028_transcription_link.py).

    Pinned here so the two halves of the fix cannot drift: if this ever stops
    being None, the integration test above stops testing the real defect and
    would keep passing.
    """

    @pytest.mark.parametrize("kind", ["voice", "video_note", "photo"])
    async def test_a_media_message_binds_content_none(self, kind: str):
        event = _make_event(quote=None)
        event.text = None
        event.caption = None
        setattr(event, kind, [MagicMock()] if kind == "photo" else MagicMock())
        data, msg_repo = _make_data()

        await MessageSaverMiddleware._save_message(event, data)

        kwargs = msg_repo.save.call_args.kwargs
        assert kwargs["content"] is None
        assert kwargs["message_type"] == kind

    async def test_a_captioned_photo_still_binds_the_caption(self):
        """Control: the None is a property of "no caption", not of "is media"."""
        event = _make_event(quote=None)
        event.text = None
        event.caption = "подпись"
        event.photo = [MagicMock()]
        data, msg_repo = _make_data()

        await MessageSaverMiddleware._save_message(event, data)

        assert msg_repo.save.call_args.kwargs["content"] == "подпись"
