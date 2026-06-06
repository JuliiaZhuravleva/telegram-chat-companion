"""Tests for src.bot.handlers.commands — DM /summary path."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers.commands import handle_summary_dm

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(chat_type: str = "private", user_id: int = 99) -> MagicMock:
    """Create a minimal mock aiogram Message for command tests."""
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


def _make_chat_config(language: str = "ru") -> MagicMock:
    """Create a minimal mock ChatConfig."""
    cfg = MagicMock()
    cfg.language = language
    return cfg


# ---------------------------------------------------------------------------
# handle_summary_dm
# ---------------------------------------------------------------------------


class TestHandleSummaryDm:
    """DM /summary handler emits the group-only notice."""

    @pytest.mark.asyncio
    async def test_ru_reply_sent(self) -> None:
        """Russian language → Russian notice sent."""
        msg = _make_message()
        cfg = _make_chat_config(language="ru")

        await handle_summary_dm(msg, chat_config=cfg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "/summary" in text
        # Verify it's the Russian variant
        assert "групповых" in text

    @pytest.mark.asyncio
    async def test_en_reply_sent(self) -> None:
        """English language → English notice sent."""
        msg = _make_message()
        cfg = _make_chat_config(language="en")

        await handle_summary_dm(msg, chat_config=cfg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "/summary" in text
        assert "group" in text

    @pytest.mark.asyncio
    async def test_unknown_language_falls_back_to_ru(self) -> None:
        """Unrecognised language code falls back to Russian."""
        msg = _make_message()
        cfg = _make_chat_config(language="de")

        await handle_summary_dm(msg, chat_config=cfg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "групповых" in text

    @pytest.mark.asyncio
    async def test_exactly_one_reply(self) -> None:
        """Handler sends exactly one reply, no more."""
        msg = _make_message()
        cfg = _make_chat_config()

        await handle_summary_dm(msg, chat_config=cfg)

        assert msg.answer.await_count == 1
