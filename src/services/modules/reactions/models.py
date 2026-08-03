"""Pure data classes and diff logic for the reactions module.

`MessageReactionUpdated` carries the full current state of a user's reactions
on a message, not a queue of add/remove events -- there is no "added"/"removed"
flag on the wire. `diff()` computes that once, at write time (ADR-0004
Decision 1), so later readers (R-4's chat-style profile, R-9's analytics) get
a plain per-row `action` column instead of re-deriving the diff on every read.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import ReactionTypeCustomEmoji, ReactionTypeEmoji, ReactionTypeUnion

# Bot API's own discriminator values (aiogram.types.ReactionType* .type), reused
# verbatim as the `reaction_type` column rather than inventing a parallel vocabulary.
ACTION_ADDED = "added"
ACTION_REMOVED = "removed"


@dataclass(frozen=True)
class ReactionEvent:
    """One row to write to `message_reactions`: a single (emoji, action) change."""

    action: str  # "added" | "removed"
    reaction_type: str  # "emoji" | "custom_emoji" | "paid"
    emoji: str | None = None
    custom_emoji_id: str | None = None


def _key(reaction: ReactionTypeUnion) -> tuple[str, str | None]:
    """Identity of a single reaction, for diffing old_reaction vs new_reaction.

    `paid` reactions carry no per-instance identifier at this layer, so every
    paid reaction collapses to the same key -- acceptable per ADR-0004
    Decision 1 (bots can't originate them; Phase 1 only needs to know one
    happened, not distinguish tiers).
    """
    if isinstance(reaction, ReactionTypeEmoji):
        return ("emoji", reaction.emoji)
    if isinstance(reaction, ReactionTypeCustomEmoji):
        return ("custom_emoji", reaction.custom_emoji_id)
    return ("paid", None)


def diff(
    old_reaction: list[ReactionTypeUnion],
    new_reaction: list[ReactionTypeUnion],
) -> list[ReactionEvent]:
    """Compute added/removed reactions between two full-state snapshots.

    A reaction present in both snapshots produces no row. Pure, I/O-free --
    unit-testable directly against constructed old/new reaction lists.
    """
    old_by_key = {_key(r): r for r in old_reaction}
    new_by_key = {_key(r): r for r in new_reaction}

    events: list[ReactionEvent] = []
    for key in old_by_key.keys() - new_by_key.keys():
        events.append(_event(key, action=ACTION_REMOVED))
    for key in new_by_key.keys() - old_by_key.keys():
        events.append(_event(key, action=ACTION_ADDED))
    return events


def _event(key: tuple[str, str | None], *, action: str) -> ReactionEvent:
    reaction_type, value = key
    if reaction_type == "emoji":
        return ReactionEvent(action=action, reaction_type=reaction_type, emoji=value)
    if reaction_type == "custom_emoji":
        return ReactionEvent(action=action, reaction_type=reaction_type, custom_emoji_id=value)
    return ReactionEvent(action=action, reaction_type=reaction_type)
