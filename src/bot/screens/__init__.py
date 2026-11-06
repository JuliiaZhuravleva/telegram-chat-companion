"""Redesigned admin screens: a screen is data, ``show()`` delivers it.

See ``docs/admin-dm-internals.md`` → "F.6 Screens" for the model, the callback
grammar, the stale-button fallback and the rules test.
"""

from src.bot.screens.i18n import Tr
from src.bot.screens.model import Button, ReplyButton, Screen
from src.bot.screens.render import back, pager, show

__all__ = ["Button", "ReplyButton", "Screen", "Tr", "back", "pager", "show"]
