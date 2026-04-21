"""Tests for src.bot.handlers.message — should_respond logic."""

from src.bot.handlers.message import should_respond
from src.models.chat_config import ChatConfig


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
