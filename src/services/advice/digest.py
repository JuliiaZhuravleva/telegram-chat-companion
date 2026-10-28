"""The owner's daily digest of what the advice classifier found.

For the first two weeks (owner's decision 2026-10-28, option "a") nothing is
said in the chat; this message in her DM is the whole visible output. It lists
unsolicited advice and full support, each with who, what the model saw, a quote
and a link to the message, so she can check a verdict against the original in
one tap.

Everything that came from a chat -- names, titles, quotes, the model's own
summary -- is escaped: the bot sends with parse_mode=HTML by default, and a
member's `<b` would otherwise open a tag and get the whole digest rejected.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date
from itertools import groupby

from src.database.repositories.advice import DigestItem
from src.utils.telegram import build_chat_url

QUOTE_CHARS = 300

_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip
_LEVEL = {"hard": "жёсткий", "soft": "мягкий"}


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _message_link(item: DigestItem) -> str | None:
    url = build_chat_url(item.chat_id, item.chat_type)
    # `tg://user` is a person, not a chat: there is no message under it.
    if url is None or not url.startswith("https://"):
        return None
    return f"{url}/{item.first_message_id}"


def _quote(text: str) -> str:
    text = text.strip()
    if len(text) > QUOTE_CHARS:
        text = text[:QUOTE_CHARS].rstrip() + "…"
    return text


def _entry(item: DigestItem, *, with_level: bool) -> str:
    name = _esc(item.name or "участник")
    link = _message_link(item)
    head = f'<a href="{html.escape(link)}">{name}</a>' if link else name
    if with_level and item.level in _LEVEL:
        head += f" · {_LEVEL[item.level]}"
    lines = [f"• {head}"]
    if item.what:
        lines.append(f"<i>{_esc(item.what)}</i>")
    quote = _quote(item.quote)
    if quote:
        lines.append(f"<blockquote>{_esc(quote)}</blockquote>")
    return "\n".join(lines)


def render_digest(items: Sequence[DigestItem], day: date) -> str:
    """One HTML message (to be split by the caller) covering every chat in `items`."""
    out = [f"🧐 <b>Советы и поддержка — {day.day} {_MONTHS[day.month - 1]}</b>"]
    for _chat_id, group in groupby(items, key=lambda x: x.chat_id):
        chat_items = list(group)
        title = chat_items[0].chat_title
        if title:
            out.append(f"\n<b>{_esc(title)}</b>")
        advice = [x for x in chat_items if x.advice == "unsolicited"]
        support = [x for x in chat_items if x.support == 2]
        if advice:
            out.append(f"\n<b>Непрошеные советы ({len(advice)})</b>")
            out.extend(_entry(x, with_level=True) for x in advice)
        if support:
            out.append(f"\n<b>Поддержка ({len(support)})</b>")
            out.extend(_entry(x, with_level=False) for x in support)
    return "\n".join(out)
