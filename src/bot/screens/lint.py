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
R7  every callback reaches a live handler (needs the real router, see ``route``)
R8  Russian copy: "вы", no singular imperatives, no nag words
R9  English copy has no Cyrillic, Russian labels no Latin words, outside user data
R10 a label fits its share of a 390 px phone row
R11 a toggle starts with its state glyph; a link button never does
R12 HTML tags are balanced and known, and the text fits 4096 UTF-16 units
R13 a DM panel screen is recognised by ``custom_emoji`` as a screen
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Final

from src.bot.custom_emoji import _is_screen
from src.bot.screens.model import Button, Screen
from src.bot.screens.render import render_text, to_markup
from src.bot.screens.routes import BACK_NAMES, CALLBACK_LIMIT, NOOP, ROUTES, prefix_of, target_of
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
STATE_GLYPHS: Final = ("✅", "⚪", "⏸")
CLOSE_LABELS: Final = frozenset({"✖️ Закрыть", "✖️ Close"})
CANCEL_LABELS: Final = frozenset({"✖️ Отмена", "✖️ Cancel"})

# ---------------------------------------------------------------------------
# Width estimator (R10)
# ---------------------------------------------------------------------------

_ZERO_WIDTH: Final = frozenset({0xFE0F, 0x200D, 0x200B, 0x200C, 0x2060, 0x2061, 0x2062, 0x2063})
_NARROW_PUNCT: Final = frozenset(".,:;·'\"!?")
ROW_WIDTH_PX: Final = 354
"""Usable keyboard width on a 390 px phone (render.py ``.k``, 14 px Roboto)."""


def _char_px(ch: str) -> float:
    cp = ord(ch)
    if cp in _ZERO_WIDTH:
        return 0.0
    if cp >= 0x2190:
        return 17.5
    if ch == " ":
        return 3.6
    if ch in _NARROW_PUNCT:
        return 3.8
    if ch.isdigit():
        return 7.8
    if "А" <= ch <= "я" or ch in "Ёё":
        return 9.6 if ch.isupper() else 7.6
    if "A" <= ch <= "Z":
        return 9.2
    if "a" <= ch <= "z":
        return 7.0
    return 7.0


def label_width_px(label: str) -> float:
    """Estimated rendered width of a button label's text, padding excluded.

    Calibrated (assumed values) against two labels only — see the two R10
    controls in the rules test. Three-per-row rows are not calibrated; the
    rendered PNG is the arbiter there.
    """
    return sum(_char_px(ch) for ch in label)


def budget(n: int) -> float:
    """Text width a label may take when it shares a row with ``n - 1`` others.

    ``(354 − 4·(n−1)) / n − 12``: row width minus the 4 px gaps, split evenly,
    minus the button's own 12 px padding.
    """
    return (ROW_WIDTH_PX - 4 * (n - 1)) / n - 12


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
    one the screen is checked as a ``panel`` with no exemptions. R7 runs only
    when ``route`` (payload → handler name or None) is given.
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
        if route is not None and b.callback != NOOP:
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
                found.append(f"R10: {b.label!r} ≈{width:.0f}px > {limit:.0f}px")

    # R11 ------------------------------------------------------------------
    for _, _, b in buttons:
        starts_with_state = b.label.startswith(STATE_GLYPHS)
        if b.url is not None and starts_with_state:
            found.append(f"R11: link button {b.label!r} starts with a state glyph")
        prefix = prefix_of(b.callback) if b.callback else None
        if prefix is not None and ROUTES[prefix].toggle and not starts_with_state:
            found.append(f"R11: toggle {b.label!r} does not start with ✅/⚪/⏸")

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
