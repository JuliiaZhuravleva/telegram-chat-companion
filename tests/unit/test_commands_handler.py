"""Tests for src.bot.handlers.commands — /summary command handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers.commands import (
    _SUMMARY500_COUNT,
    _SUMMARY_DEFAULT_COUNT,
    _SUMMARY_MAX_COUNT,
    _SUMMARY_MIN_COUNT,
    handle_summary,
    handle_summary500,
    handle_summary500_dm,
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


class TestHandleSummary500:
    """``/summary500`` (E-2): the fixed-count shortcut beside the parameter."""

    @pytest.mark.asyncio
    async def test_summarizes_exactly_500(self) -> None:
        msg = _make_message(chat_type="group", text="/summary500")
        msg.answer = AsyncMock(return_value=_make_placeholder())
        cfg = _make_chat_config()
        service = _make_summary_service()

        await handle_summary500(msg, chat_config=cfg, summary_service=service)

        service.generate.assert_awaited_once()
        assert service.generate.call_args.kwargs["count"] == 500
        assert _SUMMARY500_COUNT == 500

    @pytest.mark.asyncio
    async def test_trailing_argument_is_ignored_not_rejected(self) -> None:
        """``/summary500 300`` asks two counts at once — we honour the command."""
        msg = _make_message(chat_type="group", text="/summary500 300")
        msg.answer = AsyncMock(return_value=_make_placeholder())
        cfg = _make_chat_config()
        service = _make_summary_service()

        await handle_summary500(msg, chat_config=cfg, summary_service=service)

        assert service.generate.call_args.kwargs["count"] == 500
        msg.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_forum_topic_filter_is_preserved(self) -> None:
        msg = _make_message(chat_type="supergroup", text="/summary500")
        msg.answer = AsyncMock(return_value=_make_placeholder())
        cfg = _make_chat_config()
        service = _make_summary_service()

        await handle_summary500(msg, chat_config=cfg, summary_service=service, message_thread_id=42)

        assert service.generate.call_args.kwargs["message_thread_id"] == 42

    @pytest.mark.asyncio
    async def test_save_messages_disabled_short_circuits(self) -> None:
        msg = _make_message(chat_type="group", text="/summary500")
        cfg = _make_chat_config(save_messages=False)
        service = _make_summary_service()

        await handle_summary500(msg, chat_config=cfg, summary_service=service)

        service.generate.assert_not_awaited()
        msg.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dm_answers_group_only_naming_this_command(self) -> None:
        """The notice must name /summary500, not the /summary it borrows copy from."""
        msg = _make_message(text="/summary500")
        cfg = _make_chat_config(language="ru")

        await handle_summary500_dm(msg, chat_config=cfg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "/summary500" in text
        assert "групповых" in text
        assert "{command}" not in text

    @pytest.mark.asyncio
    async def test_dm_english_variant(self) -> None:
        msg = _make_message(text="/summary500")
        cfg = _make_chat_config(language="en")

        await handle_summary500_dm(msg, chat_config=cfg)

        text = msg.answer.call_args[0][0]
        assert "/summary500" in text
        assert "group" in text


class TestCopyIsHtmlSafe:
    """Guards a whole bug class, not one string.

    The bot sets DefaultBotProperties(parse_mode=HTML), so every reply is
    parsed as HTML. `/summary abc` used to answer with "Формат: /summary
    <число от 20 до 1000>." — Telegram rejects that entire sendMessage with
    'Unsupported start tag "число"', so the user got no reply at all, exactly
    when they needed one. Confirmed against the live Bot API on 2026-08-09;
    the handler tests could not see it because message.reply is an AsyncMock.

    Any `<` that opens what Telegram would read as a tag must therefore be
    either a real formatting tag or an entity.
    """

    _ALLOWED_TAGS = {"b", "i", "u", "s", "a", "code", "pre", "tg-spoiler", "blockquote"}

    def _offending_tags(self, text: str) -> list[str]:
        import re as _re

        # Telegram starts a tag at '<' followed by a letter (any script) and
        # stops at whitespace or '>'; anything else stays literal text.
        return [
            m
            for m in _re.findall(r"<\s*/?\s*([^\s>/]+)", text)
            if m.lower() not in self._ALLOWED_TAGS
        ]

    def test_every_summary_copy_string_parses_as_html(self) -> None:
        from src.bot.handlers import commands as mod

        bad: dict[str, list[str]] = {}
        for name in dir(mod):
            if not name.startswith("_SUMMARY"):
                continue
            value = getattr(mod, name)
            if not isinstance(value, dict):
                continue
            for lang, text in value.items():
                if not isinstance(text, str):
                    continue
                offenders = self._offending_tags(text)
                if offenders:
                    bad[f"{name}[{lang}]"] = offenders
        assert not bad, f"copy would be rejected by Telegram's HTML parser: {bad}"

    def test_the_guard_itself_catches_the_original_string(self) -> None:
        """Negative control: the exact copy that shipped must be flagged."""
        original = "🤔 Не понял количество сообщений. Формат: /summary <число от 20 до 1000>."
        assert self._offending_tags(original) == ["число"]
        assert self._offending_tags("Формат: /summary <number from 20 to 1000>.") == ["number"]
        assert self._offending_tags("<b>bold</b> and <code>x</code>") == []
