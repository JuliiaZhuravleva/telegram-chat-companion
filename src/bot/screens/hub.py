"""The admin panel's root screen (``hub``), its one delivery path `show_hub`, and
the access-request screens the hub's "🗂 Чаты" leads to.

The hub is four doors and a status line. The line reads what is already
recorded — the last health check, the last 24 hours' spend, the number of
waiting requests — and never runs a check: opening the panel must be cheap and
must not change what the next alert says. "🔁 Обновить" on the status screen is
the one place a check is run on demand.

`sample_hub` and `SAMPLE_HUB_EXEMPT` are shared: the stale fallback's
registered sample (``samples/foundation.py``) is the hub under one extra line,
so it draws whatever `sample_hub` returns and carries whatever exemptions this
module declares.

Requests and rejected chats are shown one per page: a number in the text never
has to be matched to a button, and the two actions sit right under the one
chat they act on.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from html import escape
from types import MappingProxyType
from typing import Any, Final

import structlog
from aiogram.types import CallbackQuery, Message

from src.bot.custom_emoji import fence
from src.bot.screens.deps import resolve
from src.bot.screens.i18n import Tr, plural_ru
from src.bot.screens.model import Button, Screen
from src.bot.screens.render import back, pager, show
from src.bot.screens.routes import cb
from src.bot.screens.status import verdict
from src.database.repositories.admin import AdminRepository
from src.database.repositories.response_log import ResponseLogRepository
from src.utils.telegram import build_chat_url

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# hub
# ---------------------------------------------------------------------------

TITLE = Tr("🛠 <b>Панель</b>", "🛠 <b>Panel</b>")

_SPENT = Tr("💰 расход за сутки {v}", "💰 spent in 24 h {v}")
_CHATS = Tr("🗂 Чаты", "🗂 Chats")
_STICKER_PACKS = Tr("📦 Паки стикеров", "📦 Sticker packs")
_STATUS = Tr("📊 Состояние", "📊 Status")
_SETTINGS = Tr("⚙️ Общие настройки", "⚙️ General settings")
_CLOSE = Tr("✖️ Закрыть", "✖️ Close")


def spend_label(value: Decimal | float) -> str:
    """``$0.04``; a sub-cent day keeps four places so it does not read as zero."""
    amount = Decimal(str(value))
    return f"${amount:.4f}" if Decimal(0) < amount < Decimal("0.01") else f"${amount:.2f}"


def _chats_label(lang: str, pending: int) -> str:
    if pending <= 0:
        return _CHATS(lang)
    if lang == "en":
        return f"{_CHATS(lang)} · {pending} request{'' if pending == 1 else 's'}"
    return f"{_CHATS(lang)} · {pending} {plural_ru(pending, 'заявка', 'заявки', 'заявок')}"


def build_hub(
    lang: str,
    *,
    notice: str | None = None,
    notice_raw: str | None = None,
    health: Mapping[str, Any] | None = None,
    spend: Decimal | float | None = None,
    pending: int = 0,
    known: bool = True,
) -> Screen:
    """The hub. ``known=False`` drops the status line: the repositories were unreachable.

    A status line built from nothing would claim "no checks yet" about a bot
    whose database simply could not be asked; the title alone claims nothing.
    """
    lg = "en" if lang == "en" else "ru"
    text = TITLE(lg)
    if known:
        line = verdict(health, lg)
        if spend is not None:
            line += f" · {_SPENT(lg).format(v=spend_label(spend))}"
        text += f"\n\n{line}"
    return Screen(
        id="hub",
        text=text,
        notice=notice,
        notice_raw=notice_raw,
        rows=(
            (Button(_chats_label(lg, pending), callback=cb("adm_pnl:", lg, 0)),),
            (Button(_STICKER_PACKS(lg), callback=cb("adm_stk_sets:", lg, 0)),),
            (Button(_STATUS(lg), callback=cb("adm_health:", lg)),),
            (Button(_SETTINGS(lg), callback=cb("adm_settings:", lg)),),
            (Button(_CLOSE(lg), callback=cb("adm_close:", lg), kind="exit"),),
        ),
    )


def sample_hub(lang: str) -> Screen:
    """The hub as a sample draws it: invented values, nothing read from a database."""
    return build_hub(lang, health={"status": "healthy"}, spend=Decimal("0.04"), pending=1)


SAMPLE_HUB_EXEMPT: Final[Mapping[str, str]] = MappingProxyType({})
"""The rules `sample_hub` breaks, each with its reason (`registry.make_sample`'s
``exempt``). ``test_every_exemption_is_needed`` fails on one that stops firing."""


async def show_hub(
    target: CallbackQuery | Message,
    lang: str,
    *,
    notice: str | None = None,
    notice_raw: str | None = None,
    data: Mapping[str, Any],
) -> Message | None:
    """Show the hub: edit in place for a button, a new message for a command.

    ``data`` is the calling handler's ``**kwargs`` (``dishka_container`` and
    friends); the status line's repositories come from it. When they cannot be
    had — no container, a database error — the hub still opens, without the
    line: this is also the stale fallback's screen and must never fail itself.
    """
    health: Mapping[str, Any] | None = None
    spend: Decimal | None = None
    pending = 0
    known = True
    try:
        admin_repo = await resolve(data, AdminRepository)
        response_log_repo = await resolve(data, ResponseLogRepository)
        health = await admin_repo.get_latest_health_check()
        spend = Decimal(str(await response_log_repo.get_total_cost(timedelta(hours=24))))
        pending = await admin_repo.count_attempts("pending")
    except Exception:
        known = False
        logger.warning("hub_status_unavailable", exc_info=True)
    return await show(
        target,
        build_hub(
            lang,
            notice=notice,
            notice_raw=notice_raw,
            health=health,
            spend=spend,
            pending=pending,
            known=known,
        ),
    )


# ---------------------------------------------------------------------------
# One chat's identity, as the request screens show it
# ---------------------------------------------------------------------------

CHAT_TYPES: Final[Mapping[str, Tr]] = {
    "group": Tr("группа", "group"),
    "supergroup": Tr("супергруппа", "supergroup"),
    "channel": Tr("канал", "channel"),
    "private": Tr("личка", "private chat"),
}
_UNNAMED = Tr("Без названия", "Untitled")


def _who(attempt: Mapping[str, Any]) -> str:
    uname = attempt.get("user_username")
    if uname:
        return f"@{escape(str(uname))}"
    return escape(str(attempt.get("user_first_name") or "")) or "?"


def _said(attempt: Mapping[str, Any]) -> str | None:
    text = attempt.get("message_text")
    return escape(str(text)[:50]) if text else None


def _chat_type(lang: str, attempt: Mapping[str, Any]) -> str:
    ctype = str(attempt.get("chat_type") or "")
    if not ctype:
        return ""
    words = CHAT_TYPES[ctype](lang) if ctype in CHAT_TYPES else escape(ctype)
    return f" <i>{words}</i>"


def _chat_line(lang: str, attempt: Mapping[str, Any], *, link: bool) -> str:
    """``<b>Title</b> <code>-100…</code> <i>группа</i>``; the title is a link when one can be built."""
    chat_id = int(attempt.get("chat_id") or 0)
    title = attempt.get("chat_title")
    name = f"<b>{escape(str(title))}</b>" if title else f"<b>{_UNNAMED(lang)}</b>"
    url = build_chat_url(chat_id, attempt.get("chat_type")) if link else None
    if url:
        name = f'<a href="{url}">{name}</a>'
    return f"{name} <code>{chat_id}</code>{_chat_type(lang, attempt)}"


def _about(lang: str, attempt: Mapping[str, Any], *, link: bool) -> list[str]:
    lines = [_chat_line(lang, attempt, link=link), f"👤 {_who(attempt)}"]
    said = _said(attempt)
    if said:
        lines.append(f"💬 {said}")
    return lines


def _back_to_chats(lang: str) -> tuple[Button, ...]:
    return (back("chats", cb("adm_pnl:", lang, 0), lang),)


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------

_REQUEST_N = Tr("🙏 <b>Заявка {n} из {total}</b>", "🙏 <b>Request {n} of {total}</b>")
_REQUESTS_EMPTY = Tr(
    "🙏 <b>Заявки</b>\n\nНовых заявок нет.", "🙏 <b>Requests</b>\n\nNo new requests."
)
_APPROVE = Tr("👍 Одобрить", "👍 Approve")
_REJECT = Tr("❌ Отклонить", "❌ Reject")

APPROVED: Final = Tr("Новый чат подключён", "New chat connected")
REJECTED: Final = Tr("Отклонено", "Rejected")
ALREADY_HANDLED: Final = Tr("Заявку уже разобрали", "This request was already handled")


def requests_screen(
    lang: str,
    attempt: Mapping[str, Any] | None,
    page: int,
    total: int,
    *,
    notice: str | None = None,
) -> Screen:
    """One waiting request per page; ``attempt=None`` is the empty list."""
    if attempt is None or total <= 0:
        return Screen(
            id="requests", text=_REQUESTS_EMPTY(lang), notice=notice, rows=(_back_to_chats(lang),)
        )
    aid = int(attempt["id"])
    nav = pager(page, total, lambda p: cb("adm_wl_pending:", lang, p))
    return Screen(
        id="requests",
        text="\n".join(
            [
                _REQUEST_N(lang).format(n=page + 1, total=total),
                "",
                *_about(lang, attempt, link=False),
            ]
        ),
        notice=notice,
        rows=(
            (
                Button(
                    _APPROVE(lang), callback=cb("adm_wl_apr:", lang, aid, page), style="success"
                ),
                Button(_REJECT(lang), callback=cb("adm_wl_rej:", lang, aid, page)),
            ),
            *((nav,) if nav else ()),
            _back_to_chats(lang),
        ),
    )


# ---------------------------------------------------------------------------
# rejected, rejected.forget_ask
# ---------------------------------------------------------------------------

_REJECTED_TITLE = Tr("🚫 <b>Отклонённые</b>", "🚫 <b>Rejected</b>")
_REJECTED_INTRO = Tr(
    "Бот молчит в этих чатах и не присылает от них заявок.",
    "The bot stays silent in these chats and sends no requests from them.",
)
_REJECTED_EMPTY = Tr("Отклонённых чатов нет.", "No rejected chats.")
_RESTORE = Tr("↩️ Вернуть в заявки", "↩️ Back to requests")
_FORGET = Tr("🗑 Забыть", "🗑 Forget")
_KEEP = Tr("⬅️ Не забывать", "⬅️ Keep it")
_FORGET_TITLE = Tr("🗑 <b>Забыть отказ?</b>", "🗑 <b>Forget the rejection?</b>")
_FORGET_BODY = Tr(
    "Чат снова сможет писать боту, и вам снова придёт заявка.",
    "The chat will be able to reach the bot again, and a new request will come to you.",
)

RESTORED: Final = Tr("Заявка возвращена", "Request restored")
FORGOTTEN: Final = Tr("Отказ забыт", "Rejection forgotten")
NOT_FOUND: Final = Tr("Запись не найдена", "Record not found")


def rejected_screen(
    lang: str,
    attempt: Mapping[str, Any] | None,
    page: int,
    total: int,
    *,
    notice: str | None = None,
) -> Screen:
    """One rejected chat per page, its title a link when Telegram can open it."""
    if attempt is None or total <= 0:
        return Screen(
            id="rejected",
            text=f"{_REJECTED_TITLE(lang)}\n\n{_REJECTED_EMPTY(lang)}",
            notice=notice,
            rows=(_back_to_chats(lang),),
        )
    aid = int(attempt["id"])
    nav = pager(page, total, lambda p: cb("adm_wl_rejected:", lang, p))
    return Screen(
        id="rejected",
        text="\n".join(
            [
                _REJECTED_TITLE(lang),
                "",
                _REJECTED_INTRO(lang),
                "",
                *_about(lang, attempt, link=True),
            ]
        ),
        notice=notice,
        rows=(
            (
                Button(_RESTORE(lang), callback=cb("adm_wl_restore:", lang, aid, page)),
                Button(_FORGET(lang), callback=cb("adm_wl_del_ask:", lang, aid, page)),
            ),
            *((nav,) if nav else ()),
            _back_to_chats(lang),
        ),
    )


def forget_ask_screen(lang: str, attempt: Mapping[str, Any], page: int) -> Screen:
    aid = int(attempt["id"])
    return Screen(
        id="rejected.forget_ask",
        text="\n".join(
            [
                _FORGET_TITLE(lang),
                "",
                _chat_line(lang, attempt, link=True),
                "",
                _FORGET_BODY(lang),
            ]
        ),
        rows=(
            (
                Button(_FORGET(lang), callback=cb("adm_wl_del:", lang, aid, page)),
                Button(_KEEP(lang), callback=cb("adm_wl_rejected:", lang, page), kind="cancel"),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# chat.off_ask
# ---------------------------------------------------------------------------

_OFF_TITLE = Tr("🔌 <b>Отключить бота здесь?</b>", "🔌 <b>Turn the bot off here?</b>")
_OFF_BODY = Tr(
    "Бот перестанет отвечать в этом чате. Настройки сохранятся.",
    "The bot will stop answering in this chat. Its settings are kept.",
)
_OFF = Tr("🔌 Отключить", "🔌 Turn off")
_STAY = Tr("⬅️ Не отключать", "⬅️ Keep it on")
TURNED_OFF: Final = Tr("Бот отключён в чате «{t}»", "The bot is off in «{t}»")


def off_ask_screen(lang: str, chat_id: int, title: str | None, page: int) -> Screen:
    name = f"<b>{escape(fence(title))}</b>" if title else f"<code>{chat_id}</code>"
    return Screen(
        id="chat.off_ask",
        text=f"{_OFF_TITLE(lang)}\n\n{name}\n{_OFF_BODY(lang)}",
        rows=(
            (
                Button(_OFF(lang), callback=cb("adm_wl_rm:", lang, chat_id, page)),
                Button(_STAY(lang), callback=cb("adm_pnl_menu:", lang, chat_id), kind="cancel"),
            ),
        ),
    )
