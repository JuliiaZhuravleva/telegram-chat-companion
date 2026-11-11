"""``widget_query`` updates: what the status board asks the bot, and its answers.

aiogram 3.30 does not know this update type. Its ``Update`` keeps the field
(``update.model_extra["widget_query"]``), and the dispatcher's own update
listener -- the first handler on ``dp.update`` -- warns "unknown update type"
and skips, so a handler registered on ``dp.update`` after it receives the
update with the outer middlewares already run (the Dishka container is in the
data). ``register()`` does that; ``allowed_updates()`` asks Telegram for the
type, which it otherwise never sends.

**Every query is answered**, within 15 s, whatever happens -- a refusal and a
failure included. An unanswered query is a spinner on the admin's screen and
then a TIMEOUT.

**Admin check here, not in a middleware**: ``AccessControlMiddleware`` only
understands ``Message`` and ``CallbackQuery`` and lets anything else through
ungated (CLAUDE.md). A widget only lives in the private chat it was sent to,
but the check costs one read and does not depend on that.

Payloads (≤ 64 B), written by ``status_board.html``:

* ``s:<lang>:<rev>`` -- the board appeared; answer a newer state, or nothing.
* ``n:<lang>`` -- "Проверить сейчас": run a check, answer its state.
"""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

import structlog
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods import EditMessageCaption, TelegramMethod
from aiogram.types import Update

from src.bot.screens.i18n import lang_of
from src.bot.utils import check_admin_direct
from src.bot.widgets.api import AnswerWidgetQuery, EditMessageWidgetData
from src.bot.widgets.status_board import (
    HISTORY_HOURS,
    STALE_AFTER,
    board_caption,
    board_state,
    encode,
)
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository

logger = structlog.get_logger(__name__)

WIDGET_QUERY: Final = "widget_query"

CHECK_TIMEOUT: Final = 10.0
"""Seconds the "check now" tap waits for the check: under Telegram's 15 s, with room to answer.

A check that runs longer is not cancelled -- it finishes and records its row,
and the next appearance of the board picks it up.
"""

WIDGETS_KEY: Final = "admin_widgets"
"""``bot_config`` kill switch: ``false`` stops sending boards and answering them, no release needed."""

_inflight: asyncio.Task[Any] | None = None
"""The check a tap started; further taps wait for it instead of queueing their own."""

_UNKNOWN_UPDATE: Final = r'(?s)Detected unknown update type.*"widget_query"'
"""aiogram's warning for this update type: it carries the whole update (the admin's name and id)."""


async def widgets_enabled(bot_config: BotConfigRepository) -> bool:
    """On unless ``bot_config.admin_widgets`` is ``false``.

    An unreadable value counts as **off**: a kill switch that fails open does
    nothing exactly when the database is in trouble. The text screen does not
    depend on it.
    """
    try:
        value = await bot_config.get(WIDGETS_KEY)
    except Exception:
        logger.warning("admin_widgets_flag_unreadable", exc_info=True)
        return False
    return value is not False and str(value).lower() not in {"false", "0", "off"}


def widget_query_of(update: Update) -> dict[str, Any] | bool:
    """aiogram filter: the update's ``widget_query`` as handler data, or no match."""
    query = (update.model_extra or {}).get(WIDGET_QUERY)
    return {WIDGET_QUERY: query} if isinstance(query, dict) else False


def register(dp: Dispatcher) -> None:
    """Route ``widget_query`` updates to `handle_widget_query` (after aiogram's own listener).

    Also silences aiogram's "unknown update type" warning for this type only:
    it dumps the whole update to stderr on every board view, with the admin's
    name and id in it. Other unknown types still warn.
    """
    warnings.filterwarnings("ignore", message=_UNKNOWN_UPDATE, category=RuntimeWarning)
    dp.update.register(handle_widget_query, widget_query_of)


async def stale_after(bot_config: BotConfigRepository) -> int:
    """Seconds after which the board says "the bot has not checked itself"; 0 = never.

    Three check intervals, and never under ``STALE_AFTER``: one late check is
    not news. With checks switched off nothing is late, so the board only says
    how old its check is.
    """
    try:
        if (await bot_config.get("health_check_enabled")) is False:
            return 0
        interval = int(await bot_config.get("health_check_interval_seconds") or 0)
    except Exception:
        return STALE_AFTER
    return max(STALE_AFTER, 3 * interval)


async def load_board(
    lang: str,
    admin_repo: AdminRepository,
    bot_config: BotConfigRepository,
    *,
    newer_than: int | None = None,
) -> tuple[Mapping[str, Any] | None, dict[str, Any] | None, datetime]:
    """``(row, state, now)`` for the board; ``state`` is None when nothing is newer than ``newer_than``.

    The one place the send and the answers build a board, so the two cannot drift.
    """
    now = datetime.now(UTC)
    row = await admin_repo.get_latest_health_check()
    if newer_than is not None and board_state(lang, row, [], now=now)["r"] <= newer_than:
        return row, None, now  # the board already shows the newest check: skip the day query
    history = await admin_repo.get_health_by_hour(HISTORY_HOURS)
    state = board_state(lang, row, history, now=now)
    state["o"] = await stale_after(bot_config)
    return row, state, now


def allowed_updates(dp: Dispatcher) -> list[str]:
    """What polling asks Telegram for: every type a handler uses, plus ``widget_query``.

    aiogram resolves the list from registered handlers when none is given, and
    it cannot see a type it does not know, so without this the board's queries
    never arrive.
    """
    return [*dp.resolve_used_update_types(), WIDGET_QUERY]


async def handle_widget_query(_update: Update, widget_query: dict[str, Any], **data: Any) -> None:
    """Answer one query from a board; then, when the state moved on, edit the message too."""
    bot: Bot = data["bot"]
    query_id = str(widget_query.get("id") or "")
    answer: str | None = None
    edit: tuple[int, int, str, str] | None = None
    try:
        answer, edit = await _respond(widget_query, data)
    except Exception:
        logger.warning("widget_query_failed", exc_info=True)
        answer = encode({}, "x")
    try:
        await bot(AnswerWidgetQuery(widget_query_id=query_id, data=answer))
    except TelegramAPIError as exc:
        # Too late (over 15 s) or a deleted widget: the admin sees TIMEOUT. The
        # edit still goes out: a tap that arrived late (polling lag, a restart)
        # ran a real check, and the board shows it through chatDataChanged. A
        # deleted widget fails the edit quietly, once per query.
        logger.warning("widget_query_answer_failed", error=str(exc))
    if edit is not None:
        await _edit(bot, *edit)


async def _respond(
    query: dict[str, Any], data: dict[str, Any]
) -> tuple[str | None, tuple[int, int, str, str] | None]:
    """``(answer, edit)``: the answer's data, and what to write into the message after it."""
    container = data.get("dishka_container")
    if container is None:
        raise RuntimeError("no dishka container in widget_query data")
    bot_config = await container.get(BotConfigRepository)
    user_id = (query.get("from") or {}).get("id")
    chat = query.get("chat") or {}
    is_admin = isinstance(user_id, int) and await check_admin_direct(bot_config, user_id)
    if not is_admin or chat.get("type") != "private":
        logger.warning("widget_query_refused", user_id=user_id, chat_type=chat.get("type"))
        return encode({}, "no"), None
    if not await widgets_enabled(bot_config):
        return encode({}, "off"), None

    parts = str(query.get("data") or "").split(":")
    kind = parts[0]
    lang = lang_of(parts[1] if len(parts) > 1 else None)
    note: str | None = None
    if kind == "n":
        note = await _check_now(data.get("health_checker"))
    elif kind != "s":
        return None, None

    seen: int | None = None
    if kind == "s":
        seen = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    admin_repo = await container.get(AdminRepository)
    row, state, now = await load_board(lang, admin_repo, bot_config, newer_than=seen)
    if state is None:
        return None, None
    message_id = query.get("message_id")
    chat_id = chat.get("id")
    edit = None
    if isinstance(message_id, int) and isinstance(chat_id, int):
        edit = (chat_id, message_id, encode(state), board_caption(lang, row, now=now))
    return encode(state, note), edit


async def _check_now(health_checker: Any) -> str | None:
    """Run a check for the tap; the note code when it could not finish in time."""
    global _inflight
    if health_checker is None:
        return "slow"
    if _inflight is None or _inflight.done():
        # A tap while a check still runs joins it: taps must not queue checks.
        _inflight = asyncio.ensure_future(health_checker.run_check_now())
    task = _inflight
    try:
        await asyncio.wait_for(asyncio.shield(task), CHECK_TIMEOUT)
    except TimeoutError:
        logger.warning("widget_check_slow", timeout=CHECK_TIMEOUT)
        return "slow"
    except Exception:
        logger.warning("widget_check_failed", exc_info=True)
        return "slow"
    return None


async def _edit(bot: Bot, chat_id: int, message_id: int, state: str, caption: str) -> None:
    """The new state and its text twin into the message; "not modified" is success."""
    await _quietly(
        bot, EditMessageWidgetData(chat_id=chat_id, message_id=message_id, chat_data=state)
    )
    await _quietly(
        bot,
        EditMessageCaption(
            chat_id=chat_id, message_id=message_id, caption=caption, parse_mode="HTML"
        ),
    )


async def _quietly(bot: Bot, method: TelegramMethod[Any]) -> None:
    try:
        await bot(method)
    except TelegramBadRequest as exc:
        if "not modified" not in exc.message.lower():
            logger.warning("widget_edit_failed", method=method.__api_method__, error=exc.message)
    except TelegramAPIError as exc:
        logger.warning("widget_edit_failed", method=method.__api_method__, error=str(exc))
