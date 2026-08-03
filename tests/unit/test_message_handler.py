"""Tests for src.bot.handlers.message — should_respond logic."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.bot.handlers.message import _react_to_silence, should_respond
from src.models.chat_config import ChatConfig
from src.services.relevancy.gate import GateDecision


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


class TestReactToSilence:
    """_react_to_silence — R-5 tier-3 reaction piggyback (ADR-0004 Decision 4)."""

    @staticmethod
    def _config(**overrides) -> ChatConfig:
        defaults = {"chat_id": -100, "reactions_enabled": True}
        defaults.update(overrides)
        return ChatConfig(**defaults)

    @pytest.mark.asyncio
    async def test_sets_reaction_when_enabled_and_emoji_suggested(
        self, make_message, monkeypatch
    ) -> None:
        msg = make_message(message_id=42)
        msg.bot = AsyncMock()
        set_reaction_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("src.bot.handlers.message.set_reaction", set_reaction_mock)

        decision = GateDecision(
            should_respond=False,
            tier="llm_judge",
            reason="not relevant",
            cost_usd=Decimal("0"),
            suggested_emoji="🔥",
        )
        await _react_to_silence(msg, self._config(), decision)

        set_reaction_mock.assert_awaited_once()
        call_kwargs = set_reaction_mock.call_args.kwargs
        assert call_kwargs["chat_id"] == msg.chat.id
        assert call_kwargs["message_id"] == 42
        assert call_kwargs["emoji"] == "🔥"

    @pytest.mark.asyncio
    async def test_noop_when_reactions_disabled(self, make_message, monkeypatch) -> None:
        msg = make_message()
        msg.bot = AsyncMock()
        set_reaction_mock = AsyncMock()
        monkeypatch.setattr("src.bot.handlers.message.set_reaction", set_reaction_mock)

        decision = GateDecision(
            should_respond=False, tier="llm_judge", reason="no", suggested_emoji="🔥"
        )
        await _react_to_silence(msg, self._config(reactions_enabled=False), decision)

        set_reaction_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_no_suggested_emoji(self, make_message, monkeypatch) -> None:
        msg = make_message()
        msg.bot = AsyncMock()
        set_reaction_mock = AsyncMock()
        monkeypatch.setattr("src.bot.handlers.message.set_reaction", set_reaction_mock)

        decision = GateDecision(should_respond=False, tier="engagement", reason="no")
        await _react_to_silence(msg, self._config(), decision)

        set_reaction_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_suggested_emoji_invalid(self, make_message, monkeypatch) -> None:
        """Fail-closed: a hallucinated emoji never reaches set_reaction."""
        msg = make_message()
        msg.bot = AsyncMock()
        set_reaction_mock = AsyncMock()
        monkeypatch.setattr("src.bot.handlers.message.set_reaction", set_reaction_mock)

        decision = GateDecision(
            should_respond=False, tier="llm_judge", reason="no", suggested_emoji="🥸"
        )
        await _react_to_silence(msg, self._config(), decision)

        set_reaction_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_message_has_no_bot(self, make_message, monkeypatch) -> None:
        msg = make_message()
        msg.bot = None
        set_reaction_mock = AsyncMock()
        monkeypatch.setattr("src.bot.handlers.message.set_reaction", set_reaction_mock)

        decision = GateDecision(
            should_respond=False, tier="llm_judge", reason="no", suggested_emoji="🔥"
        )
        await _react_to_silence(msg, self._config(), decision)

        set_reaction_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_reaction_exception_is_swallowed(self, make_message, monkeypatch) -> None:
        """A transient failure setting the reaction never crashes the handler."""
        msg = make_message()
        msg.bot = AsyncMock()
        set_reaction_mock = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr("src.bot.handlers.message.set_reaction", set_reaction_mock)

        decision = GateDecision(
            should_respond=False, tier="llm_judge", reason="no", suggested_emoji="🔥"
        )
        # Should not raise.
        await _react_to_silence(msg, self._config(), decision)
