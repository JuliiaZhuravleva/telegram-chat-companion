"""Tests for `strip_bot_address` — what reaches the retrieval embedding.

The positive cases are derived from the *threat* rather than from the
implementation: they are the shapes a production corpus actually contains
(vocative head, subject head, inflected forms, glued prefixes, a handle in
front, the bare trigger on its own), written out as neutral invented text
because this repository is public. A suite built by reading the regex back
would only confirm the regex.
"""

from __future__ import annotations

import pytest

from src.services.text.query_hygiene import strip_bot_address

TRIGGERS = ("бот", "bot")


class TestAddressIsRemoved:
    """The head-of-message vocative — the case R0 exists for."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("бот, а почему все любят этот фильм?", "а почему все любят этот фильм?"),
            ("Бот, кто придумал такое название", "кто придумал такое название"),
            # No comma. Dropping these would lose the memory-seeking questions
            # that motivated the change in the first place.
            ("Бот что мы решили месяц назад?", "что мы решили месяц назад?"),
            ("бот что думаешь", "что думаешь"),
            # Other address punctuation people actually type.
            ("бот — ты как?", "ты как?"),
            ("бот: расскажи про кофе", "расскажи про кофе"),
            ("бот! срочно нужен совет", "срочно нужен совет"),
            ("бот!!! срочно нужен совет", "срочно нужен совет"),
            # Latin trigger, arbitrary case.
            ("BOT, what is a monad?", "what is a monad?"),
            ("Bot tell me about tea", "tell me about tea"),
            # Leading whitespace before the address.
            ("   бот, привет", "привет"),
        ],
    )
    def test_leading_address_is_stripped(self, text: str, expected: str) -> None:
        assert strip_bot_address(text, TRIGGERS) == expected

    def test_repeated_address_is_peeled(self) -> None:
        assert strip_bot_address("бот, бот, ты тут?", TRIGGERS) == "ты тут?"


class TestContentIsPreserved:
    """Every shape where the trigger word IS the content.

    On the production corpus these are the majority of trigger-matching
    messages, so a rule that removed them would change what most questions
    ask while looking, in aggregate, like an improvement.
    """

    @pytest.mark.parametrize(
        "text",
        [
            # Mid-sentence: the bot is the object of the sentence.
            "мне очень нравится этот бот",
            "кажется, бот сегодня не отвечает",
            "не бот, а живой человек",
            # Trailing: predicate far more often than vocative — deliberately
            # left alone (see the module docstring for the rejected draft).
            "по-моему ты бот",
            "самый полезный тут бот",
            # Inflected and glued forms are not the trigger token at all.
            "ботификация всей страны",
            "Боты захватили чат",
            "Бота опять перезапустили",
            "botanist сказал своё слово",
            # A leading handle is out of scope for R0 — 635 of 667 production
            # messages that open with one are addressing a person, so peeling
            # the head would delete the addressee far more often than it would
            # remove an address to the bot.
            "@some_bot, привет",
            "@artem_k что там с ботом",
            "спроси у @some_bot про это",
            # An address must be DELIMITED. Without that requirement the
            # punctuation run ate the hyphen of a compound and left orphans
            # behind: each of these lost its head word in the first draft.
            "бот-переводчик не работает",
            "Бот-то умный какой",
            "Bot's memory is broken",
            "бот)) привет",
            "бот. а что было вчера?",
            # Peeling would leave "?" / an emoji, which is not a query.
            "Бот?",
            "бот 😀",
        ],
    )
    def test_untouched(self, text: str) -> None:
        assert strip_bot_address(text, TRIGGERS) == text


class TestNeverReturnsNothing:
    """A query that says nothing is a worse failure than a noisy one."""

    @pytest.mark.parametrize("text", ["Бот", "бот", "бот,", "бот!"])
    def test_address_only_message_keeps_its_text(self, text: str) -> None:
        assert strip_bot_address(text, TRIGGERS) == text

    def test_address_only_message_is_still_whitespace_normalised(self) -> None:
        assert strip_bot_address("  бот  ", TRIGGERS) == "бот"

    @pytest.mark.parametrize("text", ["", "   ", "\n"])
    def test_blank_input_is_returned_as_is(self, text: str) -> None:
        assert strip_bot_address(text, TRIGGERS) == text


class TestTriggerWordsAreUntrustedInput:
    """`chat_settings.trigger_words` is editable from the admin panel."""

    def test_regex_metacharacters_are_literal(self) -> None:
        # Unescaped, "b.t" would match "bot" — a chat could silently strip a
        # word it never configured.
        assert strip_bot_address("b.t, привет", ("b.t",)) == "привет"
        assert strip_bot_address("bot, привет", ("b.t",)) == "bot, привет"

    def test_blank_triggers_are_ignored(self) -> None:
        # A bare "" alternative matches the empty string at position 0, which
        # would strip the head of every message ever sent.
        assert strip_bot_address("привет мир", ("", "   ")) == "привет мир"

    def test_no_triggers_configured_is_a_no_op_beyond_handles(self) -> None:
        assert strip_bot_address("бот, привет", ()) == "бот, привет"

    def test_longest_trigger_wins(self) -> None:
        # First-match alternation would strip "бот" and leave "привет, как дела".
        assert strip_bot_address("бот привет, как дела", ("бот", "бот привет")) == "как дела"

    def test_trigger_that_starts_with_a_non_word_character(self) -> None:
        # `\b` would fail at position 0 here; the lookarounds do not. This is
        # also how a chat could opt into handle-stripping today, explicitly.
        assert strip_bot_address("@mybot привет", ("@mybot",)) == "привет"

    def test_multiword_trigger(self) -> None:
        assert strip_bot_address("эй бот, привет", ("эй бот",)) == "привет"


class TestWhitespaceIsNormalised:
    """So that "was an address removed?" is answerable as `out != text.strip()`.

    Telegram messages routinely carry a trailing newline. Without this, the
    pipeline's `query_stripped` flag — the only thing that will separate
    pre- and post-R0 rows in `retrieval_log` — would read True for ordinary
    un-addressed questions.
    """

    @pytest.mark.parametrize(
        "text", ["как варить борщ?\n", "  как варить борщ?", "как варить борщ?  "]
    )
    def test_no_address_means_only_trimming(self, text: str) -> None:
        out = strip_bot_address(text, TRIGGERS)
        assert out == text.strip()
        assert out == "как варить борщ?"

    def test_address_removal_is_distinguishable_from_trimming(self) -> None:
        text = "бот, как варить борщ?\n"
        out = strip_bot_address(text, TRIGGERS)
        assert out != text.strip()
        assert out == "как варить борщ?"
