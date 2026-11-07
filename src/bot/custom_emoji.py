"""The bot's own drawn emoji on its own screens; every other message untouched.

``CustomEmoji`` is a SESSION request middleware, so it sees every outgoing call. It rewrites a
call only when the module is on and the message is a *screen*: its inline keyboard carries a
callback of the admin panel, the rules editor, help or the knowledge base
(``SCREEN_PREFIXES``), or it opens or closes a reply keyboard — the bot shows one only in an
admin prompt (the organizer picker), and a prompt with its answers is a screen too. Model replies, transcripts, summaries and greetings carry no such
keyboard and go out byte for byte as before (only the fence below is removed, and only the bot
ever writes it).

On a screen:

- a callback button whose label starts with a glyph of the BUTTONS set and a space gets that
  picture as ``icon_custom_emoji_id`` and loses the glyph; so does a numbered row button
  ``"3 ❌"``, which keeps only its number. A label that is only a glyph keeps
  it (a client without icons would show an empty button); URL buttons are left alone;
- a reply-keyboard button is treated the same way. Its text is what the client SENDS on a tap,
  so the handler matching it must accept the label with and without the glyph
  (``bare_label``) — the glyph is gone when the module is on and present when it is off;
- in HTML text, a glyph of the MARKS set at the start of a line (after opening tags) becomes
  ``<tg-emoji>`` with the glyph as its fallback. Glyphs mid-line, and anything inside
  ``<blockquote>``, ``<code>`` or ``<pre>``, stay plain: that is where chat titles, quoted
  prompts and other people's text live. At most ``MAX_PER_MESSAGE`` per message.

Labels built from data (chat titles, rule names, a sticker's own emoji) are passed through
``fence()`` where they are built: in a keyboard, or at the start of a line of a screen's text. A
fenced label is never given an icon or a mark, and the fence is removed from every message, on
or off.

Sets are read once at startup with getStickerSet from ``CUSTOM_EMOJI_BUTTONS`` and
``CUSTOM_EMOJI_MARKS``. A set belongs to the bot that uploaded it, so the dev bot and the
production bot each need their own copy. Unset or unreadable: the module is off. A send
Telegram refuses because of a custom emoji is resent plain once and logged
(``custom_emoji_refused``). One refusal is one message's fault (a glyph edited out of the set
while the bot runs, a mark Telegram will not take where it landed), so the module stays on for
every other screen; ``REFUSALS_TO_OFF`` refusals in a row mean it is the account or the sets,
and the module turns off until restart. A rich send that goes through starts the count again.
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
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

logger = structlog.get_logger(__name__)

MAX_PER_MESSAGE: Final = 100  # Telegram's cap on custom emoji in one message
SCREEN_PREFIXES: Final = ("adm_", "ar_", "help_", "kb_")
FENCE: Final = "⁣"  # INVISIBLE SEPARATOR: "this label is data, not a mark"
_VS16: Final = "️"
# Glyphs the screens use for a meaning the set draws under another glyph.
ALIASES: Final = {"◀": "⬅", "🔄": "🔁", "⚫": "⚪", "📝": "✏"}
_REFUSAL_WORDS: Final = ("document_invalid", "emoji", "icon", "can't parse entities")
REFUSALS_TO_OFF: Final = 3
"""Rich sends refused in a row before the module turns off; one is one message's fault."""
_TAG: Final = re.compile(r"(<[^>]*>)")
_RAW_TAGS: Final = frozenset({"blockquote", "code", "pre", "tg-emoji", "a"})

Table = dict[str, str]


def fence(label: str) -> str:
    """Mark a button label built from data so a leading emoji in it is never made an icon."""
    # Every glyph of the sets sits above U+2000; letters, digits and "-100…" ids do not.
    return FENCE + label if label and ord(label[0]) >= 0x2000 and not label[0].isalpha() else label


def bare_label(text: str) -> str:
    """A button label without its leading glyph: what a reply button sends once it has an icon.

    ``"✖️ Отмена"`` and ``"Отмена"`` both give ``"Отмена"``. Compare a reply-keyboard tap
    through this, never the raw label.
    """
    i = 0
    while i < len(text) and ord(text[i]) >= 0x2000 and not text[i].isalnum():
        i += 1
    return text[i:].lstrip() if i else text


def _strip(glyph: str) -> str:
    return glyph.replace(_VS16, "")


class CustomEmoji(BaseRequestMiddleware):
    def __init__(self) -> None:
        self._buttons: Table = {}
        self._marks: Table = {}
        self._lead: re.Pattern[str] | None = None
        self._tail: re.Pattern[str] | None = None
        self._line: re.Pattern[str] | None = None
        self.on = False
        self.off_reason = "unset"
        self.refused_in_a_row = 0

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
        # "3 ❌": a row number and an action glyph; the glyph becomes the icon, the number stays.
        self._tail = self._pattern(self._buttons, r"^\d+ ({}){}?+$")
        self._line = self._pattern(self._marks, r"^([ \t]*)({}){}?+")
        self.on = True
        self.off_reason = ""
        self.refused_in_a_row = 0
        logger.info("custom_emoji_on", buttons=len(self._buttons), marks=len(self._marks))

    @staticmethod
    def _pattern(table: Table, shape: str) -> re.Pattern[str] | None:
        if not table:
            return None
        glyphs = "|".join(re.escape(g) for g in sorted(table, key=len, reverse=True))
        return re.compile(shape.format(glyphs, _VS16), re.MULTILINE)

    def _off(self, reason: str) -> None:
        self._buttons, self._marks, self._lead, self._line = {}, {}, None, None
        self._tail = None
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
            response = await make_request(bot, rich)
        except TelegramBadRequest as exc:
            if not any(word in exc.message.lower() for word in _REFUSAL_WORDS):
                raise
            self._refused(method, exc.message)
            return await make_request(bot, plain)
        self.refused_in_a_row = 0
        return response

    def _refused(self, method: TelegramMethod[Any], reason: str) -> None:
        """Count a rich send Telegram refused and the plain resend took; off at the limit."""
        self.refused_in_a_row += 1
        logger.warning(
            "custom_emoji_refused",
            method=method.__api_method__,
            reason=reason[:200],
            in_a_row=self.refused_in_a_row,
        )
        if self.refused_in_a_row >= REFUSALS_TO_OFF:
            self._off(f"refused {self.refused_in_a_row} times in a row: {reason}")

    # ── the rewrite ──────────────────────────────────────────────────────

    def _rewrite(
        self, bot: Bot, method: TelegramMethod[TelegramType], *, rich: bool
    ) -> TelegramMethod[TelegramType]:
        update: dict[str, Any] = {}
        html = False
        if rich:
            parse_mode = getattr(method, "parse_mode", None)
            if isinstance(parse_mode, Default):
                parse_mode = bot.default[parse_mode.name] if bot.default else None
            html = parse_mode == "HTML"
        for field in ("text", "caption"):
            value = getattr(method, field, None)
            if isinstance(value, str):
                # Swap first: a fenced line no longer starts with a glyph, so it stays plain.
                new = (self._text(value) if html else value).replace(FENCE, "")
                if new != value:
                    update[field] = new
        markup = getattr(method, "reply_markup", None)
        if isinstance(markup, InlineKeyboardMarkup):
            rows = [[self._button(b, rich=rich) for b in row] for row in markup.inline_keyboard]
            if rows != markup.inline_keyboard:
                update["reply_markup"] = markup.model_copy(update={"inline_keyboard": rows})
        elif isinstance(markup, ReplyKeyboardMarkup) and rich:
            reply_rows = [[self._reply_button(b) for b in row] for row in markup.keyboard]
            if reply_rows != markup.keyboard:
                update["reply_markup"] = markup.model_copy(update={"keyboard": reply_rows})
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
        if lead is not None:
            label, glyph = text[lead.end() :], lead.group(1)
        else:
            tail = self._tail.match(text) if self._tail is not None else None
            if tail is None:
                return button
            label, glyph = text[: tail.start(1)].rstrip(), tail.group(1)
        return button.model_copy(
            update={"text": label, "icon_custom_emoji_id": self._buttons[glyph]}
        )

    def _reply_button(self, button: KeyboardButton | str) -> KeyboardButton | str:
        # Only the leading-glyph form: a reply button has no row numbers.
        if isinstance(button, str) or self._lead is None or button.icon_custom_emoji_id:
            return button
        lead = self._lead.match(button.text)
        if lead is None:
            return button
        return button.model_copy(
            update={
                "text": button.text[lead.end() :],
                "icon_custom_emoji_id": self._buttons[lead.group(1)],
            }
        )


def _is_screen(markup: object) -> bool:
    if isinstance(markup, ReplyKeyboardMarkup | ReplyKeyboardRemove):
        return True
    if not isinstance(markup, InlineKeyboardMarkup):
        return False
    return any(
        (b.callback_data or "").startswith(SCREEN_PREFIXES)
        for row in markup.inline_keyboard
        for b in row
    )
