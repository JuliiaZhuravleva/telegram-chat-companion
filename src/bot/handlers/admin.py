"""Admin panel handlers: the hub, requests and rejected chats, status, costs, general settings.

Entry points, all in the private chat with the bot and for bot admins only:

- ``/admin`` → the hub; ``/settings`` → general settings; ``/costs`` → the
  costs screen as a new message.
- ``adm_…`` buttons → the screens in `src/bot/screens/hub.py`, ``status.py``
  and ``settings.py``.

Who may tap and where is decided by the FILTERS on each handler (`IsAdmin` and
the private-chat check), never inside the body: aiogram consumes an update once
a handler's filters match, so a guard in the body would swallow taps the stale
catch-all should answer. A non-admin's tap, or a tap in a group, falls through
to `src/bot/screens/stale.py`, which says why.

Turning the bot off in one chat is ``adm_wl_rm_ask:`` (the question, reached
from the chat's screen and from an approved access card) → ``adm_wl_rm:`` (the
write, then the chat list with «↩️ Вернуть» on that render) → ``adm_wl_on:``
(the way back: ``enabled`` true again, the list with a line saying so). Both
the question and the write read ``enabled`` first: a chat that is off already
is told so, and gets no «↩️ Вернуть» that would turn ON a chat that was off.

Payloads carry the panel language first: ``adm_{action}:{lang}:{args…}``. A
payload that cannot be parsed comes from a stale button: `show_stale` answers
it with the hub and a line saying nothing changed. An object that is gone (a
request someone already resolved, a forgotten rejection) is not stale — the
handler shows the parent screen with a notice of its own.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from decimal import Decimal
from html import escape
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.filters.admin import IsAdmin
from src.bot.handlers.access_card import approve_attempt, reject_attempt
from src.bot.handlers.admin_chat_panel import CHAT_GONE, show_chat, show_chats
from src.bot.screens.deps import resolve
from src.bot.screens.hub import (
    ALREADY_HANDLED,
    ALREADY_OFF,
    APPROVED,
    FORGOTTEN,
    NOT_FOUND,
    REJECTED,
    RESTORED,
    TURNED_OFF,
    TURNED_OFF_DM,
    TURNED_ON,
    forget_ask_screen,
    off_ask_screen,
    rejected_screen,
    requests_screen,
    show_hub,
)
from src.bot.screens.i18n import lang_of
from src.bot.screens.model import Screen
from src.bot.screens.render import page_slice, show
from src.bot.screens.routes import parse
from src.bot.screens.settings import (
    LANG_SET_NOTICE,
    lang_screen,
    next_sticker_mode,
    notif_screen,
    settings_screen,
)
from src.bot.screens.stale import show_stale
from src.bot.screens.status import (
    INTERVALS,
    PERIODS,
    costs_error_screen,
    costs_missing_screen,
    costs_screen,
    costs_verify_screen,
    health_text,
    stats_screen,
    status_screen,
)
from src.bot.utils import check_admin_direct
from src.config import Settings
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.response_log import ResponseLogRepository
from src.services.chat_config import ChatConfigService

logger = structlog.get_logger(__name__)

router = Router(name="admin")

Handler = Callable[..., Awaitable[None]]


def _on(prefix: str) -> Callable[[Handler], Handler]:
    """Register a panel button: this prefix, in the private chat, from a bot admin."""
    return router.callback_query(
        F.data.startswith(prefix), F.message.chat.type == "private", IsAdmin()
    )


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def _lang(callback: CallbackQuery) -> str:
    """The panel language a payload carries; Russian when it carries none."""
    parts = (callback.data or "").split(":")
    return lang_of(parts[1] if len(parts) > 1 else None)


def _ints(callback: CallbackQuery, n: int) -> tuple[str, list[int]] | None:
    """The language and the first ``n`` integer fields of a payload; None when garbled."""
    fields = parse(callback.data, n + 1)
    if fields is None:
        return None
    try:
        return lang_of(fields[0]), [int(field) for field in fields[1:]]
    except ValueError:
        return None


def _period(callback: CallbackQuery) -> tuple[str, str] | None:
    """The language and the period of a ``…:{lang}:{period}`` payload.

    An old button that wrote no period means the last 24 hours; a period this
    panel does not offer is a garbled payload.
    """
    fields = (callback.data or "").split(":")[1:]
    if not fields or not fields[0]:
        return None
    period = fields[1] if len(fields) > 1 and fields[1] else "24h"
    return (lang_of(fields[0]), period) if period in PERIODS else None


def _format_health_status(row: dict[str, Any], lang: str) -> str:
    """The status screen's text for one ``health_log`` row."""
    return health_text(row, lang)


# ---------------------------------------------------------------------------
# Commands: /admin, /settings, /costs
# ---------------------------------------------------------------------------


@router.message(Command("admin"), F.chat.type == "private", IsAdmin())
async def handle_admin_command(
    message: Message,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    """``/admin`` → the hub, as a new message."""
    lang = lang_of(await admin_repo.get_admin_language(bot_config_repo))
    await show_hub(message, lang, data=kwargs)


@router.message(Command("settings"), F.chat.type == "private", IsAdmin())
async def handle_settings_command(
    message: Message,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """``/settings`` → general settings, as a new message."""
    lang = lang_of(await admin_repo.get_admin_language(bot_config_repo))
    await show(message, settings_screen(lang))


@router.message(Command("costs"), F.chat.type == "private", IsAdmin())
async def handle_costs_command(
    message: Message,
    response_log_repo: FromDishka[ResponseLogRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """``/costs`` → the last 24 hours' costs, as a new message."""
    lang = lang_of(await admin_repo.get_admin_language(bot_config_repo))
    await show(message, await _costs(lang, "24h", response_log_repo))


# ---------------------------------------------------------------------------
# The hub
# ---------------------------------------------------------------------------


@_on("adm_menu:")
async def handle_menu(callback: CallbackQuery, **kwargs: Any) -> None:
    """Back to the hub."""
    await show_hub(callback, _lang(callback), data=kwargs)


@_on("adm_close:")
async def handle_close(callback: CallbackQuery, **_kwargs: Any) -> None:
    """``✖️ Закрыть``: delete the panel message."""
    await callback.answer()
    msg = callback.message
    if isinstance(msg, Message):
        try:
            await msg.delete()
        except Exception:
            logger.warning("Failed to delete admin panel message")


@router.callback_query(F.data == "noop")
async def handle_noop(callback: CallbackQuery, **_kwargs: Any) -> None:
    """A button that shows a state, not an action (the pager's ``n/N``)."""
    await callback.answer()


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

Fetch = Callable[[int, int], Awaitable[tuple[list[dict[str, Any]], int]]]


async def _one(fetch: Fetch, page: int) -> tuple[dict[str, Any] | None, int, int]:
    """One row of a list shown a row per page: ``(row, page, total)``, the page clamped.

    An old button can point past the end of a list that has since shrunk; it
    shows the last row then, not an empty page.
    """
    rows, total = await fetch(max(page, 0), 1)
    clamped = page_slice(total, page, 1)[0]
    if total and not rows:
        rows, total = await fetch(clamped, 1)
        clamped = page_slice(total, clamped, 1)[0]
    return (rows[0] if rows else None), clamped, total


async def _with_title(
    callback: CallbackQuery, attempt: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Fill a missing chat title from Telegram; the row stays as it was on failure."""
    if attempt is None or attempt.get("chat_title") or callback.bot is None:
        return attempt
    try:
        chat = await callback.bot.get_chat(attempt["chat_id"])
        title = chat.title or chat.full_name
    except Exception:
        logger.debug("Chat title fallback failed", chat_id=attempt.get("chat_id"), exc_info=True)
        return attempt
    return {**attempt, "chat_title": title}


async def _show_requests(
    callback: CallbackQuery,
    admin_repo: AdminRepository,
    lang: str,
    page: int,
    *,
    notice: str | None = None,
) -> None:
    attempt, page, total = await _one(admin_repo.get_pending_attempts_page, page)
    attempt = await _with_title(callback, attempt)
    await show(callback, requests_screen(lang, attempt, page, total, notice=notice))


@_on("adm_wl_pending:")
async def handle_wl_pending(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    args = _ints(callback, 1)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, (page,) = args
    await _show_requests(callback, admin_repo, lang, page)


@_on("adm_wl_apr:")
async def handle_wl_approve(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """Approve a request and open the new chat's screen.

    `approve_attempt` enables the chat before anything is rendered, so the chat
    screen already reads it as connected.
    """
    args = _ints(callback, 2)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, (attempt_id, page) = args
    attempt = await approve_attempt(admin_repo, chat_settings_repo, attempt_id)
    if attempt is None:
        await _show_requests(callback, admin_repo, lang, page, notice=ALREADY_HANDLED(lang))
        return
    chat_id = int(attempt["chat_id"])
    await _invalidate(kwargs, chat_id)
    await show_chat(callback, lang, chat_id, notice=APPROVED(lang), data=kwargs)


@_on("adm_wl_rej:")
async def handle_wl_reject(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Reject a request: the next one with a notice, or the chat list after the last."""
    args = _ints(callback, 2)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, (attempt_id, page) = args
    attempt = await reject_attempt(admin_repo, attempt_id)
    if attempt is None:
        await _show_requests(callback, admin_repo, lang, page, notice=ALREADY_HANDLED(lang))
        return
    if await admin_repo.count_attempts("pending") == 0:
        await show_chats(callback, lang, 0, notice=REJECTED(lang), data=kwargs)
        return
    await _show_requests(callback, admin_repo, lang, page, notice=REJECTED(lang))


# ---------------------------------------------------------------------------
# Rejected chats
# ---------------------------------------------------------------------------


async def _show_rejected(
    callback: CallbackQuery,
    admin_repo: AdminRepository,
    lang: str,
    page: int,
    *,
    notice: str | None = None,
    requests: int = 0,
) -> None:
    attempt, page, total = await _one(admin_repo.get_rejected_attempts_page, page)
    screen = rejected_screen(lang, attempt, page, total, notice=notice, requests=requests)
    await show(callback, screen)


@_on("adm_wl_rejected:")
async def handle_wl_rejected(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    args = _ints(callback, 1)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, (page,) = args
    await _show_rejected(callback, admin_repo, lang, page)


@_on("adm_wl_restore:")
async def handle_wl_restore(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Put a rejected chat back among the requests, and offer them in one tap:
    approving it is the step the admin came for."""
    args = _ints(callback, 2)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, (attempt_id, page) = args
    attempt = await admin_repo.get_attempt(attempt_id)
    if not attempt:
        await _show_rejected(callback, admin_repo, lang, page, notice=NOT_FOUND(lang))
        return
    if attempt.get("status") != "rejected":
        # Another admin approved it, or restored it a moment ago: nothing to undo.
        await _show_rejected(callback, admin_repo, lang, page, notice=ALREADY_HANDLED(lang))
        return
    await admin_repo.update_attempt_status(attempt_id, "pending")
    requests = await admin_repo.count_attempts("pending")
    await _show_rejected(callback, admin_repo, lang, page, notice=RESTORED(lang), requests=requests)


@_on("adm_wl_del_ask:")
async def handle_wl_delete_ask(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Ask before forgetting a rejection: the chat will be able to ask again."""
    args = _ints(callback, 2)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, (attempt_id, page) = args
    attempt = await admin_repo.get_attempt(attempt_id)
    if not attempt:
        await _show_rejected(callback, admin_repo, lang, page, notice=NOT_FOUND(lang))
        return
    await show(callback, forget_ask_screen(lang, attempt, page))


@_on("adm_wl_del:")
async def handle_wl_delete(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Forget a rejection and go back to the rejected chats."""
    args = _ints(callback, 2)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, (attempt_id, page) = args
    deleted = await admin_repo.delete_attempt(attempt_id)
    notice = FORGOTTEN(lang) if deleted else NOT_FOUND(lang)
    await _show_rejected(callback, admin_repo, lang, page, notice=notice)


# ---------------------------------------------------------------------------
# Turning the bot off in one chat
# ---------------------------------------------------------------------------


async def _invalidate(data: dict[str, Any], chat_id: int) -> None:
    """Drop the chat's cached config.

    Defensive rather than corrective: the service is request-scoped, so its
    cache never outlives this update anyway (TD-046).
    """
    try:
        service = await resolve(data, ChatConfigService)
        service.invalidate(chat_id)
    except Exception:
        logger.debug("chat_config_invalidate_skipped", chat_id=chat_id, exc_info=True)


def _title_of(row: Mapping[str, Any] | None) -> str | None:
    title = row.get("chat_title") if row else None
    return str(title) if title else None


async def _is_admin_dm(
    row: Mapping[str, Any] | None, chat_id: int, bot_config_repo: BotConfigRepository
) -> bool:
    """An admin's own private chat: `AccessControlMiddleware` answers it whether
    ``enabled`` is on or not, so turning it off silences nothing."""
    if row is None or row.get("chat_type") != "private":
        return False
    return await check_admin_direct(bot_config_repo, chat_id)


def _is_off(row: Mapping[str, Any]) -> bool:
    """``enabled`` is false already (the column is NOT NULL; a row without it is on)."""
    return not row.get("enabled", True)


@_on("adm_wl_rm_ask:")
async def handle_wl_remove_ask(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    """«Отключить бота здесь?», from the chat's screen or an approved access card.

    The way back it describes depends on the chat: one with a rejected attempt
    on record will never file a request, a channel files one only when the bot
    is added again, an admin's own private chat never files one and is answered
    either way. A chat already off is told so instead of asked (an approved
    card tapped after another admin turned it off). All of it is read here,
    where the real values are.
    """
    args = _ints(callback, 2)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, (chat_id, page) = args
    row = await chat_settings_repo.get(chat_id)
    rejected = await admin_repo.has_rejected_attempt(chat_id)
    screen = off_ask_screen(
        lang,
        chat_id,
        _title_of(row),
        page,
        rejected=rejected,
        channel=row is not None and row.get("chat_type") == "channel",
        admin_dm=await _is_admin_dm(row, chat_id, bot_config_repo),
        already_off=row is not None and _is_off(row),
    )
    await show(callback, screen)


@_on("adm_wl_rm:")
async def handle_wl_remove(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    """Turn the bot off in a chat (its settings stay) and show the chat list
    with «↩️ Вернуть» for it, on this render only.

    The undo is offered only for a change from on to off. A chat with no
    settings row has nothing to turn off, and a chat already off was not
    turned off here: the list says which, writes nothing and offers no undo,
    since «↩️ Вернуть» would turn ON a chat that was off before the tap.

    Read, then write, rather than one conditional UPDATE: two admins tapping
    at once both see an undo for a chat that was on, which is true for both.
    """
    args = _ints(callback, 2)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, (chat_id, page) = args
    row = await chat_settings_repo.get(chat_id)
    if row is None:
        await show_chats(callback, lang, page, notice_raw=CHAT_GONE(lang), data=kwargs)
        return
    title = _title_of(row) or str(chat_id)
    if _is_off(row):
        notice_raw = ALREADY_OFF(lang).format(t=escape(title))
        await show_chats(callback, lang, page, notice_raw=notice_raw, data=kwargs)
        return
    if not await chat_settings_repo.set_field(chat_id, "enabled", False):
        await show_chats(callback, lang, page, notice_raw=CHAT_GONE(lang), data=kwargs)
        return
    await _invalidate(kwargs, chat_id)
    dm = await _is_admin_dm(row, chat_id, bot_config_repo)
    notice = (TURNED_OFF_DM if dm else TURNED_OFF)(lang).format(t=title)
    await show_chats(callback, lang, page, notice=notice, undo=chat_id, data=kwargs)


@_on("adm_wl_on:")
async def handle_wl_on(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """«↩️ Вернуть» after a turn-off: the bot answers in the chat again.

    Only ``enabled`` moves, as the turn-off moved only ``enabled``, so the
    settings are the ones it had. A chat that is already on (another admin,
    an approval in between, a second tap) gets the same list and line with
    nothing written; a chat with no settings row says so on the list.
    """
    args = _ints(callback, 2)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, (chat_id, page) = args
    row = await chat_settings_repo.get(chat_id)
    if row is None:
        await show_chats(callback, lang, page, notice_raw=CHAT_GONE(lang), data=kwargs)
        return
    if not row.get("enabled"):
        await chat_settings_repo.set_field(chat_id, "enabled", True)
        await _invalidate(kwargs, chat_id)
    notice = TURNED_ON(lang).format(t=_title_of(row) or chat_id)
    await show_chats(callback, lang, page, notice=notice, data=kwargs)


# ---------------------------------------------------------------------------
# Status, statistics
# ---------------------------------------------------------------------------


@_on("adm_health:")
async def handle_health(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """The last recorded health check; ``…:now`` (``🔁 Обновить``) runs a fresh one first."""
    parts = (callback.data or "").split(":")
    if len(parts) < 2 or not parts[1]:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang = lang_of(parts[1])
    answered = False
    health_checker = kwargs.get("health_checker")
    if len(parts) > 2 and parts[2] == "now" and health_checker is not None:
        # A check can take seconds: stop the spinner first.
        await callback.answer()
        answered = True
        try:
            await health_checker.run_check_now()
        except Exception:
            # The screen still shows the last recorded check; its time says how old.
            logger.warning("health_refresh_failed", exc_info=True)
    row = await admin_repo.get_latest_health_check()
    await show(callback, status_screen(lang, row), answered=answered)


@_on("adm_stats:")
async def handle_stats(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    args = _period(callback)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, period = args
    interval = INTERVALS[period]
    screen = stats_screen(
        lang,
        period,
        enabled=await admin_repo.get_enabled_chats_count(),
        active=await admin_repo.get_active_chats_count(interval),
        messages=await admin_repo.get_message_count(interval),
        responses=await admin_repo.get_response_count(interval),
        unauth=await admin_repo.get_unauth_count(interval),
    )
    await show(callback, screen)


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------


async def _costs(lang: str, period: str, response_log_repo: ResponseLogRepository) -> Screen:
    interval = INTERVALS[period]
    return costs_screen(
        lang,
        period,
        total=await response_log_repo.get_total_cost(interval),
        by_model=await response_log_repo.get_cost_by_model(interval),
        by_task=await response_log_repo.get_cost_by_task_type(interval),
    )


@_on("adm_costs:")
async def handle_costs(
    callback: CallbackQuery,
    response_log_repo: FromDishka[ResponseLogRepository],
    **kwargs: Any,
) -> None:
    args = _period(callback)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, period = args
    await show(callback, await _costs(lang, period, response_log_repo))


# Unlike the other admin screens, this one makes an outbound call to a metered
# third-party API on every press. Held or double-tapped, it is the one button
# here that can cost money and earn a rate limit, so it gets its own cooldown.
_VERIFY_COOLDOWN_SECONDS = 10.0
_VERIFY_COOLDOWN_MAX_ENTRIES = 100
_last_verify: dict[int, float] = {}

_VERIFY_COOLDOWN_MSG: dict[str, str] = {
    "ru": "Подождите немного перед повторной сверкой.",
    "en": "Give it a moment before checking again.",
}


def _verify_on_cooldown(user_id: int) -> bool:
    """Report whether this admin checked too recently, and record the attempt.

    Monotonic clock deliberately: a wall-clock jump (NTP, DST) must not hand
    out a free pass or a stuck lockout.
    """
    now = time.monotonic()
    last = _last_verify.get(user_id)
    if last is not None and now - last < _VERIFY_COOLDOWN_SECONDS:
        return True
    # Bounded: one entry per admin who ever pressed the button would otherwise
    # live for the process lifetime. Drop everything already expired.
    if len(_last_verify) >= _VERIFY_COOLDOWN_MAX_ENTRIES:
        for uid, ts in list(_last_verify.items()):
            if now - ts >= _VERIFY_COOLDOWN_SECONDS:
                del _last_verify[uid]
    _last_verify[user_id] = now
    return False


@_on("adm_costs_verify:")
async def handle_costs_verify(
    callback: CallbackQuery,
    response_log_repo: FromDishka[ResponseLogRepository],
    settings: FromDishka[Settings],
    **kwargs: Any,
) -> None:
    """Cross-check our own OpenAI costs with OpenAI's billing report."""
    from src.services.ai.billing import OpenAIBillingClient

    args = _period(callback)
    if args is None:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, period = args

    # Before any other answer: the cooldown toast IS the answer, and it leaves
    # the screen untouched, which is also what a rejected press should do.
    user = callback.from_user
    if user is not None and _verify_on_cooldown(user.id):
        await callback.answer(_VERIFY_COOLDOWN_MSG[lang])
        return

    # Both settings are required, and neither substitutes for the other: the key
    # buys access, the project id makes the answer about this bot. Check before
    # spending a round trip, and name what is missing rather than saying "not
    # configured" — a half-done setup is the likeliest state to land here in.
    api_key = settings.openai_admin_api_key or ""
    project_id = settings.openai_project_id or ""
    missing = [
        name
        for name, value in (("OPENAI_ADMIN_API_KEY", api_key), ("OPENAI_PROJECT_ID", project_id))
        if not value
    ]
    if missing:
        await show(callback, costs_missing_screen(lang, period, missing))
        return

    # The billing call can take seconds: stop the spinner first.
    await callback.answer()
    try:
        client = OpenAIBillingClient(api_key)
        try:
            report = await client.get_costs(
                days={"1h": 1, "24h": 1, "7d": 7}[period], project_id=project_id
            )
        finally:
            await client.close()
    except Exception:
        # The query is answered already, so the global error handler can no
        # longer say anything: without this screen the tap would end in silence.
        logger.warning("costs_verify_failed", period=period, exc_info=True)
        error_screen = costs_error_screen(lang, period, code="unexpected", error="Unexpected error")
        await show(callback, error_screen, answered=True)
        return

    if report.error:
        # The screen shows the localised reason; the client's own words, which
        # can name an exception class, are kept here.
        logger.warning(
            "costs_verify_refused", period=period, code=report.error_code, error=report.error
        )
        error_screen = costs_error_screen(lang, period, code=report.error_code, error=report.error)
        await show(callback, error_screen, answered=True)
        return

    # OpenAI answers in whole aligned buckets, which routinely reach further
    # back than the window we asked for (its smallest bucket is a full day, so
    # "1h" gets a day or more). Comparing our own figure over the period the
    # admin picked would subtract two different windows and report the
    # difference as a costing error. Measure ourselves over whatever span the
    # buckets actually cover.
    interval = INTERVALS[period]
    if report.covered_from is not None:
        covered_seconds = max(int(time.time()) - report.covered_from, 0)
        if covered_seconds:
            interval = timedelta(seconds=covered_seconds)

    try:
        by_provider = await response_log_repo.get_cost_by_provider(interval)
        ours = next(
            (Decimal(row["total_cost"]) for row in by_provider if row["provider"] == "openai"),
            Decimal("0"),
        )
    except Exception:
        # Answered already, as above: a failed read of our own figure must end
        # on a screen with a retry, not on the one the admin pressed.
        logger.warning("costs_verify_ours_failed", period=period, exc_info=True)
        error_screen = costs_error_screen(
            lang, period, code="ours_failed", error="Own costs could not be read"
        )
        await show(callback, error_screen, answered=True)
        return

    screen = costs_verify_screen(
        lang,
        period,
        window_h=round(interval.total_seconds() / 3600),
        reported=report.total_usd,
        ours=ours,
        project_id=project_id,
        # The filter was requested and no foreign project came back, but an
        # all-null response means grouping was dropped and the scoping could
        # not be confirmed from the payload: the screen says which happened.
        scoped=project_id in report.project_ids_seen,
    )
    await show(callback, screen, answered=True)


# ---------------------------------------------------------------------------
# General settings: the screen, notifications, language
# ---------------------------------------------------------------------------


@_on("adm_settings:")
async def handle_settings(callback: CallbackQuery, **_kwargs: Any) -> None:
    await show(callback, settings_screen(_lang(callback)))


@_on("adm_notif:")
async def handle_notifications_menu(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **_kwargs: Any,
) -> None:
    settings = await admin_repo.get_notification_settings(bot_config_repo)
    await show(callback, notif_screen(_lang(callback), settings))


@_on("adm_nstk:")
async def handle_sticker_notification_cycle(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **_kwargs: Any,
) -> None:
    """Cycle the new-sticker notice: off → brief → detailed → off."""
    settings = await admin_repo.get_notification_settings(bot_config_repo)
    new_mode = next_sticker_mode(settings)
    await admin_repo.set_notification_setting(bot_config_repo, "sticker", new_mode)
    await show(callback, notif_screen(_lang(callback), {**settings, "sticker": new_mode}))


# The one on/off notification the screen offers. The others (jailbreak,
# blacklist, AI fallback) are no longer sent and their stored values are read
# by nothing, so a payload naming one of them comes from an old button and is
# stale.
_PANEL_TOGGLES = frozenset({"unauthorized"})


@_on("adm_ntog:")
async def handle_notification_toggle(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    fields = parse(callback.data, 2)
    if fields is None or fields[1] not in _PANEL_TOGGLES:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, ntype = lang_of(fields[0]), fields[1]
    settings = await admin_repo.get_notification_settings(bot_config_repo)
    new_value = not bool(settings.get(ntype, True))
    await admin_repo.set_notification_setting(bot_config_repo, ntype, new_value)
    await show(callback, notif_screen(lang, {**settings, ntype: new_value}))


@_on("adm_lang:")
async def handle_language_menu(callback: CallbackQuery, **_kwargs: Any) -> None:
    await show(callback, lang_screen(_lang(callback)))


@_on("adm_lang_set:")
async def handle_language_set(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    """Save the panel language and show general settings in it."""
    fields = parse(callback.data, 2)
    if fields is None or fields[1] not in ("ru", "en"):
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    new_lang = fields[1]
    await admin_repo.set_admin_language(bot_config_repo, new_lang)
    await show(callback, settings_screen(new_lang, notice=LANG_SET_NOTICE(new_lang)))


# adm_stk_* buttons are handled by the sticker routers; adm_defs* by
# admin_defaults.
