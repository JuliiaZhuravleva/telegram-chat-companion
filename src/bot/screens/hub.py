"""The admin panel's root screen (``hub``), its one delivery path `show_hub`, and
the access-request screens: the hub's "🙏 Запросы · N" opens the requests
directly, the chat list's own requests row leads to the same screen.

The hub is four doors (five while requests wait) and a status line. The line reads what is already
recorded — the last health check and the last 24 hours' spend — and never
runs a check: opening the panel must be cheap and
must not change what the next alert says. "🔁 Обновить" on the status screen is
the one place a check is run on demand. While requests wait, "🙏 Запросы · N"
shares the first row with "🗂 Чаты": the hub is at its five-row cap, and the
requests were one tap too deep behind a count on the chats button.

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
from src.bot.screens.chat import REQUESTS
from src.bot.screens.deps import resolve
from src.bot.screens.i18n import Tr
from src.bot.screens.model import Button, Screen
from src.bot.screens.orgs import UNDO
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
HUB_REQUESTS: Final = Tr("🙏 Запросы · {n}", "🙏 Requests · {n}")
"""The requests door on the hub. It shares a row with «🗂 Чаты», where a button
gets 163.5 px: the chat list's «🙏 Запросы доступа · 12» is 169 px there and
would be cut through its number, «🙏 Запросы · 999» is 117 px."""
_STICKER_PACKS = Tr("📦 Паки стикеров", "📦 Sticker packs")
_STATUS = Tr("📊 Состояние", "📊 Status")
_SETTINGS = Tr("⚙️ Общие настройки", "⚙️ General settings")
_CLOSE = Tr("✖️ Закрыть", "✖️ Close")


def spend_label(value: Decimal | float) -> str:
    """``$0.04``; a sub-cent day keeps four places so it does not read as zero."""
    amount = Decimal(str(value))
    return f"${amount:.4f}" if Decimal(0) < amount < Decimal("0.01") else f"${amount:.2f}"


def _chats_row(lang: str, pending: int) -> tuple[Button, ...]:
    """«🗂 Чаты», and «🙏 Запросы · N» beside it while requests wait.

    The chats button keeps its place and its plain name either way; the
    requests open their own screen in one tap instead of hiding as a count on
    the chats button.
    """
    chats = Button(_CHATS(lang), callback=cb("adm_pnl:", lang, 0))
    if pending <= 0:
        return (chats,)
    requests = Button(HUB_REQUESTS(lang).format(n=pending), callback=cb("adm_wl_pending:", lang, 0))
    return (chats, requests)


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
            _chats_row(lg, pending),
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

_REQUEST_N = Tr(
    "🙏 <b>Запрос доступа {n} из {total}</b>", "🙏 <b>Access request {n} of {total}</b>"
)
_REQUESTS_EMPTY = Tr(
    "🙏 <b>Запросы доступа</b>\n\nНовых запросов нет.",
    "🙏 <b>Access requests</b>\n\nNo new requests.",
)
_APPROVE = Tr("👍 Одобрить", "👍 Approve")
_REJECT = Tr("❌ Отклонить", "❌ Reject")

APPROVED: Final = Tr("Новый чат подключён", "New chat connected")
REJECTED: Final = Tr("Отклонено", "Rejected")
ALREADY_HANDLED: Final = Tr("Запрос уже решён", "The request is already resolved")


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
    "Бот молчит в этих чатах и не присылает от них запросов доступа.",
    "The bot stays silent in these chats and sends no access requests from them.",
)
_REJECTED_EMPTY = Tr("Отклонённых чатов нет.", "No rejected chats.")
_RESTORE = Tr("↩️ Вернуть в запросы", "↩️ Back to requests")
_FORGET = Tr("🗑 Забыть", "🗑 Forget")
_KEEP = Tr("⬅️ Не забывать", "⬅️ Keep it")
_FORGET_TITLE = Tr("🗑 <b>Забыть отказ?</b>", "🗑 <b>Forget the rejection?</b>")
_FORGET_BODY = Tr(
    "Чат снова сможет писать боту, и вам снова придёт запрос доступа.",
    "The chat will be able to reach the bot again, and a new access request will come to you.",
)

RESTORED: Final = Tr("Запрос возвращён", "Request restored")
FORGOTTEN: Final = Tr("Отказ забыт", "Rejection forgotten")
NOT_FOUND: Final = Tr("Запись не найдена", "Record not found")


def rejected_screen(
    lang: str,
    attempt: Mapping[str, Any] | None,
    page: int,
    total: int,
    *,
    notice: str | None = None,
    requests: int = 0,
) -> Screen:
    """One rejected chat per page, its title a link when Telegram can open it.

    ``requests``: the waiting requests, drawn as «🙏 Запросы доступа · N» on
    top. The handler passes it right after «↩️ Вернуть в запросы», whose next
    step («👍 Одобрить») is on that screen, one tap away instead of two.
    """
    top = (Button(REQUESTS(lang).format(n=requests), cb("adm_wl_pending:", lang, 0)),)
    lead = (top,) if requests > 0 else ()
    if attempt is None or total <= 0:
        return Screen(
            id="rejected",
            text=f"{_REJECTED_TITLE(lang)}\n\n{_REJECTED_EMPTY(lang)}",
            notice=notice,
            rows=(*lead, _back_to_chats(lang)),
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
            *lead,
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
_OFF_WHAT = Tr(
    "Бот перестанет отвечать, и чат уйдёт из «🗂 Чаты». Настройки сохранятся.",
    "The bot stops answering and the chat leaves «🗂 Chats». Its settings are kept.",
)
_OFF_NOW = Tr("Вернуть сразу — «{undo}» в списке.", "Back at once: «{undo}» in the list.")
_OFF_LATER = Tr(
    "Позже — когда в чате напишут, появится запрос доступа (не чаще раза в полчаса): одобрите его.",
    "Later: when someone writes there, an access request appears (at most once per half "
    "hour): approve it.",
)
_OFF_LATER_CHANNEL = Tr(
    "Позже — уберите бота из канала и добавьте снова, появится запрос доступа (не чаще раза "
    "в полчаса): одобрите его.",
    "Later: remove the bot from the channel and add it again; an access request appears (at "
    "most once per half hour): approve it.",
)
_OFF_LATER_REJECTED = Tr(
    "Позже чат сам не попросит: его уже отклоняли. Верните его из «🚫 Отклонённые» в запросы "
    "и одобрите.",
    "Later it will not ask by itself: it was rejected before. Move it from «🚫 Rejected» back "
    "to the requests and approve it.",
)
# An admin's own private chat: `AccessControlMiddleware` lets an admin's DM
# through whatever ``enabled`` says, so "the bot stops answering" would be false.
_OFF_TITLE_DM = Tr("🔌 <b>Отключить этот чат?</b>", "🔌 <b>Turn this chat off?</b>")
_OFF_WHAT_DM = Tr(
    "Это личный чат администратора: здесь бот отвечает всегда, включён чат или нет. "
    "Отключение только уберёт его из «🗂 Чаты»; настройки сохранятся.",
    "This is an admin's private chat: the bot always answers here, on or off. Turning it "
    "off only takes it out of «🗂 Chats»; its settings are kept.",
)
_OFF_LATER_DM = Tr(
    "Позже запроса не будет: из личного чата администратора он не приходит.",
    "Later no request will come: an admin's private chat files none.",
)
# The chat is off already (another admin, an approved card tapped late): the
# question would announce a change that is not one, and its «↩️ Вернуть» would
# turn ON a chat that was off.
_ALREADY_OFF_TITLE = Tr("🔌 <b>Бот здесь уже отключён</b>", "🔌 <b>The bot is already off here</b>")
_ALREADY_OFF_TITLE_DM = Tr("🔌 <b>Этот чат уже отключён</b>", "🔌 <b>This chat is already off</b>")
_ALREADY_OFF_WHAT = Tr(
    "Бот здесь уже не отвечает, и в «🗂 Чаты» этого чата нет. Настройки сохранены.",
    "The bot no longer answers here, and the chat is not in «🗂 Chats». Its settings are kept.",
)
_ALREADY_OFF_WHAT_DM = Tr(
    "Это личный чат администратора: здесь бот отвечает всегда, а в «🗂 Чаты» этого чата уже "
    "нет. Настройки сохранены.",
    "This is an admin's private chat: the bot always answers here, and the chat is no longer "
    "in «🗂 Chats». Its settings are kept.",
)
_OFF = Tr("🔌 Отключить", "🔌 Turn off")
_STAY = Tr("⬅️ Не отключать", "⬅️ Keep it on")
TURNED_OFF: Final = Tr("Бот отключён в чате «{t}»", "The bot is off in «{t}»")
TURNED_OFF_DM: Final = Tr(
    "Чат «{t}» убран из «🗂 Чаты», бот отвечает в нём, как раньше",
    "«{t}» left «🗂 Chats»; the bot answers there as before",
)
ALREADY_OFF: Final = Tr(
    "<i>Бот в чате «{t}» уже был отключён, ничего не изменилось.</i>",
    "<i>The bot was already off in «{t}», nothing changed.</i>",
)
"""A ``notice_raw``: ``{t}`` is HTML-escaped by the caller. Begins with a
letter, not ``✓``, like every line that reports no change."""
TURNED_ON: Final = Tr("Бот снова работает в чате «{t}»", "The bot is back on in «{t}»")
# What «↩️ Вернуть» says when it writes nothing, each a ``notice_raw`` with
# ``{t}`` HTML-escaped by the caller. The undo is a tap on an old render, so the
# chat may have been rejected or lost the bot since; turning it on then would
# override the admin's own refusal, or the re-consent a bot added again files.
UNDO_REJECTED: Final = Tr(
    "<i>Чат «{t}» отклонили после отключения, ничего не изменилось. Чтобы вернуть бота, "
    "перенесите чат из «🚫 Отклонённые» в запросы и одобрите.</i>",
    "<i>«{t}» was rejected after the bot was turned off there, nothing changed. To bring "
    "the bot back, move the chat from «🚫 Rejected» back to the requests and approve it.</i>",
)
UNDO_BOT_GONE: Final = Tr(
    "<i>Бота уже нет в чате «{t}», ничего не изменилось. Чтобы вернуть, снова добавьте его "
    "в чат.</i>",
    "<i>The bot is no longer in «{t}», nothing changed. To bring it back, add it to the chat "
    "again.</i>",
)
UNDO_UNCHECKED: Final = Tr(
    "<i>Не получилось проверить, что бот ещё в чате «{t}», ничего не изменилось. "
    "Попробуйте «{undo}» ещё раз.</i>",
    "<i>Could not check that the bot is still in «{t}», nothing changed. Try «{undo}» again.</i>",
)


def _off_later(lang: str, *, rejected: bool, channel: bool, admin_dm: bool = False) -> str:
    """How the chat comes back later: true for the path each kind of chat really has.

    An admin's private chat never files a request (the middleware answers it
    whether it is on or not), so it is told so before anything else.

    A chat with a rejected attempt on record files no request at all (the
    middleware's ``has_rejected_attempt``, and the add path's check), even when
    the rejection is older than the approval that enabled it -- so once off it
    shows among the rejected and waits there. A channel's posts never reach the
    access gate, so only adding the bot again files a request. Everyone else
    files one with the next message after the 30-minute card cooldown.
    """
    if admin_dm:
        return _OFF_LATER_DM(lang)
    if rejected:
        return _OFF_LATER_REJECTED(lang)
    return (_OFF_LATER_CHANNEL if channel else _OFF_LATER)(lang)


def off_ask_screen(
    lang: str,
    chat_id: int,
    title: str | None,
    page: int,
    *,
    rejected: bool = False,
    channel: bool = False,
    admin_dm: bool = False,
    already_off: bool = False,
) -> Screen:
    """«Отключить бота здесь?»: what turning off does and the two ways back.

    ``rejected``: the chat has a rejected attempt on record
    (`AdminRepository.has_rejected_attempt`); ``channel``: its ``chat_type``;
    ``admin_dm``: it is an admin's own private chat, where the bot answers
    whether it is on or not. The handler reads all three, so the screen never
    promises a request that the gate would swallow, nor a silence that the
    middleware would not keep.

    ``already_off``: ``enabled`` is false already. Then nothing is asked: the
    screen says so, with the honest way back later and «⬅️ Чат», and no
    «Вернуть сразу» line, since nothing is turned off here to undo.
    """
    name = f"<b>{escape(fence(title))}</b>" if title else f"<code>{chat_id}</code>"
    later = _off_later(lang, rejected=rejected, channel=channel, admin_dm=admin_dm)
    if already_off:
        heading = (_ALREADY_OFF_TITLE_DM if admin_dm else _ALREADY_OFF_TITLE)(lang)
        what = (_ALREADY_OFF_WHAT_DM if admin_dm else _ALREADY_OFF_WHAT)(lang)
        return Screen(
            id="chat.off_ask",
            text=f"{heading}\n\n{name}\n{what}\n{later}",
            rows=((back("chat", cb("adm_pnl_menu:", lang, chat_id), lang),),),
        )
    body = "\n".join(
        (
            (_OFF_WHAT_DM if admin_dm else _OFF_WHAT)(lang),
            _OFF_NOW(lang).format(undo=UNDO(lang)),
            later,
        )
    )
    return Screen(
        id="chat.off_ask",
        text=f"{(_OFF_TITLE_DM if admin_dm else _OFF_TITLE)(lang)}\n\n{name}\n{body}",
        rows=(
            (
                Button(_OFF(lang), callback=cb("adm_wl_rm:", lang, chat_id, page)),
                Button(_STAY(lang), callback=cb("adm_pnl_menu:", lang, chat_id), kind="cancel"),
            ),
        ),
    )
