"""ReactionSelector -- validates a candidate emoji before `setMessageReaction`.

Single shared primitive: used by R-5 (tier-3 llm_judge piggyback) now, and by
R-3/R-8 in Phase 2 (ADR-0004 Decision 2) -- do not fork a second selector when
those land.

Fail-closed (ADR-0004 Decision 4): a candidate that isn't in
`ALLOWED_REACTION_EMOJI` is rejected outright, never replaced with a default
guess. A wrong-but-plausible substitute (e.g. always falling back to 👍)
would misrepresent what actually happened, which is worse than reacting to
nothing.
"""

from __future__ import annotations

import structlog

from src.services.modules.reactions.models import ALLOWED_REACTION_EMOJI

logger = structlog.get_logger(__name__)

# U+FE0F VARIATION SELECTOR-16. Telegram's documented reaction set spells
# several emoji WITHOUT it ("❤", "⚡", "✍", "☃", "🕊", "🤷‍♂", "🤷‍♀"), while
# keyboards and LLM output overwhelmingly produce the emoji-presentation form
# with it. Comparing raw strings therefore rejected ~7 of the 73 allowed
# reactions outright, silently and permanently.
_VARIATION_SELECTOR_16 = "️"


def _normalize(emoji: str) -> str:
    return emoji.replace(_VARIATION_SELECTOR_16, "")


# Normalized form -> the spelling Telegram expects back in setMessageReaction.
# Built from ALLOWED_REACTION_EMOJI so the canonical spelling is always the
# API's own, never the caller's.
_CANONICAL: dict[str, str] = {_normalize(e): e for e in ALLOWED_REACTION_EMOJI}


class ReactionSelector:
    """Validates a candidate reaction emoji, fail-closed."""

    @staticmethod
    def select(
        candidate: str | None,
        *,
        available_reactions: frozenset[str] | None = None,
    ) -> str | None:
        """Return the canonical spelling of `candidate` if valid, else None.

        Matching ignores U+FE0F on both sides, so "❤️" and "❤" both resolve --
        and what comes back is always Telegram's own spelling, so the caller
        cannot pass a variant form on to `setMessageReaction`. Matching stays
        exact otherwise (no trimming, no fuzzy fallback): `llm_judge`'s
        `_extract_suggested_emoji` already hands over a stripped line, so
        loosening further would only widen what counts as a valid reaction.

        `available_reactions` is a chat's runtime-restricted reaction set --
        only checked here when the caller already has it in hand. It is a
        per-chat, per-message fact only Telegram can tell you at call time,
        so it is *not* fetched by the selector itself; that restriction is
        otherwise left to `responder.py` to discover from the API error
        (ADR-0004 Decision 4, point 3).

        Every rejection is logged. Without that, "R-5 is broken and drops every
        suggestion" and "R-5 is healthy, the model rarely suggests one" produce
        byte-identical logs -- the silent-no-op failure mode this feature is
        already prone to (see R-D1).
        """
        if not candidate:
            return None

        resolved = _CANONICAL.get(_normalize(candidate))
        if resolved is None:
            logger.debug(
                "Reaction candidate rejected: not an allowed Telegram reaction",
                candidate=candidate,
            )
            return None

        # Normalize both sides: the caller's set may hold either spelling.
        if available_reactions is not None and _normalize(resolved) not in {
            _normalize(a) for a in available_reactions
        }:
            logger.debug(
                "Reaction candidate rejected: restricted in this chat",
                candidate=candidate,
                resolved=resolved,
            )
            return None

        return resolved
