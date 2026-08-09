"""Tests for src.bot.handlers.commands — /summary command handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers.commands import (
    _SUMMARY_DEFAULT_COUNT,
    _SUMMARY_MAX_COUNT,
    _SUMMARY_MIN_COUNT,
    handle_summary,
    handle_summary_dm,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(
    chat_type: str = "private",
    user_id: int = 99,
    chat_id: int = -100123,
    text: str | None = "/summary",
) -> MagicMock:
    """Create a minimal mock aiogram Message for command tests."""
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.type = chat_type
    msg.chat.id = chat_id
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.text = text
    msg.answer = AsyncMock()
    msg.reply = AsyncMock()
    return msg


def _make_chat_config(language: str = "ru", save_messages: bool = True) -> MagicMock:
    """Create a minimal mock ChatConfig."""
    cfg = MagicMock()
    cfg.language = language
    cfg.save_messages = save_messages
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


# ---------------------------------------------------------------------------
# handle_summary — E-1: /summary <n> parameter (default, min/max, validation)
# ---------------------------------------------------------------------------


def _make_summary_service(result: str | None = "<b>Summary</b>") -> MagicMock:
    service = MagicMock()
    service.generate = AsyncMock(return_value=result)
    return service


def _make_placeholder() -> MagicMock:
    """The message returned by ``message.answer(processing)``."""
    placeholder = MagicMock()
    placeholder.edit_text = AsyncMock()
    placeholder.delete = AsyncMock()
    return placeholder


class TestHandleSummaryCount:
    """``/summary <n>``: default, min/max bounds, validation, forum passthrough."""

    @pytest.mark.asyncio
    async def test_no_argument_uses_default_count(self) -> None:
        msg = _make_message(chat_type="group", text="/summary")
        msg.answer = AsyncMock(return_value=_make_placeholder())
        cfg = _make_chat_config()
        service = _make_summary_service()

        await handle_summary(msg, chat_config=cfg, summary_service=service)

        service.generate.assert_awaited_once()
        assert service.generate.call_args.kwargs["count"] == _SUMMARY_DEFAULT_COUNT

    @pytest.mark.asyncio
    async def test_explicit_valid_count_is_forwarded(self) -> None:
        msg = _make_message(chat_type="group", text="/summary 500")
        msg.answer = AsyncMock(return_value=_make_placeholder())
        cfg = _make_chat_config()
        service = _make_summary_service()

        await handle_summary(msg, chat_config=cfg, summary_service=service)

        assert service.generate.call_args.kwargs["count"] == 500

    @pytest.mark.asyncio
    async def test_min_boundary_accepted(self) -> None:
        msg = _make_message(chat_type="group", text=f"/summary {_SUMMARY_MIN_COUNT}")
        msg.answer = AsyncMock(return_value=_make_placeholder())
        cfg = _make_chat_config()
        service = _make_summary_service()

        await handle_summary(msg, chat_config=cfg, summary_service=service)

        assert service.generate.call_args.kwargs["count"] == _SUMMARY_MIN_COUNT

    @pytest.mark.asyncio
    async def test_max_boundary_accepted(self) -> None:
        msg = _make_message(chat_type="group", text=f"/summary {_SUMMARY_MAX_COUNT}")
        msg.answer = AsyncMock(return_value=_make_placeholder())
        cfg = _make_chat_config()
        service = _make_summary_service()

        await handle_summary(msg, chat_config=cfg, summary_service=service)

        assert service.generate.call_args.kwargs["count"] == _SUMMARY_MAX_COUNT

    @pytest.mark.asyncio
    async def test_below_min_gets_polite_refusal_not_generation(self) -> None:
        """Owner's exact framing (source PRD, point 6): 'столько можно прочитать и самому'."""
        msg = _make_message(chat_type="group", text="/summary 5")
        cfg = _make_chat_config(language="ru")
        service = _make_summary_service()

        await handle_summary(msg, chat_config=cfg, summary_service=service)

        service.generate.assert_not_awaited()
        msg.answer.assert_not_awaited()
        msg.reply.assert_awaited_once()
        assert "прочитать и самому" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_above_max_gets_validation_error_not_generation(self) -> None:
        msg = _make_message(chat_type="group", text="/summary 1001")
        cfg = _make_chat_config(language="ru")
        service = _make_summary_service()

        await handle_summary(msg, chat_config=cfg, summary_service=service)

        service.generate.assert_not_awaited()
        msg.reply.assert_awaited_once()
        assert str(_SUMMARY_MAX_COUNT) in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_garbage_argument_gets_validation_error_not_generation(self) -> None:
        msg = _make_message(chat_type="group", text="/summary many")
        cfg = _make_chat_config(language="ru")
        service = _make_summary_service()

        await handle_summary(msg, chat_config=cfg, summary_service=service)

        service.generate.assert_not_awaited()
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_negative_argument_gets_validation_error_not_generation(self) -> None:
        """A leading '-' fails the digits-only match — not silently accepted as 'too few'."""
        msg = _make_message(chat_type="group", text="/summary -5")
        cfg = _make_chat_config(language="ru")
        service = _make_summary_service()

        await handle_summary(msg, chat_config=cfg, summary_service=service)

        service.generate.assert_not_awaited()
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_en_validation_messages(self) -> None:
        msg = _make_message(chat_type="group", text="/summary 5")
        cfg = _make_chat_config(language="en")
        service = _make_summary_service()

        await handle_summary(msg, chat_config=cfg, summary_service=service)

        assert "read yourself" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_forum_topic_thread_id_passed_through_with_count(self) -> None:
        """The topic filter (message_thread_id) must survive alongside the new count param."""
        msg = _make_message(chat_type="supergroup", text="/summary 300")
        msg.answer = AsyncMock(return_value=_make_placeholder())
        cfg = _make_chat_config()
        service = _make_summary_service()

        await handle_summary(msg, chat_config=cfg, summary_service=service, message_thread_id=42)

        service.generate.assert_awaited_once()
        assert service.generate.call_args.kwargs["count"] == 300
        assert service.generate.call_args.kwargs["message_thread_id"] == 42

    @pytest.mark.asyncio
    async def test_save_messages_disabled_short_circuits_before_count_parsing(self) -> None:
        """Disabled check wins over a garbage count — no validation reply either."""
        msg = _make_message(chat_type="group", text="/summary garbage")
        cfg = _make_chat_config(save_messages=False)
        service = _make_summary_service()

        await handle_summary(msg, chat_config=cfg, summary_service=service)

        service.generate.assert_not_awaited()
        msg.answer.assert_awaited_once()
        msg.reply.assert_not_awaited()
