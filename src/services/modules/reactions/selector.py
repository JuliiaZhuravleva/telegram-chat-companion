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

from src.services.modules.reactions.models import ALLOWED_REACTION_EMOJI

_ALLOWED = frozenset(ALLOWED_REACTION_EMOJI)


class ReactionSelector:
    """Validates a candidate reaction emoji, fail-closed."""

    @staticmethod
    def select(
        candidate: str | None,
        *,
        available_reactions: frozenset[str] | None = None,
    ) -> str | None:
        """Return `candidate` if valid, else None.

        `available_reactions` is a chat's runtime-restricted reaction set --
        only checked here when the caller already has it in hand. It is a
        per-chat, per-message fact only Telegram can tell you at call time,
        so it is *not* fetched by the selector itself; that restriction is
        otherwise left to `responder.py` to discover from the API error
        (ADR-0004 Decision 4, point 3).
        """
        if not candidate:
            return None
        if candidate not in _ALLOWED:
            return None
        if available_reactions is not None and candidate not in available_reactions:
            return None
        return candidate
