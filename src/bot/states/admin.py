"""FSM states for the admin panel."""

from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    """Admin panel FSM states."""

    # Default settings: waiting for text/array input
    awaiting_setting_value = State()

    # Sticker management: waiting for new description
    awaiting_sticker_edit = State()

    # Sticker wizard: waiting for sticker to analyze
    awaiting_sticker = State()

    # Rules: waiting for JSON config
    awaiting_rule_config = State()
