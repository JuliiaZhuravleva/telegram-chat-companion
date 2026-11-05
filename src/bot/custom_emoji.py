"""The bot's own drawn emoji on its own screens; every other message untouched.

``CustomEmoji`` is a SESSION request middleware, so it sees every outgoing call. It rewrites a
call only when the module is on and the message is a *screen*: its inline keyboard carries a
callback of the admin panel, the rules editor, help or the knowledge base
(``SCREEN_PREFIXES``). Model replies, transcripts, summaries and greetings carry no such
keyboard and go out byte for byte as before.

On a screen:

- a callback button whose label starts with a glyph of the BUTTONS set and a space gets that
  picture as ``icon_custom_emoji_id`` and loses the glyph. A label that is only a glyph keeps
  it (a client without icons would show an empty button); URL buttons are left alone;
- in HTML text, a glyph of the MARKS set at the start of a line (after opening tags) becomes
  ``<tg-emoji>`` with the glyph as its fallback. Glyphs mid-line, and anything inside
  ``<blockquote>``, ``<code>`` or ``<pre>``, stay plain: that is where chat titles, quoted
  prompts and other people's text live. At most ``MAX_PER_MESSAGE`` per message.

Labels built from data (chat titles, names, a sticker's own emoji) are passed through
``fence()`` by the keyboard that builds them; a fenced label is never given an icon, and the
fence is removed from every message, on or off.

Sets are read once at startup with getStickerSet from ``CUSTOM_EMOJI_BUTTONS`` and
``CUSTOM_EMOJI_MARKS``. A set belongs to the bot that uploaded it, so the dev bot and the
production bot each need their own copy. Unset or unreadable: the module is off. A send
Telegram refuses because of a custom emoji is resent plain once and the module turns off
until restart.
"""

from __future__ import annotations

import re
from typing import Any, Final

import structlog
from aiogram import Bot
from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods import Response, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = structlog.get_logger(__name__)

MAX_PER_MESSAGE: Final = 100  # Telegram's cap on custom emoji in one message
SCREEN_PREFIXES: Final = ("adm_", "ar_", "help_", "kb_")
FENCE: Final = "⁣"  # INVISIBLE SEPARATOR: "this label is data, not a mark"
_VS16: Final = "️"
# Glyphs the screens use for a meaning the set draws under another glyph.
ALIASES: Final = {"◀": "⬅", "🔄": "🔁", "⚫": "⚪", "📝": "✏"}
_REFUSAL_WORDS: Final = ("document_invalid", "emoji", "icon", "can't parse entities")
_TAG: Final = re.compile(r"(<[^>]*>)")
_RAW_TAGS: Final = frozenset({"blockquote", "code", "pre", "tg-emoji", "a"})

Table = dict[str, str]


def fence(label: str) -> str:
    """Mark a button label built from data so a leading emoji in it is never made an icon."""
    # Every glyph of the sets sits above U+2000; letters, digits and "-100…" ids do not.
    return FENCE + label if label and ord(label[0]) >= 0x2000 and not label[0].isalpha() else label


def _strip(glyph: str) -> str:
    return glyph.replace(_VS16, "")


class CustomEmoji(BaseRequestMiddleware):
    def __init__(self) -> None:
        self._buttons: Table = {}
        self._marks: Table = {}
        self._lead: re.Pattern[str] | None = None
        self._line: re.Pattern[str] | None = None
        self.on = False
        self.off_reason = "unset"

    async def load(self, bot: Bot, buttons_set: str | None, marks_set: str | None) -> None:
        """Read both sets; any failure leaves the module off with a reason, never stops the bot."""
        if not buttons_set and not marks_set:
            self._off("unset")
            return
        tables: list[Table] = []
        for name in (buttons_set, marks_set):
            table: Table = {}
            if name:
                try:
                    found = await bot.get_sticker_set(name=name)
                except TelegramAPIError as exc:
                    self._off(f"{name}: {exc.message}")
                    return
                except Exception as exc:  # noqa: BLE001 - the bot starts with plain emoji
                    self._off(f"{name}: {type(exc).__name__}")
                    return
                for sticker in found.stickers:
                    glyph = _strip(sticker.emoji or "")
                    if glyph and sticker.custom_emoji_id:
                        table.setdefault(glyph, sticker.custom_emoji_id)
            for alias, target in ALIASES.items():
                if target in table:
                    table.setdefault(alias, table[target])
            tables.append(table)
        self._buttons, self._marks = tables
        if not self._buttons and not self._marks:
            self._off("the sets hold no custom emoji")
            return
        self._lead = self._pattern(self._buttons, r"^({}){}?+ (?=\S)")
        self._line = self._pattern(self._marks, r"^([ \t]*)({}){}?+")
        self.on = True
        self.off_reason = ""
        logger.info("custom_emoji_on", buttons=len(self._buttons), marks=len(self._marks))

    @staticmethod
    def _pattern(table: Table, shape: str) -> re.Pattern[str] | None:
        if not table:
            return None
        glyphs = "|".join(re.escape(g) for g in sorted(table, key=len, reverse=True))
        return re.compile(shape.format(glyphs, _VS16), re.MULTILINE)

    def _off(self, reason: str) -> None:
        self._buttons, self._marks, self._lead, self._line = {}, {}, None, None
        self.on = False
        self.off_reason = reason[:200]
        if reason != "unset":
            logger.warning("custom_emoji_off", reason=self.off_reason)

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        plain = self._rewrite(bot, method, rich=False)
        if not self.on or not _is_screen(getattr(method, "reply_markup", None)):
            return await make_request(bot, plain)
        rich = self._rewrite(bot, method, rich=True)
        if rich is plain:
            return await make_request(bot, plain)
        try:
            return await make_request(bot, rich)
        except TelegramBadRequest as exc:
            if not any(word in exc.message.lower() for word in _REFUSAL_WORDS):
                raise
            response = await make_request(bot, plain)
            self._off(f"refused: {exc.message}")
            return response

    # ── the rewrite ──────────────────────────────────────────────────────

    def _rewrite(
        self, bot: Bot, method: TelegramMethod[TelegramType], *, rich: bool
    ) -> TelegramMethod[TelegramType]:
        update: dict[str, Any] = {}
        if rich:
            parse_mode = getattr(method, "parse_mode", None)
            if isinstance(parse_mode, Default):
                parse_mode = bot.default[parse_mode.name] if bot.default else None
            if parse_mode == "HTML":
                for field in ("text", "caption"):
                    value = getattr(method, field, None)
                    if isinstance(value, str):
                        new = self._text(value)
                        if new != value:
                            update[field] = new
        markup = getattr(method, "reply_markup", None)
        if isinstance(markup, InlineKeyboardMarkup):
            rows = [[self._button(b, rich=rich) for b in row] for row in markup.inline_keyboard]
            if rows != markup.inline_keyboard:
                update["reply_markup"] = markup.model_copy(update={"inline_keyboard": rows})
        return method.model_copy(update=update) if update else method

    def _text(self, text: str) -> str:
        if self._line is None:
            return text
        left = MAX_PER_MESSAGE
        out: list[str] = []
        raw = 0
        at_line_start = True
        for part in _TAG.split(text):
            if part.startswith("<"):
                name = part.strip("</>").split(maxsplit=1)[0].lower() if part.strip("</>") else ""
                if name in _RAW_TAGS:
                    raw = max(0, raw + (-1 if part.startswith("</") else 1))
                out.append(part)
                continue
            if not part:
                continue
            if raw == 0 and left > 0:
                lines = part.split("\n")
                for i, line in enumerate(lines):
                    if (i > 0 or at_line_start) and left > 0:
                        new, n = self._line.subn(self._swap, line, count=1)
                        left -= n
                        lines[i] = new
                part = "\n".join(lines)
            at_line_start = part.endswith("\n")
            out.append(part)
        return "".join(out)

    def _swap(self, match: re.Match[str]) -> str:
        indent, glyph = match.group(1), match.group(2)
        fallback = match.group(0)[len(indent) :]
        return f'{indent}<tg-emoji emoji-id="{self._marks[glyph]}">{fallback}</tg-emoji>'

    def _button(self, button: InlineKeyboardButton, *, rich: bool) -> InlineKeyboardButton:
        text = button.text
        if text.startswith(FENCE):
            return button.model_copy(update={"text": text[len(FENCE) :]})
        if not rich or self._lead is None or button.url or button.icon_custom_emoji_id:
            return button
        lead = self._lead.match(text)
        if lead is None:
            return button
        return button.model_copy(
            update={
                "text": text[lead.end() :],
                "icon_custom_emoji_id": self._buttons[lead.group(1)],
            }
        )


def _is_screen(markup: object) -> bool:
    if not isinstance(markup, InlineKeyboardMarkup):
        return False
    return any(
        (b.callback_data or "").startswith(SCREEN_PREFIXES)
        for row in markup.inline_keyboard
        for b in row
    )
