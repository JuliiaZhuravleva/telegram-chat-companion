"""Sanitize user-provided content before interpolation into AI prompts.

Neutralizes XML-like delimiter tags that structure prompts, preventing
users from breaking out of data sections via tag injection.
"""

from __future__ import annotations

import re

# Tags used as prompt section delimiters — single source of truth
_PROMPT_TAGS = frozenset(
    {
        "user_message",
        "current_topic",
        "other_topics",
        "chat_history",
        "conversation",
    }
)

# Matches opening/closing/self-closing variants of known delimiter tags
_TAG_PATTERN = re.compile(
    r"</?(" + "|".join(re.escape(t) for t in sorted(_PROMPT_TAGS)) + r")\s*/?>",
    re.IGNORECASE,
)


def sanitize_prompt_content(text: str) -> str:
    """Neutralize XML-like delimiter tags in user-provided content.

    Replaces angle brackets in matching tags with full-width Unicode
    equivalents (U+FF1C / U+FF1E) — visually similar but structurally
    inert for the LLM prompt parser.

    Only targets known delimiter tags; other angle brackets (math, HTML)
    are left untouched.
    """
    if not text:
        return text
    return _TAG_PATTERN.sub(_replace_brackets, text)


def _replace_brackets(m: re.Match[str]) -> str:
    return m.group(0).replace("<", "\uff1c").replace(">", "\uff1e")
