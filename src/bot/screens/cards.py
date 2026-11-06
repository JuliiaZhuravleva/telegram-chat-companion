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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from src.bot.screens.i18n import Lang, Tr, lang_of
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
    "Отклонено. Бот молчит в этом чате и больше не пришлёт от него заявок.",
    "Rejected. The bot stays silent in this chat and sends no more requests from it.",
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


def _usd(amount: Decimal) -> str:
    return f"{amount:.2f}"


def spend_card(lang: Lang, *, total: Decimal, limit: Decimal) -> Screen:
    """Over the daily cap: how much, against what; the costs screen is one tap away."""
    body = _SPEND_BODY(lang).format(total=_usd(total), limit=_usd(limit))
    return Screen(
        id="card.spend",
        text=f"{_SPEND_TITLE(lang)}\n\n{body}",
        rows=((Button(_COSTS(lang), callback=cb("adm_costs:", lang, "24h")),),),
    )
