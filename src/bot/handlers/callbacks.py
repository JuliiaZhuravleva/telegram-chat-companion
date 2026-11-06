"""Callback handlers for the buttons on /start, /help, /summary and /callme.

All of them live outside the admin panel: ``help_summary:`` and
``help_close:`` sit on group messages, ``help_dm:`` on the DM start screen,
``help_callme:`` on the ``/callme`` reply. A payload a handler cannot read is
an old button: it gets a one-line toast in the chat's language, never an
English alert, and the panel's stale screen is not shown — these buttons are
in front of ordinary members, not bot admins.
"""

from __future__ import annotations

from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.progress import ProgressNotice
from src.bot.screens import entry
from src.bot.screens.i18n import Tr, lang_of
from src.bot.screens.render import show
from src.database.repositories.aliases import AliasRepository, AliasWriteOutcome
from src.models.chat_config import ChatConfig
from src.services.modules.summary import SummaryService
from src.utils.aliases import build_alias_view, parse_alias
from src.utils.telegram_text import split_html

router = Router(name="callbacks")
logger = structlog.get_logger(__name__)

_NOT_YOUR_BUTTON = Tr("Эта кнопка не для вас.", "This button is not for you.")
# Mirrors the refusal `handle_summary` gives on the command path.
_SAVE_MESSAGES_DISABLED = Tr(
    "Сохранение сообщений отключено для этого чата.",
    "Message saving is disabled for this chat.",
)
_OLD_BUTTON = Tr(
    "Эта кнопка устарела. Отправьте команду ещё раз.",
    "This button is out of date. Send the command again.",
)
_GENERATING = Tr("⏳ Генерирую саммари…", "⏳ Generating the summary…")
_SUMMARY_BUSY = Tr("Уже готовлю саммари…", "Already working on the summary…")
_SUMMARY_FAILED = Tr("Не удалось создать саммари.", "Failed to generate summary.")
_CLOSED = Tr("Закрыто", "Closed")
_CALLME_TAKEN = Tr(
    "Имя «{name}» в этом чате уже занято. Своё можно задать так: /callme Имя",
    "The name “{name}” is already taken in this chat. Set your own with /callme Name",
)
_CALLME_UNUSABLE = Tr(
    "Имя из аккаунта не подходит. Своё можно задать так: /callme Имя",
    "The account name does not fit. Set your own with /callme Name",
)
_CALLME_FAILED = Tr("Не смог сохранить, попробуйте ещё раз.", "Could not save that, try again.")

_SUMMARY_MIN = 20
_SUMMARY_MAX = 1000

# (chat id, message id) of the /help messages whose summary button is being
# answered right now. A summary is a paid model call that takes seconds, and
# a double tap -- or 100 and then 500 -- used to start two of them. In memory
# and per process, like the thing it guards: a restart ends the call too.
_SUMMARIES_IN_FLIGHT: set[tuple[int, int]] = set()


def _chat_lang(chat_config: ChatConfig) -> str:
    return lang_of(chat_config.language)


def _check_owner(callback: CallbackQuery, owner_id: int) -> bool:
    """Check if the callback sender is the button owner (0 = open to all)."""
    if owner_id == 0:
        return True
    return callback.from_user is not None and callback.from_user.id == owner_id


async def _old_button(callback: CallbackQuery, lang: str, *, why: str) -> None:
    logger.info(
        "public_button_unreadable",
        action=(callback.data or "").split(":")[0],
        why=why,
        user_id=getattr(callback.from_user, "id", None),
    )
    await callback.answer(_OLD_BUTTON(lang_of(lang)))


# ---------------------------------------------------------------------------
# help_summary: — post a new summary
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("help_summary:"))
async def handle_summary_callback(
    callback: CallbackQuery,
    chat_config: ChatConfig,
    summary_service: FromDishka[SummaryService],
    message_thread_id: int | None = None,
) -> None:
    """``📋 Саммари 100/500`` under /help or a /summary refusal: post a NEW summary.

    A new message, not an edit: the button sits on /help, and turning the
    help into a summary would take away the help it was pressed on. Each
    press therefore leaves its own summary in the chat, and there is no tail
    of earlier parts to track.
    """
    lang = _chat_lang(chat_config)
    parts = (callback.data or "").split(":")
    try:
        owner_id = int(parts[1])
        count = int(parts[2])
    except (IndexError, ValueError):
        await _old_button(callback, lang, why="payload")
        return
    if len(parts) != 3:
        await _old_button(callback, lang, why="payload")
        return

    count = max(_SUMMARY_MIN, min(count, _SUMMARY_MAX))

    if not _check_owner(callback, owner_id):
        await callback.answer(_NOT_YOUR_BUTTON(lang_of(lang)), show_alert=True)
        return

    # `handle_summary` refuses on the command path when the chat has message
    # saving turned off; this button reaches the same service and must refuse
    # too, because it persists on already-sent messages and outlives the toggle.
    if not chat_config.save_messages:
        await callback.answer(_SAVE_MESSAGES_DISABLED(lang_of(lang)), show_alert=True)
        return

    msg = callback.message
    if not isinstance(msg, Message):
        await _old_button(callback, lang, why="no message")
        return

    # Check and claim with no await in between, so two taps cannot both pass.
    key = (msg.chat.id, msg.message_id)
    if key in _SUMMARIES_IN_FLIGHT:
        await callback.answer(_SUMMARY_BUSY(lang_of(lang)))
        return
    _SUMMARIES_IN_FLIGHT.add(key)
    try:
        await callback.answer()

        # The thread id MUST come from TopicMiddleware's handler kwarg, which is
        # None unless the chat is a real forum — reading it raw off the bot's own
        # message picks up the thread id Telegram stamps on linked-channel
        # discussion comments, silently narrowing the summary (TD-102).
        async with ProgressNotice(msg, text=_GENERATING(lang_of(lang))) as notice:
            html = await summary_service.generate(
                msg.chat.id,
                count=count,
                language=lang,
                message_thread_id=message_thread_id,
                linked_channel_id=chat_config.linked_channel_id,
            )

        if html:
            await notice.finish(split_html(html), language=lang)
        else:
            await notice.fail(_SUMMARY_FAILED(lang_of(lang)))
    finally:
        _SUMMARIES_IN_FLIGHT.discard(key)


# ---------------------------------------------------------------------------
# help_close: — delete the /help message
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("help_close:"))
async def handle_close_callback(callback: CallbackQuery, chat_config: ChatConfig) -> None:
    """``✖️ Закрыть`` under /help in a group: delete the message."""
    lang = _chat_lang(chat_config)
    parts = (callback.data or "").split(":")
    try:
        owner_id = int(parts[1])
    except (IndexError, ValueError):
        await _old_button(callback, lang, why="payload")
        return

    if not _check_owner(callback, owner_id):
        await callback.answer(_NOT_YOUR_BUTTON(lang_of(lang)), show_alert=True)
        return

    await callback.answer(_CLOSED(lang_of(lang)))

    msg = callback.message
    if isinstance(msg, Message):
        try:
            await msg.delete()
        except Exception:
            logger.warning("Failed to delete help message", chat_id=msg.chat.id)


# ---------------------------------------------------------------------------
# help_dm: — the DM start screen and its "What I can do"
# ---------------------------------------------------------------------------


async def bot_username(bot: Bot | None) -> str | None:
    """The bot's @username for t.me links, or None (the links are then left out).

    ``Bot.me()`` caches after the first call, so this costs one request per
    process.
    """
    if bot is None:
        return None
    try:
        me = await bot.me()
    except Exception:
        logger.warning("bot_me_failed", exc_info=True)
        return None
    return me.username


@router.callback_query(F.data.startswith("help_dm:"))
async def handle_help_dm_callback(
    callback: CallbackQuery,
    chat_config: ChatConfig,
    bot: Bot | None = None,
    **kwargs: Any,
) -> None:
    """``help_dm:{lang}:help`` opens "What I can do", ``…:start`` goes back."""
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[2] not in ("help", "start"):
        await _old_button(callback, _chat_lang(chat_config), why="payload")
        return
    lang = lang_of(parts[1])
    admin = bool(kwargs.get("is_admin"))
    username = await bot_username(bot)
    if parts[2] == "help":
        trigger = entry.pick_trigger(chat_config.trigger_words, lang)
        screen = entry.help_dm(lang, trigger=trigger, bot_username=username, admin=admin)
    elif admin:
        screen = entry.start_admin(lang)
    else:
        screen = entry.start_member(lang, bot_username=username)
    await show(callback, screen)


# ---------------------------------------------------------------------------
# help_callme: — "As in my account" and "Remove the name" under /callme
# ---------------------------------------------------------------------------


def _is_usage_owner(callback: CallbackQuery) -> bool:
    """The usage reply answers someone's /callme; only they may see it redrawn."""
    msg = callback.message
    if not isinstance(msg, Message) or callback.from_user is None:
        return False
    asked = msg.reply_to_message
    return (
        asked is not None
        and asked.from_user is not None
        and asked.from_user.id == callback.from_user.id
    )


async def _current_name(alias_repo: AliasRepository, chat_id: int, user_id: int) -> str | None:
    view = build_alias_view(await alias_repo.load_active(chat_id))
    return view.primary_by_user.get(user_id)


@router.callback_query(F.data.startswith("help_callme:"))
async def handle_callme_callback(
    callback: CallbackQuery,
    chat_config: ChatConfig,
    alias_repo: FromDishka[AliasRepository],
) -> None:
    """The two buttons under ``/callme``: they act on whoever presses them.

    Anyone in the chat may press, so the answer is a toast to the presser.
    The message itself is redrawn only when the presser is the person whose
    /callme it answers — redrawing it for somebody else would show their name
    under another member's question.
    """
    parts = (callback.data or "").split(":")
    user = callback.from_user
    msg = callback.message
    if len(parts) != 3 or parts[2] not in ("acc", "clr"):
        await _old_button(callback, _chat_lang(chat_config), why="payload")
        return
    if user is None or not isinstance(msg, Message):
        await _old_button(callback, _chat_lang(chat_config), why="no message")
        return
    lang = lang_of(parts[1])
    chat_id = msg.chat.id

    current: str | None
    try:
        if parts[2] == "acc":
            parsed = parse_alias(user.first_name or user.username or "")
            if not parsed.ok:
                await callback.answer(_CALLME_UNUSABLE(lang), show_alert=True)
                return
            outcome, _owner = await alias_repo.set_primary(
                chat_id=chat_id,
                user_id=user.id,
                alias=parsed.display,
                source="self",
                source_message_id=msg.message_id,
                source_user_id=user.id,
            )
            if outcome is AliasWriteOutcome.TAKEN:
                await callback.answer(
                    _CALLME_TAKEN(lang).format(name=parsed.display), show_alert=True
                )
                return
            toast = entry.callme_set_account_toast(lang, name=parsed.display)
            current = parsed.display
        else:
            had = await _current_name(alias_repo, chat_id, user.id)
            if had:
                await alias_repo.retire(chat_id, had)
            toast = entry.callme_cleared_toast(lang, had_name=bool(had))
            current = None
    except Exception as exc:
        logger.error(
            "callme_button_failed",
            chat_id=chat_id,
            user_id=user.id,
            action=parts[2],
            error=str(exc),
            exc_info=True,
        )
        await callback.answer(_CALLME_FAILED(lang), show_alert=True)
        return

    if _is_usage_owner(callback):
        await show(callback, entry.callme_usage(lang, current=current), toast=toast)
    else:
        await callback.answer(toast)
