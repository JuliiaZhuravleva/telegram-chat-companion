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
from datetime import UTC, datetime
from typing import Any, Final

import structlog
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods import EditMessageCaption, TelegramMethod
from aiogram.types import Update

from src.bot.screens.i18n import lang_of
from src.bot.widgets.api import AnswerWidgetQuery, EditMessageWidgetData
from src.bot.widgets.status_board import (
    HISTORY_HOURS,
    board_caption,
    board_state,
    encode,
)
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.utils import parse_admin_ids

logger = structlog.get_logger(__name__)

WIDGET_QUERY: Final = "widget_query"

CHECK_TIMEOUT: Final = 10.0
"""Seconds the "check now" tap waits for the check: under Telegram's 15 s, with room to answer.

A check that runs longer is not cancelled -- it finishes and records its row,
and the next appearance of the board picks it up.
"""

WIDGETS_KEY: Final = "admin_widgets"
"""``bot_config`` kill switch: ``false`` stops sending boards and answering them, no release needed."""

_background: set[asyncio.Task[Any]] = set()


async def widgets_enabled(bot_config: BotConfigRepository) -> bool:
    """On unless ``bot_config.admin_widgets`` is ``false``; an unreadable value counts as on."""
    try:
        value = await bot_config.get(WIDGETS_KEY)
    except Exception:
        logger.warning("admin_widgets_flag_unreadable", exc_info=True)
        return True
    return value is not False and str(value).lower() not in {"false", "0", "off"}


def widget_query_of(update: Update) -> dict[str, Any] | bool:
    """aiogram filter: the update's ``widget_query`` as handler data, or no match."""
    query = (update.model_extra or {}).get(WIDGET_QUERY)
    return {WIDGET_QUERY: query} if isinstance(query, dict) else False


def register(dp: Dispatcher) -> None:
    """Route ``widget_query`` updates to `handle_widget_query` (after aiogram's own listener)."""
    dp.update.register(handle_widget_query, widget_query_of)


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
        # Too late (over 15 s) or a deleted widget: the admin sees TIMEOUT.
        logger.warning("widget_query_answer_failed", error=str(exc))
        return
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
    admins = parse_admin_ids(await bot_config.get("admin_ids"))
    if user_id not in admins or chat.get("type") != "private":
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

    admin_repo = await container.get(AdminRepository)
    row = await admin_repo.get_latest_health_check()
    history = await admin_repo.get_health_by_hour(HISTORY_HOURS)
    now = datetime.now(UTC)
    state = board_state(lang, row, history, now=now)
    if kind == "s":
        seen = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if state["r"] <= seen:
            return None, None  # the board already shows the newest check
    message_id = query.get("message_id")
    chat_id = chat.get("id")
    edit = None
    if isinstance(message_id, int) and isinstance(chat_id, int):
        edit = (chat_id, message_id, encode(state), board_caption(lang, row, now=now))
    return encode(state, note), edit


async def _check_now(health_checker: Any) -> str | None:
    """Run a check for the tap; the note code when it could not finish in time."""
    if health_checker is None:
        return "slow"
    task = asyncio.ensure_future(health_checker.run_check_now())
    _background.add(task)
    task.add_done_callback(_background.discard)
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
