"""A button anywhere else, or another command, ends a typed-input prompt.

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

A typed command leaves a prompt the same way: on the ``message`` observer, a
private-chat message whose text (or caption) is a slash command ends the armed
prompt before any handler's filters, so /admin, /settings or /panel opens its
screen and the prompt is over, not left to read the next ordinary text as its
answer. Not /cancel, which `fsm_cancel` answers itself (it has to see the state
to say what it cancelled), and not a forwarded message: that is content, and
the organizer prompt takes a forward as its answer whatever its text.

The message that asked for the input would still be asking: the admin, back
from the other screen, types the answer into a prompt that is gone, and it
reaches the bot as an ordinary message. So the prompt's message is closed too,
after the tap's own handler: the screen it showed is noted on delivery
(`note_screen`, called by `show_markup`), and this middleware edits it down to
the screen's title, a line saying the bot no longer waits for an answer there,
and one button back to the screen the prompt belongs to (`PROMPT_HOMES`), named
by where it goes: «⬅️ Поведение» under the instruction editor. That screen
opens with no FSM state, so the button works whatever happened since; the id
it needs (the chat's, the sticker's) is read off the prompt's own buttons when
it is delivered, and so is the language a typed command does not carry. Not
when the tapped message is that message (its handler is drawing a new screen
there), and not when Telegram cannot edit it: a message carrying a reply
keyboard (the organizer prompt, the rules person search) — that is logged and
left. /cancel closes it the same way (`close_prompt_message`), with its own
line.

A prompt's reply keyboard is not taken away here: that takes a message of its
own, and one more message on every tap that leaves the prompt would be noise.
It stays until its next use, and every use removes it: its person search is
answered as out of date, and its «Отмена» is answered by `fsm_cancel` like
/cancel — also while another prompt is armed, whose answer it is not
(`reply_keyboard_up`); /cancel removes it too.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, TelegramObject

from src.bot.screens.chat import SECTION_TITLES
from src.bot.screens.i18n import Tr, lang_of
from src.bot.screens.model import Button, Screen
from src.bot.screens.render import back, render_text, to_markup
from src.bot.screens.routes import BACK_NAMES, NOOP, CallbackTooLong, UnknownRoute, cb
from src.bot.screens.sections import SECTION_SCREEN
from src.bot.states.admin import (
    PROMPT_HOMES,
    PROMPT_OWN_TAPS,
    PromptMessage,
    is_own_tap,
    take_prompt_message,
)
from src.bot.utils import safe_edit_text

logger = structlog.get_logger(__name__)

# Not an import of `fsm_cancel`: that module needs this one, and the name is
# its command's, said once more where it is let through.
_CANCEL_COMMAND = "cancel"

CLOSED = Tr(
    "Здесь ответа больше не жду: нажата другая кнопка. Ничего не сохранено — "
    "чтобы начать заново, откройте экран кнопкой ниже.",
    "No longer waiting for an answer here: another button was tapped. Nothing was saved; "
    "to start again, open the screen with the button below.",
)
"""Plain text, not an aside: it is the one thing this message still says. Says
the tap, not "you opened another screen": «Закрыть» ends a prompt too and opens
nothing.

Round 4: «Вы нажали…» read as a scolding, and nobody could tell whether the
half-done answer had been kept or what to do next, so it says both. "Nothing was
saved" is this prompt's own answer: it was never given."""

CLOSED_BY_COMMAND = Tr(
    "Здесь ответа больше не жду: отправлена другая команда. Ничего не сохранено — "
    "чтобы начать заново, откройте экран кнопкой ниже.",
    "No longer waiting for an answer here: another command was sent. Nothing was saved; "
    "to start again, open the screen with the button below.",
)
"""`CLOSED` for a prompt a typed command ended (/admin, /panel …): the same three
parts, naming what happened instead of a tap."""

CLOSED_BY_CANCEL = Tr(
    "Здесь ответа больше не жду: ввод отменён. Ничего не сохранено — "
    "чтобы начать заново, откройте экран кнопкой ниже.",
    "No longer waiting for an answer here: the input was cancelled. Nothing was saved; "
    "to start again, open the screen with the button below.",
)
"""`CLOSED` for a prompt /cancel (or the leftover keyboard's «Отмена») ended."""

CLOSED_ID = "prompt.closed"


def home_name(screen_id: str, lang: str) -> str:
    """The name of ``screen_id`` on a back button: `BACK_NAMES`, else a chat
    section's own word (``behavior`` → «Поведение»), else the panel's."""
    name = BACK_NAMES.get(screen_id)
    if name is not None:
        return name(lang)
    for section, shown in SECTION_SCREEN.items():
        title = SECTION_TITLES.get(section)
        if shown == screen_id and title is not None:
            return title(lang).split(" ", 1)[-1]
    return BACK_NAMES["hub"](lang)


def home_button(state: str, key: str | None, lang: str) -> Button:
    """``⬅️ {screen}`` to the screen ``state``'s prompt belongs to; the panel
    when the prompt has no home or its message carried no id to go back with."""
    home = PROMPT_HOMES.get(state)
    if home is not None and key:
        try:
            data = cb(home.prefix, lang, key, *home.tail)
        except (CallbackTooLong, UnknownRoute) as exc:
            logger.warning("prompt_home_unbuildable", state=state, reason=str(exc))
        else:
            return back(home.screen_id, data, lang, name=home_name(home.screen_id, lang))
    return back("hub", cb("adm_menu:", lang), lang)


def build_closed(title: str, home: Button, lang: str, reason: Tr = CLOSED) -> Screen:
    """What a prompt's message becomes once the prompt ended elsewhere: ``reason``
    says how (`CLOSED`, `CLOSED_BY_COMMAND`, `CLOSED_BY_CANCEL`)."""
    return Screen(
        id=CLOSED_ID,
        text=f"{title}\n{reason(lang)}" if title else reason(lang),
        rows=((home,),),
    )


_Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class PromptExitMiddleware(BaseMiddleware):
    """Clear a prompt state when a tap is not one of that prompt's own, or a
    command is typed, and close its message."""

    async def __call__(
        self,
        handler: _Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            return await self._tap(handler, event, data)
        if isinstance(event, Message):
            return await self._typed(handler, event, data)
        return await handler(event, data)

    async def _tap(self, handler: _Handler, event: CallbackQuery, data: dict[str, Any]) -> Any:
        ended = await leave_prompt_if_foreign(event.data, data)
        # Taken before the handler runs: a prompt it opens records its own message.
        left = _prompt_message_of(event, ended) if ended is not None else None
        try:
            return await handler(event, data)
        finally:
            if ended is not None and left is not None:
                fields = (event.data or "").split(":")
                lang = lang_of(fields[1] if len(fields) > 1 else None)
                await close_prompt_message(data.get("bot"), left, ended, lang)

    async def _typed(self, handler: _Handler, event: Message, data: dict[str, Any]) -> Any:
        ended = await leave_prompt_on_command(event, data)
        # Taken before the handler runs, for the same reason as a tap's.
        left = take_prompt_message(event.chat.id, ended) if ended is not None else None
        try:
            return await handler(event, data)
        finally:
            if ended is not None and left is not None:
                await close_prompt_message(
                    data.get("bot"), left, ended, lang_of(left.lang), reason=CLOSED_BY_COMMAND
                )


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


def typed_command(message: Message) -> str | None:
    """The command ``message`` types (``admin`` for ``/admin@bot x``), or None.

    Only in the private chat, where the panel and its prompts live, and only
    when the admin typed it: a forward is content (the organizer prompt takes
    one as its answer whatever it says). ``text or caption``, like aiogram's
    ``Command`` filter, so a captioned photo reading /admin is a command too.
    """
    if message.chat.type != "private" or message.forward_origin is not None:
        return None
    # Parsed as ``Command`` parses it: the first word, its prefix, its mention.
    words = (message.text or message.caption or "").split(maxsplit=1)
    if not words or not words[0].startswith("/"):
        return None
    name = words[0][1:].partition("@")[0]
    return name or None


async def leave_prompt_on_command(message: Message, data: dict[str, Any]) -> str | None:
    """End the prompt in ``data["state"]`` when ``message`` is a typed command
    other than /cancel; the state it ended."""
    command = typed_command(message)
    state = data.get("state")
    if command is None or command == _CANCEL_COMMAND or state is None:
        return None
    current = await state.get_state()
    if current not in PROMPT_OWN_TAPS:
        return None
    await state.clear()
    # The same copy of the state as a tap's: every filter downstream reads it.
    data["raw_state"] = None
    logger.info("Prompt left by a typed command", state=current, command=command)
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


async def close_prompt_message(
    bot: Bot | None,
    held: PromptMessage,
    state: str,
    lang: str,
    *,
    reason: Tr = CLOSED,
) -> None:
    """Edit the prompt's message to its title, ``reason`` and the one way back.

    ``held`` is the record `take_prompt_message` gave for ``state``, the prompt
    that just ended; ``lang`` is what the closed message is written in. Never
    raises a Telegram error: the prompt is over either way.
    """
    if bot is None:
        return
    if not held.editable:
        logger.info("prompt_message_left_open", screen_id=held.screen_id, reason="reply keyboard")
        return
    screen = build_closed(held.title, home_button(state, held.home_key, lang), lang, reason)
    markup = to_markup(screen)
    # Only the two ids reach Telegram; the admin panel lives in the private chat.
    chat = Chat(id=held.chat_id, type="private")
    stub = Message(message_id=held.message_id, date=datetime.now(UTC), chat=chat).as_(bot)
    try:
        # The prompt's own buttons go: the markup is the one back button.
        # «message is not modified» (a second tap that raced this one) is
        # swallowed by safe_edit_text: the message already reads closed.
        await safe_edit_text(
            stub,
            render_text(screen),
            reply_markup=markup if isinstance(markup, InlineKeyboardMarkup) else None,
            parse_mode="HTML",
        )
    except TelegramAPIError as exc:
        # Deleted, too old to edit, or Telegram is unreachable: the prompt is
        # already over, only its message still reads otherwise.
        logger.info("prompt_message_left_open", screen_id=held.screen_id, reason=str(exc))
        return
    logger.info("prompt_message_closed", screen_id=held.screen_id)
