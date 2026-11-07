"""The panel's design rules as code: ``check_screen()`` returns what a screen breaks.

``tests/unit/test_screen_rules.py`` asserts an empty list for every registered
sample in both languages; ``scripts/dump_screens.py`` prints the same list as
warnings. Each rule has a known-bad control in the test, so "no violations"
is evidence that the check can say no, not that it never looks.

Rule ids (stable — a sample's ``exempt`` names them):

R1  at most 3 buttons per row
R2  at most 5 rows, or 6 when one of them is a pager
R3  navigation by screen kind (``Sample.nav``): a panel screen has exactly one
    back, last in the last row (one action may sit before it); a prompt has
    exactly one cancel; a root screen has one ``✖️ Закрыть`` and no back; a
    card or group message has at most one back
R4  a back button says where it goes; never "Назад"/"Back"
R5  first line: emoji, space, ``<b>title</b>`` (a tail after ``</b>`` is fine)
R6  every callback fits in 64 bytes
R7  no callback writes a ``retired`` or ``compat`` prefix (always checked), and
    every other callback reaches a live handler (needs the router, see ``route``)
R8  Russian copy: "вы", no singular imperatives, no nag words
R9  English copy has no Cyrillic, Russian labels no Latin words, outside user data
R10 a label fits its share of a 390 px phone row, as our design renderer draws
    it: the widths are calibrated against that renderer, not every Telegram client
R11 a toggle starts with its state glyph; a link button never does
R12 HTML tags are balanced and known, and the text fits 4096 UTF-16 units
R13 a DM panel screen is recognised by ``custom_emoji`` as a screen
"""

from __future__ import annotations

import inspect
import re
import unicodedata
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Final

from src.bot.custom_emoji import _is_screen
from src.bot.screens.model import Button, Screen
from src.bot.screens.render import render_text, to_markup
from src.bot.screens.routes import (
    BACK_NAMES,
    CALLBACK_LIMIT,
    NOOP,
    ROUTES,
    WRITABLE,
    prefix_of,
    target_of,
)
from src.utils.telegram_text import TELEGRAM_MESSAGE_LIMIT, parsed_length

if TYPE_CHECKING:
    from aiogram import Router

    from src.bot.screens.registry import Sample

RULE_IDS: Final = tuple(f"R{n}" for n in range(1, 14))

RouteFn = Callable[[str], str | None]

# ---------------------------------------------------------------------------
# Copy rules (R8, R9)
# ---------------------------------------------------------------------------

THOU_WORDS: Final = ("ты", "тебя", "тебе", "тобой", "твой", "твоя", "твоё", "твое", "твои")
SINGULAR_IMPERATIVES: Final = (
    "нажми",
    "выбери",
    "введи",
    "отправь",
    "напиши",
    "пришли",
    "открой",
    "добавь",
    "укажи",
    "попробуй",
)
NAG_WORDS: Final = (
    "пожалуйста",
    "обязательно",
    "не забудьте",
    "внимание",
    "успешно",
    "к сожалению",
)


def _words_re(words: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in words)
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


_THOU_RE: Final = _words_re(THOU_WORDS)
_IMPERATIVE_RE: Final = _words_re(SINGULAR_IMPERATIVES)
_NAG_RE: Final = _words_re(NAG_WORDS)
_CYRILLIC_RE: Final = re.compile(r"[А-Яа-яЁё]")
_LATIN_WORD_RE: Final = re.compile(r"(?<![\w@])[A-Za-z]{3,}(?!\w)")
LATIN_ALLOWED: Final = re.compile(r"^(?:OpenAI|English|JSON)$")
"""Latin words allowed in Russian labels (``@handles`` never match `_LATIN_WORD_RE`)."""

_TAG_RE: Final = re.compile(r"<(/?)([A-Za-z][\w-]*)([^>]*)>")
KNOWN_TAGS: Final = frozenset(
    {"b", "i", "code", "pre", "a", "blockquote", "tg-emoji", "u", "s", "tg-spoiler"}
)

_TITLE_RE: Final = re.compile(r"^\S+ <b>[^<]+</b>")
_BACK_WORDS: Final = re.compile(r"^\W*(?:Назад|Back)\W*$", re.IGNORECASE)
STATE_GLYPHS: Final = ("✅", "⚪", "⏸", "▶")
"""``▶`` is the resume half of a pause toggle (a paused rule card shows ``▶ Возобновить``)."""
CLOSE_LABELS: Final = frozenset({"✖️ Закрыть", "✖️ Close"})
CANCEL_LABELS: Final = frozenset({"✖️ Отмена", "✖️ Cancel"})

# ---------------------------------------------------------------------------
# Width estimator (R10)
# ---------------------------------------------------------------------------
#
# Calibrated against our renderer, the design tool that draws a screen at
# 390 px, not against every Telegram client. There a button is 14 px weight-500
# text in `Roboto, 'Helvetica Neue', Arial` with 6 px of padding each side, a row
# is 354 px wide with 3 px gaps, and a label is cut when the button's scrollWidth
# exceeds its clientWidth. Roboto is not installed where it runs, so Chromium
# draws HelveticaNeue-Medium and Apple Color Emoji, and every number below was
# measured there. A phone draws with its own font, so "fits" means "fits the
# mock"; the two R10 controls are labels checked on a real phone, and the mock
# agrees with the phone on both.

_ZWJ: Final = 0x200D
_EMOJI_MAKERS: Final = ("\ufe0f", "\u20e3")
"""VS16 and the keycap ring: the character before either is drawn as an emoji."""
_ZERO_WIDTH: Final = frozenset(
    {0xFE0F, 0x200B, 0x200C, 0x2060, 0x2061, 0x2062, 0x2063, 0x20E3, *range(0x1F3FB, 0x1F400)}
)
"""Drawn into a neighbour: variation selectors, invisible marks, the keycap ring, skin tones."""
_FLAG_LETTERS: Final = range(0x1F1E6, 0x1F200)
"""Regional indicators: two of them draw one flag."""
EMOJI_FROM: Final = 0x2190
EMOJI_PX: Final = 18.0
"""Every colour emoji measured 18.0 px. A character from U+2190 up, or any one
followed by VS16 or the keycap ring, counts as one. The mock draws ``⏸ ▶ ◀ ✓``
as text, 8.8–14 px, so a label holding one reads up to 9 px wide; a phone draws
them as emoji."""
LETTER_PX: Final = 10.5
"""A letter of a script the table lacks (Greek, Armenian, Georgian, Hebrew…).

Armenian, the widest measured, averages 10.1 px a letter; Georgian 9.6, Greek
capitals 9.9. So no measured word reads narrow, and a word in a narrower script
reads wide: Greek lowercase by a third, joined Arabic twice over."""
OTHER_PX: Final = 14.0
"""Any other character missing from the table: one em, as wide as ``… — %``.
The few symbols measured wider than that are in the table."""
ROW_WIDTH_PX: Final = 354
"""Keyboard width on a 390 px phone (the renderer's ``.kb``)."""
ROW_GAP_PX: Final = 3
BUTTON_PADDING_PX: Final = 12

# Measured advance in px of each character, grouped by width: ``x{ch}x`` minus
# ``xx``, so kerning against a neighbour is in it. A space alone measures 0 (the
# browser trims it), which is why the advance is taken between two letters.
# A letter with an accent the table lacks reads as its base letter (``é`` as
# ``e``, measured equal); the Ukrainian and Belarusian letters are measured.
# fmt: off
_ADVANCES: Final[tuple[tuple[float, str], ...]] = (
    (1.94, "\u202f"), (2.8, "\u2009"),
    (3.11, "|"), (3.38, "`ijlіłı"), (3.63, "‹›"), (3.73, "ї"),
    (3.89, " !'(),.:;I‘’·\xa0ІЇ¡"),
    (4.14, "[]{}"), (4.41, "f"), (4.59, "′"), (4.66, "t"), (4.92, "/\\r"),
    (5.17, "*"), (5.44, "-‐"), (5.59, "°"), (5.92, "гґ"),
    (6.22, '"„“”'), (6.48, "«»"), (6.73, "т"),
    (7.0, "_z–•"), (7.27, "svyуў"), (7.52, "Jkxкх"), (7.56, "з"), (7.63, "ья"), (7.67, "в"),
    (7.78, "#$0123456789?aceаесэё₽€§є†‡‒″¿"), (7.89, "ч"),
    (8.03, "LhnuŁßµ"), (8.27, "З"), (8.3, "FTgoГТбоøҐ"), (8.39, "+<=>^~×±¶"), (8.53, "У"),
    (8.55, "Vbdpqийнпр"), (8.67, "đ"), (8.81, "EZЕЁ"), (8.95, "ъ"),
    (9.0, "л"), (9.06, "&SXYХЎ"), (9.09, "ц"), (9.33, "APАБРЬЯ"), (9.41, "Ч"), (9.59, "KК"),
    (9.64, "м"), (9.67, "д"), (9.86, "BRВ"),
    (10.11, "CDHNUИЙНПСЭЄ"), (10.3, "Л"), (10.44, "ФЪ"), (10.55, "Ц"), (10.63, "G"),
    (10.64, "OQОØ"), (10.89, "w"),
    (11.0, "ы"), (11.2, "@©®"), (11.31, "Д"), (11.39, "ю"), (11.67, "ж¼½¾"),
    (12.17, "m"), (12.44, "MМæ"), (12.48, "ш"), (12.69, "œ"),
    (13.02, "щ"), (13.22, "W"), (13.23, "Ы"), (13.48, "Æ"), (13.73, "ф"),
    (14.0, "%—…"), (14.53, "Ю"), (14.7, "Ш"),
    (15.3, "ЖŒ"), (15.45, "Щ"), (15.58, "№"), (15.7, "℃"), (16.06, "‰"), (21.78, "―"),
)
# fmt: on
_ADVANCE_PX: Final[dict[str, float]] = {ch: px for px, chars in _ADVANCES for ch in chars}


def _advance(ch: str) -> float:
    """Advance of a character drawn as text: the table, its base letter, or a fallback."""
    px = _ADVANCE_PX.get(ch)
    if px is not None:
        return px
    base = unicodedata.normalize("NFD", ch)[0]
    if base in _ADVANCE_PX:
        return _ADVANCE_PX[base]
    category = unicodedata.category(ch)
    if category in ("Mn", "Me"):  # a combining mark is drawn over its letter
        return 0.0
    return LETTER_PX if category[0] == "L" or category == "Mc" else OTHER_PX


def label_width_px(label: str) -> float:
    """Rendered width of a button label's text in px, padding excluded.

    The sum of measured advances (``_ADVANCES``, ``EMOJI_PX``, and ``LETTER_PX``
    or ``OTHER_PX`` for what the table lacks); a ZWJ sequence and a flag count
    as one emoji. Over every label the panel draws it is at most 0.2 px narrow,
    and wide by at most 9.2 px, only on ``⏸ ▶ ◀ ✓``; the rules test holds a
    measured table that keeps it so.
    """
    total = 0.0
    joined = False
    flag_open = False
    for i, ch in enumerate(label):
        cp = ord(ch)
        if cp == _ZWJ:
            joined = True
            continue
        if cp in _ZERO_WIDTH:
            continue
        emoji = cp >= EMOJI_FROM or label[i + 1 : i + 2] in _EMOJI_MAKERS
        if joined and emoji:  # drawn into the emoji before the joiner
            joined = False
            continue
        joined = False
        if cp in _FLAG_LETTERS:
            flag_open = not flag_open
            if not flag_open:  # the second letter of a flag
                continue
        else:
            flag_open = False
        total += EMOJI_PX if emoji else _advance(ch)
    return total


def budget(n: int) -> float:
    """Widest label text that is not cut when the row holds ``n`` buttons.

    ``(354 − 3·(n−1)) / n − 12``: the keyboard less its 3 px gaps, split evenly,
    less the button's 12 px padding — 342, 163.5 and 104 px. Searching the
    renderer for the first width it cuts gives 342.03, 163.53 and 104.03.
    """
    return (ROW_WIDTH_PX - ROW_GAP_PX * (n - 1)) / n - BUTTON_PADDING_PX


CLIP_MARGIN_PX: Final = 2.0
"""How far inside its row's budget a label cut at run time stops: the bound the
measured table holds the estimator to (it reads at most 2 px narrow)."""


def clip_room(n: int) -> float:
    """Width a label cut at run time (a chat title, a name) may take when its row
    holds ``n`` buttons. Every clipper uses this, so none of them keeps its own
    margin for an estimator error that no longer exists."""
    return budget(n) - CLIP_MARGIN_PX


# ---------------------------------------------------------------------------
# Routing (R7): walk an aiogram router tree the way the dispatcher does
# ---------------------------------------------------------------------------


def _probe(data: str) -> SimpleNamespace:
    """A stand-in CallbackQuery: the payload, sent from a private chat."""
    return SimpleNamespace(data=data, message=SimpleNamespace(chat=SimpleNamespace(type="private")))


def _passes(filters: Any, event: SimpleNamespace) -> bool:
    for flt in filters or ():
        result = flt.callback(event)
        if inspect.isawaitable(result):
            # IsAdmin, StateFilter…: they need a container or an FSM context the
            # walk does not have. Routing asks "does the payload reach it", so an
            # async filter counts as a pass.
            if inspect.iscoroutine(result):
                result.close()
            continue
        if not result:
            return False
    return True


def first_handler(router: Router, data: str) -> str | None:
    """``"module.function"`` of the first callback handler ``data`` reaches, or None.

    Same order aiogram uses: a router's own handlers, then its sub-routers in
    include order. Only filters are evaluated; nothing is called.
    """
    event = _probe(data)
    observer = router.observers["callback_query"]
    if _passes(observer._handler.filters, event):  # observer-level filters
        for handler in observer.handlers:
            if _passes(handler.filters, event):
                func = handler.callback
                module = getattr(func, "__module__", "?").rsplit(".", 1)[-1]
                return f"{module}.{getattr(func, '__name__', '?')}"
    for sub in router.sub_routers:
        found = first_handler(sub, data)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def _buttons(screen: Screen) -> Iterator[tuple[int, int, Button]]:
    for r, row in enumerate(screen.rows):
        for c, button in enumerate(row):
            yield r, c, button


def _strip_user_data(text: str, user_data: tuple[str, ...]) -> str:
    for item in sorted(user_data, key=len, reverse=True):
        if item:
            text = text.replace(item, " ")
    return text


def _visible(text: str) -> str:
    return _TAG_RE.sub(" ", text)


def _goes_to(button: Button, screen_id: str) -> str | None:
    if button.goes_to is not None:
        return button.goes_to
    if button.callback is not None:
        return target_of(button.callback, screen_id)
    return None


def _check_html(text: str) -> list[str]:
    problems: list[str] = []
    stack: list[str] = []
    for closing, name, _attrs in _TAG_RE.findall(text):
        tag = name.lower()
        if tag not in KNOWN_TAGS:
            problems.append(f"R12: unknown tag <{tag}>")
            continue
        if not closing:
            stack.append(tag)
        elif not stack or stack[-1] != tag:
            problems.append(f"R12: </{tag}> closes {stack[-1] if stack else 'nothing'}")
            return problems
        else:
            stack.pop()
    if stack:
        problems.append(f"R12: unclosed {', '.join(f'<{t}>' for t in stack)}")
    return problems


def check_screen(
    screen: Screen,
    sample: Sample | None,
    lang: str,
    *,
    route: RouteFn | None = None,
) -> list[str]:
    """Every rule ``screen`` breaks, as ``"R<n>: what"`` strings; [] when clean.

    ``sample`` supplies the navigation kind, user data and exemptions; without
    one the screen is checked as a ``panel`` with no exemptions. R7's routing
    half runs only when ``route`` (payload → handler name or None) is given;
    its other half — a ``retired`` or ``compat`` prefix is never written —
    reads `ROUTES` and runs always, so its verdict does not depend on which
    areas have deleted which handlers yet.
    """
    nav = sample.nav if sample is not None else "panel"
    user_data = sample.user_data if sample is not None else ()
    exempt = dict(sample.exempt) if sample is not None else {}
    found: list[str] = []
    buttons = list(_buttons(screen))
    labels = [b.label for _, _, b in buttons] + [
        rb.label for row in (screen.reply or ()) for rb in row
    ]

    # R1, R2 ---------------------------------------------------------------
    all_rows: list[tuple[Any, ...]] = [*screen.rows, *(screen.reply or ())]
    for r, row in enumerate(all_rows):
        if len(row) > 3:
            found.append(f"R1: row {r + 1} has {len(row)} buttons")
    has_pager = any(b.kind == "pager" for _, _, b in buttons)
    cap = 6 if has_pager else 5
    if len(all_rows) > cap:
        found.append(f"R2: {len(all_rows)} rows, limit {cap}")

    # R3 -------------------------------------------------------------------
    backs = [(r, c, b) for r, c, b in buttons if b.kind == "back"]
    if nav == "panel":
        last = len(screen.rows) - 1
        if len(backs) != 1:
            found.append(f"R3: panel screen has {len(backs)} back buttons, needs 1")
        else:
            r, c, _ = backs[0]
            if r != last or c != len(screen.rows[last]) - 1:
                found.append("R3: back is not the last button of the last row")
            elif len(screen.rows[last]) > 2:
                found.append("R3: the back row holds more than one other button")
    elif nav == "prompt":
        cancels = sum(b.kind == "cancel" for _, _, b in buttons) + sum(
            rb.label in CANCEL_LABELS for row in (screen.reply or ()) for rb in row
        )
        if cancels != 1:
            found.append(f"R3: prompt has {cancels} cancel buttons, needs 1")
    elif nav == "root":
        exits = [b for _, _, b in buttons if b.kind == "exit"]
        if len(exits) != 1 or exits[0].label not in CLOSE_LABELS:
            found.append("R3: root screen needs exactly one ✖️ Закрыть exit")
        if backs:
            found.append("R3: root screen has a back button")
    elif len(backs) > 1:
        found.append(f"R3: {nav} has {len(backs)} back buttons, at most 1")

    # R4 -------------------------------------------------------------------
    for _, _, b in buttons:
        tail = b.label.split(" ", 1)[1] if " " in b.label else b.label
        if _BACK_WORDS.match(b.label) or _BACK_WORDS.match(tail):
            found.append(f"R4: {b.label!r} — a back button names its destination")
            continue
        if b.kind != "back":
            continue
        if not b.label.startswith("⬅️ ") or not tail.strip():
            found.append(f"R4: back label {b.label!r} is not '⬅️ <where>'")
            continue
        where = _goes_to(b, screen.id)
        if where in BACK_NAMES and tail != BACK_NAMES[where](lang):
            found.append(f"R4: back to {where} says {tail!r}, not {BACK_NAMES[where](lang)!r}")

    # R5 -------------------------------------------------------------------
    first_line = screen.text.split("\n", 1)[0]
    if not _TITLE_RE.match(first_line):
        found.append(f"R5: first line {first_line[:40]!r} is not 'emoji <b>title</b>'")

    # R6, R7 ---------------------------------------------------------------
    for _, _, b in buttons:
        if b.callback is None:
            continue
        size = len(b.callback.encode("utf-8"))
        if size > CALLBACK_LIMIT:
            found.append(f"R6: {b.callback!r} is {size} bytes")
        prefix = prefix_of(b.callback)
        status = ROUTES[prefix].status if prefix is not None else None
        if status is not None and status not in WRITABLE:
            # Judged by the table, not by the router: today a retired prefix may
            # still have its handler, tomorrow its area deletes it. A verdict
            # that flips with someone else's merge is not a rule (see `route`).
            found.append(f"R7: {b.callback!r} writes a {status} prefix")
        elif route is not None and b.callback != NOOP:
            handler = route(b.callback)
            if handler is None or handler.startswith("stale."):
                found.append(f"R7: {b.callback!r} reaches {handler or 'no handler'}")

    # R8, R9 ---------------------------------------------------------------
    body = _strip_user_data(_visible(render_text(screen)), user_data)
    label_text = [_strip_user_data(label, user_data) for label in labels]
    if lang == "ru":
        for where, text in [("text", body), *(("label", t) for t in label_text)]:
            for name, pattern in (
                ("ты-form", _THOU_RE),
                ("singular imperative", _IMPERATIVE_RE),
                ("nag word", _NAG_RE),
            ):
                hit = pattern.search(text)
                if hit:
                    found.append(f"R8: {where} has {name} {hit.group(0)!r}")
        for label in label_text:
            for word in _LATIN_WORD_RE.findall(label):
                if not LATIN_ALLOWED.match(word):
                    found.append(f"R9: ru label has Latin word {word!r}")
    else:
        for where, text in [("text", body), *(("label", t) for t in label_text)]:
            hit = _CYRILLIC_RE.search(text)
            if hit:
                found.append(f"R9: en {where} has Cyrillic near {text.strip()[:30]!r}")

    # R10 ------------------------------------------------------------------
    for row in screen.rows:
        limit = budget(len(row))
        for b in row:
            width = label_width_px(b.label)
            if width > limit:
                found.append(f"R10: {b.label!r} ≈{width:.1f}px > {limit:.1f}px")

    # R11 ------------------------------------------------------------------
    for _, _, b in buttons:
        starts_with_state = b.label.startswith(STATE_GLYPHS)
        if b.url is not None and starts_with_state:
            found.append(f"R11: link button {b.label!r} starts with a state glyph")
        prefix = prefix_of(b.callback) if b.callback else None
        if prefix is not None and ROUTES[prefix].toggle and not starts_with_state:
            found.append(f"R11: toggle {b.label!r} does not start with ✅/⚪/⏸/▶")

    # R12 ------------------------------------------------------------------
    full = render_text(screen)
    found.extend(_check_html(full))
    length = parsed_length(full)
    if length > TELEGRAM_MESSAGE_LIMIT:
        found.append(f"R12: {length} UTF-16 units, limit {TELEGRAM_MESSAGE_LIMIT}")

    # R13 ------------------------------------------------------------------
    if nav in ("panel", "root", "prompt") and not _is_screen(to_markup(screen)):
        found.append("R13: custom_emoji does not treat this markup as a screen")

    return [v for v in found if v.split(":", 1)[0] not in exempt]
