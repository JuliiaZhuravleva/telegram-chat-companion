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
(«Как в общих настройках: …»), never on the button — «⚪ … · общее» read as
"off, or not set?". Which values say it is `sections.shows_common` and nothing
else: a legacy column always holds the chat's own value, so it never does.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from html import escape
from typing import Any, Final

from src.bot.custom_emoji import fence
from src.bot.screens.i18n import Tr, plural_ru
from src.bot.screens.lint import clip_room, label_width_px
from src.bot.screens.model import Button, Screen
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
    "rg": Tr("Не встревать без повода", "Stay out unless relevant"),
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

COMMON = Tr(" · общее", " · all")
"""The rules screen's suffix on its master switch. The chat sections no longer
put it on a button: see `COMMON_NOTE`."""
COMMON_NOTE = Tr("Как в общих настройках: {names}.", "As in the general settings: {names}.")
"""The one line that says which values on a screen come from the general settings."""
_ALL_COMMON = Tr(
    "Все значения на кнопках — как в общих настройках.",
    "Every value on the buttons is as in the general settings.",
)
_COMMON_NAMES: Final[dict[str, Tr]] = {"tol": Tr("Откровенность", "Explicitness")}
"""A name for `COMMON_NOTE` where the button is not a `LABELS` switch."""
TAP_TOGGLES = Tr("Нажатие включает или выключает.", "A tap turns a setting on or off.")

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
_CHATS_BODY = Tr(
    "Подключено: {n}. Рядом с названием — сообщений за сутки.",
    "Connected: {n}. Next to each name: messages in 24 h.",
)
_CHATS_EMPTY = Tr(
    "Пока ни одного чата. Добавьте бота в группу, сюда придёт заявка.",
    "No chats yet. Add the bot to a group and its request will show up here.",
)
_REQUESTS = Tr("🙏 Заявки · {n}", "🙏 Requests · {n}")
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
_DISCONNECT = Tr("🔌 Отключить", "🔌 Disconnect")

SECTION_TITLES: Final[dict[Section, Tr]] = {
    "behavior": Tr("🗣 Поведение", "🗣 Behavior"),
    "memory": Tr("🧠 Память", "🧠 Memory"),
    "voice": Tr("🎙 Голос и фото", "🎙 Voice and photos"),
    "stickers": Tr("🎨 Стикеры", "🎨 Stickers"),
    "reactions": Tr("😀 Реакции", "😀 Reactions"),
    "community": Tr("👋 Сообщество", "👋 Community"),
}
"""Section name on the chat root's button and, emoji kept, in its screen's title."""

_WORDS = Tr("Зовут словами: {words}", "Called by: {words}")
_NO_WORDS = Tr("нет", "none")
_CHANCE = Tr(
    "Сам отвечает: на {pct} % сообщений{interval}",
    "Joins in on its own: on {pct} % of messages{interval}",
)
_NEVER = Tr("Сам не вступает", "Never joins in on its own")
_LANGUAGE = Tr("Язык ответов: {lang}", "Reply language: {lang}")
_LANGUAGES: Final[dict[str, Tr]] = {
    "ru": Tr("русский", "Russian"),
    "en": Tr("английский", "English"),
}
_PROMPT = Tr("Инструкция: {text}", "Instruction: {text}")
_PROMPT_UNSET = Tr("не задана", "not set")
_DB_ONLY = Tr(
    "Слова, частота и язык меняются только в базе.",
    "Words, frequency and language change only in the database.",
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
_KB_COMMON_ON = Tr(
    "База знаний включена в общих настройках.",
    "The knowledge base is on in the settings for all chats.",
)
_KB_COMMON_OFF = Tr(
    "База знаний выключена в общих настройках.",
    "The knowledge base is off in the settings for all chats.",
)

_STICKER_CHANCE = Tr("Отвечает стикером: в {pct} % случаев", "Answers with a sticker: {pct} %")
_STICKER_ON_STICKER = Tr("Стикер на стикер: в {pct} %", "Sticker for a sticker: {pct} %")
_STICKER_ON_PHOTO = Tr("Стикер к фото: в {pct} %", "Sticker for a photo: {pct} %")
_STICKER_ON_STICKER_OFF = Tr("Стикер на стикер: выключен", "Sticker for a sticker: off")
_STICKER_ON_PHOTO_OFF = Tr("Стикер к фото: выключен", "Sticker for a photo: off")
_PERCENT_DB_ONLY = Tr(
    "Проценты меняются только в базе.", "Percentages change only in the database."
)
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
            "Ждать сообщения: здоровается с новичком на его первом сообщении, а не при входе",
            "Wait for a message: greets a newcomer on their first message, not on joining",
        ),
    ),
    ("wb", Tr("Возврат: пишет «с возвращением»", "Welcome back: says “welcome back”")),
    ("fp", Tr("Прощание: прощается в чате", "Farewell: says goodbye in the chat")),
    ("fd", Tr("Уходы: присылает вам сводку", "Departures: sends you a summary")),
    ("rj", Tr("Шутка: шутит, что ушедший вернётся", "Joke: jokes that the leaver will be back")),
    ("aw", Tr("Советы: замечает непрошеные советы", "Advice: notices unsolicited advice")),
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


def _marker(view: ChatView, code: str, lang: str) -> str:  # noqa: ARG001 — callers' shape
    """What a button label adds for an inherited value: nothing.

    The origin is the screen text's (`_notes`); a suffix on the button read as
    the value. Kept, returning "", for the builders that still append it.
    """
    return ""


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


def _notes(view: ChatView, codes: Sequence[str], lang: str, *extra: str) -> str | None:
    """The footnote, in plain text: ``extra`` caveats, then which of ``codes``
    come from the general settings. Plain, not italic — these are the lines a
    reader must not miss, and italics are the first thing glare takes away."""
    lines = list(extra)
    inherited = [code for code in codes if _inherits(view, code)]
    if len(inherited) == len(codes) > 2:
        lines.append(_ALL_COMMON(lang))
    elif inherited:
        names = ", ".join((_COMMON_NAMES.get(code) or LABELS[code])(lang) for code in inherited)
        lines.append(COMMON_NOTE(lang).format(names=names))
    return "\n".join(lines) if lines else None


# ---------------------------------------------------------------------------
# The chat list
# ---------------------------------------------------------------------------


def chats_per_page(requests: int, rejected: int) -> int:
    """Chats per page: one row fewer when the requests row is shown (rows cap)."""
    return 3 if requests or rejected else 4


def _entry_row(lang: str, requests: int, rejected: int) -> tuple[Button, ...] | None:
    row: list[Button] = []
    if requests:
        row.append(Button(_REQUESTS(lang).format(n=requests), cb("adm_wl_pending:", lang, 0)))
    if rejected:
        row.append(Button(_REJECTED(lang).format(n=rejected), cb("adm_wl_rejected:", lang, 0)))
    return tuple(row) or None


def build_chats(
    lang: str,
    chats: Sequence[Mapping[str, Any]],
    *,
    total: int,
    page: int,
    pages: int,
    requests: int = 0,
    rejected: int = 0,
) -> Screen:
    """``chats`` (or ``chats.empty`` when nothing is connected)."""
    entry = _entry_row(lang, requests, rejected)
    tail = (back("hub", cb("adm_menu:", lang), lang),)
    if total == 0:
        empty_rows: tuple[tuple[Button, ...], ...] = (entry, tail) if entry else (tail,)
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
    nav = pager(page, pages, lambda p: cb("adm_pnl:", lang, p))
    rows = tuple(r for r in (entry, *listed, nav, tail) if r)
    return Screen(
        id="chats",
        text=_text(_CHATS_TITLE(lang), _CHATS_BODY(lang).format(n=total)),
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
    """The chat's root: what the bot does here, and its sections."""

    def section(name: Section) -> Button:
        return Button(SECTION_TITLES[name](lang), cb("adm_pnl_grp:", lang, view.chat_id, name))

    url = build_chat_url(view.chat_id, view.chat_type)
    link = f'<a href="{escape(url, quote=True)}">{_OPEN_CHAT(lang)}</a>' if url else None
    return Screen(
        id="chat",
        text=_text(
            f"🗂 <b>{escape(view.name)}</b> <code>{view.chat_id}</code>",
            _status_line(view, lang),
            link,
        ),
        rows=(
            (section("behavior"), section("memory")),
            (section("voice"), section("stickers")),
            (section("reactions"), section("community")),
            (
                Button(_RULES(lang).format(n=view.rules), cb("ar_list:", lang, view.chat_id, 0)),
                Button(_DISCONNECT(lang), cb("adm_wl_rm_ask:", lang, view.chat_id, 0)),
            ),
            _back_to_chats(lang),
        ),
    )


def _back_to_chats(lang: str) -> tuple[Button, ...]:
    return (back("chats", cb("adm_pnl:", lang, 0), lang),)


# ── behavior ──────────────────────────────────────────────────────────────


def _interval(seconds: int, lang: str) -> str:
    if seconds <= 0:
        return ""
    minutes = seconds >= 60 and seconds % 60 == 0
    n = seconds // 60 if minutes else seconds
    if lang == "en":
        unit = "min" if minutes else "s"
        return (
            f", at most once a {'minute' if minutes else 'second'}"
            if n == 1
            else (f", at most once per {n} {unit}")
        )
    if n == 1:
        return f", не чаще раза в {'минуту' if minutes else 'секунду'}"
    words = ("минуту", "минуты", "минут") if minutes else ("секунду", "секунды", "секунд")
    return f", не чаще раза в {n} {plural_ru(n, *words)}"


def _prompt_preview(prompt: str, lang: str) -> str:
    text = prompt.strip()
    if not text:
        return _PROMPT_UNSET(lang)
    first = text.splitlines()[0]
    cut = first[:PROMPT_PREVIEW_CHARS]
    more = "…" if len(cut) < len(text) else ""
    return f"<i>{escape(cut)}{more}</i>"


def build_behavior(view: ChatView, lang: str) -> Screen:
    words = view.value("tw") or ()
    chance = float(view.value("rc") or 0)
    language = str(view.value("lg") or "")
    lang_name = _LANGUAGES.get(language)
    facts = "\n".join(
        (
            _WORDS(lang).format(
                words=", ".join(escape(str(w)) for w in words) if words else _NO_WORDS(lang)
            ),
            _CHANCE(lang).format(
                pct=_pct(chance), interval=_interval(int(view.value("ri") or 0), lang)
            )
            if chance > 0
            else _NEVER(lang),
            _LANGUAGE(lang).format(lang=lang_name(lang) if lang_name else escape(language)),
            _PROMPT(lang).format(text=_prompt_preview(str(view.value("sp") or ""), lang)),
        )
    )
    codes = ("rg", "af", "lc")
    return Screen(
        id="behavior",
        text=_text(
            _header("behavior", view, lang), facts, _notes(view, codes, lang, _DB_ONLY(lang))
        ),
        rows=(
            *((_toggle(view, code, lang),) for code in codes),
            (Button(_PROMPT_BUTTON(lang), cb("adm_pnl_sp:", lang, view.chat_id)),),
            _back_to_chat(view, lang),
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
    """The knowledge base's ``· общее`` is a footnote line: see the module docstring."""
    codes = ("sm", "ch", "rag")
    no_archive = None if view.on("sm") else _NO_ARCHIVE(lang)
    kb_common = (
        [(_KB_COMMON_ON if view.on("kb") else _KB_COMMON_OFF)(lang)]
        if _marker(view, "kb", lang)
        else []
    )
    return Screen(
        id="memory",
        text=_text(
            _header("memory", view, lang),
            "\n".join(line for line in (TAP_TOGGLES(lang), _KB_LINE(lang), no_archive) if line),
            _notes(view, codes, lang, *kb_common),
        ),
        rows=(
            (_toggle(view, "sm", lang),),
            (_toggle(view, "ch", lang),),
            (_toggle(view, "rag", lang),),
            (
                _toggle(view, "kb", lang, marked=False),
                Button(
                    _ORGANIZERS(lang).format(n=view.organizers),
                    cb("adm_kb_orgs:", lang, view.chat_id, 0),
                ),
            ),
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
    lines = [_STICKER_CHANCE(lang).format(pct=_pct(float(view.value("sc") or 0)))]
    lines.append(
        _STICKER_ON_STICKER(lang).format(pct=_pct(float(view.value("sx") or 0)))
        if view.on("sr")
        else _STICKER_ON_STICKER_OFF(lang)
    )
    lines.append(
        _STICKER_ON_PHOTO(lang).format(pct=_pct(float(view.value("ix") or 0)))
        if view.on("ic")
        else _STICKER_ON_PHOTO_OFF(lang)
    )
    tolerance = float(view.value("tol") or 0)
    codes = ("sl", "sr", "ic", "tol")
    tol_label = _TOLERANCE_BUTTON(lang).format(value=tolerance_label(tolerance, lang)) + _marker(
        view, "tol", lang
    )
    return Screen(
        id="chat.stickers",
        text=_text(
            _header("stickers", view, lang),
            "\n".join(lines),
            _notes(view, codes, lang, _PERCENT_DB_ONLY(lang)),
        ),
        rows=(
            (_toggle(view, "sl", lang),),
            (_toggle(view, "sr", lang),),
            (_toggle(view, "ic", lang),),
            (Button(tol_label, cb("adm_pnl_tol:", lang, view.chat_id)),),
            _back_to_chat(view, lang),
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
    if view.bot_is_admin is None:
        status = TAP_TOGGLES(lang)
    else:
        status = (_ADMIN_YES if view.bot_is_admin else _ADMIN_NO)(lang)
    codes = ("rx", "rh") if view.on("rx") else ("rx",)
    return Screen(
        id="reactions",
        text=_text(_header("reactions", view, lang), status, _notes(view, codes, lang)),
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
