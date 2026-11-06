"""Approve / reject from the access card, and the two operations behind them.

The card is the DM a bot admin gets when an unknown chat wants in: someone
wrote there, or the bot was added to it (``access_requests.py`` sends it for
both). Its buttons, ``adm_approve:`` and ``adm_reject:``, are byte-compatible
forever. `approve_attempt` and `reject_attempt` are shared with the requests
screen of the panel (``handlers/admin.py``), so a request resolves the same way
from either place.

Moved here from ``handlers/admin.py`` with behaviour unchanged; the two helpers
lost their leading underscore (they were ``_do_approve`` / ``_do_reject``)
because ``admin.py`` now imports them. This router is included before the
admin router.

A tap redraws the card: the request stays readable, a status line goes on
top, and the one button left leads on — to the approved chat's settings, or
to the rejected list. A card older than 48 hours keeps its text and only
has its buttons swapped for that one (an old card is history, and redrawing
its text would put a fresh status line on it weeks later). A second tap on the same card, or a
tap on a card whose request was resolved from the panel, redraws it to the
state the request is actually in; when that is what the card already shows,
Telegram's "not modified" is swallowed by `show`. A payload that cannot be
parsed shows the panel with the stale notice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.screens import cards
from src.bot.screens.i18n import Lang, Tr, lang_of
from src.bot.screens.model import Screen
from src.bot.screens.render import show, to_markup
from src.bot.screens.stale import show_stale
from src.bot.utils import safe_edit_reply_markup
from src.database.repositories.admin import AdminRepository
from src.database.repositories.chat_settings import ChatSettingsRepository

logger = structlog.get_logger(__name__)

router = Router(name="access_card")

_NOT_ADMIN: Final = Tr("Нет доступа", "Access denied")
_ALREADY_HANDLED: Final = Tr("Запрос уже решён", "The request is already resolved")
_GONE: Final = Tr("Этой заявки больше нет", "This request no longer exists")

TEXT_EDIT_WINDOW: Final = timedelta(hours=48)
"""A card older than this keeps its text; only its buttons change."""


def _guard_admin(data: dict[str, Any], callback: CallbackQuery) -> bool:
    """Check admin + private chat, log denial for audit trail."""
    if not data.get("is_admin", False):
        user_obj = data.get("event_from_user")
        uid = getattr(user_obj, "id", None) if user_obj else None
        action = (callback.data or "").split(":")[0]
        logger.warning("admin_access_denied", user_id=uid, action=action)
        return False
    msg = callback.message
    return isinstance(msg, Message) and msg.chat.type == "private"


def _parse(data: str | None) -> tuple[Lang, int | None]:
    """``adm_approve:{lang}:{attempt_id}`` → (lang, attempt_id or None)."""
    parts = (data or "").split(":")
    lang = lang_of(parts[1] if len(parts) > 1 else None)
    if len(parts) != 3:
        return lang, None
    try:
        return lang, int(parts[2])
    except ValueError:
        return lang, None


# ---------------------------------------------------------------------------
# The two operations
# ---------------------------------------------------------------------------


async def approve_attempt(
    admin_repo: AdminRepository,
    chat_settings_repo: ChatSettingsRepository,
    attempt_id: int,
) -> dict[str, Any] | None:
    """Approve attempt: update status + enable chat. Returns attempt or None."""
    attempt = await admin_repo.get_attempt(attempt_id)
    if not attempt or attempt.get("status") != "pending":
        return None
    chat_id = attempt["chat_id"]
    await admin_repo.approve_all_for_chat(chat_id)
    fields: dict[str, Any] = {"enabled": True}
    if attempt.get("chat_title"):
        fields["chat_title"] = attempt["chat_title"]
    if attempt.get("chat_type"):
        fields["chat_type"] = attempt["chat_type"]
    await chat_settings_repo.upsert(chat_id, **fields)
    return attempt


async def reject_attempt(
    admin_repo: AdminRepository,
    attempt_id: int,
) -> dict[str, Any] | None:
    """Reject attempt: update status. Returns attempt or None."""
    attempt = await admin_repo.get_attempt(attempt_id)
    if not attempt or attempt.get("status") != "pending":
        return None
    await admin_repo.update_attempt_status(attempt_id, "rejected")
    return attempt


# ---------------------------------------------------------------------------
# Callbacks: approve/reject from the card
# ---------------------------------------------------------------------------


def _resolved_screen(lang: Lang, attempt: dict[str, Any], msg: Message) -> Screen | None:
    """The card for a request in its current state, or None while it is still pending."""
    who = cards.Applicant.from_attempt(attempt, added=cards.is_added_card(msg.text))
    status = attempt.get("status")
    if status == "approved":
        return cards.access_approved(lang, who)
    if status == "rejected":
        return cards.access_rejected(lang, who)
    return None


def _text_editable(msg: Message) -> bool:
    sent = msg.date
    if not isinstance(sent, datetime):
        return True
    return datetime.now(UTC) - sent <= TEXT_EDIT_WINDOW


async def _redraw(callback: CallbackQuery, screen: Screen, *, toast: str) -> None:
    msg = callback.message
    if not isinstance(msg, Message):
        await callback.answer(toast)
        return
    if _text_editable(msg):
        await show(callback, screen, toast=toast)
        return
    await callback.answer(toast)
    markup = to_markup(screen)
    await safe_edit_reply_markup(
        msg, reply_markup=markup if isinstance(markup, InlineKeyboardMarkup) else None
    )


async def _already_handled(
    callback: CallbackQuery, admin_repo: AdminRepository, lang: Lang, attempt_id: int
) -> None:
    """A double tap, or a request resolved from the panel: show where it stands."""
    attempt = await admin_repo.get_attempt(attempt_id)
    msg = callback.message
    screen = (
        _resolved_screen(lang, attempt, msg)
        if attempt is not None and isinstance(msg, Message)
        else None
    )
    if screen is None:
        await callback.answer(
            (_GONE if attempt is None else _ALREADY_HANDLED)(lang), show_alert=True
        )
        return
    await _redraw(callback, screen, toast=_ALREADY_HANDLED(lang))


@router.callback_query(F.data.startswith("adm_approve:"))
async def handle_approve_notification(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """👍 Одобрить on the card: enable the chat, redraw the card as approved."""
    lang, attempt_id = _parse(callback.data)
    if not _guard_admin(kwargs, callback):
        await callback.answer(_NOT_ADMIN(lang), show_alert=True)
        return
    if attempt_id is None:
        await show_stale(callback, lang, data=kwargs)
        return

    attempt = await approve_attempt(admin_repo, chat_settings_repo, attempt_id)
    if attempt is None:
        await _already_handled(callback, admin_repo, lang, attempt_id)
        return

    msg = callback.message
    added = isinstance(msg, Message) and cards.is_added_card(msg.text)
    who = cards.Applicant.from_attempt(attempt, added=added)
    await _redraw(callback, cards.access_approved(lang, who), toast=cards.APPROVED(lang))


@router.callback_query(F.data.startswith("adm_reject:"))
async def handle_reject_notification(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """❌ Отклонить on the card: mark the request rejected, redraw the card."""
    lang, attempt_id = _parse(callback.data)
    if not _guard_admin(kwargs, callback):
        await callback.answer(_NOT_ADMIN(lang), show_alert=True)
        return
    if attempt_id is None:
        await show_stale(callback, lang, data=kwargs)
        return

    attempt = await reject_attempt(admin_repo, attempt_id)
    if attempt is None:
        await _already_handled(callback, admin_repo, lang, attempt_id)
        return

    msg = callback.message
    added = isinstance(msg, Message) and cards.is_added_card(msg.text)
    who = cards.Applicant.from_attempt(attempt, added=added)
    await _redraw(callback, cards.access_rejected(lang, who), toast=cards.REJECTED(lang))
