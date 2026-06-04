"""Tier 1: Zero-cost fast rule heuristics for the relevancy gate.

Pure functions — no I/O, no dependencies. Rejects messages that are clearly
not worth a bot response (acknowledgments, very short texts, emoji-only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Common single-word acknowledgments in RU/EN that don't warrant a response
_ACK_PATTERN = re.compile(
    r"^("
    r"ок|ok|okey|okay|да|нет|no|yes|ага|угу|ладно|хорошо|хор"
    r"|спс|спасибо|thx|thanks|ty"
    r"|lol|кек|ахах[а]*|хах[а]*|gg|wp|gg wp"
    r"|\+1|-1|\+|\-|плюс|минус"
    r"|норм|класс|cool|nice|круто|топ|огонь|жиза|шикарно"
    r"|го|пон|понял[а]?|ясно|lmao|bruh"
    r"|ну|ок[ей]?|ладн[оа]|мда|ммм|хм+|угу|ага|аа+"
    r")$",
    re.IGNORECASE,
)

# Emoji-only message (1-8 emoji chars, possibly with variation selectors / ZWJ)
_EMOJI_ONLY = re.compile(
    r"^[\U00010000-\U0010ffff\u200d\u2600-\u26ff\u2700-\u27bf\ufe0f\s]{1,8}$",
)

# Messages shorter than this are too trivial for the bot to respond to
_MIN_MESSAGE_LENGTH = 4


@dataclass(frozen=True)
class FastRuleResult:
    """Outcome of Tier 1 fast-rule check."""

    should_pass: bool
    reason: str = ""


def check_fast_rules(message_text: str) -> FastRuleResult:
    """Apply zero-cost heuristics to filter out low-value messages.

    Returns ``should_pass=False`` to reject the message (bot stays silent).
    """
    text = message_text.strip()

    # Short questions (e.g. "Да?", "Правда?") are genuine — pass before
    # the length check so they reach the engagement/LLM tiers.
    if text.endswith("?") and len(text) >= 3:
        return FastRuleResult(True, "question")

    if len(text) < _MIN_MESSAGE_LENGTH:
        return FastRuleResult(False, f"too_short:{len(text)}")

    if _ACK_PATTERN.match(text):
        return FastRuleResult(False, "acknowledgment")

    if _EMOJI_ONLY.match(text):
        return FastRuleResult(False, "emoji_only")

    return FastRuleResult(True)
