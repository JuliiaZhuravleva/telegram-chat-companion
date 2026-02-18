"""Inline keyboards for /help and /summary commands."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def help_keyboard(
    user_id: int,
    *,
    save_messages: bool = True,
    language: str = "ru",
    chat_type: str = "group",
) -> InlineKeyboardMarkup:
    """Build dynamic help keyboard based on enabled features."""
    buttons: list[list[InlineKeyboardButton]] = []
    is_group = chat_type in ("group", "supergroup")

    if save_messages and is_group:
        label_100 = "Саммари (100)" if language == "ru" else "Summary (100)"
        label_500 = "Саммари (500)" if language == "ru" else "Summary (500)"
        buttons.append([
            InlineKeyboardButton(
                text=label_100,
                callback_data=f"help_summary:{user_id}:100",
            ),
            InlineKeyboardButton(
                text=label_500,
                callback_data=f"help_summary:{user_id}:500",
            ),
        ])

    close_label = "Закрыть" if language == "ru" else "Close"
    buttons.append([
        InlineKeyboardButton(
            text=close_label,
            callback_data=f"help_close:{user_id}",
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def summary_keyboard(
    user_id: int,
    current_count: int,
    *,
    language: str = "ru",
) -> InlineKeyboardMarkup:
    """Build summary navigation keyboard."""
    counts = [100, 500, 1000]
    row: list[InlineKeyboardButton] = []

    for count in counts:
        if count == current_count:
            continue
        label = f"{count} сообщ." if language == "ru" else f"{count} msgs"
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"help_summary:{user_id}:{count}",
            )
        )

    close_label = "Закрыть" if language == "ru" else "Close"
    close_row = [
        InlineKeyboardButton(
            text=close_label,
            callback_data=f"help_close:{user_id}",
        )
    ]

    return InlineKeyboardMarkup(inline_keyboard=[row, close_row])
