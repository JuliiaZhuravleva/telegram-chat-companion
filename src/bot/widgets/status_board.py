"""The status board: the ``status`` screen as a widget in the admin's private chat.

A form of the existing screen, not a new one: the same health row, the same
verdict words, the same issue lines (``cards.stored_issue_line``). The text
screen stays the main path and the fallback -- the board is sent as a new
message from its "📊 Доской" button and changes nothing else.

Pure builders, like the screens: nothing here touches the database, the
network or aiogram. The handlers (``handlers/admin.py`` sends it,
``widgets/queries.py`` answers it) read, then call these.

The page (``status_board.html``) is fixed for the life of the message, so the
words are baked into it per send in the admin's language, and the state carries
only what changes -- integers and strings, never fractions (Telegram Desktop
truncates them). The state's revision ``r`` is the check's own time: a newer
check is a newer state, and the page computes "checked N min ago" and the
stale warning from it on the device, so a board nobody refreshes still says
how old it is.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from functools import cache
from html import escape
from pathlib import Path
from typing import Any, Final

from src.bot.screens.cards import stored_issue_line
from src.bot.screens.i18n import Lang, Tr, lang_of
from src.bot.screens.status import VERDICTS, _issues, _when

HISTORY_HOURS: Final = 24
"""Cells in the day strip, one per hour, the current hour last."""

STATE_LIMIT: Final = 1024
"""Bytes: the cap on ``chat_data`` and on an ``answerWidgetQuery`` answer alike."""

HTML_LIMIT: Final = 4096
"""Bytes of the page after ``zlib`` level 9, the way Telegram measures it."""

ISSUES_SHOWN: Final = 3
ISSUE_CHARS: Final = 100

_VERDICT_CODE: Final = {"healthy": "h", "warning": "w", "critical": "c", "skipped": "s"}
_STRIP: Final = {3: "c", 2: "w", 1: "h", 0: "s"}
"""``get_health_by_hour``'s rank -> the strip's letter; an hour with no row is ``.``."""

_TITLE = Tr("Состояние", "Status")
BOARD_OFF: Final = Tr(
    "Доска сейчас выключена — состояние на этом экране",
    "The board is off for now — the status is on this screen",
)
BOARD_FAILED: Final = Tr(
    "Telegram не принял доску — состояние на этом экране",
    "Telegram did not take the board — the status is on this screen",
)
_PAGE = Path(__file__).with_name("status_board.html")

# Every word the page shows, in one place. `v` is keyed by the state's verdict
# code; "" is "no checks yet", "?" a status this build does not know.
_WORDS: Final[Mapping[str, Tr]] = {
    "at": Tr("Проверено ", "Checked "),
    "old": Tr(
        "Бот давно не проверял себя — возможно, он остановлен",
        "The bot has not checked itself for a while — it may be down",
    ),
    "more": Tr("и ещё ", "and more: "),
    "ld": Tr("база данных", "database"),
    "lm": Tr("сообщений за 30 мин", "messages, 30 min"),
    "lf": Tr("запасной ИИ за 15 мин", "fallback AI, 15 min"),
    "day": Tr("Последние 24 часа", "Last 24 hours"),
    "d0": Tr("24 ч назад", "24 h ago"),
    "d1": Tr("сейчас", "now"),
    "go": Tr("🔁 Проверить сейчас", "🔁 Check now"),
    "busy": Tr("Проверяю…", "Checking…"),
}
_AGO: Final = (
    Tr("только что", "just now"),
    Tr(" мин назад", " min ago"),
    Tr(" ч назад", " h ago"),
    Tr(" дн. назад", " d ago"),
)
_TAP_ERRORS: Final[Mapping[str, Tr]] = {
    # TelegramWidget.request() rejection codes.
    "TIMEOUT": Tr("Бот не ответил вовремя — можно повторить", "The bot did not answer in time"),
    "RATE_LIMITED": Tr("Слишком часто — подождите пару секунд", "Too fast — wait a few seconds"),
    "BUSY": Tr("Предыдущая проверка ещё идёт", "The previous check is still running"),
    "NOT_ALLOWED": Tr("Здесь проверить нельзя", "Checking is not available here"),
    "FAILED": Tr("Не получилось — можно повторить", "That did not work — try again"),
}
NOTES: Final[Mapping[str, Tr]] = {
    # The state's `e`: what the bot says alongside (or instead of) a state.
    "slow": Tr(
        "Проверка не успела — показана последняя", "The check ran late — showing the last one"
    ),
    "off": Tr(
        "Доска выключена — состояние есть в меню текстом",
        "The board is off — the status is in the menu as text",
    ),
    "no": Tr("Доска только для админов бота", "The board is for bot admins only"),
    "x": Tr("Не получилось — можно повторить", "That did not work — try again"),
}


def strip(history: Sequence[Mapping[str, Any]], now: datetime) -> str:
    """The day strip: one letter per hour, oldest first, the hour of ``now`` last.

    ``history`` is ``get_health_by_hour`` rows (``hour`` a naive UTC hour,
    ``worst`` its rank). Rows outside the window are ignored, so the strip is
    always exactly ``HISTORY_HOURS`` long.
    """
    last = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    first = last - timedelta(hours=HISTORY_HOURS - 1)
    cells = ["."] * HISTORY_HOURS
    for row in history:
        hour = row.get("hour")
        if not isinstance(hour, datetime):
            continue
        if hour.tzinfo is not None:
            hour = hour.astimezone(UTC).replace(tzinfo=None)
        index = int((hour - first).total_seconds() // 3600)
        if 0 <= index < HISTORY_HOURS:
            cells[index] = _STRIP.get(int(row.get("worst") or 0), "s")
    return "".join(cells)


def _cut(text: str, limit: int) -> str:
    """At most ``limit`` characters, cut on a word with an ellipsis."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    space = head.rfind(" ")
    return (head[:space] if space > limit // 2 else head).rstrip(" ,.;:—-") + "…"


def board_state(
    lang: str,
    row: Mapping[str, Any] | None,
    history: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    """The state for a health row, before encoding; ``r`` 0 means "no checks yet"."""
    state: dict[str, Any] = {"r": 0, "v": "", "i": [], "h": strip(history, now)}
    if row is None:
        return state
    checked_at = row.get("checked_at")
    if isinstance(checked_at, datetime):
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=UTC)
        state["r"] = int(checked_at.timestamp())
    issues = _issues(row)
    state.update(
        v=_VERDICT_CODE.get(str(row.get("status", "")).lower(), "?"),
        d=1 if row.get("db_ok", True) else 0,
        m=int(row.get("messages_30m") or 0),
        f=int(row.get("fallbacks_15m") or 0),
        i=[
            [
                "c" if str(issue.get("severity", "warning")) == "critical" else "w",
                _cut(stored_issue_line(lang_of(lang), issue), ISSUE_CHARS),
            ]
            for issue in issues[:ISSUES_SHOWN]
        ],
    )
    if len(issues) > ISSUES_SHOWN:
        state["x"] = len(issues) - ISSUES_SHOWN
    return state


def encode(state: Mapping[str, Any], note: str | None = None) -> str:
    """Compact JSON within ``STATE_LIMIT`` bytes, with an optional note code (``NOTES``).

    Issues are what can grow, so when the text does not fit the last issue
    goes into the "and N more" count until it does. The other fields are
    bounded: numbers, a 24-letter strip, a one-letter verdict.
    """
    data = dict(state)
    if note is not None:
        data["e"] = note
    if "i" not in data:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    issues = list(data["i"] or [])
    while True:
        data["i"] = issues
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(text.encode()) <= STATE_LIMIT or not issues:
            return text
        issues = issues[:-1]
        data["x"] = int(data.get("x") or 0) + 1


@cache
def _page() -> str:
    return _PAGE.read_text(encoding="utf-8")


def words(lang: str) -> dict[str, Any]:
    """The page's ``T``: every label in ``lang``, plus ``l``, the language the page asks in."""
    lg = lang_of(lang)
    table: dict[str, Any] = {key: tr(lg) for key, tr in _WORDS.items()}
    table["l"] = lg
    table["ago"] = [tr(lg) for tr in _AGO]
    table["err"] = {code: tr(lg) for code, tr in _TAP_ERRORS.items()}
    table["e"] = {code: tr(lg) for code, tr in NOTES.items()}
    table["v"] = {code: VERDICTS[status][1](lg) for status, code in _VERDICT_CODE.items()}
    table["v"][""] = Tr("Проверок пока не было", "No checks yet")(lg)
    table["v"]["?"] = Tr("Состояние неизвестно", "Status unknown")(lg)
    return table


def board_html(lang: str) -> str:
    """The page with ``lang``'s words baked in -- what ``sendWidget`` carries."""
    table = json.dumps(words(lang), ensure_ascii=False, separators=(",", ":"))
    # `</` inside a JSON string would end the <script> element early.
    return _page().replace("/*T*/{}", table.replace("</", "<\\/"), 1)


def compressed_size(html: str) -> int:
    """Bytes after ``zlib`` level 9: the number Telegram checks against ``HTML_LIMIT``."""
    return len(zlib.compress(html.encode(), 9))


def board_caption(lang: Lang | str, row: Mapping[str, Any] | None, *, now: datetime) -> str:
    """The text twin under the board: title with the check time, and the verdict.

    Every client shows it, including ones that cannot draw the widget, so it
    says the one thing that matters on its own; the details are in the board
    and on the text screen. HTML, escaped (the bot's default parse mode).
    """
    title = f"📊 <b>{escape(_TITLE(lang))}</b>"
    if row is None:
        return f"{title}\n{escape(words(lang)['v'][''])}"
    checked_at = row.get("checked_at")
    if isinstance(checked_at, datetime):
        title += f" · {_when(checked_at, now)}"
    icon, verdict = VERDICTS.get(str(row.get("status", "")).lower(), ("❔", None))
    said = verdict(lang) if verdict else words(lang)["v"]["?"]
    return f"{title}\n{icon} {escape(said)}"
