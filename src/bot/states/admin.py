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

    # Sticker wizard: waiting for sticker to analyze
    awaiting_sticker = State()

    # Rules: waiting for JSON config
    awaiting_rule_config = State()

    # Knowledge Base: waiting for a forwarded message / @username to add an organizer
    awaiting_kb_organizer = State()
