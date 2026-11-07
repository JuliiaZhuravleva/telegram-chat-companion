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

The message that asked for the input would still be asking: the admin, back
from the other screen, types the answer into a prompt that is gone, and it
reaches the bot as an ordinary message. So the prompt's message is closed too,
after the tap's own handler: the screen it showed is noted on delivery
(`note_screen`, called by `show_markup`), and this middleware edits it down to
the screen's title and «Ввод закрыт.» with no buttons. Not when the tapped
message is that message (its handler is drawing a new screen there), and not
when Telegram cannot edit it: a message carrying a reply keyboard (the
organizer prompt, the rules person search) — that is logged and left.

The organizer prompt's reply keyboard is not taken away here: that takes a
message of its own, and one more message on every tap that leaves the prompt
would be noise. It stays until its next use, and every use removes it: its
person search is answered as out of date, its «Отмена» is answered by
`fsm_cancel` like /cancel, and /cancel removes it too.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Chat, Message, TelegramObject

from src.bot.screens.i18n import Tr, lang_of
from src.bot.screens.routes import NOOP
from src.bot.states.admin import PROMPT_OWN_TAPS, PromptMessage, is_own_tap, take_prompt_message
from src.bot.utils import safe_edit_text

logger = structlog.get_logger(__name__)

CLOSED = Tr("Ввод закрыт.", "Input closed.")


class PromptExitMiddleware(BaseMiddleware):
    """Clear a prompt state when a tap is not one of that prompt's own, and close its message."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)
        ended = await leave_prompt_if_foreign(event.data, data)
        # Taken before the handler runs: a prompt it opens records its own message.
        left = _prompt_message_of(event, ended) if ended is not None else None
        try:
            return await handler(event, data)
        finally:
            if left is not None:
                await _close(data.get("bot"), event, left)


async def leave_prompt_if_foreign(payload: str | None, data: dict[str, Any]) -> str | None:
    """End the prompt in ``data["state"]`` unless ``payload`` is its own; the state it ended."""
    state = data.get("state")
    if state is None or not payload or payload == NOOP:
        return None
    current = await state.get_state()
    if current not in PROMPT_OWN_TAPS or is_own_tap(current, payload):
        return None
    await state.clear()
    # aiogram read the state before this ran and hands its copy to every
    # filter: a StateFilter downstream must see the prompt as ended too.
    data["raw_state"] = None
    logger.info(
        "Prompt left by a tap elsewhere",
        state=current,
        action=payload.split(":", 1)[0],
    )
    return str(current)


def _prompt_message_of(callback: CallbackQuery, state: str) -> PromptMessage | None:
    """The message still showing ``state``'s prompt, unless it is the one tapped."""
    tapped = callback.message
    chat_id = getattr(getattr(tapped, "chat", None), "id", None)
    if not isinstance(chat_id, int):
        return None
    held = take_prompt_message(chat_id, state)
    if held is None or held.message_id == getattr(tapped, "message_id", None):
        return None
    return held


async def _close(bot: Bot | None, callback: CallbackQuery, held: PromptMessage) -> None:
    """Edit the prompt's message to its title and «Ввод закрыт.», buttons gone."""
    if bot is None:
        return
    if not held.editable:
        logger.info("prompt_message_left_open", screen_id=held.screen_id, reason="reply keyboard")
        return
    fields = (callback.data or "").split(":")
    lang = lang_of(fields[1] if len(fields) > 1 else None)
    closed = f"<i>{CLOSED(lang)}</i>"
    # Only the two ids reach Telegram; the admin panel lives in the private chat.
    chat = Chat(id=held.chat_id, type="private")
    stub = Message(message_id=held.message_id, date=datetime.now(UTC), chat=chat).as_(bot)
    try:
        # No reply_markup: an edit without one takes the inline keyboard away.
        # «message is not modified» (a second tap that raced this one) is
        # swallowed by safe_edit_text: the message already reads closed.
        await safe_edit_text(
            stub,
            f"{held.title}\n{closed}" if held.title else closed,
            reply_markup=None,
            parse_mode="HTML",
        )
    except TelegramAPIError as exc:
        # Deleted, too old to edit, or Telegram is unreachable: the prompt is
        # already over, only its message still reads otherwise.
        logger.info("prompt_message_left_open", screen_id=held.screen_id, reason=str(exc))
        return
    logger.info("prompt_message_closed", screen_id=held.screen_id)
