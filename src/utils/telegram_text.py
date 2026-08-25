"""Splitting a rendered HTML message into pieces Telegram will accept.

Telegram's ``sendMessage`` takes "1-4096 characters after entities parsing".
Nothing in this project measured that until now, and the omission was not
theoretical: a six-minute voice note transcribed to 4648 characters, the
``sendMessage`` came back ``Bad Request: message is too long``, and because
``send_quoted_reply`` re-raises anything that is not a "reply target gone"
marker -- and the global error handler only answers ``CallbackQuery`` events --
the chat saw *nothing at all*. Whisper had already been paid for and the
transcript was already in the database, so the words existed everywhere except
where the person asking for them could read them. Four such transcripts exist
in production; all four were lost the same way.

Three things about the limit drive the whole design here:

* It counts the text **after entities are parsed**, so ``<b>`` costs nothing
  and ``&amp;`` costs one, not five. Budgeting on ``len(html)`` is what made
  ``/kb`` truncate pages Telegram would have accepted (see ``_visible_len`` in
  ``handlers/commands.py``, which measures the same thing for a different
  purpose and predates this module).
* It counts **UTF-16 code units**, not Python characters. Every emoji outside
  the BMP is two. The transcription header alone opens with one.
* A cut in the wrong place produces a message Telegram rejects *wholesale*.
  Half a tag (``<b``), half an entity (``&am``) or an unclosed ``<blockquote>``
  are all fatal, so this splits on a token stream rather than on a string
  offset, and re-opens the enclosing tags on the far side of every boundary.

Measured against the live Bot API (2026-08-25, ``scripts/probe_telegram_limits.py``):

* 4096 is **inclusive** -- a body of exactly 4096 units is accepted, 4097 is
  rejected with ``Bad Request: message is too long``.
* Telegram **trims** leading and trailing ASCII whitespace, and applies the cap
  *after* trimming: ``"x" * 4096 + " "`` is accepted. U+00A0 is not trimmed.
  ``parsed_length`` deliberately does NOT model the trim, so it over-counts a
  piece with edge whitespace by a character or two. That is the conservative
  direction -- it can only ever ask for a split Telegram would not have needed,
  never skip one it does.
* Astral characters were confirmed to count double end-to-end: 2400 emoji split
  into 4000 + 800 units, and Telegram echoed exactly those numbers back.
"""

from __future__ import annotations

import re
from html import escape as html_escape
from html import unescape

import structlog

logger = structlog.get_logger(__name__)

# Telegram's documented ceiling. Kept exact and separate from the working
# budget below so the two cannot be confused at a call site.
TELEGRAM_MESSAGE_LIMIT = 4096

# What we actually fill to. The margin absorbs the continuation markers a
# caller may prepend and any disagreement between our arithmetic and
# Telegram's; an unnecessary extra message is a cosmetic cost, a rejected one
# is total.
DEFAULT_SPLIT_LIMIT = 4000

# Every tag Telegram's HTML parse mode accepts. Deliberately wider than what
# `services/text/formatter.py` emits (b, i, s, code, pre, blockquote): the
# summary path injects `<a href="tg://user?id=N">` *after* formatting, and a
# tag this module failed to recognise would be treated as text -- counted
# against the budget, and cuttable down the middle.
_TAGS = (
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "a", "code", "pre", "blockquote", "span", "tg-spoiler",
)  # fmt: skip

_TAG_RE = re.compile(
    r"</?(?:" + "|".join(_TAGS) + r")(?:\s[^<>]*)?>",
    re.IGNORECASE,
)
_TAG_NAME_RE = re.compile(r"</?([a-zA-Z-]+)")

# One indivisible unit of text: a whole character reference, or a single
# character. Cutting inside `&amp;` yields `&am`, which Telegram rejects along
# with the entire message.
_ATOM_RE = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]*|#\d+|#[xX][0-9a-fA-F]+);|[\s\S]")

# Tags that carry no closing form in practice and would desync the stack.
_VOID: frozenset[str] = frozenset()


def _utf16_len(text: str) -> int:
    """Length in UTF-16 code units -- the unit Telegram actually counts."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def _visible_text(html: str) -> str:
    """The text a reader actually sees: tags removed, entities decoded."""
    return unescape(_TAG_RE.sub("", html))


def parsed_length(html: str) -> int:
    """Length of ``html`` as Telegram measures it for the 4096 limit.

    Tags contribute nothing, ``&amp;`` contributes one, and an astral
    character contributes two.
    """
    return sum(_utf16_len(unescape(chunk)) for chunk in _TAG_RE.split(html))


class _Item:
    """One token: an opening tag, a closing tag, or a single text atom."""

    __slots__ = ("raw", "width", "tag", "closing")

    def __init__(self, raw: str, width: int, tag: str | None, closing: bool) -> None:
        self.raw = raw
        self.width = width
        self.tag = tag
        self.closing = closing


def _tokenize(html: str) -> list[_Item]:
    items: list[_Item] = []
    pos = 0
    for match in _TAG_RE.finditer(html):
        _append_text(items, html[pos : match.start()])
        raw = match.group(0)
        name_match = _TAG_NAME_RE.match(raw)
        name = name_match.group(1).lower() if name_match else ""
        items.append(_Item(raw, 0, name, raw.startswith("</")))
        pos = match.end()
    _append_text(items, html[pos:])
    return items


def _append_text(items: list[_Item], text: str) -> None:
    for atom in _ATOM_RE.findall(text):
        items.append(_Item(atom, _utf16_len(unescape(atom)), None, False))


def _well_formed(items: list[_Item]) -> bool:
    """Are the tags properly nested?

    ``markdown_to_html`` can emit *crossing* tags -- ``**a *b** c*`` becomes
    ``<b>a <i>b</b> c</i>`` -- which Telegram rejects outright, length aside.
    Splitting such a string cannot repair it and a naive open-tag stack would
    desync and emit nonsense, so this is detected and the caller degrades to
    plain text instead.
    """
    stack: list[str] = []
    for item in items:
        if item.tag is None or item.tag in _VOID:
            continue
        if item.closing:
            if not stack or stack[-1] != item.tag:
                return False
            stack.pop()
        else:
            stack.append(item.tag)
    return not stack


def _render(
    items: list[_Item],
    start: int,
    end: int,
    open_stack: list[tuple[str, str]],
    close_stack: list[tuple[str, str]],
) -> str:
    """One piece: re-opened tags, the body, then everything still open closed."""
    prefix = "".join(raw for _, raw in open_stack)
    body = "".join(items[i].raw for i in range(start, end))
    suffix = "".join(f"</{name}>" for name, _ in reversed(close_stack))
    return prefix + body + suffix


def _take(
    items: list[_Item], start: int, stack: list[tuple[str, str]], limit: int
) -> tuple[int, list[tuple[str, str]]]:
    """Walk from ``start`` until ``limit`` is reached; return the cut point.

    Prefers to cut at a line break, then at a space, so a split lands between
    words rather than inside one. A break is only honoured if it is not too
    far back -- otherwise a body with one very long unbroken run would emit a
    nearly-empty message and make no progress.
    """
    stack = list(stack)
    used = 0
    best: tuple[int, list[tuple[str, str]]] | None = None
    best_used = 0
    index = start

    while index < len(items):
        item = items[index]
        if item.width and used + item.width > limit:
            break
        if item.tag is not None:
            if item.closing:
                if stack:
                    stack.pop()
            else:
                stack.append((item.tag, item.raw))
        else:
            used += item.width
            # A break point is the position *after* the separator, so the
            # newline stays with the piece that precedes it.
            if item.raw in ("\n", " ") and used > limit // 2 and used > best_used:
                best, best_used = (index + 1, list(stack)), used
        index += 1

    if index >= len(items):
        return len(items), stack
    if best is not None:
        return best
    return (index, stack) if index > start else (start + 1, stack)


def _split_plain(html: str, limit: int) -> list[str]:
    """Fallback for input whose tags are already broken: keep the words.

    The markup is unrecoverable, but the text is not, and a plain message the
    chat can read beats a rejected one it cannot.
    """
    text = unescape(_TAG_RE.sub("", html))
    pieces: list[str] = []
    buf: list[str] = []
    used = 0
    for ch in text:
        width = 2 if ord(ch) > 0xFFFF else 1
        # Escaping can inflate one character into six (`&#x27;`), but the
        # budget is on the parsed text, which is what `ch` already is.
        if used + width > limit and buf:
            pieces.append(html_escape("".join(buf)))
            buf, used = [], 0
        buf.append(ch)
        used += width
    if buf:
        pieces.append(html_escape("".join(buf)))
    return pieces or [""]


def split_html(html: str, *, limit: int = DEFAULT_SPLIT_LIMIT) -> list[str]:
    """Split ``html`` into pieces Telegram will accept, preserving markup.

    Returns ``[html]`` unchanged whenever it already fits -- the overwhelming
    majority of messages -- so this cannot perturb the common path. Each piece
    is independently valid: tags open and close within it, entities are never
    cut, and a tag straddling a boundary is closed on one side and re-opened
    on the other.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if parsed_length(html) <= limit:
        return [html]

    items = _tokenize(html)
    if not _well_formed(items):
        logger.warning(
            "Message HTML is not well-formed; splitting as plain text",
            parsed_length=parsed_length(html),
        )
        return _split_plain(html, limit)

    pieces: list[str] = []
    pos = 0
    stack: list[tuple[str, str]] = []
    while pos < len(items):
        # A continuation should not begin with the blank line that ended the
        # previous piece -- but only when nothing is open, because whitespace
        # inside <pre> is content.
        while not stack and pos < len(items) and items[pos].raw in ("\n", " "):
            pos += 1
        if pos >= len(items):
            break
        end, end_stack = _take(items, pos, stack, limit)
        piece = _render(items, pos, end, stack, end_stack)
        # Emptiness must be judged the way Telegram judges it: it TRIMS edge
        # whitespace and *then* rejects an empty body with "text must be
        # non-empty". `parsed_length` counts that whitespace, so gating on it
        # let `<blockquote>\n</blockquote>` through as a piece -- a string with
        # no visible text at all. That rejection matches none of the markers
        # `_send_one` knows, so it re-raises and the handler dies with part one
        # already posted. Reachable from the real producer: prose plus a fenced
        # block ending in blank lines renders as `<pre></pre>`.
        if _visible_text(piece).strip():
            pieces.append(piece)
        pos, stack = end, end_stack

    # Deliberately NOT `pieces or [html]`: if nothing survived the filter there
    # is genuinely nothing to deliver, and returning the original would hand a
    # caller the very body Telegram is about to reject. Callers treat an empty
    # list as "nothing was sent", which is the truth.
    return pieces
