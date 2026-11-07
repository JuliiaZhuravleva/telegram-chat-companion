"""The chat panel: the chat list, one chat's screen, its sections, and their writes.

Handles:
- ``adm_pnl:``        — the chat list, most active first, with the requests
  row on top when there are any (``show_chats``)
- ``adm_pnl_menu:``   — one chat's root screen (``show_chat``)
- ``adm_pnl_grp:``    — one section of it; which setting lives where is
  ``screens.sections.SECTION_OF``. ``rules`` opens the rules area's own list,
  and the old group tokens (``modules``, ``kb``, ``rules``) map through
  ``sections.LEGACY_GROUPS``, so a button drawn before the redesign still lands.
- ``adm_pnl_tgl:``    — flip one bool setting and re-render its section with a
  notice. Every bool field goes through here, the knowledge base and both
  reaction switches included: their separate pickers are gone, so this is
  their only writer in the panel (the ADR-0010 superseding note in
  ``docs/admin-dm-internals.md``). A community sub-toggle while the mode is off,
  or the reactions history switch while reactions are off, changes nothing and
  says so: those buttons are hidden then, so a tap on one is an old screen.
- ``adm_pnl_tol:`` / ``adm_pnl_tolset:`` / ``adm_pnl_tolcancel:`` — sticker
  explicitness: five presets that save on a tap, and «Своё число», which asks
  for a number (``AdminStates.awaiting_setting_value``). Reject-not-clamp: a
  number outside 0..1 asks again (ADR-0008 Decision 10). Any other panel tap,
  and the `show_chat` / `show_chats` entries, close that prompt
  (`_close_number_prompt`), so text typed later is not read as a value.
- ``adm_pnl_sp:`` / ``adm_pnl_spok:`` / ``adm_pnl_spre:`` / ``adm_pnl_spx:`` —
  the per-chat instruction (``system_prompt``) editor: show the current one in
  an expandable quote, take a new one as text (a paste Telegram splits into
  several messages is reassembled), preview it, write only on «Save».
- ``/panel <query>``  — open a chat by link, id or part of its title. A
  ``Command`` and not a bare-text handler: this DM has FSM text inputs, and
  ``Command("panel")`` only ever matches ``/panel``.

The screens are pure builders in ``src/bot/screens/chat.py`` over a
`ChatView`; this module reads the repositories, writes, and calls them. A
payload a handler cannot parse is a stale button and gets `show_stale` — the
panel as it is now, in the admin's language — never an English alert.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections import defaultdict
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, Final

import structlog
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.filters.admin import IsAdmin
from src.bot.handlers.rules import show_rules
from src.bot.screens import chat as screens
from src.bot.screens.chat import ChatView, config_values
from src.bot.screens.deps import resolve, state_of
from src.bot.screens.i18n import Tr
from src.bot.screens.model import Screen
from src.bot.screens.render import page_slice, render_text, show, to_markup
from src.bot.screens.sections import SECTION_OF, Section, section_of_token
from src.bot.screens.stale import show_stale
from src.bot.settings_fields import FieldType, field_by_code
from src.bot.states.admin import AdminStates, note_screen
from src.bot.utils import check_admin_direct, is_bot_chat_admin
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.rules import RulesRepository
from src.services.chat_config import ChatConfigService
from src.utils import parse_user_id_list
from src.utils.telegram import ChatReference, parse_chat_reference
from src.utils.telegram_markdown import entities_to_markdown
from src.utils.telegram_text import split_html

logger = structlog.get_logger(__name__)

router = Router(name="admin_chat_panel")

_NOT_ADMIN = {"ru": "Нет доступа.", "en": "Access denied."}
_CHAT_GONE: Final = Tr(
    "<i>Этого чата нет в настройках, ничего не изменилось. Вот подключённые чаты.</i>",
    "<i>This chat is not in the settings and nothing changed. Here are the connected chats.</i>",
)

_LIVE_DEFAULTS: Final[dict[str, bool]] = {
    "kb_enabled": False,
    "reactions_enabled": False,
    "reactions_history_enabled": True,
}
"""The three switches whose shown value is read raw + global default (`_fresh_effective`),
with the value the bot assumes when neither is set."""


def _get_lang(raw: str | None) -> str:
    return raw if raw in ("ru", "en") else "ru"


def _is_private(callback: CallbackQuery) -> bool:
    return isinstance(callback.message, Message) and callback.message.chat.type == "private"


def _lang_of(callback: CallbackQuery) -> str:
    parts = (callback.data or "").split(":")
    return _get_lang(parts[1] if len(parts) > 1 else None)


def _parse_lang_chat(callback: CallbackQuery) -> tuple[str, int | None]:
    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        return lang, int(parts[2])
    except (ValueError, IndexError):
        return lang, None


async def _guard(callback: CallbackQuery, bot_config_repo: BotConfigRepository) -> bool:
    """A tap from a bot admin in the DM; anything else is answered and refused here."""
    if not _is_private(callback):
        await callback.answer()
        return False
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(_NOT_ADMIN["en"], show_alert=True)
        return False
    return True


async def _fresh_effective(
    row: Mapping[str, Any] | None,
    bot_config_repo: BotConfigRepository,
    key: str,
    fallback: bool,
) -> bool:
    """Effective bool for the KB and reactions switches, read raw + global default.

    The same rule admin_kb.py's ``_effective_kb_enabled`` uses, so the memory
    section and the KB area never disagree about whether the knowledge base is
    on. Not a cache workaround: ``ChatConfigService`` is ``Scope.REQUEST``
    (``src/di.py``), so nothing it caches outlives one update anyway (TD-046).
    """
    raw = row.get(key) if row else None
    if raw is not None:
        return bool(raw)
    default = await bot_config_repo.get(f"default_{key}")
    return bool(default) if default is not None else fallback


async def _load_view(
    chat_id: int,
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    chat_config_service: ChatConfigService,
    *,
    rules_repo: RulesRepository | None = None,
    bot_is_admin: bool | None = None,
) -> ChatView | None:
    """Everything a chat screen shows, or None when the chat has no settings row."""
    row = await chat_settings_repo.get(chat_id)
    if row is None:
        return None
    config = await chat_config_service.get_config(chat_id)
    live = {
        key: await _fresh_effective(row, bot_config_repo, key, fallback)
        for key, fallback in _LIVE_DEFAULTS.items()
    }
    return ChatView(
        chat_id=chat_id,
        title=row.get("chat_title"),
        chat_type=row.get("chat_type"),
        values=config_values(config, **live),
        row=row,
        rules=await rules_repo.count_for_chat(chat_id) if rules_repo is not None else 0,
        organizers=len(parse_user_id_list(row.get("kb_organizer_ids"))),
        bot_is_admin=bot_is_admin,
    )


async def _resolve_bot_id(callback: CallbackQuery, bot_id_hint: int | None) -> int | None:
    """The bot's own id: the ``dp["bot_id"]`` singleton, else ``bot.me()`` (cached by aiogram)."""
    if bot_id_hint is not None:
        return bot_id_hint
    if callback.bot is not None:
        return (await callback.bot.me()).id
    return None


async def _bot_is_admin(
    callback: CallbackQuery, chat_id: int, bot_id_hint: int | None
) -> bool | None:
    """Live answer: is the bot an admin of the chat (ADR-0004 Decision 5 — never cached).

    Without admin rights ``message_reaction`` updates simply stop arriving, with
    no error anywhere, so the reactions section asks Telegram each time it is
    drawn. None when there is no bot to ask (a test double).
    """
    bot_id = await _resolve_bot_id(callback, bot_id_hint)
    if bot_id is None or callback.bot is None:
        return None
    return await is_bot_chat_admin(callback.bot, chat_id, bot_id)


def _with_notice(
    screen: Screen, *, notice: str | None = None, notice_raw: str | None = None
) -> Screen:
    if notice is None and notice_raw is None:
        return screen
    return dataclasses.replace(screen, notice=notice, notice_raw=notice_raw)


async def _close_number_prompt(state: FSMContext | None) -> None:
    """Going anywhere else in the panel closes the tolerance number prompt.

    ``tol.in`` is edited in place, so the admin can leave it by a button on any
    older panel message; left armed, the state would read their next text as a
    tolerance value. Only this prompt's state is cleared here. The instruction
    editor is not an exception: `PromptExitMiddleware` ends
    ``awaiting_system_prompt`` and its draft on any tap outside
    ``adm_pnl_sp:``/``spx:``/``spok:``/``spre:``, before this runs.
    """
    if state is not None and await state.get_state() == AdminStates.awaiting_setting_value.state:
        await state.clear()


# ── Stable entries (other areas call these; signatures are shared) ─────────


async def _show_chat(
    target: CallbackQuery | Message,
    lang: str,
    chat_id: int,
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    chat_config_service: ChatConfigService,
    rules_repo: RulesRepository,
    admin_repo: AdminRepository | None,
    *,
    notice: str | None = None,
) -> Message | None:
    view = await _load_view(
        chat_id, chat_settings_repo, bot_config_repo, chat_config_service, rules_repo=rules_repo
    )
    if view is None:
        if admin_repo is None:
            return await show(target, screens.build_panel_notfound(lang))
        return await _show_chats(target, lang, 0, admin_repo, notice_raw=_CHAT_GONE(lang))
    return await show(target, _with_notice(screens.build_chat(view, lang), notice=notice))


async def show_chat(
    target: CallbackQuery | Message,
    lang: str,
    chat_id: int,
    *,
    notice: str | None = None,
    data: Mapping[str, Any],
) -> Message | None:
    """Show one chat's root screen: edit in place for a button, a new message otherwise.

    ``data`` is the calling handler's ``**kwargs``; the repositories come from
    the request container in it (`screens.deps`). ``notice`` is plain text,
    shown as the ``✓`` line above the screen. A chat with no settings row shows
    the chat list instead, with a line saying so.
    """
    chat_settings_repo = await resolve(data, ChatSettingsRepository)
    bot_config_repo = await resolve(data, BotConfigRepository)
    chat_config_service = await resolve(data, ChatConfigService)
    rules_repo = await resolve(data, RulesRepository)
    admin_repo = await resolve(data, AdminRepository)
    await _close_number_prompt(state_of(data))
    return await _show_chat(
        target,
        _get_lang(lang),
        chat_id,
        chat_settings_repo,
        bot_config_repo,
        chat_config_service,
        rules_repo,
        admin_repo,
        notice=notice,
    )


async def _show_chats(
    target: CallbackQuery | Message,
    lang: str,
    page: int,
    admin_repo: AdminRepository,
    *,
    notice: str | None = None,
    notice_raw: str | None = None,
    answered: bool = False,
) -> Message | None:
    requests = await admin_repo.count_attempts("pending")
    rejected = await admin_repo.count_attempts("rejected")
    per_page = screens.chats_per_page(requests, rejected)
    chats, total = await admin_repo.get_enabled_chats_page_by_activity(page, per_page)
    shown, pages, _ = page_slice(total, page, per_page)
    if shown != page:
        # An old button can carry a page the list no longer has.
        chats, total = await admin_repo.get_enabled_chats_page_by_activity(shown, per_page)
    screen = screens.build_chats(
        lang, chats, total=total, page=shown, pages=pages, requests=requests, rejected=rejected
    )
    return await show(
        target, _with_notice(screen, notice=notice, notice_raw=notice_raw), answered=answered
    )


async def show_chats(
    target: CallbackQuery | Message,
    lang: str,
    page: int = 0,
    *,
    notice: str | None = None,
    data: Mapping[str, Any],
) -> Message | None:
    """Show the list of connected chats, the same way the ``adm_pnl:`` button does."""
    admin_repo = await resolve(data, AdminRepository)
    await _close_number_prompt(state_of(data))
    return await _show_chats(target, _get_lang(lang), page, admin_repo, notice=notice)


async def _show_section(
    callback: CallbackQuery,
    lang: str,
    view: ChatView,
    section: Section,
    *,
    notice: str | None = None,
    notice_raw: str | None = None,
) -> None:
    screen = screens.SECTION_BUILDERS[section](view, lang)
    await show(callback, _with_notice(screen, notice=notice, notice_raw=notice_raw))


# ── The chat list and one chat ────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm_pnl:"))
async def handle_chat_panel_picker(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    """The chat list, page ``adm_pnl:{lang}:{page}`` (no page: the first)."""
    if not await _guard(callback, bot_config_repo):
        return
    await _close_number_prompt(state_of(kwargs))
    parts = (callback.data or "").split(":")
    lang = _lang_of(callback)
    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        await show_stale(callback, lang, data=kwargs)
        return
    await _show_chats(callback, lang, page, admin_repo)


@router.callback_query(F.data.startswith("adm_pnl_menu:"))
async def handle_chat_panel_menu(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    rules_repo: FromDishka[RulesRepository],
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """One chat's root screen."""
    if not await _guard(callback, bot_config_repo):
        return
    await _close_number_prompt(state_of(kwargs))
    lang, chat_id = _parse_lang_chat(callback)
    if chat_id is None:
        await show_stale(callback, lang, data=kwargs)
        return
    await _show_chat(
        callback,
        lang,
        chat_id,
        chat_settings_repo,
        bot_config_repo,
        chat_config_service,
        rules_repo,
        admin_repo,
    )


@router.callback_query(F.data.startswith("adm_pnl_grp:"))
async def handle_chat_panel_group(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """One section of a chat: ``adm_pnl_grp:{lang}:{chat_id}:{section}``."""
    if not await _guard(callback, bot_config_repo):
        return
    await _close_number_prompt(state_of(kwargs))
    lang, chat_id = _parse_lang_chat(callback)
    parts = (callback.data or "").split(":")
    section = section_of_token(parts[3]) if len(parts) > 3 else None
    if chat_id is None or section is None:
        await show_stale(callback, lang, data=kwargs)
        return
    if section == "rules":
        await show_rules(callback, lang, chat_id, data=kwargs)
        return

    admin_live = (
        await _bot_is_admin(callback, chat_id, kwargs.get("bot_id"))
        if section == "reactions"
        else None
    )
    view = await _load_view(
        chat_id, chat_settings_repo, bot_config_repo, chat_config_service, bot_is_admin=admin_live
    )
    if view is None:
        await _show_chats(callback, lang, 0, admin_repo, notice_raw=_CHAT_GONE(lang))
        return
    await _show_section(callback, lang, view, section)


# ── Toggles ───────────────────────────────────────────────────────────────

_HIDDEN_WHILE_OFF: Final[dict[str, tuple[str, Tr]]] = {
    **dict.fromkeys(screens.COMMUNITY_SUBTOGGLES, ("cm", screens.COMMUNITY_OFF_TAP)),
    "rh": ("rx", screens.REACTIONS_OFF_TAP),
}
"""Toggle code -> (the switch it hangs off, the line a tap on it gets while that is off).

The section hides these buttons while their switch is off, so a tap on one
comes from an older screen; writing it would change a setting the admin
cannot see on the screen they are looking at."""


@router.callback_query(F.data.startswith("adm_pnl_tgl:"))
async def handle_chat_panel_toggle(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Flip one bool setting: ``adm_pnl_tgl:{lang}:{chat_id}:{code}``.

    The negation happens inside the UPDATE (``toggle_bool_field``), so a
    double-tap cannot collapse two flips into one; the effective value only
    supplies the starting point for a NULL ("inherited") column. Re-renders
    the field's own section with a ``✓`` line naming what changed. A chat with
    no settings row shows the list with `_CHAT_GONE`, as every panel screen does.
    """
    if not await _guard(callback, bot_config_repo):
        return
    await _close_number_prompt(state_of(kwargs))
    lang, chat_id = _parse_lang_chat(callback)
    parts = (callback.data or "").split(":")
    field = field_by_code(parts[3]) if len(parts) > 3 else None
    if chat_id is None or field is None or field.type is not FieldType.BOOL:
        await show_stale(callback, lang, data=kwargs)
        return
    section = SECTION_OF[field.code]
    bot_id_hint = kwargs.get("bot_id")

    view = await _load_view(chat_id, chat_settings_repo, bot_config_repo, chat_config_service)
    if view is None:
        logger.warning("Toggle for a chat with no chat_settings row", chat_id=chat_id)
        await _show_chats(callback, lang, 0, admin_repo, notice_raw=_CHAT_GONE(lang))
        return

    parent = _HIDDEN_WHILE_OFF.get(field.code)
    if parent is not None and not view.on(parent[0]):
        if section == "reactions":
            view = dataclasses.replace(
                view, bot_is_admin=await _bot_is_admin(callback, chat_id, bot_id_hint)
            )
        await _show_section(callback, lang, view, section, notice_raw=parent[1](lang))
        return

    effective = bool(view.values.get(field.key))
    new_value = await chat_settings_repo.toggle_bool_field(chat_id, field.key, effective)
    if new_value is None:
        logger.warning("Toggle affected no chat_settings row", chat_id=chat_id, field=field.key)
        await _show_chats(callback, lang, 0, admin_repo, notice_raw=_CHAT_GONE(lang))
        return
    chat_config_service.invalidate(chat_id)

    label = screens.LABELS[field.code](lang)
    notice = (screens.TOGGLED_ON if new_value else screens.TOGGLED_OFF)(lang).format(label=label)
    if section == "rules":
        await show_rules(callback, lang, chat_id, notice=notice, data=kwargs)
        return
    admin_live = (
        await _bot_is_admin(callback, chat_id, bot_id_hint) if section == "reactions" else None
    )
    fresh = await _load_view(
        chat_id, chat_settings_repo, bot_config_repo, chat_config_service, bot_is_admin=admin_live
    )
    if fresh is None:  # the row went away between the write and the read
        await _show_chats(callback, lang, 0, admin_repo, notice_raw=_CHAT_GONE(lang))
        return
    if field.code == "rx" and new_value and fresh.bot_is_admin is False:
        screen = _with_notice(screens.build_reactions(fresh, lang), notice=notice)
        await show(callback, screen, toast=screens.REACTIONS_NOT_ADMIN_ALERT(lang), alert=True)
        return
    await _show_section(callback, lang, fresh, section, notice=notice)


# ── Sticker explicitness (ADR-0008 Decision 10) ───────────────────────────


@router.callback_query(F.data.startswith("adm_pnl_tol:"))
async def handle_chat_panel_tolerance_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """The presets (``adm_pnl_tol:{lang}:{chat_id}``), or the number prompt (``…:in``).

    The number prompt edits this message in place and remembers its id: when
    the number arrives, the prompt's Cancel is taken off, so the old message
    cannot cancel a value that is already saved.
    """
    if not await _guard(callback, bot_config_repo):
        return
    lang, chat_id = _parse_lang_chat(callback)
    if chat_id is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    view = await _load_view(chat_id, chat_settings_repo, bot_config_repo, chat_config_service)
    if view is None:
        await _show_chats(callback, lang, 0, admin_repo, notice_raw=_CHAT_GONE(lang))
        return

    parts = (callback.data or "").split(":")
    if len(parts) > 3 and parts[3] == "in":
        await state.set_state(AdminStates.awaiting_setting_value)
        prompt_id = callback.message.message_id if isinstance(callback.message, Message) else None
        await state.update_data(tol_chat_id=chat_id, tol_lang=lang, tol_prompt_id=prompt_id)
        await show(callback, screens.build_tolerance_input(view, lang))
        return
    await _close_number_prompt(state)
    await show(callback, screens.build_tolerance_prompt(view, lang))


@router.callback_query(F.data.startswith("adm_pnl_tolset:"))
async def handle_chat_panel_tolerance_set(
    callback: CallbackQuery,
    state: FSMContext,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """A preset: ``adm_pnl_tolset:{lang}:{chat_id}:{value}`` saves at once, back to stickers."""
    if not await _guard(callback, bot_config_repo):
        return
    lang, chat_id = _parse_lang_chat(callback)
    parts = (callback.data or "").split(":")
    try:
        value = float(parts[3])
    except (ValueError, IndexError):
        value = -1.0
    if chat_id is None or not 0.0 <= value <= 1.0:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    await _close_number_prompt(state)
    await _save_tolerance(
        callback,
        lang,
        chat_id,
        value,
        chat_settings_repo,
        bot_config_repo,
        chat_config_service,
        admin_repo,
    )


async def _save_tolerance(
    target: CallbackQuery | Message,
    lang: str,
    chat_id: int,
    value: float,
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    chat_config_service: ChatConfigService,
    admin_repo: AdminRepository,
) -> None:
    """Write ``tolerance_level`` and show the stickers section with the new value on top.

    A write that matched no row shows the chat list with `_CHAT_GONE`, like
    every other panel screen whose chat has gone.
    """
    written = await chat_settings_repo.set_field(chat_id, "tolerance_level", value)
    chat_config_service.invalidate(chat_id)
    view = (
        await _load_view(chat_id, chat_settings_repo, bot_config_repo, chat_config_service)
        if written
        else None
    )
    if view is None:
        logger.warning("Tolerance write affected no chat_settings row", chat_id=chat_id)
        await _show_chats(target, lang, 0, admin_repo, notice_raw=_CHAT_GONE(lang))
        return
    notice = screens.TOLERANCE_SET(lang).format(value=screens.tolerance_label(value, lang))
    await show(target, _with_notice(screens.build_stickers(view, lang), notice=notice))


async def _drop_prompt_keyboard(bot: Bot | None, chat_id: int, message_id: object) -> None:
    """Take the buttons off an earlier prompt, so only the newest one can act.

    Cosmetic, so it may fail: a message too old to edit, or already deleted,
    must not stop the value from being saved or the next prompt from going out.
    """
    if bot is None or not isinstance(message_id, int):
        return
    with suppress(TelegramBadRequest):
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None
        )


@router.message(
    AdminStates.awaiting_setting_value,
    F.chat.type == "private",
    ~F.text.startswith("/"),
)
async def handle_chat_panel_tolerance_input(
    message: Message,
    state: FSMContext,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    admin_repo: FromDishka[AdminRepository],
) -> None:
    """Take a typed ``tolerance_level``: one new message either way.

    A number in 0..1 is saved and the stickers section follows as a new
    message; anything else gets the prompt again, under a line saying what is
    needed. Either way the earlier prompt loses its Cancel. Commands pass
    through (``~F.text.startswith("/")``), and the prompt's Cancel clears the
    state — without both, this state would eat every admin command until a
    valid number arrived (2026-08-07 review).
    """
    data = await state.get_data()
    chat_id = data.get("tol_chat_id")
    lang = _get_lang(data.get("tol_lang"))
    if chat_id is None:
        await state.clear()
        return
    if not await check_admin_direct(
        bot_config_repo, message.from_user.id if message.from_user else None
    ):
        await state.clear()
        return

    await _drop_prompt_keyboard(message.bot, message.chat.id, data.get("tol_prompt_id"))
    raw = (message.text or "").strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if not 0.0 <= value <= 1.0:
        view = await _load_view(chat_id, chat_settings_repo, bot_config_repo, chat_config_service)
        if view is None:
            await state.clear()
            await _show_chats(message, lang, 0, admin_repo, notice_raw=_CHAT_GONE(lang))
            return
        sent = await show(message, screens.build_tolerance_input(view, lang, invalid=True))
        await state.update_data(tol_prompt_id=sent.message_id if sent is not None else None)
        return

    await state.clear()
    await _save_tolerance(
        message,
        lang,
        chat_id,
        value,
        chat_settings_repo,
        bot_config_repo,
        chat_config_service,
        admin_repo,
    )


@router.callback_query(F.data.startswith("adm_pnl_tolcancel:"))
async def handle_chat_panel_tolerance_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Leave the number prompt without saving: back to the stickers section.

    The state is cleared before anything else, malformed payload included —
    the way out is the one thing this handler may never refuse.
    """
    if not await _guard(callback, bot_config_repo):
        return
    if await state.get_state() == AdminStates.awaiting_setting_value.state:
        await state.clear()
    lang, chat_id = _parse_lang_chat(callback)
    if chat_id is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    view = await _load_view(chat_id, chat_settings_repo, bot_config_repo, chat_config_service)
    if view is None:
        await _show_chats(callback, lang, 0, admin_repo, notice_raw=_CHAT_GONE(lang))
        return
    await _show_section(callback, lang, view, "stickers")


# ── The instruction (system_prompt) editor

_SP_OUTDATED = {
    "ru": "Это старое превью — черновик с тех пор изменился. Нажмите кнопку под последним.",
    "en": "This preview is outdated — the draft has changed since. Use the latest one.",
}
_SP_SAVED = {
    "ru": "<i>✓ Инструкция сохранена · {n} симв.</i>",
    "en": "<i>✓ Instruction saved · {n} chars</i>",
}
_SP_SAVED_TOAST = {"ru": "Сохранено", "en": "Saved"}
_SP_CANCELLED = {"ru": "<i>Инструкция не изменена.</i>", "en": "<i>Instruction unchanged.</i>"}
_SP_STALE = {
    "ru": (
        "<i>Черновик не найден — ввод уже закрыт или бот перезапускался. "
        "Инструкция не изменена.</i>"
    ),
    "en": (
        "<i>No draft found — the input was closed or the bot restarted. Instruction unchanged.</i>"
    ),
}


# ── system_prompt editor ─────────────────────────────────────────────────────

# One lock per admin. A paste longer than Telegram's 4096 arrives as several
# messages at once, and aiogram handles updates concurrently: without the
# lock two parts read the same draft and the second write drops the first.
_sp_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def _sp_draft(parts: list[list[Any]]) -> str:
    """Reassemble the draft in message order, not in arrival order.

    Each update passes several awaiting middlewares before it reaches the
    handler, so the parts of one paste can reach the lock out of order;
    ``message_id`` is the order the admin sent them in.
    """
    return "\n".join(str(text) for _, text in sorted(parts, key=lambda p: int(p[0])))


async def _send_split(target: Message, screen: Screen) -> list[Message]:
    """Send ``screen`` as as many messages as Telegram needs; buttons on the last.

    A prompt is verbatim user text and may exceed 4096 on its own, so it goes
    through ``split_html`` (CLAUDE.md: a rejected send is no message at all).
    Not `show`: it returns the last piece only, and a newer preview has to
    delete every piece of the older ones. The last piece carries the buttons, so
    it is the one noted as the prompt's message (`note_screen`): a tap elsewhere
    that ends the prompt then marks it closed, as `show` would.
    """
    pieces = split_html(render_text(screen))
    reply_markup = to_markup(screen)
    sent: list[Message] = []
    for i, piece in enumerate(pieces):
        last = i == len(pieces) - 1
        sent.append(
            await target.answer(
                piece, parse_mode="HTML", reply_markup=reply_markup if last else None
            )
        )
    if sent:
        note_screen(sent[-1], screen.id, screen.text)
    return sent


async def _reply_refused(message: Message, refused: Screen) -> None:
    """Send the refusal as a reply to the part it refuses, noted like any prompt screen."""
    note_screen(
        await message.reply(render_text(refused), reply_markup=to_markup(refused)),
        refused.id,
        refused.text,
    )


def _parse_rev(callback: CallbackQuery) -> int | None:
    """The draft version a Save / «Start over» button was made for."""
    parts = (callback.data or "").split(":")
    try:
        return int(parts[3])
    except (ValueError, IndexError):
        return None


async def _sp_draft_for(state: FSMContext, chat_id: int) -> tuple[dict[str, Any], str] | None:
    """The live draft for ``chat_id``, or ``None`` when the button is stale.

    Stale means: the state is gone (MemoryStorage does not survive a restart,
    /cancel cleared it), another flow replaced it, or it belongs to another
    chat's editor.
    """
    if await state.get_state() != AdminStates.awaiting_system_prompt.state:
        return None
    data = await state.get_data()
    if data.get("sp_chat_id") != chat_id:
        return None
    return data, _sp_draft(list(data.get("sp_parts") or []))


async def _show_chat_gone(
    callback: CallbackQuery, lang: str, data: Mapping[str, Any], *, answered: bool
) -> None:
    """The chat list under `_CHAT_GONE`: the editor's chat has no settings row."""
    admin_repo = await resolve(data, AdminRepository)
    await _show_chats(callback, lang, 0, admin_repo, notice_raw=_CHAT_GONE(lang), answered=answered)


async def _show_behavior_with_notice(
    callback: CallbackQuery,
    notice: str,
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    chat_config_service: ChatConfigService,
    lang: str,
    chat_id: int,
    data: Mapping[str, Any] | None = None,
) -> None:
    """Edit the tapped message into the behavior section, ``notice`` (HTML) on top.

    The callback is already answered: the editor's handlers answer first,
    some of them with a toast of their own.
    """
    view = await _load_view(chat_id, chat_settings_repo, bot_config_repo, chat_config_service)
    if view is None:
        logger.warning("Instruction editor for a chat with no chat_settings row", chat_id=chat_id)
        await _show_chat_gone(callback, lang, data or {}, answered=True)
        return
    screen = dataclasses.replace(screens.build_behavior(view, lang), notice_raw=notice)
    await show(callback, screen, answered=True)


@router.callback_query(F.data.startswith("adm_pnl_sp:"))
async def handle_chat_panel_sp_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    """Show the chat's current system prompt and wait for a new one."""
    if not await _guard(callback, bot_config_repo):
        return
    lang, chat_id = _parse_lang_chat(callback)
    if chat_id is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return

    row = await chat_settings_repo.get(chat_id)
    if row is None:
        await _show_chat_gone(callback, lang, kwargs, answered=False)
        return

    await state.set_state(AdminStates.awaiting_system_prompt)
    await state.update_data(
        sp_chat_id=chat_id,
        sp_lang=lang,
        sp_parts=[],
        sp_previews=[],
        sp_rev=0,
        sp_overflow=False,
    )
    await callback.answer()

    title = row.get("chat_title")
    screen = screens.build_sp_prompt(
        chat_id, str(title) if title else None, str(row.get("system_prompt") or ""), lang
    )
    if isinstance(callback.message, Message):
        await _send_split(callback.message, screen)


@router.message(
    AdminStates.awaiting_system_prompt,
    F.chat.type == "private",
    F.text,
    ~F.text.startswith("/"),
)
async def handle_chat_panel_sp_input(
    message: Message,
    state: FSMContext,
    bot: Bot,
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Append one message to the draft and show the whole draft for approval.

    Appending, not replacing: a prompt longer than 4096 reaches the bot as
    several messages, and each must extend the draft rather than discard the
    part before it. «Start over» is the way to discard. Commands pass through
    (``~F.text.startswith("/")``); non-text messages do not match at all.
    """
    user_id = message.from_user.id if message.from_user else 0
    if not await check_admin_direct(bot_config_repo, user_id or None):
        await state.clear()
        return

    async with _sp_locks[user_id]:
        data = await state.get_data()
        chat_id = data.get("sp_chat_id")
        lang = _get_lang(data.get("sp_lang"))
        if chat_id is None:
            await state.clear()
            return

        rev = int(data.get("sp_rev") or 0)
        if data.get("sp_overflow"):
            # A part was already refused: anything added now would sit after a
            # hole, and a preview of it would look complete.
            refused = screens.build_sp_refused(chat_id, rev, lang)
            await _reply_refused(message, refused)
            return

        # Telegram turns **bold** and backticks into entities and drops the markers;
        # put them back, or a Markdown prompt is saved without its emphasis.
        text = entities_to_markdown(message.text or "", message.entities)
        parts = [*list(data.get("sp_parts") or []), [message.message_id, text]]
        draft = _sp_draft(parts)
        if len(draft) > screens.SP_MAX_CHARS:
            # Refusing this part alone would let a later, shorter part in and
            # save a prompt with its middle missing. The whole draft is
            # incomplete from here on, so Save goes away until «Start over».
            await state.update_data(sp_overflow=True)
            refused = screens.build_sp_refused(chat_id, rev, lang, length=len(draft))
            await _reply_refused(message, refused)
            return

        # The previous preview showed a shorter draft; its «Save» would now
        # save more than it shows. Only the newest preview stays.
        for old_id in data.get("sp_previews") or []:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=int(old_id))
            except Exception:
                logger.debug("sp_preview_delete_failed", message_id=old_id)

        rev = message.message_id
        sent = await _send_split(message, screens.build_sp_preview(chat_id, draft, rev, lang))
        await state.update_data(
            sp_parts=parts, sp_previews=[m.message_id for m in sent], sp_rev=rev
        )


@router.callback_query(F.data.startswith("adm_pnl_spok:"))
async def handle_chat_panel_sp_save(
    callback: CallbackQuery,
    state: FSMContext,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    **kwargs: Any,
) -> None:
    """Write the draft as the chat's system prompt."""
    if not await _guard(callback, bot_config_repo):
        return
    lang, chat_id = _parse_lang_chat(callback)
    if chat_id is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return

    user_id = callback.from_user.id if callback.from_user else 0
    async with _sp_locks[user_id]:
        found = await _sp_draft_for(state, chat_id)
        if found is not None and found[0].get("sp_overflow"):
            await callback.answer(screens.SP_INCOMPLETE(lang), show_alert=True)
            return
        if found is not None and _parse_rev(callback) != found[0].get("sp_rev"):
            await callback.answer(_SP_OUTDATED[lang], show_alert=True)
            return
        draft = found[1].strip() if found else ""
        if not draft:
            await callback.answer()
            await _show_behavior_with_notice(
                callback,
                _SP_STALE[lang],
                chat_settings_repo,
                bot_config_repo,
                chat_config_service,
                lang,
                chat_id,
                kwargs,
            )
            return

        written = await chat_settings_repo.set_field(chat_id, "system_prompt", draft)
        if not written:
            # The chat left the settings while the admin was typing: the draft
            # has nowhere to go, so the input closes too.
            logger.warning("System prompt write affected no chat_settings row", chat_id=chat_id)
            await state.clear()
            await _show_chat_gone(callback, lang, kwargs, answered=False)
            return
        chat_config_service.invalidate(chat_id)
        await state.clear()

    logger.info("System prompt updated", chat_id=chat_id, admin_id=user_id, length=len(draft))
    await callback.answer(_SP_SAVED_TOAST[lang])
    await _show_behavior_with_notice(
        callback,
        _SP_SAVED[lang].format(n=len(draft)),
        chat_settings_repo,
        bot_config_repo,
        chat_config_service,
        lang,
        chat_id,
        kwargs,
    )


@router.callback_query(F.data.startswith("adm_pnl_spre:"))
async def handle_chat_panel_sp_redo(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    **kwargs: Any,
) -> None:
    """Drop the draft and wait for the prompt again."""
    if not await _guard(callback, bot_config_repo):
        return
    lang, chat_id = _parse_lang_chat(callback)
    if chat_id is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return

    user_id = callback.from_user.id if callback.from_user else 0
    async with _sp_locks[user_id]:
        found = await _sp_draft_for(state, chat_id)
        if found is None:
            await callback.answer()
            await _show_behavior_with_notice(
                callback,
                _SP_STALE[lang],
                chat_settings_repo,
                bot_config_repo,
                chat_config_service,
                lang,
                chat_id,
                kwargs,
            )
            return
        if _parse_rev(callback) != found[0].get("sp_rev"):
            await callback.answer(_SP_OUTDATED[lang], show_alert=True)
            return
        here = callback.message.message_id if isinstance(callback.message, Message) else None
        for old_id in found[0].get("sp_previews") or []:
            if old_id == here or not isinstance(callback.message, Message):
                continue
            try:
                await bot.delete_message(chat_id=callback.message.chat.id, message_id=int(old_id))
            except Exception:
                logger.debug("sp_preview_delete_failed", message_id=old_id)
        await state.update_data(sp_parts=[], sp_previews=[], sp_rev=0, sp_overflow=False)

    await show(callback, screens.build_sp_redo(chat_id, lang))


@router.callback_query(F.data.startswith("adm_pnl_spx:"))
async def handle_chat_panel_sp_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    **kwargs: Any,
) -> None:
    """Leave the editor without writing and return to the behavior section.

    Clears the state only when it is this chat's editor: an old screen's
    «Cancel» must not close another chat's draft or another flow. Malformed
    data still clears this editor -- the way out is the one thing this
    handler may never refuse.
    """
    if not await _guard(callback, bot_config_repo):
        return
    lang, chat_id = _parse_lang_chat(callback)
    if await state.get_state() == AdminStates.awaiting_system_prompt.state:
        data = await state.get_data()
        if chat_id is None or data.get("sp_chat_id") == chat_id:
            await state.clear()
    if chat_id is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    await callback.answer()
    await _show_behavior_with_notice(
        callback,
        _SP_CANCELLED[lang],
        chat_settings_repo,
        bot_config_repo,
        chat_config_service,
        lang,
        chat_id,
        kwargs,
    )


# ── /panel <query> ────────────────────────────────────────────────────────


async def _resolve_reference_chat_id(
    ref: ChatReference,
    bot: Bot,
    chat_settings_repo: ChatSettingsRepository,
) -> int | None:
    """Resolve a parsed link/id reference to a whitelisted chat_id, or None.

    ``ref.username`` needs one live ``bot.get_chat()`` call to become a
    chat_id; ``ref.chat_id`` already is one. Either way, resolving *which*
    chat a link points to is not the same as being allowed to see its
    screen: the whitelist check below runs unconditionally, even for a
    chat_id parsed with no network call at all.
    """
    chat_id = ref.chat_id
    if chat_id is None and ref.username is not None:
        try:
            chat = await bot.get_chat(f"@{ref.username}")
        except Exception:
            logger.debug("chat_panel_shortcut_username_lookup_failed", username=ref.username)
            return None
        chat_id = chat.id

    if chat_id is None:
        return None

    row = await chat_settings_repo.get(chat_id)
    if row is None or not row.get("enabled"):
        return None
    return chat_id


# F.chat.type in the decorator, not a body check: a matched handler consumes
# the update, so an admin typing /panel in a group would get silence and the
# message would never reach the text pipeline (CLAUDE.md, aiogram gotchas).
@router.message(Command("panel"), IsAdmin(), F.chat.type == "private")
async def handle_chat_panel_shortcut(
    message: Message,
    bot: Bot,
    admin_repo: FromDishka[AdminRepository],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    rules_repo: FromDishka[RulesRepository],
    **kwargs: Any,
) -> None:
    """Open a chat by link, id or part of its title, as a new message.

    ``IsAdmin`` gates the command itself; every match (link/username
    resolution and the title search) is additionally filtered through
    ``chat_settings.enabled = true``, so this can only ever reach a connected
    chat, never one the admin merely knows a link to. Several title matches
    show a short list instead of guessing; when there are more than fit, the
    screen says so and offers the full list.
    """
    await _close_number_prompt(state_of(kwargs))
    lang = _get_lang(await admin_repo.get_admin_language(bot_config_repo))
    parts = (message.text or "").split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""
    if not query:
        await show(message, screens.build_panel_noquery(lang))
        return

    ref = parse_chat_reference(query)
    if ref is not None:
        chat_id = await _resolve_reference_chat_id(ref, bot, chat_settings_repo)
        if chat_id is None:
            await show(message, screens.build_panel_notfound(lang))
            return
        await _show_chat(
            message,
            lang,
            chat_id,
            chat_settings_repo,
            bot_config_repo,
            chat_config_service,
            rules_repo,
            None,
        )
        return

    # Not a link or an id: search the titles.
    candidates = await admin_repo.find_enabled_chats_by_title(query, limit=screens.FIND_SHOWN + 1)
    if not candidates:
        await show(message, screens.build_panel_notfound(lang))
        return
    if len(candidates) == 1:
        await _show_chat(
            message,
            lang,
            candidates[0]["chat_id"],
            chat_settings_repo,
            bot_config_repo,
            chat_config_service,
            rules_repo,
            None,
        )
        return
    await show(message, screens.build_panel_many(lang, candidates))
