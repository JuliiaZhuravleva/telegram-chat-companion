"""Tests for src.services.modules.reactions.selector — ReactionSelector (R-5).

Fail-closed contract (ADR-0004 Decision 4): an invalid/hallucinated
candidate is rejected, never substituted with a default guess.
"""

from __future__ import annotations

from src.services.modules.reactions.models import ALLOWED_REACTION_EMOJI
from src.services.modules.reactions.selector import ReactionSelector


class TestReactionSelectorFailClosed:
    def test_none_candidate_returns_none(self) -> None:
        assert ReactionSelector.select(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert ReactionSelector.select("") is None

    def test_valid_emoji_is_returned(self) -> None:
        assert ReactionSelector.select("🔥") == "🔥"

    def test_hallucinated_emoji_rejected_not_substituted(self) -> None:
        """An emoji outside ALLOWED_REACTION_EMOJI is rejected outright --
        never replaced with a fallback like 👍 (ADR-0004 Decision 4)."""
        assert ReactionSelector.select("🥸") is None

    def test_prose_is_rejected(self) -> None:
        assert ReactionSelector.select("looks like NO") is None

    def test_whitespace_padded_emoji_rejected(self) -> None:
        """No fuzzy-matching -- an exact-match-only contract keeps the
        fail-closed guarantee simple to reason about."""
        assert ReactionSelector.select(" 🔥 ") is None

    def test_every_allowed_emoji_round_trips(self) -> None:
        for emoji in ALLOWED_REACTION_EMOJI:
            assert ReactionSelector.select(emoji) == emoji


class TestVariationSelectorNormalization:
    """Telegram's reaction set spells several emoji without U+FE0F, while
    keyboards and LLM output overwhelmingly emit the presentation form with it.
    Comparing raw strings silently made those unreachable."""

    # Every allowed emoji whose spelling is affected by U+FE0F at all.
    _VS16_SENSITIVE = ["❤", "⚡", "✍", "☃", "🕊"]

    def test_presentation_form_resolves(self) -> None:
        for bare in self._VS16_SENSITIVE:
            assert ReactionSelector.select(bare + "️") == bare, (
                f"{bare!r} unreachable when the model emits the U+FE0F form"
            )

    def test_returns_telegrams_spelling_not_the_callers(self) -> None:
        """setMessageReaction must receive the API's own spelling, so the
        variant form must not survive into the outbound call."""
        resolved = ReactionSelector.select("❤️")
        assert resolved == "❤"
        assert "️" not in (resolved or "")

    def test_every_allowed_emoji_reachable_via_presentation_form(self) -> None:
        for emoji in ALLOWED_REACTION_EMOJI:
            assert ReactionSelector.select(emoji + "️") == emoji

    def test_normalization_does_not_admit_non_reactions(self) -> None:
        """Stripping U+FE0F must not turn a disallowed emoji into an allowed
        one -- fail-closed still holds."""
        assert ReactionSelector.select("🥸️") is None
        assert ReactionSelector.select("🫠") is None

    def test_available_reactions_matches_either_spelling(self) -> None:
        """The chat's restricted set comes from Telegram in its own spelling,
        but a caller may hold the variant form; neither should misfire."""
        assert ReactionSelector.select("❤️", available_reactions=frozenset({"❤"})) == "❤"
        assert ReactionSelector.select("❤", available_reactions=frozenset({"❤️"})) == "❤"


class TestReactionSelectorAvailableReactions:
    def test_restricted_to_available_reactions_when_known(self) -> None:
        assert ReactionSelector.select("🔥", available_reactions=frozenset({"👍"})) is None

    def test_passes_when_in_available_reactions(self) -> None:
        assert ReactionSelector.select("🔥", available_reactions=frozenset({"🔥", "👍"})) == "🔥"

    def test_available_reactions_not_checked_when_unknown(self) -> None:
        """available_reactions=None (the R-5 default) skips that check --
        it's a runtime fact only Telegram can tell you at call time."""
        assert ReactionSelector.select("🔥", available_reactions=None) == "🔥"
