"""Tests for src.services.relevancy.fast_rules — Tier 1 zero-cost heuristics."""

import pytest

from src.services.relevancy.fast_rules import check_fast_rules


class TestFastRules:
    """Test check_fast_rules() rejects low-value messages."""

    # --- Rejections: too short ---

    @pytest.mark.parametrize("text", ["", "ok", "да", "+", "no", "к"])
    def test_rejects_short_messages(self, text: str) -> None:
        result = check_fast_rules(text)
        assert result.should_pass is False
        assert result.reason.startswith("too_short")

    # --- Rejections: acknowledgments ---

    @pytest.mark.parametrize(
        "text",
        [
            "Ладно",
            "хорошо",
            "спасибо",
            "thanks",
            "cool",
            "nice",
            "норм",
            "класс",
            "понял",
            "поняла",
            "ясно",
            "lmao",
            "bruh",
        ],
    )
    def test_rejects_acknowledgments(self, text: str) -> None:
        result = check_fast_rules(text)
        assert result.should_pass is False
        assert result.reason == "acknowledgment"

    def test_rejects_acknowledgment_case_insensitive(self) -> None:
        result = check_fast_rules("ЛАДНО")
        assert result.should_pass is False

    # --- Rejections: emoji-only ---

    def test_rejects_single_emoji(self) -> None:
        result = check_fast_rules("\U0001f600")  # 😀
        assert result.should_pass is False

    def test_rejects_multiple_emoji(self) -> None:
        result = check_fast_rules("\U0001f44d\U0001f525")  # 👍🔥
        assert result.should_pass is False

    # --- Passes: normal messages ---

    @pytest.mark.parametrize(
        "text",
        [
            "Привет, как дела?",
            "What do you think about this?",
            "Кто пойдёт в кино?",
            "I need help with Python",
            "Кто-нибудь знает хороший рецепт пасты?",
        ],
    )
    def test_passes_normal_messages(self, text: str) -> None:
        result = check_fast_rules(text)
        assert result.should_pass is True

    def test_passes_trigger_word_in_sentence(self) -> None:
        """Messages that contain trigger words should pass (they're handled separately)."""
        result = check_fast_rules("hey bot what do you think")
        assert result.should_pass is True

    def test_whitespace_stripped(self) -> None:
        result = check_fast_rules("  ok  ")
        assert result.should_pass is False
