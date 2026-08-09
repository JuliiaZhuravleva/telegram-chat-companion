"""Inline keyboards for the Reactions admin sub-router (R-D1).

Callback data pattern: ``adm_react_{action}:{lang}:{params...}``

Reactions config (``reactions_enabled`` / ``reactions_history_enabled``,
ADR-0004) is per-chat, so this mirrors ``admin_kb.py``'s chat-picker +
per-chat-submenu shape rather than ``admin_sticker.py``'s single global menu.
"""

from __future__ import annotations

import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.keyboards.nav import back_callback, origin_suffix
from src.bot.settings_fields import FIELDS_BY_KEY


def reactions_chat_picker_keyboard(
    chats: list[dict[str, object]],
    *,
    lang: str,
    page: int,
    total: int,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated list of whitelisted chats to pick one to manage reactions for."""
    rows: list[list[InlineKeyboardButton]] = []

    for chat in chats:
        chat_id = chat.get("chat_id")
        title = str(chat.get("chat_title") or chat_id)[:35]
        rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=f"adm_react_menu:{lang}:{chat_id}",
                ),
            ]
        )

    total_pages = max(1, math.ceil(total / per_page))
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm_react:{lang}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm_react:{lang}:{page + 1}"))
    rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад" if lang == "ru" else "◀️ Back",
                callback_data=f"adm_menu:{lang}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reactions_menu_keyboard(
    lang: str,
    *,
    chat_id: int,
    reactions_enabled: bool,
    reactions_history_enabled: bool,
    origin: str = "",
) -> InlineKeyboardMarkup:
    """Per-chat reactions submenu: master toggle + history toggle.

    Two independent toggles per ADR-0004 Decision 3 -- ``reactions_enabled``
    gates the whole module (incl. R-5's bot-initiated reactions),
    ``reactions_history_enabled`` gates only the ``message_reactions``
    INSERT. Deliberately not collapsed into one button.

    The toggles address their field by the settings registry's short code
    (``rx``/``rh``) rather than the full column name: spelled out, this
    payload was 60 of Telegram's 64 callback bytes, leaving no room for the
    ``origin`` token that decides where Back goes (``keyboards/nav.py``).
    The handler still accepts the old long form so a keyboard rendered
    before this change keeps working.
    """
    enabled_status = "✅" if reactions_enabled else "⚫"
    history_status = "✅" if reactions_history_enabled else "⚫"
    enabled_label = "Модуль реакций" if lang == "ru" else "Reactions module"
    history_label = "Хранение истории" if lang == "ru" else "History recording"
    suffix = origin_suffix(origin)
    enabled_code = FIELDS_BY_KEY["reactions_enabled"].code
    history_code = FIELDS_BY_KEY["reactions_history_enabled"].code

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"😀 {enabled_label}: {enabled_status}",
                    callback_data=f"adm_react_toggle:{lang}:{chat_id}:{enabled_code}{suffix}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"📝 {history_label}: {history_status}",
                    callback_data=f"adm_react_toggle:{lang}:{chat_id}:{history_code}{suffix}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад" if lang == "ru" else "◀️ Back",
                    callback_data=back_callback(
                        origin, lang=lang, chat_id=chat_id, default=f"adm_react:{lang}:0"
                    ),
                ),
            ],
        ]
    )
