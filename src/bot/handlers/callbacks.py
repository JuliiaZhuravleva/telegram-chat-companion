"""Callback query handlers for inline keyboards."""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.help import summary_keyboard
from src.models.chat_config import ChatConfig
from src.services.modules.summary import SummaryService
from src.utils.telegram_text import split_html

router = Router(name="callbacks")
logger = structlog.get_logger(__name__)

_NOT_YOUR_BUTTON = {
    "ru": "Эта кнопка не для вас.",
    "en": "This button is not for you.",
}

# Mirrors the refusal `handle_summary` gives on the command path
# (`src/bot/handlers/commands.py`). Kept as its own copy rather than imported
# to avoid a handlers -> handlers dependency for two strings.
_SAVE_MESSAGES_DISABLED = {
    "ru": "Сохранение сообщений отключено для этого чата.",
    "en": "Message saving is disabled for this chat.",
}


# Continuation messages posted by the last refresh of a multi-part summary,
# keyed by (chat_id, anchor message id).
#
# A refreshed summary is rendered by editing the anchor in place, so a shorter
# new summary cannot overwrite the parts the previous one appended below it --
# they simply stay, stranded, and the next press strands another set. Telegram
# offers no way to ask "which messages did I send under this one", so the ids
# have to be remembered here.
#
# Process-lifetime and handler-consumed, so a module-level dict rather than
# Dishka (see the ADR in CLAUDE.md); the same shape as
# `_FAILED_SET_REGISTRATION` in handlers/media.py. Losing it on restart is
# harmless: the worst case is one refresh that leaves an old tail behind, which
# is what happens today anyway. Bounded so a long-lived process cannot grow it
# without limit -- summaries are per-chat and few, and evicting the oldest entry
# only costs that anchor its cleanup.
_CONTINUATIONS: dict[tuple[int, int], list[int]] = {}
_MAX_TRACKED_SUMMARIES = 256


async def _drop_continuations(anchor: Message) -> None:
    """Delete what the previous refresh appended under ``anchor``."""
    stale = _CONTINUATIONS.pop((anchor.chat.id, anchor.message_id), None)
    if not stale:
        return
    for message_id in stale:
        try:
            await anchor.bot.delete_message(anchor.chat.id, message_id)  # type: ignore[union-attr]
        except Exception as exc:
            # Already deleted by a human, too old to delete, or the bot lost the
            # right to. None of that should stop the refresh being rendered.
            logger.info(
                "Could not remove a stale summary continuation",
                chat_id=anchor.chat.id,
                message_id=message_id,
                error_type=type(exc).__name__,
            )


def _remember_continuations(anchor: Message, message_ids: list[int]) -> None:
    if len(_CONTINUATIONS) >= _MAX_TRACKED_SUMMARIES:
        _CONTINUATIONS.pop(next(iter(_CONTINUATIONS)), None)
    _CONTINUATIONS[(anchor.chat.id, anchor.message_id)] = message_ids


def _check_owner(callback: CallbackQuery, owner_id: int) -> bool:
    """Check if the callback sender is the button owner (0 = open to all)."""
    if owner_id == 0:
        return True
    return callback.from_user is not None and callback.from_user.id == owner_id


@router.callback_query(F.data.startswith("help_summary:"))
async def handle_summary_callback(
    callback: CallbackQuery,
    chat_config: ChatConfig,
    summary_service: FromDishka[SummaryService],
    message_thread_id: int | None = None,
) -> None:
    """Handle summary button press from help keyboard."""
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Invalid callback data.")
        return

    try:
        owner_id = int(parts[1])
        count = int(parts[2])
    except ValueError:
        await callback.answer("Invalid callback data.")
        return

    # Clamp count to allowed values
    count = max(1, min(count, 1000))
    lang = chat_config.language if chat_config.language in _NOT_YOUR_BUTTON else "ru"

    if not _check_owner(callback, owner_id):
        await callback.answer(_NOT_YOUR_BUTTON[lang], show_alert=True)
        return

    # `handle_summary` refuses on the command path when the chat has message
    # saving turned off; this button reaches the same service and must refuse
    # too. It is rendered by /help and by the summary's own navigation
    # keyboard, both of which persist on already-sent messages, so without
    # this check disabling `save_messages` left every existing button able to
    # summarize the history saved before the toggle was flipped.
    if not chat_config.save_messages:
        await callback.answer(_SAVE_MESSAGES_DISABLED[lang], show_alert=True)
        return

    # Early return if message context is gone (e.g. expired callback)
    msg = callback.message
    if not isinstance(msg, Message):
        await callback.answer("Message expired.", show_alert=True)
        return

    processing = "⏳ Генерирую..." if lang == "ru" else "⏳ Generating..."
    await callback.answer(processing)

    # Forum-aware summary: filter by topic if applicable. The thread id MUST
    # come from TopicMiddleware's handler kwarg, which is None unless the chat
    # is a real forum — reading it raw off the bot's own message (as this code
    # once did) picks up the thread id Telegram stamps on linked-channel
    # discussion comments, silently narrowing the refresh to ~2 messages while
    # the /summary command right next to it covers the whole chat (TD-102).
    html = await summary_service.generate(
        msg.chat.id,
        count=count,
        language=lang,
        message_thread_id=message_thread_id,
    )

    if not html:
        return

    user_id = callback.from_user.id if callback.from_user else 0
    keyboard = summary_keyboard(user_id, count, language=lang)

    # Split before editing: a refresh producing more than 4096 characters used
    # to fail here and be swallowed by the bare `except` below, leaving the
    # previous summary standing with a Refresh button that silently did
    # nothing, for ever, and logging one line with no chat_id to find it by.
    parts = split_html(html)
    if not parts:
        return

    # Whatever the PREVIOUS refresh appended has to go before this one renders,
    # or each press strands another orphaned tail above the new summary. The
    # anchor (this message) is edited in place, so only the continuations need
    # removing -- and only we know their ids, which is why they are remembered.
    await _drop_continuations(msg)

    try:
        # The keyboard rides on the ANCHOR, never on the last part. Two reasons,
        # and the second is the load-bearing one: a continuation that fails to
        # send would otherwise leave the summary with no buttons at all (no
        # refresh, no count switch, no close), because the anchor's own keyboard
        # was already cleared; and keeping it on the anchor means the next press
        # arrives with `callback.message` still pointing at the anchor rather
        # than at the tail, so this handler edits the right message every time.
        await msg.edit_text(parts[0], parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        logger.warning(
            "Failed to edit summary message",
            chat_id=msg.chat.id,
            part_count=len(parts),
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return

    appended: list[int] = []
    for index, part in enumerate(parts[1:], start=2):
        try:
            sent = await msg.answer(part, parse_mode="HTML")
        except Exception as exc:
            logger.warning(
                "Failed to send a continuation of the refreshed summary",
                chat_id=msg.chat.id,
                part_index=index,
                part_count=len(parts),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            break
        appended.append(sent.message_id)

    if appended:
        _remember_continuations(msg, appended)


@router.callback_query(F.data.startswith("help_close:"))
async def handle_close_callback(
    callback: CallbackQuery,
    chat_config: ChatConfig,
) -> None:
    """Handle close button press — delete the help message."""
    parts = (callback.data or "").split(":")
    if len(parts) != 2:
        await callback.answer("Invalid callback data.")
        return

    try:
        owner_id = int(parts[1])
    except ValueError:
        await callback.answer("Invalid callback data.")
        return

    lang = chat_config.language if chat_config.language in _NOT_YOUR_BUTTON else "ru"

    if not _check_owner(callback, owner_id):
        await callback.answer(_NOT_YOUR_BUTTON[lang], show_alert=True)
        return

    closed = "Закрыто" if lang == "ru" else "Closed"
    await callback.answer(closed)

    msg = callback.message
    if isinstance(msg, Message):
        try:
            await msg.delete()
        except Exception:
            logger.warning("Failed to delete help message")
