"""Cards the bot sends an admin on its own: an access request, a health alert, the spend cap.

Pure builders — no Bot, no database, no aiogram markup — so a service can build
a card without dragging the handler layer in. A caller that needs markup takes
`render.to_markup` itself; services do that inside the sending function,
because importing ``src.bot.screens.render`` at module level from a service
closes an import cycle (render → reply_flow → services).

Every card is in the admin's language (`admin_lang`), not the chat's: the
reader is the bot admin in their DM, whatever language the chat speaks.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Final

from src.bot.screens.i18n import Lang, Tr, lang_of, plural_ru
from src.bot.screens.model import Button, Screen
from src.bot.screens.routes import cb
from src.utils.telegram import build_chat_url

MESSAGE_PREVIEW_CHARS: Final = 100
"""How much of the applicant's message the access card quotes."""


def admin_lang(raw: object) -> Lang:
    """The panel language from ``bot_config.admin_settings`` (JSON text or a dict).

    The same reading as ``AdminRepository.get_admin_language``, without the
    repository, for the services that only hold a ``BotConfigRepository``.
    Anything unreadable is Russian, the panel's default.
    """
    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return "ru"
    if isinstance(data, Mapping):
        return lang_of(data.get("lang"))
    return "ru"


# ---------------------------------------------------------------------------
# Access request
# ---------------------------------------------------------------------------

_ASKS: Final = Tr("🙏 <b>Новый чат просит доступ</b>", "🙏 <b>A new chat asks for access</b>")
_ADDED: Final = Tr(
    "➕ <b>Бота добавили в новый чат</b>", "➕ <b>The bot was added to a new chat</b>"
)
_PRIVATE: Final = Tr("Личные сообщения", "Private messages")
_NO_NAME: Final = Tr("без имени", "no name")
_NO_TITLE: Final = Tr("Чат без названия", "Untitled chat")
_APPROVE: Final = Tr("👍 Одобрить", "👍 Approve")
_REJECT: Final = Tr("❌ Отклонить", "❌ Reject")
_CHAT_SETTINGS: Final = Tr("⚙️ Настройки чата", "⚙️ Chat settings")
_REJECTED_LIST: Final = Tr("🚫 Отклонённые", "🚫 Rejected")
APPROVED: Final = Tr("Одобрено", "Approved")
REJECTED: Final = Tr(
    "Отклонено. Бот молчит в этом чате и больше не пришлёт от него запросов доступа.",
    "Rejected. The bot stays silent in this chat and sends no more access requests from it.",
)


@dataclass(frozen=True, slots=True)
class Applicant:
    """Who wants in: the chat, the person who wrote, what they wrote."""

    chat_id: int
    chat_title: str | None = None
    chat_type: str | None = None
    chat_username: str | None = None
    user_id: int | None = None
    user_first_name: str | None = None
    user_last_name: str | None = None
    user_username: str | None = None
    message_text: str | None = None
    added: bool = False
    """The bot was added to the chat — nobody wrote, so the heading says so."""

    @classmethod
    def from_attempt(cls, row: Mapping[str, Any], *, added: bool = False) -> Applicant:
        """From an ``unauthorized_attempts`` row (it keeps no username or last name)."""
        return cls(
            chat_id=int(row["chat_id"]),
            chat_title=row.get("chat_title"),
            chat_type=row.get("chat_type"),
            user_id=row.get("user_id"),
            user_first_name=row.get("user_first_name"),
            user_username=row.get("user_username"),
            message_text=row.get("message_text"),
            added=added,
        )


def _chat_line(who: Applicant, lang: Lang) -> str:
    private = who.chat_type == "private"
    if who.chat_title:
        label = html.escape(who.chat_title)
    elif private:
        label = _PRIVATE(lang)
    else:
        # The id follows in <code>; repeating it as the label says nothing.
        label = _NO_TITLE(lang)
    # A private chat's link would only repeat the person's link on the next line.
    url = build_chat_url(who.chat_id, None if private else who.chat_type, who.chat_username)
    shown = f'<a href="{html.escape(url)}">{label}</a>' if url else label
    return f"🗂 {shown} <code>{who.chat_id}</code>"


def _user_line(who: Applicant, lang: Lang, *, with_id: bool) -> str | None:
    name = " ".join(p for p in (who.user_first_name, who.user_last_name) if p).strip()
    if not name and who.user_id is None and not who.user_username:
        return None
    shown = html.escape(name) if name else _NO_NAME(lang)
    if who.user_id is not None:
        shown = f'<a href="tg://user?id={who.user_id}">{shown}</a>'
    parts = [f"👤 {shown}"]
    if who.user_username:
        parts.append(f"@{html.escape(who.user_username.lstrip('@'))}")
    if with_id and who.user_id is not None:
        parts.append(f"<code>{who.user_id}</code>")
    return " ".join(parts)


def _access_text(who: Applicant, lang: Lang, *, with_message: bool, with_user_id: bool) -> str:
    lines = [(_ADDED if who.added else _ASKS)(lang), "", _chat_line(who, lang)]
    user = _user_line(who, lang, with_id=with_user_id)
    if user:
        lines.append(user)
    if with_message and who.message_text:
        lines.append(f"💬 {html.escape(who.message_text[:MESSAGE_PREVIEW_CHARS])}")
    return "\n".join(lines)


def access_card(lang: Lang, who: Applicant, *, attempt_id: int | None) -> Screen:
    """The request itself: who, where, what they wrote; approve or reject.

    ``attempt_id=None`` — the queue row could not be written — draws the card
    without buttons: there is nothing for them to resolve, and the request
    still reaches the panel's list the next time someone writes in that chat.
    """
    rows: tuple[tuple[Button, ...], ...] = ()
    if attempt_id is not None:
        rows = (
            (
                Button(_APPROVE(lang), callback=cb("adm_approve:", lang, attempt_id)),
                Button(_REJECT(lang), callback=cb("adm_reject:", lang, attempt_id)),
            ),
        )
    return Screen(
        id="card.access",
        text=_access_text(who, lang, with_message=True, with_user_id=True),
        rows=rows,
    )


def is_added_card(text: object) -> bool:
    """Whether a sent access card is the "bot was added" kind, judged by its heading.

    The queue row does not record why it was filed, so the handler that
    redraws a resolved card reads the card it is redrawing. Both the current
    heading and the one before the redesign start with ➕.
    """
    return isinstance(text, str) and text.startswith("➕")


def access_approved(lang: Lang, who: Applicant) -> Screen:
    """The card after approval: the request stays readable, the way on is the chat."""
    return Screen(
        id="card.access.ok",
        text=_access_text(who, lang, with_message=False, with_user_id=True),
        notice=APPROVED(lang),
        rows=((Button(_CHAT_SETTINGS(lang), callback=cb("adm_pnl_menu:", lang, who.chat_id)),),),
    )


def access_rejected(lang: Lang, who: Applicant) -> Screen:
    """The card after rejection: the way back is the rejected list."""
    return Screen(
        id="card.access.no",
        text=_access_text(who, lang, with_message=False, with_user_id=False),
        notice=REJECTED(lang),
        rows=((Button(_REJECTED_LIST(lang), callback=cb("adm_wl_rejected:", lang, 0)),),),
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

_FAILING: Final = Tr("🔥 <b>Сбой бота</b>", "🔥 <b>The bot is failing</b>")
_FAILING_AGAIN: Final = Tr("🔥 <b>Сбой бота всё ещё идёт</b>", "🔥 <b>The bot is still failing</b>")
_WARNING: Final = Tr("⚠️ <b>Перебои в работе бота</b>", "⚠️ <b>The bot has problems</b>")
_WARNING_AGAIN: Final = Tr("⚠️ <b>Перебои всё ещё идут</b>", "⚠️ <b>The problems are still there</b>")
_RECOVERED: Final = Tr("💚 <b>Бот снова здоров</b>", "💚 <b>The bot is healthy again</b>")
_STATUS: Final = Tr("📊 Состояние", "📊 Status")
_COUNTS: Final = Tr(
    "За 30 минут сообщений: {messages}, запасной ИИ за 15 минут: {fallbacks}",
    "Messages in 30 min: {messages}, fallback AI in 15 min: {fallbacks}",
)


# The problem lines. The checker hands over FACTS -- which check, how many,
# since when -- and the words are chosen here, in the admin's language. The
# checker's own English message is still what ``health_log`` stores and what a
# keyless fingerprint falls back to; it is not what the admin reads.
_DB_DOWN: Final[Mapping[str, Tr]] = {
    "connection": Tr(
        "База данных недоступна: нет соединения", "The database is unreachable: no connection"
    ),
    "query": Tr(
        "База данных недоступна: запросы не выполняются",
        "The database is unreachable: queries fail",
    ),
    "unexpected": Tr(
        "База данных недоступна: непредвиденная ошибка",
        "The database is unreachable: an unexpected error",
    ),
}
_FALLBACKS: Final = Tr(
    "Запасной ИИ включался {count} {times} за 15 минут",
    "The fallback AI stepped in {count} {times} in 15 min",
)
_FAILED_CALLS: Final = Tr(
    "Вызовы ИИ не прошли за 15 минут: {task} ×{count}",
    "AI calls failed outright in 15 min: {task} ×{count}",
)
_SINCE: Final = Tr(", с {since}", ", since {since}")
_EMBEDDINGS: Final = Tr("эмбеддинги", "embeddings")
_REPLIES: Final = Tr("ответы", "replies")
# Both spellings: ``ai_failure_log`` says "embeddings"/"text_generation",
# ``response_log`` says "embedding"/"text". An unknown task shows its raw name.
_TASKS: Final[Mapping[str, Tr]] = {
    "embeddings": _EMBEDDINGS,
    "embedding": _EMBEDDINGS,
    "transcription": Tr("расшифровка", "transcription"),
    "vision": Tr("картинки", "images"),
    "text_generation": _REPLIES,
    "text": _REPLIES,
}
_CLEARED_DB: Final = Tr("База данных отвечает", "The database answers")
_CLEARED_FALLBACK: Final = Tr(
    "Основной ИИ отвечает без запасного", "The main AI answers without the fallback"
)
_CLEARED_TASK: Final = Tr("Вызовы ИИ проходят: {task}", "AI calls go through: {task}")
# English for each of ``diagnosis``'s hints, keyed by the Russian text it
# returns. Keyed by the text rather than given its own id because the hint is
# part of the failure's de-duplication key: changing what ``diagnosis`` stores
# would make every open failure alert once more. A hint missing here is shown
# in Russian, and a unit test keeps that from happening.
_HINT_EN: Final[Mapping[str, str]] = {
    "Кредиты провайдера кончились — само не пройдёт, нужно пополнить "
    "(Gemini: https://ai.studio/projects, вкладка Billing)": (
        "Provider credits have run out — this will not pass by itself, top up "
        "(Gemini: https://ai.studio/projects, the Billing tab)"
    ),
    "Ключ недействителен — перевыпустить и обновить секрет в vault": (
        "The key is invalid — issue a new one and update the secret in the vault"
    ),
    "Ключу запрещён этот вызов — проверить, включён ли API в проекте": (
        "The key may not make this call — check that the API is enabled in the project"
    ),
    "Выбрана суточная квота — пройдёт само после сброса (у Gemini это 11:00 по Тбилиси)": (
        "The daily quota is used up — passes by itself after the reset "
        "(11:00 Tbilisi time for Gemini)"
    ),
    "Минутная квота — пройдёт само; если держится, воркер бьёт пачкой": (
        "The per-minute quota — passes by itself; if it holds, a worker is sending in bursts"
    ),
    "Похоже на биллинг, а не на скорость — проверить баланс проекта": (
        "Looks like billing rather than rate — check the project balance"
    ),
}


def _task(lang: Lang, task: str) -> str:
    label = _TASKS.get(task)
    return label(lang) if label else task


def _held_text(lang: Lang, since: str, seconds: int) -> str:
    """``03.11 14:05 UTC (2 ч 5 мин)`` from the start as written and the seconds held."""
    hours, minutes = seconds // 3600, seconds % 3600 // 60
    if lang == "en":
        elapsed = f"{hours}h {minutes}m" if hours else f"{minutes}m"
    else:
        elapsed = f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"
    return f"{since} ({elapsed})"


def _held(lang: Lang, since: datetime, at: datetime) -> str:
    """``03.11 14:05 UTC (2 ч 5 мин)``: when it started and how long it has held."""
    seconds = max(int((at - since).total_seconds()), 0)
    return _held_text(lang, since.strftime("%d.%m %H:%M UTC"), seconds)


def db_down_line(lang: Lang, cause: str) -> str:
    """The database check failed; ``cause`` is connection / query / unexpected."""
    return _DB_DOWN.get(cause, _DB_DOWN["unexpected"])(lang)


def fallback_line(lang: Lang, count: int) -> str:
    """The fallback provider answered ``count`` times in the window."""
    if lang == "en":
        times = "time" if count == 1 else "times"
    else:
        times = plural_ru(count, "раз", "раза", "раз")
    return _FALLBACKS(lang).format(count=count, times=times)


def failure_line(
    lang: Lang,
    *,
    task: str,
    count: int,
    provider: str | None,
    since: datetime | None,
    at: datetime,
) -> str:
    """Every provider for ``task`` failed ``count`` times; since when, measured at ``at``."""
    held = _held(lang, since, at) if since else None
    return _failure_text(lang, task=task, count=count, provider=provider, held=held)


def _failure_text(
    lang: Lang, *, task: str, count: int, provider: str | None, held: str | None
) -> str:
    line = _FAILED_CALLS(lang).format(task=_task(lang, task), count=count)
    if provider:
        line += f" ({provider})"
    if held:
        line += _SINCE(lang).format(since=held)
    return line


def hint_line(lang: Lang, hint: str | None) -> str | None:
    """``diagnosis``'s advice in the admin's language (it is written in Russian)."""
    if not hint or lang != "en":
        return hint
    return _HINT_EN.get(hint, hint)


# Reading a stored issue back. ``health_log.issues`` keeps the checker's
# English message and the issue's key, and neither may change: the key is what
# de-duplication compares, and a keyless row's fingerprint IS its message, so
# rewording either would make every open problem alert all admins once more.
# The status screen therefore recovers the facts from those two at render time
# and says them with the same words as the card. The patterns mirror the
# messages ``src/services/health/checker.py`` writes; a unit test round-trips
# every kind through the real checker, so a reworded message fails there
# instead of quietly showing English on the Russian screen.
_STORED_DB_CAUSE: Final[Mapping[str, str]] = {
    "Database connectivity failed: connection unavailable": "connection",
    "Database connectivity failed: query error": "query",
    "Database connectivity failed: unexpected error": "unexpected",
}
_STORED_FALLBACK: Final = re.compile(
    r"AI fallback activated (?P<count>\d+) time\(s\) in last 15 min"
)
_STORED_FAILURE: Final = re.compile(
    r"AI calls failed outright in last 15 min: (?P<task>\S+) x(?P<count>\d+)"
    r"(?: \((?P<provider>[^()]+)\))?"
    r"(?:, holding since (?P<since>\d\d\.\d\d \d\d:\d\d UTC)"
    r" \((?:(?P<hours>\d+)h )?(?P<minutes>\d+)m\))?"
)


def stored_issue_line(lang: Lang, row: Mapping[str, Any]) -> str:
    """A stored ``health_log`` issue in the admin's language, as the card says it.

    The kind comes from the row's key, the facts from its message. A kind this
    does not know, or a message whose shape it does not recognise, shows the
    stored text as it is -- the same fallback the card has for a plain issue.
    """
    key = str(row.get("key") or "")
    message = str(row.get("message") or "")
    kind = key.split(":", 1)[0]
    if kind == "db":
        cause = _STORED_DB_CAUSE.get(message)
        if cause:
            return db_down_line(lang, cause)
    elif kind == "ai_fallback":
        found = _STORED_FALLBACK.fullmatch(message)
        if found:
            return fallback_line(lang, int(found["count"]))
    elif kind == "ai_failure":
        found = _STORED_FAILURE.fullmatch(message)
        if found:
            held = None
            if found["since"]:
                seconds = int(found["hours"] or 0) * 3600 + int(found["minutes"]) * 60
                held = _held_text(lang, found["since"], seconds)
            return _failure_text(
                lang,
                task=found["task"],
                count=int(found["count"]),
                provider=found["provider"],
                held=held,
            )
    return message


def cleared_line(lang: Lang, *, key: str, message: str) -> str:
    """What stopped failing, from a stored issue's key; its message when the key is unknown."""
    if key == "db":
        return _CLEARED_DB(lang)
    if key == "ai_fallback":
        return _CLEARED_FALLBACK(lang)
    if key.startswith("ai_failure:"):
        task = key.split(":")[1]
        if task:
            return _CLEARED_TASK(lang).format(task=_task(lang, task))
    return message


@dataclass(frozen=True, slots=True)
class IssueLine:
    """One problem on the health card."""

    critical: bool
    message: str
    detail: str | None = None
    hint: str | None = None


def _clock(moment: datetime) -> str:
    return moment.strftime("%H:%M UTC")


def health_card(
    lang: Lang,
    *,
    at: datetime,
    critical: bool,
    issues: Sequence[IssueLine],
    messages_30m: int,
    fallbacks_15m: int,
    reminder: bool = False,
) -> Screen:
    """Something is wrong: what, since when, and what to do (the 💡 line)."""
    if critical:
        title = (_FAILING_AGAIN if reminder else _FAILING)(lang)
    else:
        title = (_WARNING_AGAIN if reminder else _WARNING)(lang)
    lines = [f"{title} · {_clock(at)}"]
    if issues:
        lines.append("")
    for issue in issues:
        icon = "🔥" if issue.critical else "⚠️"
        lines.append(f"{icon} {html.escape(issue.message)}")
        if issue.detail:
            # Plain text, not <code>: Telegram drops links inside code spans,
            # and a provider's billing URL is the most useful part of a detail.
            lines.append(f"     {html.escape(issue.detail)}")
        if issue.hint:
            lines.append(f"     💡 {html.escape(issue.hint)}")
    lines.append("")
    counts = _COUNTS(lang).format(messages=messages_30m, fallbacks=fallbacks_15m)
    lines.append(f"<i>{counts}</i>")
    return Screen(
        id="card.health",
        text="\n".join(lines),
        rows=((Button(_STATUS(lang), callback=cb("adm_health:", lang)),),),
    )


def recovered_card(lang: Lang, *, at: datetime, cleared: Sequence[str]) -> Screen:
    """The all-clear, naming what stopped failing."""
    lines = [f"{_RECOVERED(lang)} · {_clock(at)}"]
    if cleared:
        lines.append("")
        lines.extend(f"✓ {html.escape(item)}" for item in cleared)
    return Screen(id="card.recovered", text="\n".join(lines))


# ---------------------------------------------------------------------------
# Spend cap
# ---------------------------------------------------------------------------

_SPEND_TITLE: Final = Tr(
    "💰 <b>Дневной лимит расходов превышен</b>", "💰 <b>The daily spend limit is exceeded</b>"
)
_SPEND_BODY: Final = Tr(
    "${total} при лимите ${limit}. Бот продолжает отвечать.",
    "${total} against a limit of ${limit}. The bot keeps answering.",
)
_COSTS: Final = Tr("💰 Расходы", "💰 Costs")


_CENT: Final = Decimal("0.01")


def spend_card(lang: Lang, *, total: Decimal, limit: Decimal) -> Screen:
    """Over the daily cap: how much, against what; the costs screen is one tap away.

    The total is rounded up and the limit down, so a card that says "exceeded"
    never shows two equal numbers: plain rounding prints $1.004 over $1.00 as
    "$1.00 при лимите $1.00".
    """
    shown_total = total.quantize(_CENT, rounding=ROUND_CEILING)
    shown_limit = limit.quantize(_CENT, rounding=ROUND_FLOOR)
    body = _SPEND_BODY(lang).format(total=f"{shown_total:.2f}", limit=f"{shown_limit:.2f}")
    return Screen(
        id="card.spend",
        text=f"{_SPEND_TITLE(lang)}\n\n{body}",
        rows=((Button(_COSTS(lang), callback=cb("adm_costs:", lang, "24h")),),),
    )
