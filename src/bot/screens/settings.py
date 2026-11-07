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

from src.bot.screens import rules
from src.bot.screens.chat import COMMUNITY_LINES, LABELS, tolerance_label
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
    """The stored sticker-report mode, read the way the sender reads it.

    ``handlers/media.py`` sends the report unless the value is exactly ``"off"``,
    and only ``"detailed"`` adds the collage. Anything else (no value, None, a
    word from an older version) therefore gets the brief report, so the screen
    says ``"on"`` for it rather than claiming the reports are off.
    """
    mode = str(settings.get("sticker", "on"))
    return mode if mode in STICKER_CYCLE else "on"


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
_CHANCE = Tr("{label}: в {p} % случаев", "{label}: {p} % of the time")
"""Under the switch of the same name, so it reads that switch's label."""
_TOLERANCE = Tr("Откровенность стикеров: {v}", "Sticker explicitness: {v}")


def _tolerance(value: object, lang: str) -> str:
    """The panel's own words for the value: ``0.5, обычно`` on a preset."""
    try:
        return tolerance_label(float(value), lang)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"


def _percent(value: object) -> str:
    try:
        return f"{float(value) * 100:g}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"


def _info_lines(lang: str, section: Section, values: Mapping[str, Any]) -> list[str]:
    """The values this screen shows but cannot change: a number or a mode is not a yes/no."""
    if section == "stickers":
        return [
            _CHANCE(lang).format(
                label=LABELS["sr"](lang),
                p=_percent(values.get("sticker_reply_to_sticker_chance")),
            ),
            _CHANCE(lang).format(
                label=LABELS["ic"](lang), p=_percent(values.get("image_comment_sticker_chance"))
            ),
            _TOLERANCE(lang).format(v=_tolerance(values.get("tolerance_level"), lang)),
        ]
    if section == "rules":
        return [rules.mode_line(str(values.get("rules_mode")), lang)]
    if section == "community":
        # The six followers sit two to a row, so the short words need saying out.
        return ["\n".join(line(lang) for _, line in COMMUNITY_LINES)]
    return []


def defs_section_screen(lang: str, section: Section, values: Mapping[str, Any]) -> Screen:
    icon, name = SECTION_NAMES[section]
    lines = [_SECTION_TITLE(lang).format(icon=icon, name=name(lang))]
    info = _info_lines(lang, section, values)
    if info:
        lines += ["", *info]

    def toggle(code: str) -> Button:
        on = bool(values.get(KEY_OF[code]))
        # The rules switch says its state the way the rules screen does.
        name = rules.switch_name(on, lang) if code == "re" else LABELS[code](lang)
        return Button(f"{'✅' if on else '⚪'} {name}", callback=cb("adm_defs_tgl:", lang, code))

    return Screen(
        id="defs.section",
        text="\n".join(lines),
        rows=(
            *(tuple(toggle(code) for code in row) for row in SECTION_TOGGLES[section]),
            (back("defs", cb("adm_defs:", lang, 0), lang),),
        ),
    )
