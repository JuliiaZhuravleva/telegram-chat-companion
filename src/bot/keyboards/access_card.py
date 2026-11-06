"""Keyboards of the access card: the DM a bot admin gets when an unknown chat wants in.

``adm_approve:{lang}:{attempt}`` / ``adm_reject:{lang}:{attempt}`` are
byte-compatible forever: cards already sitting in admins' DMs carry them.
Moved here unchanged from ``keyboards/admin.py``.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_CHAT_PANEL = {"ru": "⚙️ Настройки чата", "en": "⚙️ Chat settings"}


def access_keyboard(lang: str, attempt_id: int) -> InlineKeyboardMarkup:
    """Approve/reject buttons for unauthorized access notification."""
    approve_label = {"ru": "✅ Одобрить", "en": "✅ Approve"}
    reject_label = {"ru": "❌ Отклонить", "en": "❌ Reject"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=approve_label.get(lang, "Approve"),
                    callback_data=f"adm_approve:{lang}:{attempt_id}",
                ),
                InlineKeyboardButton(
                    text=reject_label.get(lang, "Reject"),
                    callback_data=f"adm_reject:{lang}:{attempt_id}",
                ),
            ],
        ]
    )


def approved_notification_keyboard(lang: str, chat_id: int) -> InlineKeyboardMarkup:
    """Status indicator + settings-panel link after approval (D-1).

    Replaces the approve/reject buttons on the DM notification. ``chat_id``
    is already known at approve time, so the settings button links straight
    to the per-chat panel (ADR-0006 Decision 4) -- no picker step needed.
    """
    label = {"ru": "✅ Одобрено", "en": "✅ Approved"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label.get(lang, "✅ Approved"), callback_data="noop"),
                InlineKeyboardButton(
                    text=_CHAT_PANEL.get(lang, _CHAT_PANEL["ru"]),
                    callback_data=f"adm_pnl_menu:{lang}:{chat_id}",
                ),
            ],
        ]
    )


def rejected_notification_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Status indicator after rejection (replaces approve/reject buttons)."""
    label = {"ru": "❌ Отклонено", "en": "❌ Rejected"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label.get(lang, "❌ Rejected"), callback_data="noop")],
        ]
    )
