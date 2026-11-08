"""Where a callback payload lands in the REAL handler tree, without a bot or a database.

Shared by ``test_screen_rules.py`` (lint R7) and ``test_routes.py``. The walk
itself lives in `src.bot.screens.lint.first_handler` so ``scripts/dump_screens.py``
can use it too; this module only binds it to ``src.bot.handlers.router``, the
router ``main.py`` includes into the dispatcher.

What it proves and what it does not: it evaluates each handler's registered
filters in aiogram's order (own handlers, then sub-routers in include order),
so it answers "which handler would consume this payload". Async filters
(``IsAdmin``, ``StateFilter``) count as passing — they need a container or an
FSM context the walk does not have. It never calls a handler.

`deliver` is the other half: a real ``propagate_event`` through the main
router, outer middlewares and async filters included, with an FSM state the
test controls. It stops at the handler that would take the update and names
it, so the handler's own body (and its database) never runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram import BaseMiddleware
from aiogram.fsm.state import State
from aiogram.types import CallbackQuery, Chat, Message, TelegramObject, User

from src.bot.handlers import router as main_router
from src.bot.screens.lint import first_handler

ADMIN_ID = 555


def first_match(data: str) -> str | None:
    """``"module.function"`` of the first callback handler ``data`` reaches, or None."""
    return first_handler(main_router, data)


class FakeState:
    """An FSMContext stand-in: every method awaitable, ``clear`` counted."""

    def __init__(self, state: State | str | None = None) -> None:
        self._state: str | None = state.state if isinstance(state, State) else state
        self._data: dict[str, object] = {}
        self.clear_calls = 0

    async def get_state(self) -> str | None:
        return self._state

    async def set_state(self, state: State | str | None = None) -> None:
        self._state = state.state if isinstance(state, State) else state

    async def get_data(self) -> dict[str, object]:
        return dict(self._data)

    async def update_data(self, **kwargs: object) -> dict[str, object]:
        self._data.update(kwargs)
        return dict(self._data)

    async def set_data(self, data: dict[str, object]) -> None:
        self._data = dict(data)

    async def clear(self) -> None:
        self.clear_calls += 1
        self._state = None
        self._data = {}


class _Stop(BaseMiddleware):
    """An inner middleware: notes the handler aiogram picked and does not call it."""

    def __init__(self) -> None:
        self.taken: list[str] = []
        self.data: dict[str, Any] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        func = data["handler"].callback
        self.taken.append(f"{func.__module__.rsplit('.', 1)[-1]}.{func.__name__}")
        self.data = dict(data)
        return None


def admin_tap(data: str) -> MagicMock:
    """A callback query from the admin's DM carrying ``data``."""
    callback = MagicMock(spec=CallbackQuery)
    callback.data = data
    callback.answer = AsyncMock()
    callback.from_user = User(id=ADMIN_ID, is_bot=False, first_name="Admin")
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.type = "private"
    callback.message.delete = AsyncMock()
    return callback


def admin_text(text: str) -> tuple[Message, AsyncMock]:
    """A typed message in the admin's DM, and the bot it would answer through."""
    bot = AsyncMock()
    msg = Message(
        message_id=1,
        date=datetime(2026, 11, 7, 12, 0, tzinfo=UTC),
        chat=Chat(id=ADMIN_ID, type="private"),
        from_user=User(id=ADMIN_ID, is_bot=False, first_name="Admin"),
        text=text,
    ).as_(bot)
    return msg, bot


async def deliver(
    update_type: str,
    event: TelegramObject,
    state: FakeState,
    *,
    call: bool = False,
    seen: dict[str, Any] | None = None,
    **data: Any,
) -> str | None:
    """Propagate ``event`` through the main router with ``state``; the handler that took it.

    ``raw_state`` is read off ``state`` as aiogram's FSM middleware would. With
    ``call=False`` the handler is named and not run; with ``call=True`` it runs
    for real on whatever ``data`` carries. ``seen`` (with ``call=False``) is
    filled with the data the handler would have been called with.
    """
    data.setdefault("is_admin", True)
    data.setdefault("bot", event.bot if isinstance(event, Message) else AsyncMock())
    data.setdefault("event_from_user", User(id=ADMIN_ID, is_bot=False, first_name="Admin"))
    observer = main_router.observers[update_type]
    stop = _Stop()
    if not call:
        observer.middleware(stop)
    try:
        await main_router.propagate_event(
            update_type, event, state=state, raw_state=await state.get_state(), **data
        )
    finally:
        if not call:
            observer.middleware.unregister(stop)
    if seen is not None:
        seen.update(stop.data)
    return stop.taken[0] if stop.taken else None
