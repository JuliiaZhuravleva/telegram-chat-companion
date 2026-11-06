"""Today's hub, pinned to the keyboard and title it was drawn with before the
screen model existed: moving it onto `screens.hub` changed nothing a user sees.

The hub area owns this file and deletes it in the change that redesigns the
hub. Nothing else may depend on the hub looking like this — the stale
catch-all's tests patch `show_hub`, and the stale sample draws
`hub.sample_hub`.
"""

from __future__ import annotations

import pytest

from src.bot.handlers.admin import _MENU_TITLE
from src.bot.keyboards.admin import main_menu_keyboard
from src.bot.screens.hub import build_hub
from src.bot.screens.render import to_markup


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_the_hub_looks_exactly_as_today(lang: str):
    """Same rows, labels and payloads as ``main_menu_keyboard``."""
    built = to_markup(build_hub(lang))
    assert built is not None
    assert built.model_dump(exclude_none=True) == main_menu_keyboard(lang).model_dump(
        exclude_none=True
    )


def test_the_hub_title_is_todays():
    assert build_hub("ru").text == _MENU_TITLE["ru"]
    assert build_hub("en").text == _MENU_TITLE["en"]
