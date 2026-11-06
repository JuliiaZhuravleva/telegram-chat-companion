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
"""

from __future__ import annotations

from typing import Any

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.access_card import (
    approved_notification_keyboard,
    rejected_notification_keyboard,
)
from src.bot.utils import safe_edit_reply_markup
from src.database.repositories.admin import AdminRepository
from src.database.repositories.chat_settings import ChatSettingsRepository

logger = structlog.get_logger(__name__)

router = Router(name="access_card")

_NOT_ADMIN: dict[str, str] = {
    "ru": "У вас нет доступа.",
    "en": "Access denied.",
}

_APPROVED: dict[str, str] = {
    "ru": "✅ Одобрено",
    "en": "✅ Approved",
}

_REJECTED: dict[str, str] = {
    "ru": "❌ Отклонено",
    "en": "❌ Rejected",
}

_ALREADY_HANDLED: dict[str, str] = {
    "ru": "Запрос уже обработан.",
    "en": "Request already handled.",
}


def _get_lang(lang: str | None) -> str:
    """Normalize language code to ru/en."""
    return lang if lang in ("ru", "en") else "ru"


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


@router.callback_query(F.data.startswith("adm_approve:"))
async def handle_approve_notification(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """Approve access from inline notification message."""
    if not _guard_admin(kwargs, callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        attempt_id = int(parts[2]) if len(parts) > 2 else None
    except ValueError:
        attempt_id = None

    if attempt_id is None:
        await callback.answer("Invalid data", show_alert=True)
        return

    attempt = await approve_attempt(admin_repo, chat_settings_repo, attempt_id)
    if attempt is None:
        await callback.answer(_ALREADY_HANDLED[lang], show_alert=True)
        return

    await callback.answer(_APPROVED[lang])

    # Replace buttons with status indicator + settings-panel link (D-1)
    msg = callback.message
    if isinstance(msg, Message):
        await safe_edit_reply_markup(
            msg,
            reply_markup=approved_notification_keyboard(lang, attempt["chat_id"]),
        )


@router.callback_query(F.data.startswith("adm_reject:"))
async def handle_reject_notification(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Reject access from inline notification message."""
    if not _guard_admin(kwargs, callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        attempt_id = int(parts[2]) if len(parts) > 2 else None
    except ValueError:
        attempt_id = None

    if attempt_id is None:
        await callback.answer("Invalid data", show_alert=True)
        return

    attempt = await reject_attempt(admin_repo, attempt_id)
    if attempt is None:
        await callback.answer(_ALREADY_HANDLED[lang], show_alert=True)
        return

    await callback.answer(_REJECTED[lang])

    # Replace buttons with status indicator
    msg = callback.message
    if isinstance(msg, Message):
        await safe_edit_reply_markup(
            msg,
            reply_markup=rejected_notification_keyboard(lang),
        )
