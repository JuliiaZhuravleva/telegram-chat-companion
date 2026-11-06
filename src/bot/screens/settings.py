"""General settings: what belongs to the bot as a whole rather than to one chat.

``settings`` is the door (defaults for every chat, notifications, panel
language). ``defs`` lists the same seven sections a chat's own panel has, and
``defs.section`` shows one of them: only the fields that can honestly be set
globally (``settings_fields.new_fields()``), toggles for the yes/no ones and a
text line for the rest.

Pure builders: the handlers read the values and pass them in.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from src.bot.screens.i18n import Tr
from src.bot.screens.model import Button, Screen
from src.bot.screens.render import back
from src.bot.screens.routes import cb
from src.bot.screens.sections import SECTIONS, Section
from src.bot.settings_fields import FIELDS_BY_CODE

# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

_SETTINGS_TEXT = Tr(
    "⚙️ <b>Общие настройки</b>\n\nТо, что не относится к одному чату.",
    "⚙️ <b>General settings</b>\n\nWhat does not belong to a single chat.",
)
_DEFAULTS = Tr("🌍 Для всех чатов", "🌍 All chats")
_NOTIFICATIONS = Tr("🔔 Уведомления", "🔔 Notifications")
_LANGUAGE = Tr("🌐 Язык: Русский", "🌐 Language: English")


def settings_screen(lang: str, *, notice: str | None = None) -> Screen:
    return Screen(
        id="settings",
        text=_SETTINGS_TEXT(lang),
        notice=notice,
        rows=(
            (Button(_DEFAULTS(lang), callback=cb("adm_defs:", lang, 0)),),
            (Button(_NOTIFICATIONS(lang), callback=cb("adm_notif:", lang)),),
            (Button(_LANGUAGE(lang), callback=cb("adm_lang:", lang)),),
            (back("hub", cb("adm_menu:", lang), lang),),
        ),
    )


# ---------------------------------------------------------------------------
# notif
# ---------------------------------------------------------------------------

STICKER_CYCLE: Final = ("off", "on", "detailed")
"""What a new sticker set's report carries, in tap order: nothing, the line, the line and a collage."""

_STICKER_MODES: Final[Mapping[str, Tr]] = {
    "off": Tr("выкл", "off"),
    "on": Tr("кратко", "brief"),
    "detailed": Tr("подробно", "detailed"),
}
_NOTIF_TEXT = Tr(
    "🔔 <b>Уведомления</b>\n\nЧто присылать вам в личку. Сбои бота приходят всегда.",
    "🔔 <b>Notifications</b>\n\nWhat to send to your private chat. Bot failures always arrive.",
)
_REQUESTS_QUIET = Tr(
    "Заявки чатов сейчас не присылаются: они ждут в «🗂 Чаты».",
    "Chat requests are not sent now: they wait in «🗂 Chats».",
)
_STICKERS = Tr("🖼 Новые стикеры: {mode}", "🖼 New stickers: {mode}")
_REQUESTS = Tr("Заявки чатов", "Chat requests")


def sticker_mode(settings: Mapping[str, Any]) -> str:
    """The stored sticker-report mode; anything unknown counts as the first one, off."""
    mode = str(settings.get("sticker", "on"))
    return mode if mode in STICKER_CYCLE else STICKER_CYCLE[0]


def next_sticker_mode(settings: Mapping[str, Any]) -> str:
    return STICKER_CYCLE[(STICKER_CYCLE.index(sticker_mode(settings)) + 1) % len(STICKER_CYCLE)]


def notif_screen(lang: str, settings: Mapping[str, Any]) -> Screen:
    """Two controls. The jailbreak, blacklist and fallback switches are not shown.

    Their stored values still apply; hiding them only stops the screen from
    offering switches nobody needs once the health alerts cover the same ground.
    """
    requests_on = bool(settings.get("unauthorized", True))
    text = _NOTIF_TEXT(lang)
    if not requests_on:
        text += f"\n\n{_REQUESTS_QUIET(lang)}"
    return Screen(
        id="notif",
        text=text,
        rows=(
            (
                Button(
                    _STICKERS(lang).format(mode=_STICKER_MODES[sticker_mode(settings)](lang)),
                    callback=cb("adm_nstk:", lang),
                ),
            ),
            (
                Button(
                    f"{'✅' if requests_on else '⚪'} {_REQUESTS(lang)}",
                    callback=cb("adm_ntog:", lang, "unauthorized"),
                ),
            ),
            (back("settings", cb("adm_settings:", lang), lang),),
        ),
    )


# ---------------------------------------------------------------------------
# lang
# ---------------------------------------------------------------------------

_LANG_TEXT = Tr(
    "🌐 <b>Язык панели</b>\n\nСейчас: русский.",
    "🌐 <b>Panel language</b>\n\nNow: English.",
)
# The English screen names Russian in English: one language per screen.
_RUSSIAN = Tr("🇷🇺 Русский", "🇷🇺 Russian")
_ENGLISH = "🇬🇧 English"
LANG_SET_NOTICE: Final = Tr("Язык панели: русский", "Panel language: English")


def lang_screen(lang: str) -> Screen:
    return Screen(
        id="lang",
        text=_LANG_TEXT(lang),
        rows=(
            (
                Button(_RUSSIAN(lang), callback=cb("adm_lang_set:", lang, "ru")),
                Button(_ENGLISH, callback=cb("adm_lang_set:", lang, "en")),
            ),
            (back("settings", cb("adm_settings:", lang), lang),),
        ),
    )


# ---------------------------------------------------------------------------
# defs, defs.section
# ---------------------------------------------------------------------------

SECTION_NAMES: Final[Mapping[Section, tuple[str, Tr]]] = {
    "behavior": ("🗣", Tr("Поведение", "Behaviour")),
    "memory": ("🧠", Tr("Память", "Memory")),
    "voice": ("🎙", Tr("Голос и фото", "Voice and photos")),
    "stickers": ("🎨", Tr("Стикеры", "Stickers")),
    "reactions": ("😀", Tr("Реакции", "Reactions")),
    "community": ("👋", Tr("Сообщество", "Community")),
    "rules": ("📏", Tr("Правила", "Rules")),
}

_DEFS_TEXT = Tr(
    "🌍 <b>Для всех чатов</b>\n\n"
    "Действуют в каждом чате, где нет своего значения, в том числе в уже подключённых.",
    "🌍 <b>All chats</b>\n\n"
    "These apply in every chat without a value of its own, including chats already connected.",
)


def _section_button(lang: str, section: Section) -> Button:
    icon, name = SECTION_NAMES[section]
    return Button(f"{icon} {name(lang)}", callback=cb("adm_defs_sec:", lang, section))


def defs_screen(lang: str) -> Screen:
    pairs = (SECTIONS[0:2], SECTIONS[2:4], SECTIONS[4:6], SECTIONS[6:])
    return Screen(
        id="defs",
        text=_DEFS_TEXT(lang),
        rows=(
            *(tuple(_section_button(lang, s) for s in pair) for pair in pairs),
            (back("settings", cb("adm_settings:", lang), lang),),
        ),
    )


# Short labels: a phone fits about 17 characters on a full-width button and
# about 8 on a third. The community section pairs its six followers up, so it
# carries a legend saying what each short word does.
TOGGLE_LABELS: Final[Mapping[str, Tr]] = {
    "rg": Tr("Не встревать без повода", "Stay out unless it fits"),
    "lc": Tr("Читать ссылки", "Read links"),
    "ch": Tr("Искать по архиву чата", "Search the chat archive"),
    "kb": Tr("База знаний", "Knowledge base"),
    "tx": Tr("Сворачивать расшифровки", "Collapse transcripts"),
    "sr": Tr("Стикер на стикер", "Sticker for a sticker"),
    "ic": Tr("Стикер к фото", "Sticker on photos"),
    "rx": Tr("Ставить реакции", "Add reactions"),
    "rh": Tr("Запоминать чужие", "Remember others'"),
    "cm": Tr("Режим сообщества", "Community mode"),
    "gf": Tr("Новичок", "Newcomer"),
    "wb": Tr("Возвращение", "Comeback"),
    "fp": Tr("Прощание", "Farewell"),
    "fd": Tr("Уходы", "Departures"),
    "rj": Tr("Шутка", "Joke"),
    "aw": Tr("Советы", "Advice"),
    "re": Tr("Свои правила", "Custom rules"),
}

SECTION_TOGGLES: Final[Mapping[Section, tuple[tuple[str, ...], ...]]] = {
    "behavior": (("rg",), ("lc",)),
    "memory": (("ch",), ("kb",)),
    "voice": (("tx",),),
    "stickers": (("sr",), ("ic",)),
    "reactions": (("rx",), ("rh",)),
    "community": (("cm",), ("gf", "wb"), ("fp", "fd"), ("rj", "aw")),
    "rules": (("re",),),
}
"""Toggle rows per section, by field code. Every bool code of ``new_fields()`` is here once (tested)."""

KEY_OF: Final[Mapping[str, str]] = {
    code: FIELDS_BY_CODE[code].key
    for rows in SECTION_TOGGLES.values()
    for row in rows
    for code in row
}

_SECTION_TITLE = Tr(
    "{icon} <b>{name}</b> · все чаты без своего значения",
    "{icon} <b>{name}</b> · every chat without its own value",
)
_COMMUNITY_LEGEND = Tr(
    "Новичок: приветствовать на первом сообщении\n"
    "Возвращение: «с возвращением» тем, кто вернулся\n"
    "Прощание: прощаться в чате\n"
    "Уходы: сообщать админам об уходе в личку\n"
    "Шутка: «он вернётся»\n"
    "Советы: следить за советами",
    "Newcomer: greet on the first message\n"
    "Comeback: welcome back whoever returns\n"
    "Farewell: say goodbye in the chat\n"
    "Departures: tell the admins in private\n"
    "Joke: «they'll be back»\n"
    "Advice: watch for advice",
)
_RULES_MODES: Final[Mapping[str, Tr]] = {
    "all": Tr("все сработавшие", "every match"),
    "highest_weight": Tr("одно, самое весомое", "the heaviest one"),
    "weighted_random": Tr("одно, случайно по весу", "one, at random by weight"),
}
_STICKER_CHANCE = Tr(
    "Стикер на стикер: в {p} % случаев", "Sticker for a sticker: {p} % of the time"
)
_PHOTO_CHANCE = Tr("Стикер к фото: в {p} % случаев", "Sticker on photos: {p} % of the time")
_TOLERANCE = Tr("Откровенность стикеров: {v}", "Sticker frankness: {v}")
_RULES_MODE = Tr("Срабатывают: {mode}", "Applied: {mode}")


def _percent(value: object) -> str:
    try:
        return f"{float(value) * 100:g}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"


def _info_lines(lang: str, section: Section, values: Mapping[str, Any]) -> list[str]:
    """The values this screen shows but cannot change: a number or a mode is not a yes/no."""
    if section == "stickers":
        return [
            _STICKER_CHANCE(lang).format(p=_percent(values.get("sticker_reply_to_sticker_chance"))),
            _PHOTO_CHANCE(lang).format(p=_percent(values.get("image_comment_sticker_chance"))),
            _TOLERANCE(lang).format(v=values.get("tolerance_level")),
        ]
    if section == "rules":
        mode = str(values.get("rules_mode"))
        label = _RULES_MODES[mode](lang) if mode in _RULES_MODES else mode
        return [_RULES_MODE(lang).format(mode=label)]
    if section == "community":
        return [_COMMUNITY_LEGEND(lang)]
    return []


def defs_section_screen(lang: str, section: Section, values: Mapping[str, Any]) -> Screen:
    icon, name = SECTION_NAMES[section]
    lines = [_SECTION_TITLE(lang).format(icon=icon, name=name(lang))]
    info = _info_lines(lang, section, values)
    if info:
        lines += ["", *info]

    def toggle(code: str) -> Button:
        on = bool(values.get(KEY_OF[code]))
        return Button(
            f"{'✅' if on else '⚪'} {TOGGLE_LABELS[code](lang)}",
            callback=cb("adm_defs_tgl:", lang, code),
        )

    return Screen(
        id="defs.section",
        text="\n".join(lines),
        rows=(
            *(tuple(toggle(code) for code in row) for row in SECTION_TOGGLES[section]),
            (back("defs", cb("adm_defs:", lang, 0), lang),),
        ),
    )
