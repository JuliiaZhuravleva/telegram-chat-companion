"""The text a rule posts in the chat: placeholders, Telegram's count, the cap.

A warning or a reply is sent as plain text (``parse_mode=None``), so Telegram
counts its UTF-16 code units, and a custom reply only reaches its real length
once a person's name is put in. Both the builder (which refuses a text that
cannot fit) and the executor (which never sends one that does not) measure here,
with the ruler in `src.utils.telegram_text`.
"""

from __future__ import annotations

import re
from html import escape, unescape
from typing import Final

import structlog

from src.utils.telegram_text import TELEGRAM_MESSAGE_LIMIT, parsed_length, split_html

logger = structlog.get_logger(__name__)

_PLACEHOLDER_RE: Final = re.compile(r"\{(first_name|username|rule_name)\}")

WORST_FIRST_NAME: Final = "😀" * 64
"""The longest first name Telegram allows (64 characters), each one astral: 128 units."""
WORST_USERNAME: Final = "u" * 32
"""The longest username Telegram allows (32 characters)."""

_ELLIPSIS: Final = "…"


def fill(template: str, *, first_name: str, username: str, rule_name: str) -> str:
    """Put the values into ``template`` in one pass.

    One pass, so a value is never read as a placeholder: a person named
    ``{rule_name}`` stays ``{rule_name}``, and a name cannot grow the text a
    second time.
    """
    values = {"first_name": first_name, "username": username, "rule_name": rule_name}
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def sent_length(text: str) -> int:
    """How long Telegram counts ``text`` sent as plain text (UTF-16 code units)."""
    return parsed_length(escape(text, quote=False))


def worst_length(template: str, *, rule_name: str) -> int:
    """The longest ``template`` can get once the longest possible name is put in."""
    return sent_length(
        fill(template, first_name=WORST_FIRST_NAME, username=WORST_USERNAME, rule_name=rule_name)
    )


def fit_one_message(text: str, *, rule_id: int | None = None) -> str:
    """``text`` as it can be sent: whole if it fits one message, else cut with ``…``.

    The builder refuses a text that cannot fit, so a cut here means a rule made
    before that check, or by JSON written elsewhere. Cutting beats sending:
    Telegram rejects an over-long message whole, and the chat would see nothing.
    """
    if sent_length(text) <= TELEGRAM_MESSAGE_LIMIT:
        return text
    pieces = split_html(escape(text, quote=False), limit=TELEGRAM_MESSAGE_LIMIT - 1)
    head = unescape(pieces[0]).rstrip() if pieces else ""
    logger.warning(
        "rule_text_cut_to_one_message",
        rule_id=rule_id,
        length=sent_length(text),
        kept=sent_length(head),
    )
    return head + _ELLIPSIS
