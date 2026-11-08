"""Rules: one chat's list, a rule's card, and the button builder that makes a rule.

Callback routes (all DM-only, admin-only):

- ``ar_list:{lang}:{chat_id}:{page}``         — the chat's rules, three a page
- ``ar_all:{lang}:{chat_id}[:{rule_id}]``     — every rule of the chat on/off
  (``chat_settings.rules_enabled``); with a rule id, redraws that rule's card
- ``ar_view:{lang}:{rule_id}[:{chat_id}[:j|n]]`` — a rule's card; ``j`` opens its
  JSON, ``n`` sends the card as a new message (from the admin card a fired rule sends)
- ``ar_tog:{lang}:{rule_id}:{chat_id}:{page}`` — pause or resume one rule
- ``ar_del_ask:{lang}:{rule_id}[:{chat_id}]``  — confirm deleting it
- ``ar_del:{lang}:{rule_id}[:{chat_id}]``      — delete it, back to the list
- ``ar_add:{lang}:{chat_id}``                 — the builder's first step: what sets it off
- ``ar_b_type:{lang}:{chat_id}:{k|u|f|s}[:{page}]`` — words, a person, stickers, messages
- ``ar_b_match:{lang}:{chat_id}:{c|e}``       — the word anywhere, even inside a word, or alone
- ``ar_b_who:{lang}:{chat_id}:{user_id}``     — whose messages
- ``ar_b_find:{lang}:{chat_id}``              — whose messages, found with Telegram's
  own person search: a new message with a reply keyboard (``request_users``)
- ``ar_b_n:{lang}:{chat_id}:{n}``             — how many a minute is too many
- ``ar_b_act:{lang}:{chat_id}:{n|w|c|r|ri}``  — what the rule does
- ``ar_b_txt:{lang}:{chat_id}:{i}``           — a ready warning text
- ``ar_b_rx:{lang}:{chat_id}:{i}``            — a ready reaction
- ``ar_cfg:{lang}:{chat_id}``                 — the whole rule as JSON
- ``ar_type:{lang}:{chat_id}:{rule_type}``    — old buttons: the JSON prompt for a type
- ``ar_cancel:{lang}:{chat_id}``              — old buttons: leave the JSON prompt

The builder keeps its answers in FSM data (``rule_*`` keys) and asks for typed
input in four states (`_RULE_STATES`). A payload a handler cannot parse, or a
builder step whose draft is gone or belongs to another chat, is a stale button.

The person search is the one reply keyboard here. It is open while the state
is ``awaiting_rule_who`` AND ``rule_finding`` in FSM data holds its chat
(`_finding_chat`), and its picks carry ``REQUEST_IDS.RULE_WHO``, so the
knowledge base's picker never takes them. Every way out of it (a pick, a name
tapped in the inline picker, «✖️ Отмена», the button's label sent back as text
by an app without the search, a stale pick, ``/cancel``) takes the keyboard away.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from html import escape
from typing import Any, Final

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from dishka.integrations.aiogram import FromDishka

from src.bot.custom_emoji import bare_label
from src.bot.filters.admin import IsAdmin
from src.bot.screens import rules as screens
from src.bot.screens.deps import resolve, state_of
from src.bot.screens.i18n import Tr, lang_of
from src.bot.screens.model import Screen
from src.bot.screens.render import CANCEL, page_slice, show
from src.bot.screens.routes import REQUEST_IDS, cb, parse
from src.bot.screens.sections import shows_common
from src.bot.screens.stale import show_stale
from src.bot.settings_fields import FIELDS_BY_CODE
from src.bot.states.admin import AdminStates
from src.bot.utils import safe_edit_reply_markup
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.messages import MessageRepository
from src.database.repositories.rules import RulesRepository
from src.models.rules import (
    _VALID_RULE_ACTIONS,
    _VALID_RULE_TYPES,
    MAX_WEIGHT,
    RuleAction,
    is_weight,
)
from src.services.chat_config import ChatConfigService
from src.services.modules.reactions.selector import ReactionSelector
from src.services.rules.reply_text import sent_length, worst_length
from src.utils.telegram_text import TELEGRAM_MESSAGE_LIMIT

logger = structlog.get_logger(__name__)

router = Router(name="rules")

_NOT_ADMIN: dict[str, str] = {
    "ru": "У вас нет доступа.",
    "en": "Access denied.",
}

CREATED: Final = Tr("Правило создано", "Rule created")
DELETED: Final = Tr("Правило удалено", "Rule deleted")
SPAM_NAME: Final = Tr("Сообщения: {n} за минуту", "Messages: {n} a minute")
FLOOD_NAME: Final = Tr("Стикеры: {n} за минуту", "Stickers: {n} a minute")
_NAME_CHARS: Final = screens.NAME_CHARS
"""A rule's name is cut here, whoever wrote it: the name heads its card."""
_SHOWN_WORD_CHARS: Final = 30
"""An over-long word is quoted back cut to this, so the error stays one line."""
NOT_CREATED: Final = Tr(
    "<i>Правило не создано: начните заново.</i>",
    "<i>The rule was not created: please start again.</i>",
)
"""Over the list when a typed last answer finds its draft incomplete (no ✓: nothing was done)."""

_RULE_STATES: Final = frozenset(
    {
        AdminStates.awaiting_rule_config.state,
        AdminStates.awaiting_rule_words.state,
        AdminStates.awaiting_rule_text.state,
        AdminStates.awaiting_rule_react.state,
        AdminStates.awaiting_rule_who.state,
    }
)
"""The typed-input prompts this file arms. Only these does a rules screen end."""

_DRAFT_KEYS: Final = (
    "rule_chat_id",
    "rule_type",
    "rule_words",
    "rule_match",
    "rule_user",
    "rule_name",
    "rule_n",
    "rule_action",
    "rule_people",
    "rule_prompt_id",
    "rule_finding",
    "rule_no_find",
)
"""The builder's answers in FSM data, set to None when a new rule starts.

``rule_finding`` is the chat id while the person search (``rule.who_find``) is open."""

_STATE_SETTERS: Final[Mapping[str, str]] = {
    "handle_type_selected": (
        "an old type button opens the JSON prompt for its type: it arms "
        "awaiting_rule_config, so clearing it first would be undone by the same tap"
    ),
    "handle_rule_cfg": "opens the JSON prompt: it arms awaiting_rule_config",
    "handle_builder_type": (
        "«Ключевые слова» opens the words prompt; the other kinds leave any armed "
        "prompt through the helper with keep_draft, because the draft is this tap's input"
    ),
    "handle_builder_action": (
        "warning and reply texts and a typed reaction arm their prompts; the other "
        "actions leave any armed prompt through the helper"
    ),
}
"""Callback handlers that set a state, so they cannot start by leaving one.

Every other callback handler here starts with `_leave_config_prompt`; the
escape test walks this module and holds both halves, with these reasons.
"""


def _check_admin(data: Mapping[str, Any]) -> bool:
    return bool(data.get("is_admin", False))


def _is_private(callback: CallbackQuery) -> bool:
    msg = callback.message
    if isinstance(msg, Message):
        return msg.chat.type == "private"
    return False


async def _denied(callback: CallbackQuery, data: Mapping[str, Any]) -> bool:
    if _check_admin(data) and _is_private(callback):
        return False
    lang = lang_of(_field(_fields(callback.data), 0))
    await callback.answer(_NOT_ADMIN[lang], show_alert=True)
    return True


async def _leave_config_prompt(state: FSMContext, *, keep_draft: bool = False) -> None:
    """Drop a rule prompt (`_RULE_STATES`) when the admin navigates away from it.

    Without this the state outlives the screen that created it. Concrete
    misfire: open the type prompt for chat A, tap «Назад», wander off, and
    paste any valid JSON dict into the DM an hour later -- a rule is silently
    created in chat A with the type chosen back then. `/cancel` and the cancel
    button do not cover it, because the admin never realises a dialog is still
    open. The words, text and reaction prompts have the same shape: a stray
    message an hour later would become a rule.

    Only OUR state is cleared. A rules screen has no business ending someone
    else's dialog, and `state.clear()` is indiscriminate.

    ``keep_draft`` drops only the state, keeping the builder's answers: a
    builder step that leaves a prompt still needs what was picked before it.
    """
    if await state.get_state() in _RULE_STATES:
        if keep_draft:
            await state.set_state(None)
        else:
            await state.clear()


async def _retire_prompt(message: Message, prompt_id: object) -> None:
    """Take the keyboard off an earlier prompt, so one live Cancel remains.

    A typed answer arrives as a new message and is answered by one; the prompt
    above it would otherwise keep a Cancel that still works. Cosmetic: when
    Telegram refuses (the prompt was deleted, or is too old), the old Cancel
    stays and is harmless, since the list it opens is current.
    """
    if not isinstance(prompt_id, int) or isinstance(prompt_id, bool) or message.bot is None:
        return
    stub = Message(message_id=prompt_id, date=message.date, chat=message.chat).as_(message.bot)
    try:
        await safe_edit_reply_markup(stub, reply_markup=None)
    except TelegramAPIError as exc:
        logger.info("rule_prompt_keyboard_kept", prompt_id=prompt_id, error=str(exc))


def _int(raw: str | None) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _field(fields: list[str], i: int) -> str | None:
    return fields[i] if len(fields) > i and fields[i] else None


def _fields(data: str | None) -> list[str]:
    return (data or "").split(":")[1:]


# ---------------------------------------------------------------------------
# What the screens read
# ---------------------------------------------------------------------------


async def _chat(
    chat_id: int,
    chat_config_service: ChatConfigService,
    chat_settings_repo: ChatSettingsRepository,
) -> tuple[screens.ChatRules, str]:
    """The chat's rule settings (NULL-aware, like the panel's toggles) and its title."""
    config = await chat_config_service.get_config(chat_id)
    row = await chat_settings_repo.get(chat_id) or {}
    chat = screens.ChatRules(
        enabled=bool(config.rules_enabled),
        common=shows_common(FIELDS_BY_CODE["re"], row),
        mode=str(config.rules_mode),
    )
    title = row.get("chat_title")
    return chat, title if isinstance(title, str) and title else screens.chat_id_text(chat_id)


async def _list(
    lang: str,
    chat_id: int,
    page: int,
    *,
    rules_repo: RulesRepository,
    chat_config_service: ChatConfigService,
    chat_settings_repo: ChatSettingsRepository,
    notice: str | None = None,
    notice_raw: str | None = None,
    gone: bool = False,
) -> Screen:
    """The chat's rules on ``page`` (clamped into range), or ``rule.gone`` over it."""
    chat, title = await _chat(chat_id, chat_config_service, chat_settings_repo)
    rows, total = await rules_repo.get_rules_page(chat_id, page, screens.PER_PAGE)
    clamped, pages, _ = page_slice(total, page, screens.PER_PAGE)
    if clamped != page:
        rows, total = await rules_repo.get_rules_page(chat_id, clamped, screens.PER_PAGE)
        clamped, pages, _ = page_slice(total, clamped, screens.PER_PAGE)
    rules = [screens.rule_view(row) for row in rows]
    if gone:
        return screens.gone_screen(
            lang,
            chat_id=chat_id,
            title=title,
            chat=chat,
            rules=rules,
            page=clamped,
            pages=pages,
            notice=notice,
        )
    return screens.rules_screen(
        lang,
        chat_id=chat_id,
        title=title,
        chat=chat,
        rules=rules,
        page=clamped,
        pages=pages,
        notice=notice,
        notice_raw=notice_raw,
    )


async def _card(
    lang: str,
    rule: screens.RuleView,
    *,
    chat_config_service: ChatConfigService,
    chat_settings_repo: ChatSettingsRepository,
    show_json: bool = False,
    notice: str | None = None,
) -> Screen:
    chat, _ = await _chat(rule.chat_id, chat_config_service, chat_settings_repo)
    return screens.rule_screen(lang, rule, chat=chat, show_json=show_json, notice=notice)


# ---------------------------------------------------------------------------
# The list and the switch for all of a chat's rules
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("ar_list:"))
async def handle_rules_list(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """One chat's rules, a page of them."""
    if await _denied(callback, kwargs):
        return
    await _leave_config_prompt(state)

    fields = parse(callback.data, 2)
    lang = lang_of(_field(_fields(callback.data), 0))
    chat_id = _int(fields[1]) if fields else None
    page = _int(_field(_fields(callback.data), 2)) or 0
    if chat_id is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    await show(
        callback,
        await _list(
            lang,
            chat_id,
            page,
            rules_repo=rules_repo,
            chat_config_service=chat_config_service,
            chat_settings_repo=chat_settings_repo,
        ),
    )


async def show_rules(
    target: CallbackQuery | Message,
    lang: str,
    chat_id: int,
    *,
    notice: str | None = None,
    data: Mapping[str, Any],
) -> Message | None:
    """Show one chat's rules list (first page): edit in place, or a new message.

    Stable entry other areas call; ``data`` is the calling handler's
    ``**kwargs`` (`screens.deps`). Renders as the ``ar_list:`` button does,
    and like it leaves a rule prompt: the list has no Cancel, so a prompt left
    armed would take the admin's next message as a rule's input.
    """
    rules_repo = await resolve(data, RulesRepository)
    chat_config_service = await resolve(data, ChatConfigService)
    chat_settings_repo = await resolve(data, ChatSettingsRepository)
    state = state_of(data)
    if state is not None:
        await _leave_config_prompt(state)
    screen = await _list(
        lang_of(lang),
        chat_id,
        0,
        rules_repo=rules_repo,
        chat_config_service=chat_config_service,
        chat_settings_repo=chat_settings_repo,
        notice=notice,
    )
    return await show(target, screen)


@router.callback_query(F.data.startswith("ar_all:"))
async def handle_rules_all(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """Turn every rule of a chat on or off (``chat_settings.rules_enabled``).

    NULL-aware like the panel's toggles: an inherited value flips from what the
    chat actually does, and the flip happens inside the UPDATE, so a double tap
    is two flips. With a rule id the tap came from that rule's card, which is
    redrawn; otherwise the list is. Either one says what the tap did.
    """
    if await _denied(callback, kwargs):
        return
    await _leave_config_prompt(state)

    fields = parse(callback.data, 2)
    lang = lang_of(_field(_fields(callback.data), 0))
    chat_id = _int(fields[1]) if fields else None
    raw_rule = _field(_fields(callback.data), 2)
    rule_id = _int(raw_rule)
    if chat_id is None or (raw_rule is not None and rule_id is None):
        await show_stale(callback, lang, data=kwargs, state=state)
        return

    effective = bool((await chat_config_service.get_config(chat_id)).rules_enabled)
    stored = await chat_settings_repo.toggle_bool_field(chat_id, "rules_enabled", effective)
    if stored is None:
        logger.warning("rules_toggle_no_row", chat_id=chat_id)
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    chat_config_service.invalidate(chat_id)
    notice = (screens.RULES_ON if stored else screens.RULES_OFF)(lang)

    row = await rules_repo.get(rule_id) if rule_id is not None else None
    if row is not None:
        screen = await _card(
            lang,
            screens.rule_view(row),
            chat_config_service=chat_config_service,
            chat_settings_repo=chat_settings_repo,
            notice=notice,
        )
    else:
        screen = await _list(
            lang,
            chat_id,
            0,
            rules_repo=rules_repo,
            chat_config_service=chat_config_service,
            chat_settings_repo=chat_settings_repo,
            notice=notice,
            gone=rule_id is not None,
        )
    await show(callback, screen)


# ---------------------------------------------------------------------------
# One rule: its card, pause, delete
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("ar_view:"))
async def handle_rule_view(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """A rule's card, its JSON open with ``j``; a new message with ``n``."""
    if await _denied(callback, kwargs):
        return
    await _leave_config_prompt(state)

    fields = parse(callback.data, 2)
    lang = lang_of(_field(_fields(callback.data), 0))
    rule_id = _int(fields[1]) if fields else None
    raw_chat = _field(_fields(callback.data), 2)
    chat_id = _int(raw_chat)
    flag = _field(_fields(callback.data), 3)
    if rule_id is None or (raw_chat is not None and chat_id is None):
        await show_stale(callback, lang, data=kwargs, state=state)
        return

    row = await rules_repo.get(rule_id)
    if row is None:
        await _show_gone(
            callback,
            lang,
            chat_id,
            data=kwargs,
            state=state,
            rules_repo=rules_repo,
            chat_config_service=chat_config_service,
            chat_settings_repo=chat_settings_repo,
            new_message=flag == "n",
        )
        return
    screen = await _card(
        lang,
        screens.rule_view(row),
        chat_config_service=chat_config_service,
        chat_settings_repo=chat_settings_repo,
        show_json=flag == "j",
    )
    await show(callback, screen, new_message=flag == "n")


async def _show_gone(
    callback: CallbackQuery,
    lang: str,
    chat_id: int | None,
    *,
    data: Mapping[str, Any],
    state: FSMContext,
    rules_repo: RulesRepository,
    chat_config_service: ChatConfigService,
    chat_settings_repo: ChatSettingsRepository,
    new_message: bool = False,
) -> None:
    """The rule a button points at is gone: its chat's list says so.

    An old button that carries no chat id cannot say which list; that one is
    a stale button and gets the hub. ``new_message`` is the alert card's
    button: the list goes under it, and the card stays the record of what fired.
    """
    if chat_id is None:
        await show_stale(callback, lang, data=data, state=state)
        return
    await show(
        callback,
        await _list(
            lang,
            chat_id,
            0,
            rules_repo=rules_repo,
            chat_config_service=chat_config_service,
            chat_settings_repo=chat_settings_repo,
            gone=True,
        ),
        new_message=new_message,
    )


@router.callback_query(F.data.startswith("ar_tog:"))
async def handle_rule_toggle(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """Pause or resume one rule, and redraw its card."""
    if await _denied(callback, kwargs):
        return
    await _leave_config_prompt(state)

    fields = parse(callback.data, 3)
    lang = lang_of(_field(_fields(callback.data), 0))
    rule_id = _int(fields[1]) if fields else None
    chat_id = _int(fields[2]) if fields else None
    if rule_id is None or chat_id is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return

    row = await rules_repo.get(rule_id)
    if row is not None:
        row = await rules_repo.toggle(rule_id, enabled=not bool(row.get("enabled", True)))
    if row is None:
        await _show_gone(
            callback,
            lang,
            chat_id,
            data=kwargs,
            state=state,
            rules_repo=rules_repo,
            chat_config_service=chat_config_service,
            chat_settings_repo=chat_settings_repo,
        )
        return
    screen = await _card(
        lang,
        screens.rule_view(row),
        chat_config_service=chat_config_service,
        chat_settings_repo=chat_settings_repo,
    )
    await show(callback, screen)


@router.callback_query(F.data.startswith("ar_del_ask:"))
async def handle_rule_delete_ask(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """The one confirmation here: deleting a rule cannot be undone."""
    if await _denied(callback, kwargs):
        return
    await _leave_config_prompt(state)

    fields = parse(callback.data, 2)
    lang = lang_of(_field(_fields(callback.data), 0))
    rule_id = _int(fields[1]) if fields else None
    raw_chat = _field(_fields(callback.data), 2)
    chat_id = _int(raw_chat)
    if rule_id is None or (raw_chat is not None and chat_id is None):
        await show_stale(callback, lang, data=kwargs, state=state)
        return

    row = await rules_repo.get(rule_id)
    if row is None:
        await _show_gone(
            callback,
            lang,
            chat_id,
            data=kwargs,
            state=state,
            rules_repo=rules_repo,
            chat_config_service=chat_config_service,
            chat_settings_repo=chat_settings_repo,
        )
        return
    await show(callback, screens.delete_ask_screen(lang, screens.rule_view(row)))


@router.callback_query(F.data.startswith("ar_del:"))
async def handle_rule_delete(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """Delete a rule and show its chat's list with a notice."""
    if await _denied(callback, kwargs):
        return
    await _leave_config_prompt(state)

    fields = parse(callback.data, 2)
    lang = lang_of(_field(_fields(callback.data), 0))
    rule_id = _int(fields[1]) if fields else None
    raw_chat = _field(_fields(callback.data), 2)
    chat_id = _int(raw_chat)
    if rule_id is None or (raw_chat is not None and chat_id is None):
        await show_stale(callback, lang, data=kwargs, state=state)
        return

    row = await rules_repo.get(rule_id)
    if row is None or not await rules_repo.delete(rule_id):
        await _show_gone(
            callback,
            lang,
            chat_id,
            data=kwargs,
            state=state,
            rules_repo=rules_repo,
            chat_config_service=chat_config_service,
            chat_settings_repo=chat_settings_repo,
        )
        return
    await show(
        callback,
        await _list(
            lang,
            screens.rule_view(row).chat_id,
            0,
            rules_repo=rules_repo,
            chat_config_service=chat_config_service,
            chat_settings_repo=chat_settings_repo,
            notice=DELETED(lang),
        ),
    )


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


async def _draft(state: FSMContext, chat_id: int) -> dict[str, Any] | None:
    """The builder's answers so far, or None when they are gone or for another chat.

    A builder button outlives its draft: a new rule started for another chat,
    a finished rule, a restart. Such a tap is stale, never a rule in the
    wrong chat.
    """
    data = await state.get_data()
    if data.get("rule_chat_id") != chat_id or data.get("rule_type") not in _VALID_RULE_TYPES:
        return None
    return data


def _builder_args(callback: CallbackQuery, n: int) -> tuple[str, int | None, list[str]]:
    """``(lang, chat_id, the other fields)``; chat_id None when the payload is garbled."""
    lang = lang_of(_field(_fields(callback.data), 0))
    fields = parse(callback.data, n)
    if fields is None:
        return lang, None, []
    return lang, _int(fields[1]), [*fields[2:], *_fields(callback.data)[n:]]


def _config_for(draft: Mapping[str, Any]) -> dict[str, Any] | None:
    """The engine's config for what the builder picked, or None when a step is missing."""
    rule_type = draft.get("rule_type")
    name = draft.get("rule_name")
    if not isinstance(name, str):
        return None
    if rule_type == "keyword_trigger":
        words = draft.get("rule_words")
        match = draft.get("rule_match")
        if not isinstance(words, list) or match not in ("contains", "exact"):
            return None
        return {"name": name, "keywords": words, "match_type": match}
    if rule_type == "user_specific":
        user_id = draft.get("rule_user")
        if not isinstance(user_id, int):
            return None
        # `any_message` is what makes the rule fire on the person's every
        # message: without it the engine looks for `conditions.keywords`,
        # finds none, and the rule never fires.
        return {"name": name, "user_ids": [user_id], "conditions": {"any_message": True}}
    n = draft.get("rule_n")
    if not isinstance(n, int):
        return None
    if rule_type == "spam_detect":
        # The engine's default length cap is 4000 characters, which the
        # builder never asks about: written out as 0 ("off") so the rule does
        # exactly what its card says, a count of messages.
        return {
            "name": name,
            "max_messages": n,
            "time_window_seconds": 60,
            "max_chars_per_message": 0,
        }
    if rule_type == "sticker_flood":
        return {"name": name, "max_stickers_per_interval": n, "interval_seconds": 60}
    return None


async def _create(
    target: CallbackQuery | Message,
    lang: str,
    chat_id: int,
    draft: Mapping[str, Any],
    action: str,
    extra: Mapping[str, Any],
    *,
    rules_repo: RulesRepository,
    chat_config_service: ChatConfigService,
    chat_settings_repo: ChatSettingsRepository,
) -> Message | None:
    """Save the rule the builder describes and show its card with «Правило создано»."""
    config = _config_for(draft)
    if config is None:
        return None
    config = {**config, "action": action, **extra}
    rule_type = str(draft["rule_type"])
    rule_id = await rules_repo.create(
        chat_id=chat_id, rule_type=rule_type, config=config, weight=1, mandatory=False
    )
    logger.info(
        "rule_created", chat_id=chat_id, rule_id=rule_id, rule_type=rule_type, via="builder"
    )
    rule = screens.RuleView(id=int(rule_id), chat_id=chat_id, rule_type=rule_type, config=config)
    screen = await _card(
        lang,
        rule,
        chat_config_service=chat_config_service,
        chat_settings_repo=chat_settings_repo,
        notice=CREATED(lang),
    )
    return await show(target, screen)


@router.callback_query(F.data.startswith("ar_add:"))
async def handle_add_rule(
    callback: CallbackQuery,
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """A new rule: start a fresh draft for this chat and ask what sets it off."""
    if await _denied(callback, kwargs):
        return
    await _leave_config_prompt(state)

    lang, chat_id, _ = _builder_args(callback, 2)
    if chat_id is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    # One mapping: `rule_chat_id` is itself a draft key, and spelling it twice
    # as keyword arguments is a TypeError on every tap.
    await state.update_data({**dict.fromkeys(_DRAFT_KEYS), "rule_chat_id": chat_id, "lang": lang})
    await show(callback, screens.type_screen(lang, chat_id))


@router.callback_query(F.data.startswith("ar_b_type:"))
async def handle_builder_type(
    callback: CallbackQuery,
    state: FSMContext,
    message_repo: FromDishka[MessageRepository],
    **kwargs: Any,
) -> None:
    """What sets the rule off. Words are typed; the rest is picked."""
    if await _denied(callback, kwargs):
        return
    lang, chat_id, rest = _builder_args(callback, 3)
    kind = rest[0] if rest else None
    rule_type = screens.KINDS.get(kind or "")
    data = await state.get_data()
    if chat_id is None or rule_type is None or data.get("rule_chat_id") != chat_id:
        await show_stale(callback, lang, data=kwargs, state=state)
        return

    if kind == "k":
        await state.set_state(AdminStates.awaiting_rule_words)
        await state.update_data(rule_type=rule_type, lang=lang)
        sent = await show(callback, screens.words_screen(lang, chat_id))
        await state.update_data(rule_prompt_id=sent.message_id if sent else None)
        return

    await _leave_config_prompt(state, keep_draft=True)
    await state.update_data(rule_type=rule_type, lang=lang)
    if kind == "u":
        page = _int(rest[1] if len(rest) > 1 else None) or 0
        await show(callback, await _who(state, message_repo, lang, chat_id, page))
        return
    await show(callback, screens.count_screen(lang, chat_id, kind or ""))


async def _who(
    state: FSMContext, message_repo: MessageRepository, lang: str, chat_id: int, page: int
) -> Screen:
    """The picker of the chat's most active people, remembering the names it shows.

    Without «🔍 Найти в Telegram» once this admin's app sent its label back as
    text (``rule_no_find``): there the button opens nothing.
    """
    people, total = await message_repo.get_top_active_users(chat_id, page, screens.WHO_PER_PAGE)
    clamped, pages, _ = page_slice(total, page, screens.WHO_PER_PAGE)
    if clamped != page:
        people, total = await message_repo.get_top_active_users(
            chat_id, clamped, screens.WHO_PER_PAGE
        )
        clamped, pages, _ = page_slice(total, clamped, screens.WHO_PER_PAGE)
    persons = [
        screens.Person(int(p["user_id"]), p.get("first_name"), p.get("username")) for p in people
    ]
    no_find = (await state.get_data()).get("rule_no_find") is True
    await state.update_data(rule_people={str(p.user_id): p.display for p in persons})
    return screens.who_screen(lang, chat_id, persons, page=clamped, pages=pages, find=not no_find)


@router.callback_query(F.data.startswith("ar_b_match:"))
async def handle_builder_match(
    callback: CallbackQuery,
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """The word anywhere in a message (even inside a longer word), or only the word."""
    if await _denied(callback, kwargs):
        return
    await _leave_config_prompt(state, keep_draft=True)
    lang, chat_id, rest = _builder_args(callback, 3)
    match = {"c": "contains", "e": "exact"}.get(rest[0] if rest else "")
    draft = await _draft(state, chat_id) if chat_id is not None else None
    if (
        chat_id is None
        or match is None
        or draft is None
        or draft.get("rule_type") != "keyword_trigger"
        or not isinstance(draft.get("rule_words"), list)
    ):
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    await state.update_data(rule_match=match)
    await show(callback, screens.action_screen(lang, chat_id, str(draft.get("rule_name") or "")))


@router.callback_query(F.data.startswith("ar_b_who:"))
async def handle_builder_who(
    callback: CallbackQuery,
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Whose messages: one person from the chat's most active ones.

    A name tapped while the person search is still open (Telegram kept the
    picker's buttons, ``rule_who_keyboard_kept``) ends the search the way a
    pick in it does: the keyboard goes on its own short line, and the next
    step comes under it. Read off the flag, not the state, which a tap on
    another screen may already have dropped while the keyboard stayed up.
    """
    if await _denied(callback, kwargs):
        return
    searching = isinstance((await state.get_data()).get("rule_finding"), int)
    await _leave_config_prompt(state, keep_draft=True)
    lang, chat_id, rest = _builder_args(callback, 3)
    user_id = _int(rest[0] if rest else None)
    draft = await _draft(state, chat_id) if chat_id is not None else None
    if (
        chat_id is None
        or user_id is None
        or draft is None
        or draft.get("rule_type") != "user_specific"
    ):
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    people = draft.get("rule_people")
    shown = people.get(str(user_id)) if isinstance(people, dict) else None
    name = _cap_name(shown if isinstance(shown, str) and shown else str(user_id))
    await state.update_data(rule_user=user_id, rule_name=name, rule_finding=None)
    if searching and isinstance(callback.message, Message):
        await callback.answer()
        await _leave_find(callback.message, screens.WHO_PICKED(lang).format(name=name))
        await show(
            callback, screens.action_screen(lang, chat_id, name), answered=True, new_message=True
        )
        await _retire(callback.message)
        return
    await show(callback, screens.action_screen(lang, chat_id, name))


@router.callback_query(F.data.startswith("ar_b_find:"))
async def handle_builder_find(
    callback: CallbackQuery,
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Whose messages, found with Telegram's person search: anyone, not only who wrote.

    A new message, since a reply keyboard can only be sent. The picker above
    loses its buttons, so the search's «✖️ Отмена» is the one way out.
    """
    if await _denied(callback, kwargs):
        return
    await _leave_config_prompt(state, keep_draft=True)
    lang, chat_id, _ = _builder_args(callback, 2)
    draft = await _draft(state, chat_id) if chat_id is not None else None
    if chat_id is None or draft is None or draft.get("rule_type") != "user_specific":
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    await state.update_data(rule_finding=chat_id, lang=lang)
    await state.set_state(AdminStates.awaiting_rule_who)
    await show(callback, screens.who_find_screen(lang))
    if isinstance(callback.message, Message):
        await _retire(callback.message)


async def _retire(picker: Message) -> None:
    """Take the inline picker's buttons away: the search is the live screen now, or is over."""
    try:
        await safe_edit_reply_markup(picker, reply_markup=None)
    except TelegramAPIError as exc:
        logger.info("rule_who_keyboard_kept", error=str(exc))


async def _finding_chat(state: FSMContext) -> int | None:
    """The chat of the open person search, or None when no search is open.

    Open means BOTH: the state is ``awaiting_rule_who`` and ``rule_finding``
    holds a chat id. The flag alone outlives a search that a tap on another
    screen ended (the prompt-exit middleware drops the state, not the data).
    """
    if await state.get_state() != AdminStates.awaiting_rule_who.state:
        return None
    finding = (await state.get_data()).get("rule_finding")
    return finding if isinstance(finding, int) and not isinstance(finding, bool) else None


class _Finding(Filter):
    """The rules person search is open (see `_finding_chat`)."""

    async def __call__(self, _event: Message, state: FSMContext | None = None) -> bool:
        return state is not None and await _finding_chat(state) is not None


_CANCEL_LABELS: Final = frozenset(bare_label(CANCEL(lang)) for lang in ("ru", "en"))
_FIND_LABELS: Final = frozenset(bare_label(screens.FIND(lang)) for lang in ("ru", "en"))


async def _leave_find(message: Message, line: str) -> None:
    """Take the search keyboard away on a short line of its own (a message
    carrying ``ReplyKeyboardRemove`` cannot carry inline buttons too)."""
    await message.answer(line, parse_mode=None, reply_markup=ReplyKeyboardRemove())


@router.message(
    F.users_shared,
    F.users_shared.request_id == REQUEST_IDS.RULE_WHO,
    F.chat.type == "private",
    IsAdmin(),
)
async def handle_builder_found(message: Message, state: FSMContext) -> None:
    """The person picked in Telegram's search: the rule's person, then what it does.

    Matched in any state by its own ``request_id``, ahead of everything that
    would read a message with no text. A pick from a search that is no longer
    open (the rule was finished, cancelled or restarted, or a tap on another
    screen ended the search) is answered as stale.
    """
    shared = message.users_shared
    data = await state.get_data()
    lang = lang_of(data.get("lang"))
    chat_id = await _finding_chat(state)
    if await state.get_state() == AdminStates.awaiting_rule_who.state:
        await state.set_state(None)
    draft = await _draft(state, chat_id) if chat_id is not None else None
    if (
        shared is None
        or not shared.users
        or draft is None
        or draft.get("rule_type") != "user_specific"
        or chat_id is None
    ):
        logger.info("rule_who_pick_stale", has_search=chat_id is not None)
        await state.update_data(rule_finding=None)
        await _leave_find(message, screens.WHO_STALE(lang))
        return
    person = shared.users[0]
    name = _cap_name(screens.Person(person.user_id, person.first_name, person.username).display)
    await state.update_data(rule_user=person.user_id, rule_name=name, rule_finding=None)
    await _leave_find(message, screens.WHO_PICKED(lang).format(name=name))
    await show(message, screens.action_screen(lang, chat_id, name))


@router.message(
    _Finding(),
    F.chat.type == "private",
    F.forward_origin.is_(None),
    F.text.func(lambda text: bare_label(text) in _CANCEL_LABELS),
    IsAdmin(),
)
async def handle_builder_find_cancel(
    message: Message,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
) -> None:
    """«✖️ Отмена» under the person search: no rule, the keyboard goes, the chat's rules."""
    data = await state.get_data()
    lang = lang_of(data.get("lang"))
    chat_id = data.get("rule_finding")
    await state.set_state(None)
    await state.update_data(**dict.fromkeys(_DRAFT_KEYS))
    await _leave_find(message, screens.WHO_CANCELLED(lang))
    if isinstance(chat_id, int):
        await show(
            message,
            await _list(
                lang,
                chat_id,
                0,
                rules_repo=rules_repo,
                chat_config_service=chat_config_service,
                chat_settings_repo=chat_settings_repo,
            ),
        )


@router.message(
    _Finding(),
    F.chat.type == "private",
    F.forward_origin.is_(None),
    F.text.func(lambda text: bare_label(text) in _FIND_LABELS),
    IsAdmin(),
)
async def handle_builder_find_unsupported(
    message: Message,
    state: FSMContext,
    message_repo: FromDishka[MessageRepository],
) -> None:
    """«🔍 Найти в Telegram» came back as text: this app has no person search.

    Said plainly, the keyboard goes, and the chat's own people come back as
    the inline picker, without the button that opened nothing; its «Отмена»
    is the way back to the chat's rules.
    """
    data = await state.get_data()
    lang = lang_of(data.get("lang"))
    chat_id = await _finding_chat(state)
    await state.set_state(None)
    await state.update_data(rule_finding=None, rule_no_find=True)
    logger.info("rule_who_search_unsupported")
    await _leave_find(message, screens.WHO_UNSUPPORTED(lang))
    if chat_id is not None:
        await show(message, await _who(state, message_repo, lang, chat_id, 0))


@router.callback_query(F.data.startswith("ar_b_n:"))
async def handle_builder_count(
    callback: CallbackQuery,
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """How many stickers in the chat, or messages from one person, a minute."""
    if await _denied(callback, kwargs):
        return
    await _leave_config_prompt(state, keep_draft=True)
    lang, chat_id, rest = _builder_args(callback, 3)
    n = _int(rest[0] if rest else None)
    draft = await _draft(state, chat_id) if chat_id is not None else None
    rule_type = draft.get("rule_type") if draft else None
    choices, name = (
        (screens.FLOOD_CHOICES, FLOOD_NAME)
        if rule_type == "sticker_flood"
        else (screens.SPAM_CHOICES, SPAM_NAME)
    )
    if (
        chat_id is None
        or draft is None
        or rule_type not in ("sticker_flood", "spam_detect")
        or n not in choices
    ):
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    rule_name = name(lang).format(n=n)
    await state.update_data(rule_n=n, rule_name=rule_name)
    await show(callback, screens.action_screen(lang, chat_id, rule_name))


@router.callback_query(F.data.startswith("ar_b_act:"))
async def handle_builder_action(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """What the rule does. «Написать админу» saves it at once; the rest ask one more thing."""
    if await _denied(callback, kwargs):
        return
    lang, chat_id, rest = _builder_args(callback, 3)
    token = rest[0] if rest else ""
    draft = await _draft(state, chat_id) if chat_id is not None else None
    if (
        chat_id is None
        or draft is None
        or token not in (*screens.ACTIONS, "ri")
        or _config_for(draft) is None
    ):
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    name = str(draft.get("rule_name") or "")

    if token in ("w", "c"):
        action = screens.ACTIONS[token]
        await state.set_state(AdminStates.awaiting_rule_text)
        await state.update_data(rule_action=action, lang=lang)
        sent = await show(callback, screens.text_screen(lang, chat_id, name, action=action))
        await state.update_data(rule_prompt_id=sent.message_id if sent else None)
        return
    if token == "ri":
        await state.set_state(AdminStates.awaiting_rule_react)
        await state.update_data(rule_action=RuleAction.SET_REACTION.value, lang=lang)
        sent = await show(callback, screens.react_in_screen(lang, chat_id, name))
        await state.update_data(rule_prompt_id=sent.message_id if sent else None)
        return

    if token == "r":
        await _leave_config_prompt(state, keep_draft=True)
        await show(callback, screens.react_screen(lang, chat_id, name))
        return
    await _leave_config_prompt(state)
    await state.update_data(**dict.fromkeys(_DRAFT_KEYS))
    await _create(
        callback,
        lang,
        chat_id,
        draft,
        RuleAction.NOTIFY_ADMIN.value,
        {},
        rules_repo=rules_repo,
        chat_config_service=chat_config_service,
        chat_settings_repo=chat_settings_repo,
    )


@router.callback_query(F.data.startswith("ar_b_txt:"))
async def handle_builder_preset_text(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """A ready text: the rule is saved, as ``warn_user`` with that text.

    Stored as a warning whichever screen offered it: a ready text has no name
    placeholders, and ``warn_user`` is what such a rule always was.
    """
    if await _denied(callback, kwargs):
        return
    lang, chat_id, rest = _builder_args(callback, 3)
    i = _int(rest[0] if rest else None)
    draft = await _draft(state, chat_id) if chat_id is not None else None
    await _leave_config_prompt(state)
    if (
        chat_id is None
        or draft is None
        or i is None
        or not 0 <= i < len(screens.WARN_PRESETS)
        # «Ответить автору» (a reply draft) offers the ready texts too, and the
        # retired ⚠️ choice (a warning draft) may still be on an old keyboard.
        or draft.get("rule_action")
        not in (RuleAction.WARN_USER.value, RuleAction.CUSTOM_RESPONSE.value)
        or _config_for(draft) is None
    ):
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    await state.update_data(**dict.fromkeys(_DRAFT_KEYS))
    await _create(
        callback,
        lang,
        chat_id,
        draft,
        RuleAction.WARN_USER.value,
        {"warning_message": screens.WARN_PRESETS[i](lang)},
        rules_repo=rules_repo,
        chat_config_service=chat_config_service,
        chat_settings_repo=chat_settings_repo,
    )


@router.callback_query(F.data.startswith("ar_b_rx:"))
async def handle_builder_preset_reaction(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """A ready reaction: the rule is saved."""
    if await _denied(callback, kwargs):
        return
    lang, chat_id, rest = _builder_args(callback, 3)
    i = _int(rest[0] if rest else None)
    draft = await _draft(state, chat_id) if chat_id is not None else None
    await _leave_config_prompt(state)
    if (
        chat_id is None
        or draft is None
        or i is None
        or not 0 <= i < len(screens.REACTIONS)
        or _config_for(draft) is None
    ):
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    await state.update_data(**dict.fromkeys(_DRAFT_KEYS))
    await _create(
        callback,
        lang,
        chat_id,
        draft,
        RuleAction.SET_REACTION.value,
        {"emoji": screens.REACTIONS[i]},
        rules_repo=rules_repo,
        chat_config_service=chat_config_service,
        chat_settings_repo=chat_settings_repo,
    )


# ---------------------------------------------------------------------------
# The JSON prompt
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("ar_cfg:"))
async def handle_rule_cfg(
    callback: CallbackQuery,
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """The whole rule as JSON, for what the buttons do not cover."""
    if await _denied(callback, kwargs):
        return
    lang, chat_id, _ = _builder_args(callback, 2)
    if chat_id is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    await state.set_state(AdminStates.awaiting_rule_config)
    await _cfg_prompt(callback, state, lang, chat_id, None)


async def _cfg_prompt(
    callback: CallbackQuery, state: FSMContext, lang: str, chat_id: int, rule_type: str | None
) -> None:
    """The JSON prompt's screen and draft; the caller has armed its state."""
    await state.update_data(rule_chat_id=chat_id, rule_type=rule_type, lang=lang)
    sent = await show(callback, screens.cfg_screen(lang, chat_id, rule_type))
    await state.update_data(rule_prompt_id=sent.message_id if sent else None)


@router.callback_query(F.data.startswith("ar_type:"))
async def handle_type_selected(
    callback: CallbackQuery,
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """An old type button: the JSON prompt for its type."""
    if await _denied(callback, kwargs):
        return
    lang, chat_id, rest = _builder_args(callback, 3)
    rule_type = rest[0] if rest else ""
    if chat_id is None or rule_type not in _VALID_RULE_TYPES:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    await state.set_state(AdminStates.awaiting_rule_config)
    await _cfg_prompt(callback, state, lang, chat_id, rule_type)


@router.callback_query(F.data.startswith("ar_cancel:"))
async def handle_rule_config_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """An old Cancel under the JSON prompt: leave it without creating anything.

    Gated exactly like every other callback in this file: a cancel screen
    re-renders a chat's rule list, so it is not "harmless navigation".
    """
    if await _denied(callback, kwargs):
        return
    # NOT a bare `state.clear()`. These cancel buttons ride on standalone reply
    # messages (the invalid-input re-prompts) that nothing ever edits, so
    # they stay tappable indefinitely — an admin who later opens a DIFFERENT
    # dialog and scrolls up to an old «✖️ Отмена» would otherwise have that
    # dialog silently wiped, state and data. Same rule the sibling helper
    # states: a rules screen has no business ending someone else's dialog.
    await _leave_config_prompt(state)
    lang, chat_id, _ = _builder_args(callback, 2)
    if chat_id is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    await show(
        callback,
        await _list(
            lang,
            chat_id,
            0,
            rules_repo=rules_repo,
            chat_config_service=chat_config_service,
            chat_settings_repo=chat_settings_repo,
        ),
    )


# ---------------------------------------------------------------------------
# Typed answers
# ---------------------------------------------------------------------------
#
# Four filters on each, and each one closes a hole the body could not (TD-049):
#
# ``IsAdmin()`` -- these handlers write a rule for whatever ``chat_id`` the FSM
# data carries. The FSM key binds the state to one user, which is exactly the
# TOCTOU the security register records as S-11: it binds it to the user who
# *was* an admin when the dialog opened.
#
# ``F.chat.type == "private"`` -- the dialogs are only ever opened from a DM
# callback, so this costs nothing and stops a state from ever applying anywhere
# else.
#
# The two slash guards are one guard. aiogram resolves a command from
# ``message.text or message.caption``, so ``/help`` sent as a photo caption is a
# real command -- and ``~F.text.startswith("/")`` returns True for it, because
# ``.text`` is None. Measured: with the text-only guard, a captioned ``/help``
# was still eaten by the JSON handler and answered "Невалидный JSON". The same
# shape is why the escape hatches live on the keyboard and on ``/cancel`` rather
# than in a handler's body: a filtered-out update is one a handler never sees.


def _typed(message: Message) -> str:
    return (message.text or message.caption or "").strip()


async def _reject(
    message: Message,
    state: FSMContext,
    screen_id: str,
    text: str,
    cancel_cb: str,
    lang: str,
) -> None:
    """A typed answer that cannot be used: say why, keep the prompt armed.

    The error carries the one live Cancel; the prompt above it loses its own.
    """
    data = await state.get_data()
    sent = await show(message, screens.error_screen(screen_id, text, cancel_cb, lang))
    await _retire_prompt(message, data.get("rule_prompt_id"))
    await state.update_data(rule_prompt_id=sent.message_id if sent else None)


def _split_words(text: str) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for chunk in text.replace("\n", ",").split(","):
        word = chunk.strip()
        if word and word.casefold() not in seen:
            seen.add(word.casefold())
            words.append(word)
    return words


def _cap_name(name: str) -> str:
    """A rule's name as stored: `screens.cap_name`, the cut its card and alert show."""
    return screens.cap_name(name)


def _words_name(words: list[str]) -> str:
    return _cap_name(", ".join(words))


def _too_long(text: str, *, template: bool, rule_name: str, lang: str) -> str | None:
    """Why ``text`` cannot be a rule's warning or reply, or None when it fits.

    Measured as Telegram counts the message it becomes: a reply with the
    longest name Telegram allows put in, a warning as it is.
    """
    plain = sent_length(text)
    worst = worst_length(text, rule_name=rule_name) if template else plain
    if worst <= TELEGRAM_MESSAGE_LIMIT:
        return None
    return screens.text_too_long(lang, worst - TELEGRAM_MESSAGE_LIMIT, filled=worst > plain)


@router.message(
    AdminStates.awaiting_rule_words,
    F.chat.type == "private",
    IsAdmin(),
    ~F.text.startswith("/"),
    ~F.caption.startswith("/"),
)
async def handle_rule_words_input(message: Message, state: FSMContext) -> None:
    """The words, typed: then how to match them."""
    data = await state.get_data()
    lang = lang_of(data.get("lang"))
    chat_id = data.get("rule_chat_id")
    if not isinstance(chat_id, int):
        logger.warning("rule_prompt_without_chat", state=await state.get_state())
        await state.clear()
        return
    cancel_cb = cb("ar_list:", lang, chat_id, 0)
    words = _split_words(_typed(message))
    if not words:
        await _reject(message, state, "rule.words", screens.WORDS_NONE(lang), cancel_cb, lang)
        return
    if len(words) > screens.MAX_WORDS:
        text = screens.WORDS_TOO_MANY(lang).format(n=screens.MAX_WORDS)
        await _reject(message, state, "rule.words", text, cancel_cb, lang)
        return
    long = next((w for w in words if len(w) > screens.MAX_WORD_CHARS), None)
    if long is not None:
        shown = long if len(long) <= _SHOWN_WORD_CHARS else long[:_SHOWN_WORD_CHARS] + "…"
        text = screens.WORDS_TOO_LONG(lang).format(n=screens.MAX_WORD_CHARS, w=f"«{escape(shown)}»")
        await _reject(message, state, "rule.words", text, cancel_cb, lang)
        return

    await state.set_state(None)
    await state.update_data(rule_words=words, rule_name=_words_name(words))
    await show(message, screens.match_screen(lang, chat_id, words))
    await _retire_prompt(message, data.get("rule_prompt_id"))
    await state.update_data(rule_prompt_id=None)


@router.message(
    AdminStates.awaiting_rule_text,
    F.chat.type == "private",
    IsAdmin(),
    ~F.text.startswith("/"),
    ~F.caption.startswith("/"),
)
async def handle_rule_text_input(
    message: Message,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
) -> None:
    """The warning or the reply, typed: the rule is saved."""
    data = await state.get_data()
    lang = lang_of(data.get("lang"))
    chat_id = data.get("rule_chat_id")
    action = data.get("rule_action")
    if not isinstance(chat_id, int) or action not in (
        RuleAction.WARN_USER.value,
        RuleAction.CUSTOM_RESPONSE.value,
    ):
        logger.warning("rule_prompt_without_chat", state=await state.get_state())
        await state.clear()
        return
    cancel_cb = cb("ar_list:", lang, chat_id, 0)
    text = _typed(message)
    if not text:
        await _reject(message, state, "rule.text", screens.TEXT_EMPTY(lang), cancel_cb, lang)
        return
    template = action == RuleAction.CUSTOM_RESPONSE.value
    error = _too_long(
        text, template=template, rule_name=str(data.get("rule_name") or ""), lang=lang
    )
    if error is not None:
        await _reject(message, state, "rule.text", error, cancel_cb, lang)
        return
    key = "response_template" if template else "warning_message"
    await _finish_typed(
        message,
        state,
        data,
        lang,
        chat_id,
        str(action),
        {key: text},
        rules_repo=rules_repo,
        chat_config_service=chat_config_service,
        chat_settings_repo=chat_settings_repo,
    )


@router.message(
    AdminStates.awaiting_rule_react,
    F.chat.type == "private",
    IsAdmin(),
    ~F.text.startswith("/"),
    ~F.caption.startswith("/"),
)
async def handle_rule_react_input(
    message: Message,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
) -> None:
    """One emoji, typed or as a sticker's; only Telegram's reaction set is accepted.

    A sticker counts as the emoji it stands for (``sticker.emoji``), checked
    exactly like a typed one.
    """
    data = await state.get_data()
    lang = lang_of(data.get("lang"))
    chat_id = data.get("rule_chat_id")
    if not isinstance(chat_id, int):
        logger.warning("rule_prompt_without_chat", state=await state.get_state())
        await state.clear()
        return
    raw = _typed(message)
    sticker_emoji = getattr(getattr(message, "sticker", None), "emoji", None)
    if not raw and isinstance(sticker_emoji, str):
        raw = sticker_emoji.strip()
    emoji = ReactionSelector.select(raw or None)
    if emoji is None:
        cancel_cb = cb("ar_b_act:", lang, chat_id, "r")
        await _reject(message, state, "rule.react_in", screens.EMOJI_BAD(lang), cancel_cb, lang)
        return
    await _finish_typed(
        message,
        state,
        data,
        lang,
        chat_id,
        RuleAction.SET_REACTION.value,
        {"emoji": emoji},
        rules_repo=rules_repo,
        chat_config_service=chat_config_service,
        chat_settings_repo=chat_settings_repo,
    )


async def _finish_typed(
    message: Message,
    state: FSMContext,
    data: Mapping[str, Any],
    lang: str,
    chat_id: int,
    action: str,
    extra: Mapping[str, Any],
    *,
    rules_repo: RulesRepository,
    chat_config_service: ChatConfigService,
    chat_settings_repo: ChatSettingsRepository,
) -> None:
    """Save the rule from a typed last answer; the prompt above loses its Cancel.

    The draft is claimed (state and data cleared) before the write, so a second
    copy of the answer finds no draft and cannot make a second rule. A draft
    with a step missing makes no rule and says so over the chat's list.
    """
    await state.clear()
    if _config_for(data) is None:
        logger.warning("rule_draft_incomplete", chat_id=chat_id, rule_type=data.get("rule_type"))
        await show(
            message,
            await _list(
                lang,
                chat_id,
                0,
                rules_repo=rules_repo,
                chat_config_service=chat_config_service,
                chat_settings_repo=chat_settings_repo,
                notice_raw=NOT_CREATED(lang),
            ),
        )
        await _retire_prompt(message, data.get("rule_prompt_id"))
        return
    await _create(
        message,
        lang,
        chat_id,
        data,
        action,
        extra,
        rules_repo=rules_repo,
        chat_config_service=chat_config_service,
        chat_settings_repo=chat_settings_repo,
    )
    await _retire_prompt(message, data.get("rule_prompt_id"))


@router.message(
    AdminStates.awaiting_rule_config,
    F.chat.type == "private",
    IsAdmin(),
    ~F.text.startswith("/"),
    ~F.caption.startswith("/"),
)
async def handle_rule_config_input(
    message: Message,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    chat_config_service: FromDishka[ChatConfigService],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
) -> None:
    """Receive a rule as JSON and create it.

    The type comes from the JSON's ``type`` field, else from the old type
    button that opened the prompt, else it is keywords.
    """
    data = await state.get_data()
    lang = lang_of(data.get("lang"))
    chat_id = data.get("rule_chat_id")
    if not isinstance(chat_id, int):
        logger.warning("rule_prompt_without_chat", state=await state.get_state())
        await state.clear()
        return
    cancel_cb = cb("ar_list:", lang, chat_id, 0)

    try:
        # A file or photo carries its words as a caption: the JSON may be there.
        config = json.loads(message.text or message.caption or "")
        if not isinstance(config, dict):
            raise ValueError  # noqa: TRY301
    except ValueError:
        await _reject(message, state, "rule.cfg_bad", screens.CFG_NOT_JSON(lang), cancel_cb, lang)
        return

    stated = config.pop("type", None)
    rule_type = stated if stated is not None else data.get("rule_type") or "keyword_trigger"
    # A list or an object is unhashable: membership in the frozenset would
    # raise TypeError, and the admin would see nothing at all.
    if not isinstance(rule_type, str) or rule_type not in _VALID_RULE_TYPES:
        error = screens.CFG_BAD_TYPE(lang).format(
            t=_quoted(rule_type),
            valid=", ".join(f"<code>{t}</code>" for t in screens.KINDS.values()),
        )
        await _reject(message, state, "rule.cfg_bad", error, cancel_cb, lang)
        return
    if rule_type == "keyword_trigger" and not _has_keywords(config):
        await _reject(
            message, state, "rule.cfg_bad", screens.CFG_NO_KEYWORDS(lang), cancel_cb, lang
        )
        return

    # Validate what the engine would otherwise drop silently at evaluation
    # time: an unknown action, or a set_reaction emoji outside Telegram's
    # reaction set, both yield "Rule created" and a permanent no-op whose
    # only trace is a server-side log line.
    action = config.get("action")
    if action is not None and (not isinstance(action, str) or action not in _VALID_RULE_ACTIONS):
        error = screens.CFG_BAD_ACTION(lang).format(
            action=_quoted(action),
            valid=", ".join(f"<code>{a}</code>" for a in RuleAction),
        )
        await _reject(message, state, "rule.cfg_bad", error, cancel_cb, lang)
        return
    if action == RuleAction.SET_REACTION:
        emoji = config.get("emoji")
        if not isinstance(emoji, str) or ReactionSelector.select(emoji) is None:
            await _reject(
                message, state, "rule.cfg_bad", screens.CFG_BAD_EMOJI(lang), cancel_cb, lang
            )
            return
    text_error = _cfg_text_error(config, lang)
    if text_error is not None:
        await _reject(message, state, "rule.cfg_bad", text_error, cancel_cb, lang)
        return
    # Checked here, before the prompt is claimed below: asyncpg refuses a weight
    # or "mandatory" of the wrong type at the INSERT, after `state.clear()`, and
    # the admin got no answer and no prompt to try again in.
    _whole_floats_to_int(str(rule_type), config)
    field_error = _cfg_field_error(str(rule_type), config, lang) or _cfg_column_error(config, lang)
    if field_error is not None:
        logger.info("rule_cfg_refused", chat_id=chat_id, rule_type=rule_type)
        await _reject(message, state, "rule.cfg_bad", field_error, cancel_cb, lang)
        return
    if "name" in config:
        config["name"] = _cap_name(config["name"])

    weight = config.pop("weight", 1)
    mandatory = config.pop("mandatory", False)
    # Claimed before the write, like the builder: a second copy of this
    # message finds no draft (`rule_chat_id` is gone) and makes no second rule.
    await state.clear()
    rule_id = await rules_repo.create(
        chat_id=chat_id,
        rule_type=str(rule_type),
        config=config,
        weight=weight,
        mandatory=mandatory,
    )
    logger.info("rule_created", chat_id=chat_id, rule_id=rule_id, rule_type=rule_type, via="json")
    rule = screens.RuleView(
        id=int(rule_id),
        chat_id=chat_id,
        rule_type=str(rule_type),
        config=config,
        weight=weight,
        mandatory=mandatory,
    )
    screen = await _card(
        lang,
        rule,
        chat_config_service=chat_config_service,
        chat_settings_repo=chat_settings_repo,
        notice=CREATED(lang),
    )
    await show(message, screen)
    await _retire_prompt(message, data.get("rule_prompt_id"))


def _quoted(value: object) -> str:
    """A JSON value quoted back in an error: as written, cut to one line, escaped."""
    shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(shown) > _SHOWN_WORD_CHARS:
        shown = shown[:_SHOWN_WORD_CHARS] + "…"
    return escape(shown)


_RULE_ID_WORST: Final = f"Rule #{2**63 - 1}"
"""What a reply's ``{rule_name}`` becomes for a rule with no name, at its longest."""


def _has_keywords(config: Mapping[str, Any]) -> bool:
    """A non-empty list of words that are text and not blank: what `_eval_keyword`
    needs to fire at all, and never on everything (``""`` is in every text)."""
    words = config.get("keywords")
    return (
        isinstance(words, list)
        and bool(words)
        and all(isinstance(w, str) and w.strip() for w in words)
    )


def _cfg_text_error(config: Mapping[str, Any], lang: str) -> str | None:
    """Why the JSON's name, warning or reply cannot be used, or None.

    Each must be text; a warning or reply must also fit one Telegram message
    once sent (`_too_long`), the reply with its ``{rule_name}`` as it will read.
    """
    for key in ("name", "warning_message", "response_template"):
        if key in config and not isinstance(config[key], str):
            return screens.CFG_NOT_TEXT(lang).format(key=key)
    name = config.get("name")
    rule_name = _cap_name(name) if isinstance(name, str) else _RULE_ID_WORST
    for key, template in (("warning_message", False), ("response_template", True)):
        text = config.get(key)
        if isinstance(text, str):
            error = _too_long(text, template=template, rule_name=rule_name, lang=lang)
            if error is not None:
                return error
    return None


_NUMBER_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "sticker_flood": ("max_stickers_per_interval", "interval_seconds"),
    "spam_detect": ("max_messages", "time_window_seconds", "max_chars_per_message"),
}
"""The numbers the engine compares counts with, by rule type."""
_ID_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "keyword_trigger": ("target_users", "exclude_users"),
    "user_specific": ("user_ids",),
}
"""The lists the engine looks a sender's id up in, by rule type."""


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _whole_floats_to_int(rule_type: str, config: dict[str, Any]) -> None:
    """``60.0`` in a number field is 60: JSON does not tell the two apart, the admin neither.

    The engine read such a value before the guard existed, so refusing it would refuse a
    rule that worked. A fractional value stays refused: the card shows whole numbers only.
    """
    for key in _NUMBER_FIELDS.get(rule_type, ()):
        value = config.get(key)
        if isinstance(value, float) and value.is_integer():
            config[key] = int(value)


def _cfg_field_error(rule_type: str, config: Mapping[str, Any], lang: str) -> str | None:
    """Why a field the engine reads for ``rule_type`` cannot be read, or None.

    The engine takes these as written. A number typed as text raises in its
    comparison on every message, and per-rule error handling turns that into
    a log line; an id written as text is never equal to a sender's id; an
    unknown ``match_type`` matches nothing. Each is «Правило создано» and a
    rule that never fires.
    """
    for key in _NUMBER_FIELDS.get(rule_type, ()):
        if key in config and not _whole(config[key]):
            return screens.CFG_NOT_NUMBER(lang).format(key=key)
    for key in _ID_FIELDS.get(rule_type, ()):
        ids = config.get(key)
        if key in config and not (isinstance(ids, list) and all(_whole(i) for i in ids)):
            return screens.CFG_NOT_IDS(lang).format(key=key)
    if rule_type == "keyword_trigger":
        match = config.get("match_type", "contains")
        if not isinstance(match, str) or match not in screens.MATCH_TYPES:
            return screens.CFG_BAD_MATCH(lang).format(
                m=_quoted(match),
                valid=", ".join(f"<code>{m}</code>" for m in screens.MATCH_TYPES),
            )
        if "case_sensitive" in config and not isinstance(config["case_sensitive"], bool):
            return screens.CFG_NOT_BOOL(lang).format(key="case_sensitive")
    if rule_type == "user_specific" and "conditions" in config:
        conditions = config["conditions"]
        if not isinstance(conditions, dict):
            return screens.CFG_BAD_CONDITIONS(lang)
        words = conditions.get("keywords")
        if "keywords" in conditions and not (
            isinstance(words, list) and all(isinstance(w, str) for w in words)
        ):
            return screens.CFG_NOT_WORDS(lang).format(key="conditions.keywords")
    return None


def _cfg_column_error(config: Mapping[str, Any], lang: str) -> str | None:
    """Why ``weight`` or ``mandatory`` cannot be stored as typed, or None.

    They are columns, not config: asyncpg binds them to an ``INT`` and a
    ``BOOLEAN`` and refuses ``"5"``, ``1`` for true, or a weight past int32. A
    weight below 1 would be stored, and a weight mode cannot choose among rules
    that weigh nothing.
    """
    if "weight" in config and not is_weight(config["weight"]):
        return screens.CFG_BAD_WEIGHT(lang).format(priority=screens.PRIORITY(lang), max=MAX_WEIGHT)
    if "mandatory" in config and not isinstance(config["mandatory"], bool):
        return screens.CFG_NOT_BOOL(lang).format(key="mandatory")
    return None
