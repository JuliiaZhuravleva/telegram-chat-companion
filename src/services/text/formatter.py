"""Markdown → Telegram-safe HTML converter.

ADR-012: Convert a subset of Markdown to Telegram-compatible HTML tags.
Handles: headings, bold, italic, code, pre, strikethrough, blockquote, lists.
Escapes all raw HTML entities first, then applies conversions.
"""

from __future__ import annotations

import re


def markdown_to_html(text: str) -> str:
    """Convert Markdown-formatted text to Telegram-compatible HTML.

    Supported conversions:
    - ``# text``, ``## text``, ``### text`` → ``▎<b>text</b>``
    - ``**text**`` or ``__text__`` → ``<b>text</b>``
    - ``*text*`` or ``_text_`` → ``<i>text</i>``
    - `` `text` `` → ``<code>text</code>``
    - ````text```` → ``<pre>text</pre>``
    - ``~~text~~`` → ``<s>text</s>``
    - ``> text`` (line start) → ``<blockquote>text</blockquote>``
    - ``- text`` → ``• text``
    - ``---`` or ``***`` → (stripped)
    """
    # 1. Escape HTML entities (must be first)
    text = _escape_html(text)

    # 2. Code blocks (``` ... ```) — protect from further processing
    code_blocks: list[str] = []
    text = _extract_code_blocks(text, code_blocks)

    # 3. Inline code (` ... `)
    inline_codes: list[str] = []
    text = _extract_inline_code(text, inline_codes)

    # 4. Bold: **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)

    # 5. Italic: *text* or _text_ (word-boundary aware to avoid conflicts)
    text = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<i>\1</i>", text)

    # 6. Strikethrough: ~~text~~
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)

    # 7. Blockquote: > at line start
    text = _convert_blockquotes(text)

    # 8. Headings: # / ## / ### at line start → ▎<b>text</b>
    text = _convert_headings(text)

    # 9. Lists: - item → • item; horizontal rules → blank line
    text = _convert_lists(text)

    # 10. Restore inline code
    text = _restore_inline_code(text, inline_codes)

    # 11. Restore code blocks
    text = _restore_code_blocks(text, code_blocks)

    return text


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


_CODE_BLOCK_RE = re.compile(r"```(?:\w*\n)?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")

_CODE_BLOCK_PLACEHOLDER = "\x00CB{}\x00"
_INLINE_CODE_PLACEHOLDER = "\x00IC{}\x00"


def _extract_code_blocks(text: str, store: list[str]) -> str:
    """Replace code blocks with placeholders to protect from processing."""

    def _replacer(match: re.Match[str]) -> str:
        idx = len(store)
        store.append(match.group(1))
        return _CODE_BLOCK_PLACEHOLDER.format(idx)

    return _CODE_BLOCK_RE.sub(_replacer, text)


def _extract_inline_code(text: str, store: list[str]) -> str:
    """Replace inline code with placeholders."""

    def _replacer(match: re.Match[str]) -> str:
        idx = len(store)
        store.append(match.group(1))
        return _INLINE_CODE_PLACEHOLDER.format(idx)

    return _INLINE_CODE_RE.sub(_replacer, text)


def _restore_code_blocks(text: str, store: list[str]) -> str:
    """Restore code block placeholders with <pre> tags."""
    for idx, content in enumerate(store):
        text = text.replace(
            _CODE_BLOCK_PLACEHOLDER.format(idx),
            f"<pre>{content}</pre>",
        )
    return text


def _restore_inline_code(text: str, store: list[str]) -> str:
    """Restore inline code placeholders with <code> tags."""
    for idx, content in enumerate(store):
        text = text.replace(
            _INLINE_CODE_PLACEHOLDER.format(idx),
            f"<code>{content}</code>",
        )
    return text


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
_HRULE_RE = re.compile(r"^[-*]{3,}\s*$", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^(\s*)[-*]\s+(.+)$", re.MULTILINE)


def _convert_headings(text: str) -> str:
    """Convert ``# text`` / ``## text`` / ``### text`` → ``▎<b>text</b>``."""
    return _HEADING_RE.sub(r"▎<b>\2</b>", text)


def _convert_lists(text: str) -> str:
    """Convert ``- item`` → ``• item`` and strip horizontal rules."""
    text = _HRULE_RE.sub("", text)
    text = _LIST_ITEM_RE.sub(r"\1• \2", text)
    return text


def _convert_blockquotes(text: str) -> str:
    """Convert consecutive ``> `` lines into a single <blockquote>."""
    lines = text.split("\n")
    result: list[str] = []
    quote_buffer: list[str] = []

    for line in lines:
        # &gt; because we already escaped >
        stripped = line.lstrip()
        if stripped.startswith("&gt; "):
            quote_buffer.append(stripped[5:])
        elif stripped == "&gt;":
            quote_buffer.append("")
        else:
            if quote_buffer:
                result.append(f"<blockquote>{chr(10).join(quote_buffer)}</blockquote>")
                quote_buffer = []
            result.append(line)

    if quote_buffer:
        result.append(f"<blockquote>{chr(10).join(quote_buffer)}</blockquote>")

    return "\n".join(result)
