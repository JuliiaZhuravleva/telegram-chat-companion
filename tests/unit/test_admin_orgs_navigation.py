"""The organizers from the chat's root and back again, through the real router.

The buttons are read off the screens the handlers send and their payloads
delivered through ``src.bot.handlers.router`` (`screen_routing.deliver`), so a
callback that drifted from its handler fails here, not on a phone.
"""

from __future__ import annotations

import pytest

from src.bot.screens import chat as screens
from src.bot.screens import orgs
from src.bot.screens.chat import ChatView, config_values
from src.bot.screens.model import Button
from src.bot.screens.registry import load_all
from src.models.chat_config import ChatConfig
from tests.unit.screen_routing import FakeState, admin_tap, deliver

CHAT_ID = -1000000000002  # invented


def _view() -> ChatView:
    return ChatView(
        chat_id=CHAT_ID,
        title="Тестовый чат",
        chat_type="supergroup",
        values=config_values(ChatConfig(chat_id=CHAT_ID, enabled=True)),
        row={},
        organizers=3,
    )


def _button(rows: tuple[tuple[Button, ...], ...], label_start: str) -> Button:
    found = [b for row in rows for b in row if b.label.startswith(label_start)]
    assert len(found) == 1, [b.label for row in rows for b in row]
    return found[0]


async def _lands(data: str | None) -> str | None:
    assert data is not None
    return await deliver("callback_query", admin_tap(data), FakeState())


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_the_roots_organizers_button_opens_the_organizers(lang: str) -> None:
    button = _button(screens.build_chat(_view(), lang).rows, "👥 ")

    assert await _lands(button.callback) == "admin_kb.handle_kb_organizers"


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_the_organizers_go_back_to_the_chat(lang: str) -> None:
    screen = orgs.orgs_screen(lang, CHAT_ID, "Тестовый чат", [(1, "Аня")])
    back = screen.rows[-1][0]

    assert back.kind == "back"
    assert back.label == ("⬅️ Чат" if lang == "ru" else "⬅️ Chat")
    assert await _lands(back.callback) == "admin_chat_panel.handle_chat_panel_menu"


async def test_an_old_back_to_memory_still_opens_memory() -> None:
    """Messages sent before the move carry ``adm_pnl_grp:…:memory``: it still
    opens the memory section, which still exists."""
    old = f"adm_pnl_grp:ru:{CHAT_ID}:memory"

    assert await _lands(old) == "admin_chat_panel.handle_chat_panel_group"


def test_the_samples_agree_on_how_many_organizers_there_are() -> None:
    """The chat's root sample says how many organizers; every one-page
    organizer list in the samples shows that many names. A reader walking
    the screens compares them."""
    by_id = load_all()
    roots = [item.build("ru") for item in by_id["chat"]]
    counts = {
        int(b.label.rsplit("·", 1)[1])
        for screen in roots
        for row in screen.rows
        for b in row
        if b.label.startswith("👥 ") and b.label.rsplit("·", 1)[1].strip() != "0"
    }
    assert len(counts) == 1, counts
    (count,) = counts

    lists = [item.build("ru") for item in by_id["orgs"]]
    one_page = [
        s
        for s in lists
        if not any(b.kind == "pager" or b.label.startswith("◀") for row in s.rows for b in row)
    ]
    people = [
        sum(1 for row in s.rows for b in row if b.callback and b.callback.startswith("adm_kb_org:"))
        for s in one_page
    ]
    assert len(one_page) >= 3
    assert sorted({n for n in people if n}) == [count], people
