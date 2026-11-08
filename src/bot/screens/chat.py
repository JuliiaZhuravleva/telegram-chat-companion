"""The chat list, one chat's root screen and its sections, as `Screen` data.

Pure builders: everything a screen shows arrives in a `ChatView` (or a list of
rows for the chat list), so the samples and the rules test draw the same
screens the handlers send, without a database. The handlers in
``handlers/admin_chat_panel.py`` read the repositories and call these.

Which setting lives in which section is ``sections.SECTION_OF``. `LABELS` and
`COMMUNITY_LINES` are the one set of names a setting has: the screen of
defaults for all chats (``screens/settings.py``) reads them too, so a switch is
called the same on both (tested). ``FieldSpec.label`` is not shown on either.

A button shows the value that is in force, and only that: ✅ or ⚪ and the
name. Where the value comes from is said once, in plain text under the screen
(«Берутся из общих настроек: …»), never on the button — «⚪ … · общее» read as
"off, or not set?". Which values say it is `sections.shows_common` and nothing
else: a legacy column always holds the chat's own value, so it never does.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from html import escape
from typing import Any, Final, Literal

from src.bot.custom_emoji import fence
from src.bot.screens.i18n import Tr
from src.bot.screens.lint import clip_room, label_width_px
from src.bot.screens.model import Button, Screen
from src.bot.screens.orgs import UNDO
from src.bot.screens.render import back, cancel, pager
from src.bot.screens.routes import cb
from src.bot.screens.sections import Section, shows_common
from src.bot.settings_fields import FIELDS_BY_CODE
from src.utils.telegram import build_chat_url

# ---------------------------------------------------------------------------
# Data a chat screen reads
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChatView:
    """One chat as its screens see it."""

    chat_id: int
    title: str | None
    chat_type: str | None
    values: Mapping[str, Any]
    """Effective value per ``ChatConfig`` key (plus ``enabled``)."""
    row: Mapping[str, Any] = field(default_factory=dict)
    """The raw ``chat_settings`` columns; ``{}`` when the chat has no row."""
    rules: int = 0
    organizers: int = 0
    bot_is_admin: bool | None = None
    """Live answer for the reactions section; None when it was not asked."""

    @property
    def name(self) -> str:
        return self.title or str(self.chat_id)

    def on(self, code: str) -> bool:
        return bool(self.values.get(FIELDS_BY_CODE[code].key))

    def value(self, code: str) -> Any:
        return self.values.get(FIELDS_BY_CODE[code].key)


def config_values(config: Any, **overrides: Any) -> dict[str, Any]:
    """A ``ChatConfig``'s fields as the ``values`` a `ChatView` reads, with overrides."""
    values = {f.name: getattr(config, f.name) for f in fields(config)}
    values.update(overrides)
    return values


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

LABELS: Final[dict[str, Tr]] = {
    "rg": Tr("Встревать только по делу", "Join in only when useful"),
    "af": Tr("Защита от злоупотреблений", "Abuse protection"),
    "lc": Tr("Читать ссылки", "Read links"),
    "sm": Tr("Сохранять сообщения", "Save messages"),
    "ch": Tr("Искать по архиву чата", "Search the chat archive"),
    "rag": Tr("Помнить свои диалоги", "Remember its conversations"),
    "kb": Tr("База знаний", "Knowledge base"),
    "tv": Tr("Голосовые", "Voice"),
    "tn": Tr("Кружочки", "Video notes"),
    "tx": Tr("Сворачивать расшифровки", "Collapse transcripts"),
    "ia": Tr("Понимать картинки", "Understand pictures"),
    "sl": Tr("Учиться на стикерах", "Learn from stickers"),
    "sr": Tr("Стикер на стикер", "Sticker for a sticker"),
    "ic": Tr("Стикер к фото", "Sticker for a photo"),
    "rx": Tr("Реакции в чате", "Reactions in the chat"),
    "rh": Tr("Запоминать реакции людей", "Keep people's reactions"),
    "cm": Tr("Режим сообщества", "Community mode"),
    "gf": Tr("Ждать сообщения", "Wait for a message"),
    "wb": Tr("Возврат", "Welcome back"),
    "fp": Tr("Прощание", "Farewell"),
    "fd": Tr("Уходы", "Departures"),
    "rj": Tr("Шутка", "Joke"),
    "aw": Tr("Советы", "Advice"),
    "re": Tr("Правила", "Rules"),
}
"""Toggle labels, by field code: every bool code a section shows, here and on the
defaults screen. Short, because a phone fits about 17 characters on a full-width
button and about 8 on a third.

A name says what the switch changes, never more: the greeting itself is the
mode's, so its follower is «Ждать сообщения» (when the greeting comes) — under
⚪ «Новичок» read "no greeting" while the bot greeted every newcomer on joining."""

COMMON_NOTE = Tr(
    "Берутся из общих настроек: {names}. Изменение здесь касается только этого чата.",
    "Taken from the general settings: {names}. A change here applies to this chat only.",
)
"""The one line that says which values on a screen come from the general settings.

Its second sentence answers what round 4 could not: which value wins once
the reader taps one of them (this chat's, from then on, and nowhere else).
Round 5 changed the first: «Как в общих настройках» read as a comparison
("the chat's value is checked against the general one") to two of five, and
all five listed it as unclear; «берутся из» says where the value comes from."""
_ALL_COMMON = Tr(
    "Все значения на кнопках берутся из общих настроек. "
    "Изменение здесь касается только этого чата.",
    "Every value on the buttons is taken from the general settings. "
    "A change here applies to this chat only.",
)
_COMMON_NAMES: Final[dict[str, Tr]] = {"tol": Tr("Откровенность", "Explicitness")}
"""A name for `COMMON_NOTE` where the button is not a `LABELS` switch."""
TAP_TOGGLES = Tr(
    "✅ — включено, ⚪ — выключено. Нажатие переключает.",
    "✅ is on, ⚪ is off. A tap switches it.",
)
"""On every screen with yes/no switches, as the first line under the title: all
five readers of round 4 had to guess that ⚪ means off, and none could tell it
from "not set". Round 5 read it where it led the screen (Memory: ⚪ went from
five guesses to five sure answers) and called it easy to miss in a footer."""

TOGGLED_ON = Tr("Включено: {label}", "On: {label}")
TOGGLED_OFF = Tr("Выключено: {label}", "Off: {label}")
TOLERANCE_SET = Tr("Откровенность: {value}", "Explicitness: {value}")
COMMUNITY_OFF_TAP = Tr(
    "<i>Режим сообщества выключен, эта кнопка ничего не изменила.</i>",
    "<i>Community mode is off, so this button changed nothing.</i>",
)
REACTIONS_OFF_TAP = Tr(
    "<i>Реакции в чате выключены, эта кнопка ничего не изменила.</i>",
    "<i>Reactions are off in this chat, so this button changed nothing.</i>",
)
REACTIONS_NOT_ADMIN_ALERT = Tr(
    "Реакции включены, но бот не администратор чата: Telegram не будет показывать ему реакции. "
    "Дайте боту права администратора.",
    "Reactions are on, but the bot is not a chat admin: Telegram will not show it reactions. "
    "Give the bot admin rights.",
)
"""The popup when reactions are switched on in a chat where the bot cannot see them
(ADR-0004 Decision 5: the check runs at the moment of enabling, and nothing else
would tell the admin — the updates just never arrive)."""
TOLERANCE_INVALID = Tr(
    "<i>Нужно число от 0.0 до 1.0, например 0.7.</i>",
    "<i>A number from 0.0 to 1.0 is needed, for example 0.7.</i>",
)

_CHATS_TITLE = Tr("🗂 <b>Чаты</b>", "🗂 <b>Chats</b>")
_CHATS_COUNT = Tr("Подключено: {n}", "Connected: {n}")
_CHATS_SHOWN = Tr(", показаны {first}–{last}", ", showing {first}–{last}")
_CHATS_SHOWN_ONE = Tr(", показан {first}-й", ", showing no. {first}")
_CHATS_BODY = Tr(
    "{count}. Рядом с названием — сообщений за сутки.",
    "{count}. Next to each name: messages in 24 h.",
)
_CHATS_EMPTY = Tr(
    "Пока ни одного чата. Добавьте бота в группу, сюда придёт запрос доступа.",
    "No chats yet. Add the bot to a group and its access request will show up here.",
)
REQUESTS: Final = Tr("🙏 Запросы доступа · {n}", "🙏 Access requests · {n}")
_REJECTED = Tr("🚫 Отклонённые · {n}", "🚫 Rejected · {n}")

_FIND_TITLE = Tr("🔎 <b>Поиск чата</b>", "🔎 <b>Find a chat</b>")
_FIND_USAGE = Tr(
    "Укажите ссылку на чат или часть названия:\n<code>/panel название</code>",
    "Give a chat link or part of its title:\n<code>/panel name</code>",
)
_FIND_NONE = Tr(
    "Не нашёл такого чата среди подключённых.",
    "No connected chat matches.",
)
_FIND_MANY = Tr("Подходит несколько чатов, какой открыть?", "Several chats match. Which one?")
_FIND_CUT = Tr(
    "Показаны первые {n}, уточните название.",
    "Showing the first {n}; narrow the title down.",
)
_ALL_CHATS = Tr("🗂 Все чаты", "🗂 All chats")

_ON = Tr("Бот включён.", "The bot is on.")
_OFF = Tr("Бот выключен в этом чате.", "The bot is off in this chat.")
_WHEN_CALLED = Tr("Отвечает, когда зовут", "Answers when called")
_SUMMARY: Final[tuple[tuple[str, Tr], ...]] = (
    ("rag", Tr("помнит диалоги", "remembers conversations")),
    ("tv", Tr("расшифровывает голосовые", "transcribes voice")),
    ("cm", Tr("встречает новичков", "greets newcomers")),
)
_OPEN_CHAT = Tr("Открыть чат", "Open the chat")
_RULES = Tr("📏 Правила · {n}", "📏 Rules · {n}")
_DISCONNECT = Tr("🔌 Отключить бота", "🔌 Turn the bot off")

SECTION_TITLES: Final[dict[Section, Tr]] = {
    "behavior": Tr("🗣 Поведение", "🗣 Behavior"),
    "memory": Tr("🧠 Память", "🧠 Memory"),
    "voice": Tr("🎙 Голос и фото", "🎙 Voice and photos"),
    "stickers": Tr("🎨 Стикеры", "🎨 Stickers"),
    "reactions": Tr("😀 Реакции", "😀 Reactions"),
    "community": Tr("👋 Новички и уходы", "👋 Joining and leaving"),
}
"""Section name on the chat root's button and, emoji kept, in its screen's title."""

_WORDS = Tr("Зовут словами: {words}", "Called by: {words}")
_NO_WORDS = Tr("нет", "none")
_JOINS = Tr("Встревает", "Joins in")
"""How often the bot answers unasked: one name on the button, the line, the
picker and the notice."""
_JOINS_AT = Tr(
    "Встревает: в {pct} % сообщений, без зова",
    "Joins in: on {pct} % of messages, unasked",
)
_JOINS_UP_TO = Tr(
    "Встревает: до {pct} % сообщений, без зова и только когда есть что добавить",
    "Joins in: on up to {pct} % of messages, unasked and only when it has something to add",
)
_JOINS_NEVER = Tr(
    "Встревает: никогда — отвечает, только когда зовут",
    "Joins in: never; it answers only when called",
)
_LANGUAGE = Tr("Язык ответов: {lang}", "Reply language: {lang}")
_LANGUAGES: Final[dict[str, Tr]] = {
    "ru": Tr("русский", "Russian"),
    "en": Tr("английский", "English"),
}
"""Every language the prompt builder answers in (``prompt_builder._language_section``)."""
_LANGUAGE_BUTTON = Tr("🌐 Язык ответов", "🌐 Reply language")
_PROMPT = Tr("Инструкция: {text}", "Instruction: {text}")
_PROMPT_UNSET = Tr("не задана", "not set")
_WORDS_FIXED = Tr(
    "Слова, которыми зовут бота, здесь не меняются.",
    "The words that call the bot cannot be changed here.",
)
_PROMPT_BUTTON = Tr("📝 Инструкция", "📝 Instruction")
PROMPT_PREVIEW_CHARS: Final = 60

_NO_ARCHIVE = Tr(
    "Без сохранения сообщений нет саммари и поиска по архиву.",
    "Without saved messages there are no summaries and no archive search.",
)
_ORGANIZERS = Tr("👥 Организаторы · {n}", "👥 Organizers · {n}")
_KB_LINE = Tr(
    "База знаний — факты о чате, которые админы бота и организаторы записывают через /remember.",
    "Knowledge base: facts about the chat that the bot's admins and organizers save with /remember.",
)

_STICKER_ON_STICKER = Tr("Стикер на стикер: в {pct} %", "Sticker for a sticker: {pct} %")
_STICKER_ON_PHOTO = Tr("Стикер к фото: в {pct} %", "Sticker for a photo: {pct} %")
_STICKER_ON_STICKER_OFF = Tr("Стикер на стикер: выключен", "Sticker for a sticker: off")
_STICKER_ON_PHOTO_OFF = Tr("Стикер к фото: выключен", "Sticker for a photo: off")
_NO_STICKER_LEARNING = Tr(
    "Пока выключено «{sl}», бот стикерами не отвечает.",
    "While “{sl}” is off, the bot does not answer with stickers.",
)
"""Both sticker answers live inside the learning handler (``handlers/media.py``):
with ``sticker_learning_enabled`` off neither fires, whatever their own switch says."""
_HOW_OFTEN = Tr("Как часто", "How often")
_TOLERANCE_BUTTON = Tr("🔞 Откровенность: {value}", "🔞 Explicitness: {value}")

TOLERANCE_PRESETS: Final[tuple[tuple[float, Tr], ...]] = (
    (0.0, Tr("Строго", "Strict")),
    (0.25, Tr("Скромно", "Modest")),
    (0.5, Tr("Обычно", "Normal")),
    (0.75, Tr("Смелее", "Bolder")),
    (1.0, Tr("Всё можно", "Anything")),
)
_TOL_TITLE = Tr("🔞 <b>Откровенность стикеров</b>", "🔞 <b>Sticker explicitness</b>")
_TOL_BODY = Tr(
    "Сейчас {value}. Чем больше, тем откровеннее стикеры может слать бот. Нажатие сразу сохраняет.",
    "Now {value}. The higher it is, the more explicit the stickers the bot may send. "
    "A tap saves at once.",
)
_TOL_INPUT = Tr(
    "Напишите число от 0.0 до 1.0. Сейчас {value}.",
    "Send a number from 0.0 to 1.0. Now {value}.",
)
_TOL_CUSTOM = Tr("✏️ Своё число", "✏️ Custom")

_ADMIN_YES = Tr(
    "✓ Бот — админ чата, реакции видны.",
    "✓ The bot is a chat admin, so it sees reactions.",
)
_ADMIN_NO = Tr(
    "⚠️ Бот не администратор чата: Telegram не показывает ему реакции.",
    "⚠️ The bot is not a chat admin: Telegram does not show it reactions.",
)

COMMUNITY_LINES: Final[tuple[tuple[str, Tr], ...]] = (  # what each short label does
    (
        "gf",
        Tr(
            "Ждать сообщения: здороваться с новичком на его первом сообщении, а не при входе",
            "Wait for a message: greet a newcomer on their first message, not on joining",
        ),
    ),
    ("wb", Tr("Возврат: писать «с возвращением»", "Welcome back: say “welcome back”")),
    ("fp", Tr("Прощание: прощаться в чате", "Farewell: say goodbye in the chat")),
    ("fd", Tr("Уходы: присылать вам сводку", "Departures: send you a summary")),
    ("rj", Tr("Шутка: шутить, что ушедший вернётся", "Joke: joke that the leaver will be back")),
    ("aw", Tr("Советы: замечать непрошеные советы", "Advice: notice unsolicited advice")),
)
"""What each switch does when it is on: said as an action, not as a fact, so a
line under a ⚪ switch does not claim the bot does it."""
COMMUNITY_LEGEND: Final = Tr(
    "Что делает переключатель, когда включён:", "What a switch does when on:"
)
_COMMUNITY_OFF = Tr(
    "Режим выключен: бот не здоровается, не прощается и не пишет вам об уходах.",
    "The mode is off: the bot does not greet, say goodbye or tell you who left.",
)
_NOW = Tr("Сейчас бот:", "Right now the bot:")
_GREETS_FIRST = Tr(
    "здоровается с новичком на его первом сообщении, а не при входе",
    "greets a newcomer on their first message, not on joining",
)
_GREETS_JOIN = Tr(
    "здоровается с новичком сразу при входе", "greets a newcomer as soon as they join"
)
_WELCOMES_BACK = Tr(
    "тех, кто вернулся, встречает словами «с возвращением»",
    "meets those who come back with “welcome back”",
)
_SAYS_GOODBYE = Tr(
    "прощается в чате с тем, кто ушёл сам",
    "says goodbye in the chat to whoever left on their own",
)
_JOKES = Tr(
    "над тем, кто уходит не в первый раз, шутит в чате, что он вернётся",
    "jokes in the chat that someone leaving again will be back",
)
_TELLS_YOU = Tr("присылает вам в личку, кто ушёл", "sends you a direct message about who left")
_WATCHES_ADVICE = Tr(
    "замечает непрошеные советы в обсуждении канала и раз в день присылает вам сводку",
    "spots unsolicited advice in a channel's discussion and sends you a daily summary",
)

COMMUNITY_SUBTOGGLES: Final = frozenset({"gf", "wb", "fp", "fd", "rj"})
"""Shown, and written, only while community mode is on. ``aw`` is not one of
them: the advice watcher reads only its own flag (``services/advice/watcher.py``)."""

# ---------------------------------------------------------------------------
# Small pieces
# ---------------------------------------------------------------------------


def fit_label(title: str, suffix: str, per_row: int = 1) -> str:
    """``title + suffix`` cut at the title's end so the suffix stays visible on a phone."""
    limit = clip_room(per_row)
    if label_width_px(title + suffix) <= limit:
        return title + suffix
    cut = title
    while cut and label_width_px(cut + "…" + suffix) > limit:
        cut = cut[:-1]
    return cut.rstrip() + "…" + suffix


def _number(value: float) -> str:
    return f"{value:g}"


def _pct(value: float) -> str:
    return f"{round(value * 100, 1):g}"


def tolerance_preset(value: float) -> Tr | None:
    """The preset ``value`` sits on, or None for a custom number."""
    for preset, name in TOLERANCE_PRESETS:
        if abs(value - preset) < 0.005:
            return name
    return None


def tolerance_label(value: float, lang: str) -> str:
    """``0.5, обычно`` on a preset, the bare number otherwise."""
    name = tolerance_preset(value)
    return f"{_number(value)}, {name(lang).lower()}" if name else _number(value)


def _header(section: Section, view: ChatView, lang: str) -> str:
    emoji, word = SECTION_TITLES[section](lang).split(" ", 1)
    return f"{emoji} <b>{word}</b> · {escape(view.name)}"


def _inherits(view: ChatView, code: str) -> bool:
    """The chat has no value of its own for ``code``: the general settings decide it."""
    return shows_common(FIELDS_BY_CODE[code], view.row)


def _toggle(view: ChatView, code: str, lang: str, *, marked: bool = True) -> Button:
    """A switch: ✅ or ⚪ and its name. ``marked`` is kept for its callers and
    changes nothing now — no button says where its value comes from."""
    del marked
    mark = "✅" if view.on(code) else "⚪"
    return Button(f"{mark} {LABELS[code](lang)}", cb("adm_pnl_tgl:", lang, view.chat_id, code))


def _back_to_chat(view: ChatView, lang: str) -> tuple[Button, ...]:
    return (back("chat", cb("adm_pnl_menu:", lang, view.chat_id), lang),)


def _text(*blocks: str | None) -> str:
    return "\n\n".join(block for block in blocks if block)


def _common_name(code: str) -> Tr:
    """What `COMMON_NOTE` calls ``code``: its switch, its own name, or its percentage's."""
    if code in _COMMON_NAMES:
        return _COMMON_NAMES[code]
    if code in LABELS:
        return LABELS[code]
    return CHANCES[code].name


def _notes(view: ChatView, codes: Sequence[str], lang: str, *extra: str) -> str | None:
    """The footnote, in plain text: ``extra`` caveats, then which of ``codes``
    come from the general settings. Plain, not italic — these are the lines a
    reader must not miss, and italics are the first thing glare takes away."""
    lines = list(extra)
    inherited = [code for code in codes if _inherits(view, code)]
    if len(inherited) == len(codes) > 2:
        lines.append(_ALL_COMMON(lang))
    elif inherited:
        names = ", ".join(_common_name(code)(lang) for code in inherited)
        lines.append(COMMON_NOTE(lang).format(names=names))
    return "\n".join(lines) if lines else None


# ---------------------------------------------------------------------------
# The chat list
# ---------------------------------------------------------------------------


SHORT_PAGES: Final = "u"
"""The last field of an ``adm_pnl:`` pager payload drawn on the undo render:
the page it opens is counted at the undo render's size. Without it ▶ from that
render (one chat fewer per page) opened an ordinary page whose first chat was
one further on, so a chat between the two was on neither."""

REJECTED_BEFORE: Final = "r"
"""The last field of an ``adm_wl_on:`` undo payload when the chat already had a
rejected attempt on record at the turn-off. The undo writes nothing for a chat
rejected AFTER the turn-off (a request filed while it was off and rejected from
a card), and ``has_rejected_attempt`` alone cannot tell that from a rejection
older than the approval that enabled the chat: the payload remembers which."""


def chats_per_page(requests: int, rejected: int, *, undo: bool = False) -> int:
    """Chats per page: one row fewer for each extra row the list carries (rows cap).

    The requests row and the «↩️ Вернуть» row after a turn-off each cost a chat,
    so the worst page -- undo, requests, two chats, the pager and the last row --
    is still four action rows, a pager and a back row. ``rejected`` costs no row:
    that button shares the last row with the back button. Every input stays a
    parameter so the handler and the builder keep asking the same question.

    ``undo`` is the undo render's page size, and also every page its pager
    reaches (`SHORT_PAGES`): the same offsets, so ▶ skips no chat.
    """
    del rejected
    return 4 - (1 if requests else 0) - (1 if undo else 0)


def _requests_row(lang: str, requests: int) -> tuple[Button, ...] | None:
    """The waiting requests on a row of their own: «Запросы доступа · 12» needs it."""
    if not requests:
        return None
    return (Button(REQUESTS(lang).format(n=requests), cb("adm_wl_pending:", lang, 0)),)


def _undo_row(
    lang: str, undo: int | None, page: int, *, rejected: bool
) -> tuple[Button, ...] | None:
    """«↩️ Вернуть» for the chat just turned off: on that one render, never again.

    ``rejected``: the chat had a rejected attempt before the turn-off, so the
    payload carries `REJECTED_BEFORE` and the undo does not mistake it for one
    rejected since.
    """
    if undo is None:
        return None
    if rejected:
        return (Button(UNDO(lang), cb("adm_wl_on:", lang, undo, page, REJECTED_BEFORE)),)
    return (Button(UNDO(lang), cb("adm_wl_on:", lang, undo, page)),)


def _chats_tail(lang: str, rejected: int) -> tuple[Button, ...]:
    """The last row: the rejected chats, rarely opened, next to the way back."""
    hub = back("hub", cb("adm_menu:", lang), lang)
    if not rejected:
        return (hub,)
    return (Button(_REJECTED(lang).format(n=rejected), cb("adm_wl_rejected:", lang, 0)), hub)


def _chats_page(lang: str, page: int, *, short: bool) -> str:
    """A chat list page, carrying `SHORT_PAGES` when it is counted at the undo size."""
    if short:
        return cb("adm_pnl:", lang, page, SHORT_PAGES)
    return cb("adm_pnl:", lang, page)


def _chats_count(lang: str, total: int, page: int, pages: int, shown: int, per_page: int) -> str:
    """«Подключено: 5», plus which of them this page lists when there is more than one page."""
    count = _CHATS_COUNT(lang).format(n=total)
    if pages <= 1 or shown <= 0:
        return count
    first = page * per_page + 1
    last = first + shown - 1
    if first == last:
        return count + _CHATS_SHOWN_ONE(lang).format(first=first)
    return count + _CHATS_SHOWN(lang).format(first=first, last=last)


def build_chats(
    lang: str,
    chats: Sequence[Mapping[str, Any]],
    *,
    total: int,
    page: int,
    pages: int,
    requests: int = 0,
    rejected: int = 0,
    undo: int | None = None,
    undo_rejected: bool = False,
    short: bool = False,
) -> Screen:
    """``chats`` (or ``chats.empty`` when nothing is connected).

    ``page`` is counted with `chats_per_page`, the same count the handler
    fetched the page with, so the body can say which chats the page lists.
    ``undo`` is the chat just turned off: its «↩️ Вернуть» row comes first,
    under the notice it takes back, marked `REJECTED_BEFORE` when
    ``undo_rejected``. ``short`` is a page reached from that
    render's pager: it has no undo row, but keeps its page size, and its own
    pager keeps the marker (`SHORT_PAGES`).
    """
    short = short or undo is not None
    redo = _undo_row(lang, undo, page, rejected=undo_rejected)
    entry = _requests_row(lang, requests)
    tail = _chats_tail(lang, rejected)
    if total == 0:
        empty_rows = tuple(r for r in (redo, entry, tail) if r)
        return Screen(
            id="chats.empty", text=_text(_CHATS_TITLE(lang), _CHATS_EMPTY(lang)), rows=empty_rows
        )

    listed: list[tuple[Button, ...]] = []
    for chat in chats:
        chat_id = int(chat["chat_id"])
        title = str(chat.get("chat_title") or chat_id)
        count = int(chat.get("message_count_24h") or 0)
        label = fence(fit_label(title, f" · {count}"))
        listed.append((Button(label, cb("adm_pnl_menu:", lang, chat_id)),))
    nav = pager(page, pages, lambda p: _chats_page(lang, p, short=short))
    rows = tuple(r for r in (redo, entry, *listed, nav, tail) if r)
    return Screen(
        id="chats",
        text=_text(
            _CHATS_TITLE(lang),
            _CHATS_BODY(lang).format(
                count=_chats_count(
                    lang,
                    total,
                    page,
                    pages,
                    len(chats),
                    chats_per_page(requests, rejected, undo=short),
                )
            ),
        ),
        rows=rows,
    )


# ---------------------------------------------------------------------------
# /panel <query>
# ---------------------------------------------------------------------------

FIND_SHOWN: Final = 4
"""Candidates ``panel.many`` lists; the handler asks for one more to know it cut."""


def _find_tail(lang: str) -> tuple[Button, ...]:
    return (
        Button(_ALL_CHATS(lang), cb("adm_pnl:", lang, 0)),
        back("hub", cb("adm_menu:", lang), lang),
    )


def build_panel_noquery(lang: str) -> Screen:
    return Screen(
        id="panel.noquery",
        text=_text(_FIND_TITLE(lang), _FIND_USAGE(lang)),
        rows=(_find_tail(lang),),
    )


def build_panel_notfound(lang: str) -> Screen:
    return Screen(
        id="panel.notfound",
        text=_text(_FIND_TITLE(lang), _FIND_NONE(lang)),
        rows=(_find_tail(lang),),
    )


def build_panel_many(lang: str, chats: Sequence[Mapping[str, Any]]) -> Screen:
    """Up to `FIND_SHOWN` matches; says so when there were more."""
    shown = chats[:FIND_SHOWN]
    rows = [
        (
            Button(
                fence(fit_label(str(c.get("chat_title") or c["chat_id"]), "")),
                cb("adm_pnl_menu:", lang, int(c["chat_id"])),
            ),
        )
        for c in shown
    ]
    cut = _FIND_CUT(lang).format(n=FIND_SHOWN) if len(chats) > FIND_SHOWN else None
    return Screen(
        id="panel.many",
        text=_text(_FIND_TITLE(lang), _FIND_MANY(lang), cut),
        rows=(*rows, _find_tail(lang)),
    )


# ---------------------------------------------------------------------------
# One chat
# ---------------------------------------------------------------------------


def _status_line(view: ChatView, lang: str) -> str:
    if not view.values.get("enabled", True):
        return _OFF(lang)
    clauses = [_WHEN_CALLED(lang)] + [text(lang) for code, text in _SUMMARY if view.on(code)]
    return f"{_ON(lang)} {' · '.join(clauses)}"


def build_chat(view: ChatView, lang: str) -> Screen:
    """The chat's root: what the bot does here, its sections, and who may save facts.

    The organizers sit here and not under memory: the people a task names
    ("remove the organizer who left") are looked for at the chat, not in a
    setting. Section labels pair by width: two halves, then three thirds.
    """

    def section(name: Section) -> Button:
        return Button(SECTION_TITLES[name](lang), cb("adm_pnl_grp:", lang, view.chat_id, name))

    url = build_chat_url(view.chat_id, view.chat_type)
    link = f'<a href="{escape(url, quote=True)}">{_OPEN_CHAT(lang)}</a>' if url else None
    # Only while the bot is on: on a chat that is off it offered to do what
    # the status line says is done, and its «↩️ Вернуть» turned the chat ON.
    off = (
        (Button(_DISCONNECT(lang), cb("adm_wl_rm_ask:", lang, view.chat_id, 0)),)
        if view.values.get("enabled", True)
        else ()
    )
    return Screen(
        id="chat",
        text=_text(
            f"🗂 <b>{escape(view.name)}</b> <code>{view.chat_id}</code>",
            _status_line(view, lang),
            link,
        ),
        rows=(
            (section("behavior"), section("voice")),
            (section("memory"), section("stickers"), section("reactions")),
            (
                section("community"),
                Button(
                    _ORGANIZERS(lang).format(n=view.organizers),
                    cb("adm_kb_orgs:", lang, view.chat_id, 0),
                ),
            ),
            (Button(_RULES(lang).format(n=view.rules), cb("ar_list:", lang, view.chat_id, 0)),),
            # Next to the way out, not in the section grid: among the sections'
            # nouns it read as a state to all five readers of round 4. It opens
            # a confirmation (`chat.off_ask`), so the bottom row is safe.
            (*off, *_back_to_chats(lang)),
        ),
    )


def _back_to_chats(lang: str) -> tuple[Button, ...]:
    return (back("chats", cb("adm_pnl:", lang, 0), lang),)


# ── behavior ──────────────────────────────────────────────────────────────


def _prompt_preview(prompt: str, lang: str) -> str:
    text = prompt.strip()
    if not text:
        return _PROMPT_UNSET(lang)
    first = text.splitlines()[0]
    cut = first[:PROMPT_PREVIEW_CHARS]
    more = "…" if len(cut) < len(text) else ""
    return f"<i>{escape(cut)}{more}</i>"


def _percent(pct: float, lang: str) -> str:
    """``5,5`` in Russian, ``5.5`` in English: a number already in percent."""
    text = f"{round(pct, 1):g}"
    return text.replace(".", ",") if lang == "ru" else text


def _share(view: ChatView, code: str, lang: str) -> str:
    """A stored 0..1 share as the percent a reader sees: ``0.055`` → ``5,5``."""
    return _percent(float(view.value(code) or 0) * 100, lang)


def _joins_line(view: ChatView, lang: str) -> str:
    """How often it answers unasked. With the relevance check on, the roll is a
    ceiling: the reply is dropped when the model finds nothing to add."""
    if float(view.value("rc") or 0) <= 0:
        return _JOINS_NEVER(lang)
    line = _JOINS_UP_TO if view.on("rg") else _JOINS_AT
    return line(lang).format(pct=_share(view, "rc", lang))


def _language_name(code: str, lang: str) -> str:
    name = _LANGUAGES.get(code)
    return name(lang) if name else escape(code)


def _behavior_name(lang: str) -> str:
    return SECTION_TITLES["behavior"](lang).split(" ", 1)[1]


def _back_to_behavior(view: ChatView, lang: str) -> tuple[Button, ...]:
    callback = cb("adm_pnl_grp:", lang, view.chat_id, "behavior")
    return (back("behavior", callback, lang, name=_behavior_name(lang)),)


def build_behavior(view: ChatView, lang: str) -> Screen:
    words = view.value("tw") or ()
    facts = "\n".join(
        (
            _joins_line(view, lang),
            _LANGUAGE(lang).format(lang=_language_name(str(view.value("lg") or ""), lang)),
            _PROMPT(lang).format(text=_prompt_preview(str(view.value("sp") or ""), lang)),
            _WORDS(lang).format(
                words=", ".join(escape(str(w)) for w in words) if words else _NO_WORDS(lang)
            ),
        )
    )
    codes = ("rg", "af", "lc")
    chance = f"🎲 {_JOINS(lang)} · {_share(view, 'rc', lang)} %"
    return Screen(
        id="behavior",
        text=_text(
            _header("behavior", view, lang),
            TAP_TOGGLES(lang),
            facts,
            _WORDS_FIXED(lang),
            _notes(view, codes, lang),
        ),
        rows=(
            (
                Button(chance, cb("adm_pnl_pct:", lang, view.chat_id, "rc")),
                Button(_LANGUAGE_BUTTON(lang), cb("adm_pnl_lg:", lang, view.chat_id)),
            ),
            *((_toggle(view, code, lang),) for code in codes),
            (
                Button(_PROMPT_BUTTON(lang), cb("adm_pnl_sp:", lang, view.chat_id)),
                *_back_to_chat(view, lang),
            ),
        ),
    )


# ── the instruction editor ────────────────────────────────────────────────
#
# Four prompts of one input: the current instruction, the preview of a new
# one, a refused part, and a cleared draft. They show the admin's own text
# verbatim, so the handler sends them through ``split_html`` and keeps every
# piece's id (a newer preview deletes the older ones): it delivers
# `render_text` and `to_markup` itself rather than through `show`, which
# returns the last piece only.

SP_MAX_CHARS: Final = 12000
_SP_CURRENT_TITLE = Tr("📝 <b>Инструкция для чата</b>", "📝 <b>Chat instruction</b>")
_SP_NONE = Tr(
    "<i>Своей инструкции у чата нет.</i>", "<i>This chat has no instruction of its own.</i>"
)
_SP_ASK = Tr(
    "Пришлите новую инструкцию сообщением — она заменит эту. Или нажмите «Отмена».",
    "Send the new instruction as a message — it replaces this one. Or tap «Cancel».",
)
_SP_NEW_TITLE = Tr("📝 <b>Новая инструкция</b>", "📝 <b>New instruction</b>")
_SP_LENGTH = Tr("{n} симв.", "{n} chars")
_SP_CONFIRM = Tr(
    "Сохраняем? Если это ещё не всё — пришлите продолжение, оно допишется в конец.",
    "Save it? If there is more, send the rest — it is appended at the end.",
)
_SP_TOO_LONG = Tr(
    "Слишком длинно: вышло бы {n} симв., можно до {max}. "
    "Эта часть не вошла, поэтому черновик неполный и сохранить его нельзя. "
    "Нажмите «Заново» и пришлите текст покороче.",
    "Too long: that would make {n} chars, the limit is {max}. "
    "This part did not fit, so the draft is incomplete and cannot be saved. "
    "Tap «Start over» and send a shorter text.",
)
_SP_INCOMPLETE = Tr(
    "Черновик неполный: часть текста не влезла. Нажмите «Заново».",
    "The draft is incomplete: part of the text did not fit. Tap «Start over».",
)
SP_INCOMPLETE: Final = _SP_INCOMPLETE
"""Also the alert of a Save tapped on such a draft's older preview."""
_SP_CLEARED = Tr(
    "Черновик очищен. Пришлите инструкцию сообщением.",
    "Draft cleared. Send the instruction as a message.",
)
_SP_SAVE = Tr("✅ Сохранить", "✅ Save")
_SP_REDO = Tr("🔄 Заново", "🔄 Start over")


def _sp_cancel(chat_id: int, lang: str) -> Button:
    return cancel(cb("adm_pnl_spx:", lang, chat_id), lang)


def _sp_redo_row(chat_id: int, rev: int, lang: str) -> tuple[Button, ...]:
    return (
        Button(_SP_REDO(lang), cb("adm_pnl_spre:", lang, chat_id, rev)),
        _sp_cancel(chat_id, lang),
    )


def build_sp_prompt(chat_id: int, title: str | None, current: str, lang: str) -> Screen:
    """``sp.prompt`` — the chat's instruction as it is, and the ask for a new one."""
    label = fence(escape(title)) if title else str(chat_id)
    text = current.strip()
    body = f"<blockquote expandable>{escape(text)}</blockquote>" if text else _SP_NONE(lang)
    return Screen(
        id="sp.prompt",
        text=_text(
            f"{_SP_CURRENT_TITLE(lang)}\n{label} <code>{chat_id}</code>", body, _SP_ASK(lang)
        ),
        rows=((_sp_cancel(chat_id, lang),),),
    )


def build_sp_preview(chat_id: int, draft: str, rev: int, lang: str) -> Screen:
    """``sp.preview`` — the whole draft so far, under Save / «Start over» / Cancel.

    ``rev`` is the draft version it shows (the id of the last message added
    to it). Save and «Start over» carry it, so a button under an older preview
    -- or one from an earlier editing session -- cannot act on a draft it does
    not show. Their prefixes differ from ``adm_pnl_sp:`` at the character
    after ``sp``, so ``startswith("adm_pnl_sp:")`` cannot swallow them.
    """
    title = f"{_SP_NEW_TITLE(lang)} · {_SP_LENGTH(lang).format(n=len(draft))}"
    return Screen(
        id="sp.preview",
        text=_text(
            f"{title}\n<blockquote expandable>{escape(draft)}</blockquote>", _SP_CONFIRM(lang)
        ),
        rows=(
            (Button(_SP_SAVE(lang), cb("adm_pnl_spok:", lang, chat_id, rev), style="success"),),
            _sp_redo_row(chat_id, rev, lang),
        ),
    )


def build_sp_refused(chat_id: int, rev: int, lang: str, *, length: int | None = None) -> Screen:
    """``sp.refused`` — a part that did not fit (``length``: the draft it would
    have made), or any part after one: the draft is incomplete, so no Save."""
    text = (
        _SP_TOO_LONG(lang).format(n=length, max=SP_MAX_CHARS)
        if length is not None
        else _SP_INCOMPLETE(lang)
    )
    return Screen(
        id="sp.refused",
        text=_text(_SP_NEW_TITLE(lang), text),
        rows=(_sp_redo_row(chat_id, rev, lang),),
    )


def build_sp_redo(chat_id: int, lang: str) -> Screen:
    """``sp.redo`` — the draft is gone and the input waits again."""
    return Screen(
        id="sp.redo",
        text=_text(_SP_NEW_TITLE(lang), _SP_CLEARED(lang)),
        rows=((_sp_cancel(chat_id, lang),),),
    )


# ── memory ────────────────────────────────────────────────────────────────


def build_memory(view: ChatView, lang: str) -> Screen:
    """Four switches, one row each; the organizers are on the chat's root."""
    codes = ("sm", "ch", "rag", "kb")
    no_archive = None if view.on("sm") else _NO_ARCHIVE(lang)
    return Screen(
        id="memory",
        text=_text(
            _header("memory", view, lang),
            "\n".join(line for line in (TAP_TOGGLES(lang), _KB_LINE(lang), no_archive) if line),
            _notes(view, codes, lang),
        ),
        rows=(
            (_toggle(view, "sm", lang),),
            (_toggle(view, "ch", lang),),
            (_toggle(view, "rag", lang),),
            (_toggle(view, "kb", lang),),
            _back_to_chat(view, lang),
        ),
    )


# ── voice and photos ──────────────────────────────────────────────────────


def build_voice(view: ChatView, lang: str) -> Screen:
    codes = ("tv", "tn", "tx", "ia")
    return Screen(
        id="voice",
        text=_text(_header("voice", view, lang), TAP_TOGGLES(lang), _notes(view, codes, lang)),
        rows=(
            (_toggle(view, "tv", lang), _toggle(view, "tn", lang)),
            (_toggle(view, "tx", lang),),
            (_toggle(view, "ia", lang),),
            _back_to_chat(view, lang),
        ),
    )


# ── stickers ──────────────────────────────────────────────────────────────


def build_stickers(view: ChatView, lang: str) -> Screen:
    if view.on("sl"):
        lines = [
            _STICKER_ON_STICKER(lang).format(pct=_share(view, "sx", lang))
            if view.on("sr")
            else _STICKER_ON_STICKER_OFF(lang),
            _STICKER_ON_PHOTO(lang).format(pct=_share(view, "ix", lang))
            if view.on("ic")
            else _STICKER_ON_PHOTO_OFF(lang),
        ]
    else:
        lines = [_NO_STICKER_LEARNING(lang).format(sl=LABELS["sl"](lang))]
    tolerance = float(view.value("tol") or 0)
    codes = ("sl", "sr", "ic", "tol")
    tol_label = _TOLERANCE_BUTTON(lang).format(value=tolerance_label(tolerance, lang))
    return Screen(
        id="chat.stickers",
        text=_text(
            _header("stickers", view, lang),
            TAP_TOGGLES(lang),
            "\n".join(lines),
            _notes(view, codes, lang),
        ),
        rows=(
            (_toggle(view, "sl", lang),),
            (_toggle(view, "sr", lang),),
            (_toggle(view, "ic", lang),),
            (Button(tol_label, cb("adm_pnl_tol:", lang, view.chat_id)),),
            (
                Button(f"🎲 {_HOW_OFTEN(lang)}", cb("adm_pnl_pct:", lang, view.chat_id)),
                *_back_to_chat(view, lang),
            ),
        ),
    )


def _back_to_stickers(view: ChatView, lang: str) -> tuple[Button, ...]:
    return (back("chat.stickers", cb("adm_pnl_grp:", lang, view.chat_id, "stickers"), lang),)


def build_tolerance_prompt(view: ChatView, lang: str) -> Screen:
    """``tol.prompt``: five presets that save on a tap, and a way to type a number."""
    current = float(view.value("tol") or 0)
    marked = tolerance_preset(current)

    def preset(value: float, name: Tr) -> Button:
        dot = "• " if name is marked else ""
        return Button(
            f"{dot}{name(lang)} {_number(value)}",
            cb("adm_pnl_tolset:", lang, view.chat_id, _number(value)),
        )

    buttons = [preset(value, name) for value, name in TOLERANCE_PRESETS]
    custom = Button(
        _TOL_CUSTOM(lang), cb("adm_pnl_tol:", lang, view.chat_id, "in"), goes_to="tol.in"
    )
    return Screen(
        id="tol.prompt",
        text=_text(_TOL_TITLE(lang), _TOL_BODY(lang).format(value=_number(current))),
        rows=(tuple(buttons[:3]), (*buttons[3:], custom), _back_to_stickers(view, lang)),
    )


# ── how often: one picker for every percentage ────────────────────────────


@dataclass(frozen=True, slots=True)
class Chance:
    """A percentage the panel sets: its name, four presets, and the typed limit."""

    name: Tr
    presets: tuple[tuple[int, Tr], ...]
    about: Tr
    cap: int = 100
    """Highest number the admin may type, in percent."""
    cap_reason: Tr | None = None
    """Why the cap is below 100, said on the picker, the prompt and a refusal."""
    toggle: str | None = None
    """The switch on «Стикеры» without which this number does nothing."""


_RARELY = Tr("Редко", "Rarely")
_SOMETIMES = Tr("Иногда", "Sometimes")
_OFTEN = Tr("Часто", "Often")
_HALF = Tr("Через раз", "Half the time")
_STICKER_CAP_REASON = Tr(
    "каждый такой ответ — запрос к модели, а их число в день ограничено",
    "every such answer is a request to the model, and those are limited per day",
)
"""A sticker answer searches the sticker store by an embedding of the incoming
sticker or photo (``StickerLearningService.search``): one model request per
hit, uncached, against the bot's shared daily quota. Free in money, not in
quota, so the typed limit sits at the YAML default for a sticker (50 %)."""

CHANCES: Final[dict[str, Chance]] = {
    "rc": Chance(
        _JOINS,
        ((0, Tr("Никогда", "Never")), (2, _RARELY), (5, _SOMETIMES), (10, _OFTEN)),
        Tr(
            "Доля сообщений, на которые бот может ответить сам, без зова.",
            "The share of messages the bot may answer on its own, unasked.",
        ),
        cap=20,
        cap_reason=Tr(
            "каждый такой ответ — платный запрос к модели",
            "every such reply is a paid request to the model",
        ),
    ),
    "sx": Chance(
        LABELS["sr"],
        ((10, _RARELY), (25, _SOMETIMES), (40, _OFTEN), (50, _HALF)),
        Tr(
            "Доля стикеров, на которые бот отвечает своим стикером.",
            "The share of stickers the bot answers with a sticker of its own.",
        ),
        cap=50,
        cap_reason=_STICKER_CAP_REASON,
        toggle="sr",
    ),
    "ix": Chance(
        LABELS["ic"],
        ((10, _RARELY), (20, _SOMETIMES), (30, _OFTEN), (50, _HALF)),
        Tr(
            "Доля фото, к которым бот добавляет стикер.",
            "The share of photos the bot adds a sticker to.",
        ),
        cap=50,
        cap_reason=_STICKER_CAP_REASON,
        toggle="ic",
    ),
}
"""Field code → its picker. ``sticker_response_chance`` (``sc``) is not here: no
code reads it, so a number for it would change nothing."""
STICKER_CHANCES: Final = ("sx", "ix")

_CHANCE_BODY = Tr(
    "Сейчас {pct} %. {about} Нажатие сразу сохраняет.",
    "Now {pct} %. {about} A tap saves at once.",
)
_CHANCE_LIMIT = Tr("Больше {cap} % нельзя: {reason}.", "No more than {cap} %: {reason}.")
_CHANCE_CEILING = Tr(
    "Пока включено «{gate}», это верхняя граница: бот отвечает, только когда есть что добавить.",
    "While “{gate}” is on, this is a ceiling: the bot replies only when it has something to add.",
)
_CHANCE_OFF = Tr(
    "«{name}» сейчас выключен: число начнёт действовать, когда вы его включите на экране «Стикеры».",
    "“{name}” is off now: the number takes effect once you enable it on the Stickers screen.",
)
_CHANCE_NO_LEARNING = Tr(
    "Пока на экране «Стикеры» выключено «{sl}», бот стикерами не отвечает, что бы здесь ни стояло.",
    "While “{sl}” is off on the Stickers screen, the bot does not answer with stickers, "
    "whatever is set here.",
)
_CHANCE_NEVER = Tr(
    "Чтобы этого не было совсем, выключите «{name}» на экране «Стикеры».",
    "To stop it entirely, turn off “{name}” on the Stickers screen.",
)
_CHANCE_INPUT = Tr(
    "Напишите число от 0 до {cap} — процент. Сейчас {pct} %.",
    "Send a number from 0 to {cap}, in percent. Now {pct} %.",
)
_CHANCE_INVALID = Tr(
    "<i>Нужно число от 0 до {cap}, например {example}.</i>",
    "<i>A number from 0 to {cap} is needed, for example {example}.</i>",
)
_STICKER_CHANCES_BODY = Tr(
    "Как часто бот отвечает стикером там, где это включено. Нажмите кнопку, "
    "чтобы выбрать частоту. Включают и выключают на экране «Стикеры».",
    "How often the bot answers with a sticker where that is on. Tap a button "
    "to choose how often. They are switched on and off on the Stickers screen.",
)
_LANGUAGE_TITLE = Tr("🌐 <b>Язык ответов</b>", "🌐 <b>Reply language</b>")
_LANGUAGE_BODY = Tr(
    "Сейчас {lang}. На этом языке бот отвечает в чате, пишет саммари и расшифровывает "
    "голосовые. Нажатие сразу сохраняет.",
    "Now {lang}. In this language the bot answers in the chat, writes summaries and "
    "transcribes voice messages. A tap saves at once.",
)
_LANGUAGE_VOICE = Tr(
    "Если в чате говорят на другом языке, расшифровки голосовых могут выйти с ошибками.",
    "If people in the chat speak another language, voice transcripts may come out wrong.",
)
"""The same setting is the language hint sent with every voice message
(``handlers/media.py`` → ``transcribe(language=…)``)."""
LANGUAGE_CODES: Final = frozenset(_LANGUAGES)


def chance_key(code: str) -> str:
    """The ``chat_settings`` column a percentage picker writes (``rc`` → its key)."""
    return FIELDS_BY_CODE[code].key


def chance_set(code: str, pct: float, lang: str) -> str:
    """The notice after a percentage is saved: «Встревает: 5 %»."""
    return f"{CHANCES[code].name(lang)}: {_percent(pct, lang)} %"


def language_set(code: str, lang: str) -> str:
    """The notice after the reply language is saved: «Язык ответов: английский»."""
    return _LANGUAGE(lang).format(lang=_language_name(code, lang))


def _chance_pct(view: ChatView, code: str) -> float:
    return round(float(view.value(code) or 0) * 100, 1)


def _chance_title(code: str, lang: str) -> str:
    return f"🎲 <b>{CHANCES[code].name(lang)}</b>"


def _chance_limit(spec: Chance, lang: str) -> str | None:
    if spec.cap_reason is None:
        return None
    return _CHANCE_LIMIT(lang).format(cap=spec.cap, reason=spec.cap_reason(lang))


def _back_to_chance_home(view: ChatView, code: str, lang: str) -> tuple[Button, ...]:
    if code not in STICKER_CHANCES:
        return _back_to_behavior(view, lang)
    callback = cb("adm_pnl_pct:", lang, view.chat_id)
    return (back("chance.stickers", callback, lang, name=_HOW_OFTEN(lang)),)


def build_sticker_chances(view: ChatView, lang: str) -> Screen:
    """``chance.stickers``: how often each sticker answer fires; a tap opens its picker."""
    rows = tuple(
        (
            Button(
                f"🎲 {CHANCES[code].name(lang)} · {_share(view, code, lang)} %",
                cb("adm_pnl_pct:", lang, view.chat_id, code),
            ),
        )
        for code in STICKER_CHANCES
    )
    if view.on("sl"):
        off = " ".join(
            _CHANCE_OFF(lang).format(name=CHANCES[code].name(lang))
            for code in STICKER_CHANCES
            if not view.on(CHANCES[code].toggle or code)
        )
    else:
        off = _CHANCE_NO_LEARNING(lang).format(sl=LABELS["sl"](lang))
    return Screen(
        id="chance.stickers",
        text=_text(
            f"🎲 <b>{_HOW_OFTEN(lang)}</b> · {escape(view.name)}",
            _STICKER_CHANCES_BODY(lang),
            off,
            _notes(view, STICKER_CHANCES, lang),
        ),
        rows=(*rows, _back_to_stickers(view, lang)),
    )


def build_chance_prompt(view: ChatView, code: str, lang: str) -> Screen:
    """``chance.prompt``: four presets that save on a tap, and a way to type a number.

    A stored number off the presets (``0.3 %``) is said in the text and marks none.
    """
    spec = CHANCES[code]
    now = _chance_pct(view, code)

    def preset(pct: int, name: Tr) -> Button:
        dot = "• " if abs(now - pct) < 0.05 else ""
        return Button(
            f"{dot}{name(lang)} {pct} %", cb("adm_pnl_pctset:", lang, view.chat_id, code, pct)
        )

    buttons = [preset(pct, name) for pct, name in spec.presets]
    custom = Button(_TOL_CUSTOM(lang), cb("adm_pnl_pct:", lang, view.chat_id, code, "in"))
    if spec.toggle is None:
        aside = _CHANCE_CEILING(lang).format(gate=LABELS["rg"](lang)) if view.on("rg") else None
    elif not view.on("sl"):
        aside = _CHANCE_NO_LEARNING(lang).format(sl=LABELS["sl"](lang))
    elif view.on(spec.toggle):
        aside = _CHANCE_NEVER(lang).format(name=spec.name(lang))
    else:
        aside = _CHANCE_OFF(lang).format(name=spec.name(lang))
    return Screen(
        id="chance.prompt",
        text=_text(
            _chance_title(code, lang),
            _CHANCE_BODY(lang).format(pct=_percent(now, lang), about=spec.about(lang)),
            _chance_limit(spec, lang),
            aside,
        ),
        rows=(
            tuple(buttons[:2]),
            tuple(buttons[2:]),
            (custom,),
            _back_to_chance_home(view, code, lang),
        ),
    )


def build_chance_input(
    view: ChatView, code: str, lang: str, *, invalid: Literal["number", "high"] | None = None
) -> Screen:
    """``chance.in``, or ``chance.invalid`` under what was wrong with the last
    number: not one in range (``number``) or above a cap that has a reason (``high``)."""
    spec = CHANCES[code]
    limit = _chance_limit(spec, lang)
    if invalid == "high" and limit is not None:
        # The reason is the notice now; said again under the prompt it is noise.
        notice: str | None = f"<i>{limit}</i>"
        limit = None
    elif invalid:
        example = 3 if spec.cap <= 20 else 40
        notice = _CHANCE_INVALID(lang).format(cap=spec.cap, example=example)
    else:
        notice = None
    return Screen(
        id="chance.invalid" if invalid else "chance.in",
        text=_text(
            _chance_title(code, lang),
            _CHANCE_INPUT(lang).format(cap=spec.cap, pct=_share(view, code, lang)),
            limit,
        ),
        rows=((cancel(cb("adm_pnl_pctx:", lang, view.chat_id, code), lang),),),
        notice_raw=notice,
    )


def build_language_prompt(view: ChatView, lang: str) -> Screen:
    """``chat.lang``: the languages the bot answers in; a tap saves.

    A stored code outside them is said as it is and marks none.
    """
    current = str(view.value("lg") or "")
    buttons = tuple(
        Button(
            f"{'• ' if code == current else ''}{name(lang).capitalize()}",
            cb("adm_pnl_lgset:", lang, view.chat_id, code),
        )
        for code, name in _LANGUAGES.items()
    )
    return Screen(
        id="chat.lang",
        text=_text(
            _LANGUAGE_TITLE(lang),
            _LANGUAGE_BODY(lang).format(lang=_language_name(current, lang)),
            _LANGUAGE_VOICE(lang),
        ),
        rows=(buttons, _back_to_behavior(view, lang)),
    )


def build_tolerance_input(view: ChatView, lang: str, *, invalid: bool = False) -> Screen:
    """``tol.in``, or ``tol.invalid`` — the same prompt under the "not a number" line."""
    current = float(view.value("tol") or 0)
    return Screen(
        id="tol.invalid" if invalid else "tol.in",
        text=_text(_TOL_TITLE(lang), _TOL_INPUT(lang).format(value=_number(current))),
        rows=((cancel(cb("adm_pnl_tolcancel:", lang, view.chat_id), lang),),),
        notice_raw=TOLERANCE_INVALID(lang) if invalid else None,
    )


# ── reactions ─────────────────────────────────────────────────────────────


def build_reactions(view: ChatView, lang: str) -> Screen:
    # The legend is not an alternative to the admin line: the handler always
    # asks Telegram, so a legend shown only when the answer is unknown would be
    # shown to nobody (it was, after round 4: the samples drew it, the chat did not).
    admin = None if view.bot_is_admin is None else (_ADMIN_YES if view.bot_is_admin else _ADMIN_NO)
    codes = ("rx", "rh") if view.on("rx") else ("rx",)
    return Screen(
        id="reactions",
        text=_text(
            _header("reactions", view, lang),
            TAP_TOGGLES(lang),
            admin(lang) if admin else None,
            _notes(view, codes, lang),
        ),
        rows=(*((_toggle(view, code, lang),) for code in codes), _back_to_chat(view, lang)),
    )


# ── community ─────────────────────────────────────────────────────────────


_COMMUNITY_FACTS: Final[tuple[tuple[str, Tr], ...]] = (
    ("wb", _WELCOMES_BACK),
    ("fp", _SAYS_GOODBYE),
    ("rj", _JOKES),
    ("fd", _TELLS_YOU),
)
"""Community parts that add a line of their own while they are on, in screen order."""


def _community_now(view: ChatView, lang: str) -> str:
    """What the bot does in this chat with these switches, and nothing it does not.

    The toggles already name the parts; the text says only what is in force
    and what their names leave out (when a greeting comes, that a removal gets
    no goodbye, where the advice summary goes). A part that is off is not
    described at all, so the text cannot read as a fact its toggle denies.
    Behaviour as in ``services/community.py`` and ``services/advice/watcher.py``.
    """
    facts: list[Tr] = []
    if view.on("cm"):
        facts.append(_GREETS_FIRST if view.on("gf") else _GREETS_JOIN)
        facts += [line for code, line in _COMMUNITY_FACTS if view.on(code)]
    if view.on("aw"):
        facts.append(_WATCHES_ADVICE)
    now = "\n".join([_NOW(lang), *(f"— {fact(lang)}" for fact in facts)]) if facts else ""
    return now if view.on("cm") else _text(_COMMUNITY_OFF(lang), now)


def build_community(view: ChatView, lang: str) -> Screen:
    body = _community_now(view, lang)
    if view.on("cm"):
        codes: tuple[str, ...] = ("cm", "gf", "wb", "fp", "fd", "rj", "aw")
        rows: tuple[tuple[Button, ...], ...] = (
            (_toggle(view, "cm", lang),),
            (_toggle(view, "gf", lang), _toggle(view, "wb", lang)),
            (_toggle(view, "fp", lang), _toggle(view, "fd", lang)),
            (_toggle(view, "rj", lang), _toggle(view, "aw", lang)),
        )
    else:
        codes = ("cm", "aw")
        rows = ((_toggle(view, "cm", lang),), (_toggle(view, "aw", lang),))
    return Screen(
        id="community",
        text=_text(_header("community", view, lang), body, _notes(view, codes, lang)),
        rows=(*rows, _back_to_chat(view, lang)),
    )


SECTION_BUILDERS: Final[dict[Section, Callable[[ChatView, str], Screen]]] = {
    "behavior": build_behavior,
    "memory": build_memory,
    "voice": build_voice,
    "stickers": build_stickers,
    "reactions": build_reactions,
    "community": build_community,
}
"""Every section but ``rules``, which is the rules area's own screen."""
