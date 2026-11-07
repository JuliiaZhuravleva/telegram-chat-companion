"""One name for a chat waiting for the owner's decision: «запрос доступа».

The hub, the chat list, the request list and card, the rejected list and the
notification settings used «заявка» in some places and «запрос» in others, so
a reader could not tell they were the same thing. Every registered sample is
rendered in Russian and searched, buttons and notices included.
"""

from __future__ import annotations

import re

from src.bot.screens.model import Screen
from src.bot.screens.registry import load_all
from src.bot.screens.samples.foundation import DESIGN_COPIES

OLD_WORD = re.compile(r"[Зз]аявк")


def _words(screen: Screen) -> str:
    labels = [b.label for row in screen.rows for b in row]
    return "\n".join([screen.text, screen.notice or "", screen.notice_raw or "", *labels])


def test_no_screen_calls_it_a_zayavka() -> None:
    # Known positive in the same pass: the unregistered design copies still
    # say «Заявка», so a search that matched nothing would fail here.
    assert any(OLD_WORD.search(_words(item.build("ru"))) for item in DESIGN_COPIES)

    samples = [item for variants in load_all().values() for item in variants]
    found = [
        f"{item.screen_id}: {item.title}"
        for item in samples
        if OLD_WORD.search(_words(item.build("ru")))
    ]
    assert len(samples) > 100
    assert found == []


def test_the_request_screens_use_the_new_name() -> None:
    by_id = load_all()
    for screen_id in ("hub", "requests", "rejected", "chats", "notif"):
        texts = [_words(item.build("ru")) for item in by_id[screen_id]]
        assert any("запрос" in t.lower() for t in texts), screen_id
