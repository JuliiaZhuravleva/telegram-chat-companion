"""Bot API 10.4 widget methods, which aiogram 3.30 does not have yet.

Plain ``TelegramMethod`` subclasses, so the calls go through the bot's own
session -- and with it the flood-control retry and every other session
middleware -- exactly like a built-in method. Delete these when aiogram ships
its own (aiogram PR #2004).

``parse_mode`` is a plain field here, not aiogram's ``Default("parse_mode")``,
so the bot-wide HTML default does NOT apply: the caller says HTML and escapes.
"""

from __future__ import annotations

from aiogram.methods import TelegramMethod
from aiogram.types import InlineKeyboardMarkup, Message


class SendWidget(TelegramMethod[Message]):
    """https://core.telegram.org/bots/api#sendwidget -- the HTML page plus its first state."""

    __returning__ = Message
    __api_method__ = "sendWidget"

    chat_id: int
    html: str
    width: int | None = None
    height: int | None = None
    chat_data: str | None = None
    caption: str | None = None
    parse_mode: str | None = None
    allow_queries: bool | None = None
    reply_markup: InlineKeyboardMarkup | None = None


class EditMessageWidgetData(TelegramMethod[bool]):
    """https://core.telegram.org/bots/api#editmessagewidgetdata -- a new state, silently."""

    __returning__ = bool
    __api_method__ = "editMessageWidgetData"

    chat_id: int
    message_id: int
    chat_data: str


class AnswerWidgetQuery(TelegramMethod[bool]):
    """https://core.telegram.org/bots/api#answerwidgetquery -- owed to every query, within 15 s."""

    __returning__ = bool
    __api_method__ = "answerWidgetQuery"

    widget_query_id: str
    data: str | None = None
