"""A button anywhere else ends a typed-input prompt.

A prompt (`AdminStates`) reads the admin's next message as its answer, and the
admin panel is buttons on many messages: the admin can leave a prompt by
tapping any older one. Each area closed its prompt from its own handlers only,
so leaving through another area left it armed, and the next text — hours
later, meant for something else — became a tolerance value, a line of a chat's
instruction, or a rule.

This middleware sits on the main router's ``callback_query`` observer, so it
runs once per tap, before any handler's filters: when the FSM holds a prompt
and the payload is not one of that prompt's own (`PROMPT_OWN_TAPS`, kept next
to the states so a new prompt cannot be added without one), the prompt is
cleared, ``raw_state`` with it, and the handler that takes the tap sees no
state. A page counter (``noop``) changes nothing and ends nothing.

The organizer prompt's reply keyboard is not taken away here: that takes a
message of its own, and one more message on every tap that leaves the prompt
would be noise. It stays until its next use, and every use removes it: its
person search is answered as out of date, its «Отмена» is answered by
`fsm_cancel` like /cancel, and /cancel removes it too.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from src.bot.screens.routes import NOOP
from src.bot.states.admin import PROMPT_OWN_TAPS, is_own_tap

logger = structlog.get_logger(__name__)


class PromptExitMiddleware(BaseMiddleware):
    """Clear a prompt state when a tap is not one of that prompt's own."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            await leave_prompt_if_foreign(event.data, data)
        return await handler(event, data)


async def leave_prompt_if_foreign(payload: str | None, data: dict[str, Any]) -> bool:
    """End the prompt in ``data["state"]`` unless ``payload`` is its own; True if it did."""
    state = data.get("state")
    if state is None or not payload or payload == NOOP:
        return False
    current = await state.get_state()
    if current not in PROMPT_OWN_TAPS or is_own_tap(current, payload):
        return False
    await state.clear()
    data["raw_state"] = None
    logger.info(
        "Prompt left by a tap elsewhere",
        state=current,
        action=payload.split(":", 1)[0],
    )
    return True
