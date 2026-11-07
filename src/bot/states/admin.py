"""FSM states for the admin panel, and the buttons each prompt calls its own."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

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
        "adm_kb_org_x:",
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
