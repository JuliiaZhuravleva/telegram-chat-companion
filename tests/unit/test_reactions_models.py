"""Tests for src.services.modules.reactions.models — diff() (R-1, ADR-0004)."""

from __future__ import annotations

from aiogram.types import ReactionTypeCustomEmoji, ReactionTypeEmoji, ReactionTypePaid

from src.services.modules.reactions.models import ReactionEvent, diff


def _emoji(e: str) -> ReactionTypeEmoji:
    return ReactionTypeEmoji(emoji=e)


def _custom(cid: str) -> ReactionTypeCustomEmoji:
    return ReactionTypeCustomEmoji(custom_emoji_id=cid)


class TestDiff:
    """MessageReactionUpdated carries the full current state, not a delta --
    diff() has to compute added/removed itself."""

    def test_no_change_produces_no_events(self) -> None:
        old = [_emoji("👍")]
        new = [_emoji("👍")]
        assert diff(old, new) == []

    def test_empty_to_empty_produces_no_events(self) -> None:
        assert diff([], []) == []

    def test_new_reaction_is_added(self) -> None:
        events = diff([], [_emoji("👍")])
        assert events == [ReactionEvent(action="added", reaction_type="emoji", emoji="👍")]

    def test_removed_reaction_is_removed(self) -> None:
        events = diff([_emoji("👍")], [])
        assert events == [ReactionEvent(action="removed", reaction_type="emoji", emoji="👍")]

    def test_replacing_one_emoji_with_another_yields_both_events(self) -> None:
        events = diff([_emoji("👍")], [_emoji("🔥")])
        assert set(events) == {
            ReactionEvent(action="removed", reaction_type="emoji", emoji="👍"),
            ReactionEvent(action="added", reaction_type="emoji", emoji="🔥"),
        }

    def test_unchanged_reaction_among_others_produces_no_row_for_itself(self) -> None:
        old = [_emoji("👍"), _emoji("🔥")]
        new = [_emoji("👍"), _emoji("🎉")]
        events = diff(old, new)
        assert set(events) == {
            ReactionEvent(action="removed", reaction_type="emoji", emoji="🔥"),
            ReactionEvent(action="added", reaction_type="emoji", emoji="🎉"),
        }

    def test_custom_emoji_added(self) -> None:
        events = diff([], [_custom("123456")])
        assert events == [
            ReactionEvent(action="added", reaction_type="custom_emoji", custom_emoji_id="123456")
        ]

    def test_custom_emoji_distinguished_by_id(self) -> None:
        """Two different custom emoji must not be treated as the same reaction."""
        old = [_custom("111")]
        new = [_custom("222")]
        events = diff(old, new)
        assert set(events) == {
            ReactionEvent(action="removed", reaction_type="custom_emoji", custom_emoji_id="111"),
            ReactionEvent(action="added", reaction_type="custom_emoji", custom_emoji_id="222"),
        }

    def test_paid_reaction_added_carries_no_emoji_info(self) -> None:
        """Bots can't set paid reactions, but users can send them -- the read
        side must not crash or silently drop the row (ADR-0004 Decision 1)."""
        events = diff([], [ReactionTypePaid()])
        assert events == [ReactionEvent(action="added", reaction_type="paid")]

    def test_paid_reaction_present_in_both_produces_no_event(self) -> None:
        """All paid reactions collapse to the same key -- a second paid
        reaction is indistinguishable from the first, by design."""
        events = diff([ReactionTypePaid()], [ReactionTypePaid()])
        assert events == []

    def test_mixed_types_diffed_independently(self) -> None:
        old = [_emoji("👍")]
        new = [_emoji("👍"), _custom("999"), ReactionTypePaid()]
        events = diff(old, new)
        assert set(events) == {
            ReactionEvent(action="added", reaction_type="custom_emoji", custom_emoji_id="999"),
            ReactionEvent(action="added", reaction_type="paid"),
        }
