"""Turn a `Screen` into a Telegram message, and the small builders every screen shares.

This is the ONLY place that builds aiogram markup for redesigned screens, and
`show()` is the only delivery path: edit the message the button sits on when
that is possible, send a new message when it is not, and answer the callback
query exactly once either way — an unanswered query keeps the button spinning.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

import structlog
from aiogram.client.default import Default
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    CopyTextButton,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from src.bot.reply_flow import send_html_parts
from src.bot.screens.i18n import Tr
from src.bot.screens.model import Button, ReplyButton, Screen
from src.bot.screens.routes import BACK_NAMES, NOOP
from src.bot.states.admin import note_screen
from src.bot.utils import safe_edit_text
from src.utils.telegram_text import TELEGRAM_MESSAGE_LIMIT, parsed_length, split_html

logger = structlog.get_logger(__name__)

Markup = InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove

CANCEL = Tr("✖️ Отмена", "✖️ Cancel")


# ---------------------------------------------------------------------------
# Screen -> aiogram
# ---------------------------------------------------------------------------


def _inline(button: Button) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=button.label,
        callback_data=button.callback,
        url=button.url,
        copy_text=CopyTextButton(text=button.copy_text) if button.copy_text is not None else None,
        style=button.style,
    )


def _reply(button: ReplyButton) -> KeyboardButton:
    request = button.request_users
    return KeyboardButton(
        text=button.label,
        request_users=(
            KeyboardButtonRequestUsers(
                request_id=request.request_id,
                user_is_bot=request.user_is_bot,
                max_quantity=request.max_quantity,
                request_name=request.request_name,
                request_username=request.request_username,
            )
            if request is not None
            else None
        ),
    )


def to_markup(s: Screen) -> Markup | None:
    """The screen's keyboard as aiogram markup, or None for a screen without one.

    A reply keyboard is ``resize_keyboard`` + ``one_time_keyboard``, as the one
    reply keyboard the panel had before the redesign (the organizer picker):
    one-time only HIDES it, which is why every exit from such a prompt sends
    ``remove_reply`` explicitly.
    """
    if s.rows and s.reply is not None:
        raise ValueError(f"screen {s.id!r}: inline rows and a reply keyboard are exclusive")
    if s.remove_reply and (s.rows or s.reply is not None):
        raise ValueError(f"screen {s.id!r}: remove_reply cannot carry a keyboard")
    if s.rows:
        return InlineKeyboardMarkup(inline_keyboard=[[_inline(b) for b in row] for row in s.rows])
    if s.reply is not None:
        return ReplyKeyboardMarkup(
            keyboard=[[_reply(b) for b in row] for row in s.reply],
            resize_keyboard=True,
            one_time_keyboard=True,
            input_field_placeholder=s.placeholder,
        )
    if s.remove_reply:
        return ReplyKeyboardRemove(remove_keyboard=True)
    return None


def with_notice(text: str, notice: str | None = None, notice_raw: str | None = None) -> str:
    """Prepend the one-line notice a screen shows after a change.

    ``notice`` is plain text and is escaped, rendered as ``<i>✓ …</i>``;
    ``notice_raw`` is trusted HTML (the stale line) and is used as is.
    """
    if notice is not None and notice_raw is not None:
        raise ValueError("notice and notice_raw are exclusive")
    if notice_raw is not None:
        return f"{notice_raw}\n\n{text}"
    if notice is not None:
        return f"<i>✓ {html.escape(notice)}</i>\n\n{text}"
    return text


def render_text(s: Screen) -> str:
    """Notice line (if any) + the screen text."""
    if s.notice is not None and s.notice_raw is not None:
        raise ValueError(f"screen {s.id!r}: notice and notice_raw are exclusive")
    return with_notice(s.text, s.notice, s.notice_raw)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def _is_callback(target: Any) -> bool:
    """CallbackQuery or Message? By type, with a duck-typed fallback for test doubles.

    Handler tests across the suite build plain ``MagicMock`` callbacks whose
    ``.message`` is a ``MagicMock(spec=Message)``; a plain mock is neither type,
    so it is classified by what its ``.message`` is. A real ``Message`` never
    reaches the fallback.
    """
    if isinstance(target, CallbackQuery):
        return True
    if isinstance(target, Message):
        return False
    return isinstance(getattr(target, "message", None), Message | InaccessibleMessage)


async def _send_new(
    anchor: Message | InaccessibleMessage,
    text: str,
    markup: Markup | None,
    *,
    screen_id: str,
    parse_mode: str | None | Default,
    disable_web_page_preview: bool | None | Default,
) -> Message:
    """Send ``text`` as a new message in ``anchor``'s chat, splitting it when too long."""
    length = parsed_length(text)
    if length > TELEGRAM_MESSAGE_LIMIT:
        # Builders cap their lists so this never happens; the rules test checks
        # the samples. Reaching it means a real list outgrew its cap: deliver
        # every word, keyboard on the last piece, and leave a findable trace.
        logger.warning("screen_too_long", screen_id=screen_id, parsed_length=length)
        parts = split_html(text)
        head, text = parts[:-1], parts[-1]
        if isinstance(anchor, Message):
            await send_html_parts(
                message=anchor, parts=head, reply_to_message_id=None, language="ru"
            )
        else:
            for part in head:
                await anchor.answer(part, parse_mode="HTML")
    return await anchor.answer(
        text,
        parse_mode=parse_mode,
        reply_markup=markup,
        disable_web_page_preview=disable_web_page_preview,
    )


async def show_markup(
    target: CallbackQuery | Message,
    text: str,
    markup: Markup | None,
    *,
    screen_id: str,
    toast: str | None = None,
    alert: bool = False,
    answered: bool = False,
    new_message: bool = False,
    parse_mode: str | None | Default = Default("parse_mode"),
    disable_web_page_preview: bool | None | Default = Default("link_preview_is_disabled"),
    title_from: str | None = None,
) -> Message | None:
    """`show()`'s delivery for text and markup that are not (yet) a `Screen`.

    Exists for the stable entry wrappers (``show_chat``, ``show_chats``,
    ``show_rules``) that still render through today's keyboard functions; an
    area that converts its screen to a builder calls `show()` instead. The
    ``parse_mode``/``disable_web_page_preview`` defaults inherit the bot's
    defaults, exactly like `safe_edit_text`.

    The message the screen lands in is noted (`note_screen`): a prompt screen's
    message is closed by `PromptExitMiddleware` when a tap elsewhere ends the
    prompt. ``title_from`` is the text whose first line names the screen there
    (`show()` passes the screen text without its notice); default ``text``.
    """
    editable = markup is None or isinstance(markup, InlineKeyboardMarkup)
    shown = await _deliver(
        target,
        text,
        markup,
        screen_id=screen_id,
        toast=toast,
        alert=alert,
        answered=answered,
        new_message=new_message or not editable,
        parse_mode=parse_mode,
        disable_web_page_preview=disable_web_page_preview,
    )
    note_screen(shown, screen_id, text if title_from is None else title_from, editable=editable)
    return shown


async def _deliver(
    target: CallbackQuery | Message,
    text: str,
    markup: Markup | None,
    *,
    screen_id: str,
    toast: str | None,
    alert: bool,
    answered: bool,
    new_message: bool,
    parse_mode: str | None | Default,
    disable_web_page_preview: bool | None | Default,
) -> Message | None:
    """`show_markup` without the note: edit in place when possible, else a new message."""
    if not _is_callback(target):
        message: Message = target  # type: ignore[assignment]  # narrowed by _is_callback
        return await _send_new(
            message,
            text,
            markup,
            screen_id=screen_id,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )

    callback: CallbackQuery = target  # type: ignore[assignment]  # narrowed by _is_callback
    if not answered:
        if toast is None and not alert:
            await callback.answer()
        else:
            await callback.answer(toast, show_alert=alert)

    anchor = callback.message
    if anchor is None:
        # An inline-mode query: there is no message to edit or reply under.
        logger.warning("screen_without_message", screen_id=screen_id)
        return None

    too_long = parsed_length(text) > TELEGRAM_MESSAGE_LIMIT
    if isinstance(anchor, Message) and not (new_message or too_long):
        inline = markup if isinstance(markup, InlineKeyboardMarkup) else None
        try:
            await safe_edit_text(
                anchor,
                text,
                reply_markup=inline,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
        except TelegramBadRequest as exc:
            # "message can't be edited", "message to edit not found", "there is
            # no text in the message to edit" (the button sits under a sticker
            # or a photo): the screen still has to appear somewhere.
            logger.info("screen_edit_fallback", screen_id=screen_id, error=str(exc))
        else:
            return anchor

    return await _send_new(
        anchor,
        text,
        markup,
        screen_id=screen_id,
        parse_mode=parse_mode,
        disable_web_page_preview=disable_web_page_preview,
    )


async def show(
    target: CallbackQuery | Message,
    s: Screen,
    *,
    toast: str | None = None,
    alert: bool = False,
    answered: bool = False,
    new_message: bool = False,
) -> Message | None:
    """Deliver a screen.

    * CallbackQuery: answer the query once (``toast``, ``show_alert=alert``)
      unless ``answered=True`` — a handler that runs a long operation answers
      first and says so — then edit the button's message in place. An edit
      Telegram refuses for any reason other than "not modified" falls back to a
      new message; so does an inaccessible message.
    * Message target, ``new_message=True``, or a screen with a reply keyboard
      or ``remove_reply``: always a new message (a reply keyboard cannot be
      edited into an existing message).
    * Text over 4096 UTF-16 units after parsing: split, keyboard on the last piece.

    Returns the message the screen now lives in, or None when there is none.
    """
    return await show_markup(
        target,
        render_text(s),
        to_markup(s),
        screen_id=s.id,
        toast=toast,
        alert=alert,
        answered=answered,
        new_message=new_message or s.reply is not None or s.remove_reply,
        parse_mode="HTML",
        disable_web_page_preview=not s.link_preview,
        title_from=s.text,
    )


# ---------------------------------------------------------------------------
# Shared buttons
# ---------------------------------------------------------------------------


def back(target_id: str, callback: str, lang: str, *, name: str | None = None) -> Button:
    """``⬅️ {where it goes}``: the back button names its destination, never "Назад"."""
    label = name if name is not None else BACK_NAMES[target_id](lang)
    return Button(f"⬅️ {label}", callback=callback, kind="back", goes_to=target_id)


def cancel(callback: str, lang: str) -> Button:
    """``✖️ Отмена`` — input prompts only; everything else has a named back."""
    return Button(CANCEL(lang), callback=callback, kind="cancel")


def pager(page: int, pages: int, make_cb: Callable[[int], str]) -> tuple[Button, ...] | None:
    """``◀ · 2/5 · ▶`` with wrap-around, or None when everything fits on one page."""
    if pages <= 1:
        return None
    return (
        Button("◀", callback=make_cb((page - 1) % pages), kind="pager"),
        Button(f"{page + 1}/{pages}", callback=NOOP, kind="noop"),
        Button("▶", callback=make_cb((page + 1) % pages), kind="pager"),
    )


def page_slice(total: int, page: int, per_page: int) -> tuple[int, int, int]:
    """``(page, pages, offset)`` with ``page`` clamped into range.

    An old button can carry a page that no longer exists (the list shrank since
    it was sent); clamping shows the nearest real page instead of an empty one.
    """
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    pages = max(1, -(-max(total, 0) // per_page))
    page = min(max(page, 0), pages - 1)
    return page, pages, page * per_page
