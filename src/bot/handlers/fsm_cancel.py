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
an update, and the FSM-owning routers sit at positions 2, 3, 5 and 9. Measured
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
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka

from src.bot.custom_emoji import bare_label
from src.bot.filters.admin import IsAdmin
from src.bot.keyboards.admin_kb import ORG_CANCEL_LABEL
from src.bot.middleware.prompt_exit import CLOSED_BY_CANCEL, close_prompt_message
from src.bot.screens.i18n import lang_of
from src.bot.screens.render import take_reply_keyboard_away
from src.bot.states.admin import REPLY_KEYBOARD_PROMPTS, reply_keyboard_up, take_prompt_message
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository

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

_LEFTOVER_CANCEL: frozenset[str] = frozenset(bare_label(v) for v in ORG_CANCEL_LABEL.values())
"""What the organizer prompt's «✖️ Отмена» reply button sends, with or without its glyph."""


def _is_leftover_cancel(text: str) -> bool:
    return bare_label(text) in _LEFTOVER_CANCEL


class _LeftoverKeyboard(Filter):
    """No prompt armed, or one armed while a reply keyboard it did not put up
    is still under the input.

    The first is where a tap on another screen leaves the keyboard. The second
    is the same keyboard one step later: the admin opened another prompt, whose
    message has inline buttons only, and tapped the «Отмена» still on screen.
    Without a keyboard up (`reply_keyboard_up`) the word is that prompt's
    answer: «Отмена» is a word a rule may well be asked to watch for. In the
    prompts that put the keyboard up (`REPLY_KEYBOARD_PROMPTS`) the button is
    their own handler's to answer.
    """

    async def __call__(self, message: Message, raw_state: str | None = None) -> bool:
        if raw_state is None:
            return True
        return reply_keyboard_up(message.chat.id) and raw_state not in REPLY_KEYBOARD_PROMPTS


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
async def handle_cancel(
    message: Message,
    state: FSMContext,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Clear whatever dialog the user is parked in, and say which.

    The language comes from `admin_settings`, the same place every other
    admin-facing string resolves it — NOT from the client's `language_code`.
    Two reasons: an admin who set the panel to English while running a Russian
    Telegram client would get one Russian string among English ones, and a
    region-qualified locale (`en-US`, `pt-br` are real values Telegram sends)
    matches neither key and silently falls through to Russian, making the
    English string unreachable for those clients.
    """
    await _cancel(message, state, admin_repo, bot_config_repo, via="command")


@router.message(
    F.chat.type == "private",
    F.forward_origin.is_(None),
    F.text.func(_is_leftover_cancel),
    _LeftoverKeyboard(),
    # A tap on a reply-keyboard button is never a reply. A REPLY «Отмена» is
    # typed under one of the bot's messages, and that message's handler reads
    # it: an admin's reply to a sticker card is the card's answer
    # (admin_sticker.handle_admin_sticker_reply, on the router after this one).
    F.reply_to_message.is_(None),
    # Only an admin was ever shown that keyboard; anyone else's «Отмена» is
    # an ordinary message to the bot.
    IsAdmin(),
)
async def handle_leftover_cancel(
    message: Message,
    state: FSMContext,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """«✖️ Отмена» from a prompt's reply keyboard after that prompt ended.

    A tap on another screen's button, or another command, ends the organizer
    prompt or the rules person search, and a reply keyboard cannot be taken
    away by an edit, so it outlives the prompt
    (`src/bot/middleware/prompt_exit.py`). Its «Отмена» then means what /cancel
    means, and is answered the same way, keyboard removed — not passed on to
    the bot as a chat message, nor to the prompt armed since as its answer.
    """
    await _cancel(message, state, admin_repo, bot_config_repo, via="leftover keyboard")


async def _cancel(
    message: Message,
    state: FSMContext,
    admin_repo: AdminRepository,
    bot_config_repo: BotConfigRepository,
    *,
    via: str,
) -> None:
    current = await state.get_state()
    await state.clear()

    lang = await admin_repo.get_admin_language(bot_config_repo)
    texts = _CANCELLED if current is not None else _NOTHING_TO_CANCEL
    # Also takes away a reply keyboard a prompt left (the organizer picker):
    # a one-time keyboard only hides, it stays behind the input's icon. The
    # bot has no standing reply menu to lose by this.
    await take_reply_keyboard_away(message, texts.get(lang, texts["ru"]), parse_mode=None)
    # The message that asked would still be asking: closed like one a tap
    # elsewhere ended, saying it was cancelled.
    if current is not None:
        held = take_prompt_message(message.chat.id, current)
        if held is not None:
            await close_prompt_message(
                message.bot, held, current, lang_of(lang), reason=CLOSED_BY_CANCEL
            )

    logger.info(
        "FSM dialog cancelled by the user",
        user_id=message.from_user.id if message.from_user else None,
        cleared_state=current,
        via=via,
    )
