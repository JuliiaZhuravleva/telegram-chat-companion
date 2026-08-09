"""FSM states for the admin panel."""

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
