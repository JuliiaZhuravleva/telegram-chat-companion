"""A single, always-reachable way out of any FSM prompt (TD-049).

Every multi-step admin dialog in this bot parks the user in an aiogram FSM
state and then relies on that dialog's own handler to let them out again. When
one of those handlers returns early -- invalid input, a failed authority check
-- the state survives and the next message is consumed by the same handler.
`MemoryStorage` (what `Dispatcher()` builds by default, `main.py`) has no TTL,
so until this module existed the only thing that had ever cleared a stuck state
was a process restart, i.e. a deploy.

Two properties make this an escape rather than another dialog:

**It is included FIRST.** `handlers/__init__.py` decides which handler consumes
an update, and the FSM-owning routers sit at positions 2, 3, 5 and 8. Measured
with the real `main_router.propagate_event`: appended LAST, `/cancel` in
`awaiting_rule_config` is eaten by the rules handler and answered "Невалидный
JSON" -- the cancel handler never runs. At position 1 it fires in every state.
Registration is not firing; position is the entire fix.

It does carry ONE content filter, and only one: a forwarded message is content
rather than a command, so `/cancel` arriving as a forward falls through to
whichever dialog is open — `admin_kb`'s organizer handler exists precisely to
accept such a forward.

**It carries no state filter,** and that is deliberate rather than an omission.
aiogram applies no implicit state check to a handler, so `Command("cancel")`
already matches in every state including `None` -- a `StateFilter("*")` here
would be decoration, not mechanism. Matching with no state is wanted: it lets
the handler say "nothing to cancel" instead of consuming an update and
silently doing nothing.

Known limit, stated because the alternative is a false promise: an admin
removed from `bot_config.admin_ids` mid-dialog cannot reach this handler
either. `AccessControlMiddleware` drops their DM update before any router runs
(their private chat is not `enabled`, and the admin bypass no longer applies).
Their stranded state is inert rather than a trap -- the dialogs that could
consume it are `IsAdmin`-gated too -- but it survives until the process
restarts. An `IsAdmin()` filter here would not change that; it would only make
the promise look narrower than it is.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

router = Router(name="fsm_cancel")
logger = structlog.get_logger(__name__)

_CANCELLED: dict[str, str] = {
    "ru": "Отменено.",
    "en": "Cancelled.",
}

_NOTHING_TO_CANCEL: dict[str, str] = {
    "ru": "Нечего отменять.",
    "en": "Nothing to cancel.",
}


@router.message(
    Command("cancel"),
    F.chat.type == "private",
    # A FORWARDED message is content, not a command — even when its text reads
    # "/cancel". aiogram's Command filter takes `message.text or message.caption`
    # and never looks at `forward_origin`, so without this the escape hatch
    # would eat the very input `admin_kb`'s organizer handler carves forwards
    # out for: forwarding someone's message to add them as an organizer.
    F.forward_origin.is_(None),
)
async def handle_cancel(message: Message, state: FSMContext) -> None:
    """Clear whatever dialog the user is parked in, and say which."""
    current = await state.get_state()
    await state.clear()

    lang = (message.from_user.language_code or "ru") if message.from_user else "ru"
    texts = _CANCELLED if current is not None else _NOTHING_TO_CANCEL
    await message.answer(texts.get(lang, texts["ru"]), parse_mode=None)

    logger.info(
        "FSM dialog cancelled by the user",
        user_id=message.from_user.id if message.from_user else None,
        cleared_state=current,
    )
