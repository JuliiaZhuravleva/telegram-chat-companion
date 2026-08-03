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


class TestReactionSelectorAvailableReactions:
    def test_restricted_to_available_reactions_when_known(self) -> None:
        assert ReactionSelector.select("🔥", available_reactions=frozenset({"👍"})) is None

    def test_passes_when_in_available_reactions(self) -> None:
        assert ReactionSelector.select("🔥", available_reactions=frozenset({"🔥", "👍"})) == "🔥"

    def test_available_reactions_not_checked_when_unknown(self) -> None:
        """available_reactions=None (the R-5 default) skips that check --
        it's a runtime fact only Telegram can tell you at call time."""
        assert ReactionSelector.select("🔥", available_reactions=None) == "🔥"
