"""Sticker explicitness/tolerance comparison (ADR-0008 Decision 2).

Single named function for the one comparison this feature is built around,
so the inequality direction is never re-derived (and never accidentally
flipped) at each call site. The hot path (``StickerRepository.search_by_embedding``,
ADR-0008 Decision 6) applies the *equivalent* predicate in SQL for
performance/`LIMIT` correctness; this Python function is the production-adjacent
assertion point tests pin the inequality direction against.
"""

from __future__ import annotations


def is_within_tolerance(explicitness_score: float | None, tolerance_level: float) -> bool:
    """A sticker is an eligible response candidate iff it has been scored
    AND its explicitness does not exceed the chat's ceiling. See ADR-0008.

    ``tolerance_level`` is a ceiling on acceptable explicitness (raising it
    admits a strict superset of what a lower value admits) — not a floor,
    not a minimum-required-spice knob. ``explicitness_score is None``
    (unscored) always excludes the sticker, even at ``tolerance_level = 1.0``
    (Decision 3, fail-closed) — never treat NULL as 0.0 (would ship
    unscored/pre-migration content) or as 1.0 (collapses "unknown" and
    "verified maximally explicit" onto the same value).
    """
    return explicitness_score is not None and explicitness_score <= tolerance_level
