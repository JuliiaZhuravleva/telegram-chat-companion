"""FSM states for the admin panel: the buttons each prompt calls its own, the
screens it shows, the screen its message goes back to once it is closed, the
message showing it now, and the prompts that take a sticker as their answer."""

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    """Admin panel FSM states."""

    # Default settings: waiting for text/array input. Also reused by the
    # chat settings panel's tolerance_level FSM edit flow (ADR-0008
    # Decision 10, admin_chat_panel.py) -- a small, single-field flow
    # independent of F-1's still-deferred generic non-BOOL editing.
    awaiting_setting_value = State()

    # Sticker management: waiting for new description
    awaiting_sticker_edit = State()

    # Sticker management: waiting for a manually-typed explicitness score
    # (ADR-0009 Decision 7 / A-4). Deliberately a fresh state rather than
    # reusing awaiting_sticker_edit (docstring above already means "waiting
    # for a new description" -- a different flow on the same router) or
    # awaiting_setting_value (now owned by admin_chat_panel.py's per-chat
    # tolerance FSM) -- Decision 7's cosmetic-alternative escape hatch.
    awaiting_sticker_score = State()

    # Sticker wizard: waiting for sticker to analyze
    awaiting_sticker = State()

    # Rules: waiting for JSON config
    awaiting_rule_config = State()

    # Knowledge Base: waiting for a forwarded message / @username to add an organizer
    awaiting_kb_organizer = State()

    # Chat settings panel: waiting for a new per-chat system prompt. Its own
    # state, not awaiting_setting_value: that one belongs to the tolerance
    # flow, whose input handler parses a float and would answer a pasted
    # prompt with "enter a number".
    awaiting_system_prompt = State()

    # Rule builder (button-driven): the three steps that take typed input —
    # the trigger words, the reply text, and a reaction typed as an emoji.
    # Everything else the builder collects arrives by button and lives in
    # FSM data, not in a state of its own.
    awaiting_rule_words = State()
    awaiting_rule_text = State()
    awaiting_rule_react = State()

    # Rule builder: Telegram's person search is open under a reply keyboard,
    # waiting for a pick (or its «Отмена»). A state of its own, like every other
    # prompt, so a tap on another screen ends it and its «Отмена» is the rules'.
    awaiting_rule_who = State()

    # Chat settings panel: a typed percentage (how often the bot joins in
    # unasked, how often it answers with a sticker). Its own state, not
    # awaiting_setting_value: that one's input handler reads a 0..1 tolerance.
    awaiting_response_chance = State()


_OWN_TAPS: dict[State, tuple[str, ...]] = {
    # The tolerance number prompt: its opener and its «Отмена».
    AdminStates.awaiting_setting_value: ("adm_pnl_tol:", "adm_pnl_tolcancel:"),
    # The instruction editor: its opener, «Отмена», and the preview's
    # «Сохранить» / «Заново», which read the draft the state holds.
    AdminStates.awaiting_system_prompt: (
        "adm_pnl_sp:",
        "adm_pnl_spx:",
        "adm_pnl_spok:",
        "adm_pnl_spre:",
    ),
    # The sticker score prompt: its opener and its «Отмена».
    AdminStates.awaiting_sticker_score: ("adm_stk_expedit:", "adm_stk_expcancel:"),
    # The organizer prompt: its opener, the buttons of its failure answer,
    # and the participant list those lead to. The handlers that end it
    # (back to the organizers, a picked name, «Отмена») need to see the
    # state to take the reply keyboard away with it.
    AdminStates.awaiting_kb_organizer: (
        "adm_kb_org_add:",
        "adm_kb_org_list:",
        "adm_kb_org_pick:",
        "adm_kb_orgs:",
    ),
    # The rule prompts: every rules screen ends them itself, keeping the
    # builder's answers when its next step needs them.
    AdminStates.awaiting_rule_config: ("ar_",),
    AdminStates.awaiting_rule_words: ("ar_",),
    AdminStates.awaiting_rule_text: ("ar_",),
    AdminStates.awaiting_rule_react: ("ar_",),
    AdminStates.awaiting_rule_who: ("ar_",),
    # The percentage prompt: the pickers it opens from, and its «Отмена».
    AdminStates.awaiting_response_chance: ("adm_pnl_pct:", "adm_pnl_pctx:"),
    # Declared, never set: any tap ends them.
    AdminStates.awaiting_sticker_edit: (),
    AdminStates.awaiting_sticker: (),
}


def _key(state: State) -> str:
    if state.state is None:  # only a State outside a StatesGroup has no name
        raise ValueError(f"unnamed state {state!r}")
    return state.state


PROMPT_OWN_TAPS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {_key(state): taps for state, taps in _OWN_TAPS.items()}
)
"""State → the callback prefixes that do NOT end it (a prefix, or a family ending in ``_``).

A prompt reads the admin's next message as its answer, and the panel is
buttons on many messages: any tap outside this list ends the prompt before a
handler runs (`src.bot.middleware.prompt_exit`). A new state needs a row here,
or the routes test fails; an empty row means "every tap ends it".
"""


def is_own_tap(state: str, data: str) -> bool:
    """Whether the payload ``data`` belongs to the prompt ``state`` (see `PROMPT_OWN_TAPS`)."""
    return any(data.startswith(prefix) for prefix in PROMPT_OWN_TAPS.get(state, ()))


# ---------------------------------------------------------------------------
# The screens each prompt shows, and the message that shows it now
# ---------------------------------------------------------------------------

_SCREENS: dict[State, tuple[str, ...]] = {
    AdminStates.awaiting_setting_value: ("tol.in", "tol.invalid"),
    AdminStates.awaiting_system_prompt: ("sp.prompt", "sp.preview", "sp.refused", "sp.redo"),
    AdminStates.awaiting_sticker_score: ("stk.score_in",),
    AdminStates.awaiting_kb_organizer: ("org.add", "org.fail", "org.list"),
    AdminStates.awaiting_rule_config: ("rule.cfg", "rule.cfg_bad"),
    AdminStates.awaiting_rule_words: ("rule.words",),
    AdminStates.awaiting_rule_text: ("rule.text",),
    AdminStates.awaiting_rule_react: ("rule.react", "rule.react_in"),
    AdminStates.awaiting_rule_who: ("rule.who_find",),
    AdminStates.awaiting_response_chance: ("chance.in", "chance.invalid"),
    # Declared, never set: no screen asks for them.
    AdminStates.awaiting_sticker_edit: (),
    AdminStates.awaiting_sticker: (),
}

PROMPT_SCREENS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {_key(state): ids for state, ids in _SCREENS.items()}
)
"""State → the screens shown while it is armed (their buttons must be its own taps).

The routes test requires a row for every state, and a non-empty one for every
state the bot sets. `PromptExitMiddleware` uses it to find the message that
still asks for input when a tap elsewhere ends the prompt.
"""

_PROMPT_SCREEN_IDS: Final[frozenset[str]] = frozenset(
    screen_id for ids in _SCREENS.values() for screen_id in ids
)


@dataclass(frozen=True, slots=True)
class PromptHome:
    """Where a prompt's message sends the admin once a tap elsewhere closed it.

    The button is ``{prefix}{lang}:{key}`` and then `tail`; ``key`` is the id the
    prompt's own buttons carry after their language (the chat's, the sticker's
    for the score prompt), read off them on delivery (`note_screen`). The
    screen it opens needs nothing from the FSM: the prompt is over by then.
    """

    screen_id: str
    prefix: str
    tail: tuple[str, ...] = ()


_RULES_HOME = PromptHome("rules", "ar_list:", ("0",))

_HOMES: dict[State, PromptHome | None] = {
    AdminStates.awaiting_setting_value: PromptHome("chat.stickers", "adm_pnl_grp:", ("stickers",)),
    AdminStates.awaiting_system_prompt: PromptHome("behavior", "adm_pnl_grp:", ("behavior",)),
    AdminStates.awaiting_sticker_score: PromptHome("stk.card", "adm_stk_card:"),
    AdminStates.awaiting_kb_organizer: PromptHome("orgs", "adm_kb_orgs:", ("0",)),
    # A rule builder's answers live in the state: with it gone, the list.
    AdminStates.awaiting_rule_config: _RULES_HOME,
    AdminStates.awaiting_rule_words: _RULES_HOME,
    AdminStates.awaiting_rule_text: _RULES_HOME,
    AdminStates.awaiting_rule_react: _RULES_HOME,
    AdminStates.awaiting_rule_who: _RULES_HOME,
    # Declared, never set: no screen asks for them.
    AdminStates.awaiting_sticker_edit: None,
    AdminStates.awaiting_sticker: None,
}

PROMPT_HOMES: Final[Mapping[str, PromptHome | None]] = MappingProxyType(
    {_key(state): home for state, home in _HOMES.items()}
)
"""State → the screen its closed message offers to go back to (``None``: the panel).

The routes test requires a row for every state, and drives every prompt
screen's closed message through the real router with no state at all.
"""


@dataclass(frozen=True, slots=True)
class PromptMessage:
    """The message a prompt screen was last delivered into."""

    chat_id: int
    message_id: int
    screen_id: str
    title: str
    """The screen's title line (`title_line`); empty for an error reply."""
    editable: bool
    """False for a message sent with a reply keyboard: Telegram refuses to edit those."""
    home_key: str | None = None
    """The id its `PromptHome` button needs, off the screen's own buttons; None
    when they carry none (a reply keyboard): the closed message offers the panel."""


# Process-local, one entry per chat: the FSM storage is `MemoryStorage` (what
# `Dispatcher()` in main.py builds by default), which lives in the process too,
# so a restart loses a prompt and the record of its message together and
# neither can outlive the other.
_ON_SCREEN: dict[int, PromptMessage] = {}

_TAG = re.compile(r"<(/?)([a-zA-Z][\w-]*)[^>]*>")
_TITLE_MAX = 200


def title_line(text: str) -> str:
    """The title of the screen in HTML ``text``: its first line, safe to send alone.

    A screen's title is bold. A first line without ``<b>`` is an error reply's
    reason («… отправьте ещё раз»), which a closed prompt must not repeat: that
    gives "". A line whose tags do not close on it (a ``<blockquote>`` that goes
    on) or that runs past `_TITLE_MAX` characters comes back as escaped plain text.
    """
    line = next((part.strip() for part in text.split("\n") if part.strip()), "")
    if "<b>" not in line:
        return ""
    open_tags: list[str] = []
    balanced = True
    for match in _TAG.finditer(line):
        closing, name = match.group(1), match.group(2).lower()
        if not closing:
            open_tags.append(name)
        elif not open_tags or open_tags.pop() != name:
            balanced = False
            break
    plain = html.unescape(_TAG.sub("", line))
    if balanced and not open_tags and len(plain) <= _TITLE_MAX:
        return line
    if len(plain) > _TITLE_MAX:
        plain = plain[: _TITLE_MAX - 1].rstrip() + "…"
    return html.escape(plain, quote=False)


def _home_key(markup: object) -> str | None:
    """The id a prompt's inline buttons carry after their language
    (``prefix:lang:{id}…``), from the first button that has one."""
    for row in getattr(markup, "inline_keyboard", None) or ():
        for button in row:
            parts = (getattr(button, "callback_data", None) or "").split(":")
            if len(parts) > 2 and parts[2]:
                return parts[2]
    return None


def note_screen(
    message: object,
    screen_id: str,
    text: str,
    *,
    editable: bool = True,
    markup: object = None,
) -> None:
    """Record that ``message`` now shows ``screen_id`` (called by every delivery).

    A prompt screen becomes its chat's record; any other screen delivered into
    the recorded message means that message no longer asks for input, and the
    record goes. Anything without integer ids (a test double) is ignored.
    ``markup`` is what was sent; without it, the keyboard Telegram returned on
    ``message`` — either gives the id the closed message's button needs.
    """
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    message_id = getattr(message, "message_id", None)
    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return
    if screen_id in _PROMPT_SCREEN_IDS:
        sent = markup if markup is not None else getattr(message, "reply_markup", None)
        _ON_SCREEN[chat_id] = PromptMessage(
            chat_id, message_id, screen_id, title_line(text), editable, _home_key(sent)
        )
        return
    held = _ON_SCREEN.get(chat_id)
    if held is not None and held.message_id == message_id:
        del _ON_SCREEN[chat_id]


def take_prompt_message(chat_id: int, state: str) -> PromptMessage | None:
    """Forget ``chat_id``'s prompt message; return it if it shows ``state``'s prompt.

    A prompt that ended through its own handler (an answer, its «Отмена») leaves
    its record behind; the next prompt screen replaces it, and the state check
    here keeps a record of another prompt from being taken for this one.
    """
    held = _ON_SCREEN.pop(chat_id, None)
    if held is None or held.screen_id not in PROMPT_SCREENS.get(state, ()):
        return None
    return held


# ---------------------------------------------------------------------------
# Which prompts take a sticker as their answer
# ---------------------------------------------------------------------------

_TAKES_A_STICKER: dict[State, Literal["any", "forwarded"]] = {
    # The reaction prompt: a sticker stands for its emoji.
    AdminStates.awaiting_rule_react: "any",
    # The stickers area's score prompt: its own handler answers a sticker
    # with "the number goes in as text" (it is also registered first).
    AdminStates.awaiting_sticker_score: "any",
    # The organizer prompt: a forward names its sender, whatever it carries.
    # A sticker the admin sends themself is neither a forward nor a @username.
    AdminStates.awaiting_kb_organizer: "forwarded",
}

STICKER_ANSWERS: Final[Mapping[str, Literal["any", "forwarded"]]] = MappingProxyType(
    {_key(state): which for state, which in _TAKES_A_STICKER.items()}
)
"""Prompts whose handler reads a sticker as an answer; every other prompt leaves a
sticker to the stickers area's DM check, exactly as with no prompt open."""


def takes_a_sticker(state: str | None, *, forwarded: bool) -> bool:
    """Whether the prompt ``state`` answers a sticker (``forwarded``: it came as a forward)."""
    which = STICKER_ANSWERS.get(state) if state is not None else None
    return which == "any" or (which == "forwarded" and forwarded)
