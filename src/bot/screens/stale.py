"""The catch-all for panel buttons nothing else handles: show the hub, change nothing.

A button in an admin's DM outlives the code that drew it. When its handler is
gone (a retired prefix), its payload is garbled, or its prefix is new and its
area has not landed yet, the tap would otherwise spin until Telegram gives up
— and a spinner is indistinguishable from a broken bot. This router sits after
every router that owns an ``adm_``/``ar_`` prefix (``rules`` is the last) and
before ``commands``; nothing after it handles those prefixes.

"Object gone" (a deleted rule, a resolved request, a removed chat) is NOT
stale: the live handler renders the parent screen with a notice of its own.

Three answers, by who tapped and where: a bot admin in the DM gets the hub; a
bot admin anywhere else (an ``adm_`` button forwarded or posted into a group)
is told the panel lives in the DM — "access denied" would be false and would
send them looking for a permission problem; anyone else gets "access denied".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot.filters.admin import IsAdmin
from src.bot.screens.deps import state_of
from src.bot.screens.hub import show_hub
from src.bot.screens.i18n import Tr, lang_of
from src.bot.states.admin import AdminStates

logger = structlog.get_logger(__name__)

router = Router(name="stale")

PANEL_PREFIXES: Final = ("adm_", "ar_")

STALE_NOTICE: Final = Tr(
    "<i>Эта кнопка устарела и ничего не изменила. Вот панель сейчас.</i>",
    "<i>This button is out of date and changed nothing. Here is the panel now.</i>",
)
NO_ACCESS: Final = Tr("Нет доступа", "Access denied")
IN_DM_ONLY: Final = Tr(
    "Панель открывается в личке с ботом",
    "The panel opens in a private chat with the bot",
)


def _lang(data: str | None) -> str:
    parts = (data or "").split(":")
    return lang_of(parts[1] if len(parts) > 1 else None)


async def show_stale(
    callback: CallbackQuery,
    lang: str,
    *,
    data: Mapping[str, Any] | None = None,
    state: FSMContext | None = None,
) -> Message | None:
    """Answer an out-of-date tap with the hub and a line saying nothing changed.

    Clears the FSM state first when it is one of the panel's own prompts: the
    old button may have been the way out of one, and a prompt left armed would
    swallow the admin's next message. A state that belongs to anything else is
    left alone. No popup — the line on the screen is the explanation.

    Live handlers that cannot parse their payload call this instead of
    returning silently.
    """
    data = data or {}
    context = state if state is not None else state_of(data)
    if context is not None:
        current = await context.get_state()
        if current is not None and current in AdminStates.__all_states_names__:
            await context.clear()
    logger.info(
        "stale_button",
        action=(callback.data or "").split(":")[0],
        user_id=getattr(callback.from_user, "id", None),
    )
    return await show_hub(callback, lang, notice_raw=STALE_NOTICE(lang), data=data)


@router.callback_query(
    F.data.startswith(PANEL_PREFIXES),
    F.message.chat.type == "private",
    IsAdmin(),
)
async def handle_stale(callback: CallbackQuery, **kwargs: Any) -> None:
    """A panel payload no live handler took, from a bot admin in the DM."""
    await show_stale(callback, _lang(callback.data), data=kwargs)


@router.callback_query(
    F.data.startswith(PANEL_PREFIXES),
    ~(F.message.chat.type == "private"),
    IsAdmin(),
)
async def handle_stale_outside_dm(callback: CallbackQuery, **_kwargs: Any) -> None:
    """A bot admin tapped a panel payload outside the DM: say where the panel is.

    "Outside" includes a button with no message at all (an inline message):
    ``F.message.chat.type != "private"`` cannot resolve there and would send a
    bot admin to "access denied", hence the negated equality.
    """
    logger.info(
        "stale_button_outside_dm",
        action=(callback.data or "").split(":")[0],
        user_id=getattr(callback.from_user, "id", None),
    )
    await callback.answer(IN_DM_ONLY(_lang(callback.data)), show_alert=True)


@router.callback_query(F.data.startswith(PANEL_PREFIXES))
async def handle_stale_denied(callback: CallbackQuery, **_kwargs: Any) -> None:
    """The same payload from anyone else: answer it, so the button stops spinning."""
    logger.warning(
        "stale_button_denied",
        action=(callback.data or "").split(":")[0],
        user_id=getattr(callback.from_user, "id", None),
    )
    await callback.answer(NO_ACCESS(_lang(callback.data)), show_alert=True)
