"""The reply keyboard of the add-organizer prompt, and the labels it sends back.

The organizer screens themselves are plain data in ``screens/orgs.py`` and are
sent by ``handlers/admin_kb.py``; what stays here is what the handlers MATCH:
the request id a pick arrives with and the labels a tap comes back as.
"""

from __future__ import annotations

from typing import cast

from aiogram.types import ReplyKeyboardMarkup

from src.bot.screens import orgs
from src.bot.screens.render import CANCEL, to_markup
from src.bot.screens.routes import REQUEST_IDS

#: ``request_id`` of the organizer picker. A ``users_shared`` carrying any other
#: id is not this picker's. Which chat the pick is for lives in FSM state, not
#: here: a request id is a signed 32-bit int and a supergroup id (-100…) does
#: not fit in it.
ORG_PICK_REQUEST_ID = REQUEST_IDS.ORG_PICK

ORG_PICK_LABEL = {"ru": orgs.PICK_LABEL("ru"), "en": orgs.PICK_LABEL("en")}
ORG_CANCEL_LABEL = {"ru": CANCEL("ru"), "en": CANCEL("en")}


def kb_org_add_prompt_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """One-time keyboard under the input on the add-organizer prompt.

    The top button opens Telegram's own person search (``request_users``):
    contacts, recent chats and a global search by name or @username — the
    path that needs no forwarded message and no username the bot has seen.
    It is private-chat only, which the admin DM is.

    ``✖️ Отмена`` is a plain button: its label comes back as the admin's text
    and is routed before the forward/@username reader. Every answer to the
    pick sends ``ReplyKeyboardRemove`` — ``one_time_keyboard`` only HIDES the
    keyboard, it stays reachable behind the input's keyboard icon.
    """
    return cast(ReplyKeyboardMarkup, to_markup(orgs.org_add_screen(lang)))
