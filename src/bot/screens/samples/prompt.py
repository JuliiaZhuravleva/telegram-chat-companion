"""Samples of what a prompt's message becomes once a tap elsewhere ended the prompt.

Built by `src.bot.middleware.prompt_exit`, which edits the message itself; the
title is the prompt screen's own, the one button the prompt's `PROMPT_HOMES` row.
"""

from __future__ import annotations

from src.bot.middleware.prompt_exit import CLOSED_ID, build_closed, home_button
from src.bot.screens import chat as screens
from src.bot.screens.i18n import Lang
from src.bot.screens.model import Screen
from src.bot.screens.registry import sample
from src.bot.screens.samples import fake
from src.bot.states.admin import AdminStates, title_line


def _closed(state: str, screen: Screen, key: str | None, lang: Lang) -> Screen:
    return build_closed(title_line(screen.text), home_button(state, key, lang), lang)


@sample(
    CLOSED_ID,
    section="Чаты",
    title="Инструкция: ввод закрыт",
    nav="panel",
    user_data=(fake.CHAT_TITLE,),
)
def instruction_closed(lang: Lang) -> Screen:
    prompt = screens.build_sp_prompt(fake.CHAT_ID, fake.CHAT_TITLE, "", lang)
    return _closed(str(AdminStates.awaiting_system_prompt.state), prompt, str(fake.CHAT_ID), lang)


@sample(
    CLOSED_ID,
    section="Чаты",
    title="Ввод закрыт: кнопки не вернулись",
    nav="panel",
    user_data=(fake.CHAT_TITLE,),
)
def closed_without_a_key(lang: Lang) -> Screen:
    """Telegram returned the prompt's message without its buttons: no id to go
    back with, so the one button is the panel's."""
    prompt = screens.build_sp_prompt(fake.CHAT_ID, fake.CHAT_TITLE, "", lang)
    return _closed(str(AdminStates.awaiting_system_prompt.state), prompt, None, lang)
