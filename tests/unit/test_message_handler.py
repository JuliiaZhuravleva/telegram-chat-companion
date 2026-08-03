"""Tests for src.bot.handlers.message — should_respond logic and the
handle_text_message typing-indicator wiring (I-2).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.message import handle_text_message, should_respond
from src.models.chat_config import ChatConfig
from src.models.enums import ResponseType, TriggerType
from src.services.text.pipeline import PipelineResult


class TestShouldRespond:
    """Test should_respond() with various message types and settings."""

    @staticmethod
    def _call(message, trigger_words=None, random_chance=0.0):
        """Helper: call should_respond with a ChatConfig."""
        config = ChatConfig(
            chat_id=message.chat.id,
            trigger_words=tuple(trigger_words or ["bot"]),
            random_response_chance=random_chance,
        )
        return should_respond(message, config)

    def test_trigger_word_in_text(self, make_message):
        msg = make_message(text="hey bot how are you")
        result, trigger_type = self._call(msg)
        assert result is True
        assert trigger_type == "trigger"

    def test_trigger_word_case_insensitive(self, make_message):
        msg = make_message(text="Hey BOT")
        result, trigger_type = self._call(msg)
        assert result is True
        assert trigger_type == "trigger"

    def test_russian_trigger_word(self, make_message):
        msg = make_message(text="эй бот привет")
        result, trigger_type = self._call(
            msg,
            trigger_words=["bot", "бот"],
        )
        assert result is True
        assert trigger_type == "trigger"

    def test_no_trigger_no_random(self, make_message):
        msg = make_message(text="just a normal message")
        result, trigger_type = self._call(msg, random_chance=0.0)
        assert result is False
        assert trigger_type == "none"

    def test_random_response_always(self, make_message):
        msg = make_message(text="no trigger here")
        result, trigger_type = self._call(msg, random_chance=1.0)
        assert result is True
        assert trigger_type == "random"

    def test_random_response_never(self, make_message):
        msg = make_message(text="no trigger here")
        result, trigger_type = self._call(msg, random_chance=0.0)
        assert result is False
        assert trigger_type == "none"

    def test_caption_contains_trigger(self, make_message):
        msg = make_message(text=None, caption="look at this bot")
        result, trigger_type = self._call(msg)
        assert result is True
        assert trigger_type == "trigger"

    def test_none_text_and_none_caption(self, make_message):
        msg = make_message(text=None, caption=None)
        result, trigger_type = self._call(msg, random_chance=0.0)
        assert result is False
        assert trigger_type == "none"

    def test_trigger_takes_priority_over_random(self, make_message):
        """If trigger word matches, result is 'trigger' not 'random'."""
        msg = make_message(text="hello bot")
        result, trigger_type = self._call(msg, random_chance=1.0)
        assert result is True
        assert trigger_type == "trigger"


# ── handle_text_message: typing-indicator wiring (I-2) ────────────────────


def _make_bot():
    bot = MagicMock()
    bot_info = MagicMock()
    bot_info.id = 999
    bot.me = AsyncMock(return_value=bot_info)
    bot.send_chat_action = AsyncMock()
    return bot


@pytest.fixture
def message_deps(make_message):
    """Common mocks for handle_text_message: message, config, pipeline, gate, spend limit."""
    message = make_message(text="hey bot how are you", chat_id=-100123, message_id=42)
    message.answer = AsyncMock(return_value=MagicMock(message_id=43))
    message.answer_sticker = AsyncMock()

    chat_config = ChatConfig(chat_id=-100123, trigger_words=("bot",), random_response_chance=1.0)

    pipeline = MagicMock()
    pipeline.process = AsyncMock(
        return_value=PipelineResult(
            should_respond=True,
            html_text="Hello there!",
            trigger_type=TriggerType.TRIGGER,
            response_type=ResponseType.NORMAL,
        )
    )
    pipeline.post_send = AsyncMock()

    relevancy_gate = MagicMock()
    relevancy_gate.evaluate = AsyncMock(return_value=MagicMock(should_respond=True))

    spend_limit_svc = MagicMock()
    spend_limit_svc.get_warning_if_exceeded = AsyncMock(return_value=None)

    return {
        "message": message,
        "chat_config": chat_config,
        "pipeline": pipeline,
        "relevancy_gate": relevancy_gate,
        "spend_limit_svc": spend_limit_svc,
        "bot": _make_bot(),
    }


class TestHandleTextMessageTypingIndicator:
    """Regression guard: pipeline.process() must run under the shared
    typing_indicator helper, except for TriggerType.RANDOM (Q1 decision —
    no indicator before unsolicited replies).
    """

    @pytest.mark.asyncio
    async def test_wraps_pipeline_process_for_trigger_word(self, message_deps):
        with patch("src.bot.handlers.message.typing_indicator") as mock_indicator:
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_text_message(
                message_deps["message"],
                message_deps["chat_config"],
                message_deps["pipeline"],
                message_deps["relevancy_gate"],
                message_deps["spend_limit_svc"],
                message_deps["bot"],
            )

        mock_indicator.assert_called_once_with(
            message_deps["bot"],
            message_deps["message"].chat.id,
            None,
            enabled=True,
        )
        message_deps["pipeline"].process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_forwards_message_thread_id(self, message_deps):
        with patch("src.bot.handlers.message.typing_indicator") as mock_indicator:
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_text_message(
                message_deps["message"],
                message_deps["chat_config"],
                message_deps["pipeline"],
                message_deps["relevancy_gate"],
                message_deps["spend_limit_svc"],
                message_deps["bot"],
                message_thread_id=777,
            )

        mock_indicator.assert_called_once_with(
            message_deps["bot"],
            message_deps["message"].chat.id,
            777,
            enabled=True,
        )

    @pytest.mark.asyncio
    async def test_disabled_for_random_trigger(self, message_deps, make_message):
        """No trigger word, no reply — falls through to RANDOM. Gate approves,
        but the typing indicator must stay off (Q1)."""
        message = make_message(text="just chatting", chat_id=-100123, message_id=42)
        message.answer = AsyncMock(return_value=MagicMock(message_id=43))
        message.answer_sticker = AsyncMock()
        message_deps["message"] = message
        message_deps["pipeline"].process = AsyncMock(
            return_value=PipelineResult(
                should_respond=True,
                html_text="Random reply",
                trigger_type=TriggerType.RANDOM,
                response_type=ResponseType.NORMAL,
            )
        )

        with patch("src.bot.handlers.message.typing_indicator") as mock_indicator:
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_text_message(
                message_deps["message"],
                message_deps["chat_config"],
                message_deps["pipeline"],
                message_deps["relevancy_gate"],
                message_deps["spend_limit_svc"],
                message_deps["bot"],
            )

        mock_indicator.assert_called_once_with(
            message_deps["bot"],
            message.chat.id,
            None,
            enabled=False,
        )

    @pytest.mark.asyncio
    async def test_indicator_stops_even_if_pipeline_raises(self, message_deps):
        """The real typing_indicator (ChatActionSender) guarantees the action
        stops on exception; here we assert we don't swallow/skip the context
        manager's exit path by calling pipeline.process() outside of it.
        """
        message_deps["pipeline"].process = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await handle_text_message(
                message_deps["message"],
                message_deps["chat_config"],
                message_deps["pipeline"],
                message_deps["relevancy_gate"],
                message_deps["spend_limit_svc"],
                message_deps["bot"],
            )
