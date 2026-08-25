"""Rules management handlers — admin interface for custom rules.

Callback routes (all DM-only, admin-only):
- ``adm_rules:{lang}:{page?}``     — chat selection (entry from main menu)
- ``ar_list:{lang}:{chat_id}:{page}`` — rule list for a chat
- ``ar_view:{lang}:{rule_id}``     — view rule details
- ``ar_tog:{lang}:{rule_id}:{chat_id}:{page}`` — toggle on/off
- ``ar_del:{lang}:{rule_id}:{chat_id}:{page}`` — delete a rule
- ``ar_add:{lang}:{chat_id}``      — start creation (select type)
- ``ar_type:{lang}:{chat_id}:{rule_type}`` — selected type, await config
- ``ar_cancel:{lang}:{chat_id}``   — leave the config prompt without creating
"""

from __future__ import annotations

import json
from html import escape
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.filters.admin import IsAdmin
from src.bot.keyboards.rules import (
    confirm_delete_rule_keyboard,
    rule_config_cancel_keyboard,
    rule_detail_keyboard,
    rule_type_keyboard,
    rules_chat_list_keyboard,
    rules_list_keyboard,
)
from src.bot.states.admin import AdminStates
from src.bot.utils import safe_edit_text
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.rules import RulesRepository
from src.models.rules import _VALID_RULE_ACTIONS, _VALID_RULE_TYPES, RuleAction
from src.services.modules.reactions.selector import ReactionSelector

logger = structlog.get_logger(__name__)

router = Router(name="rules")

_PER_PAGE = 5

# ---------------------------------------------------------------------------
# i18n texts
# ---------------------------------------------------------------------------

_RULES_TITLE: dict[str, str] = {
    "ru": "<b>Управление правилами</b>\n\nВыберите чат:",
    "en": "<b>Rules Management</b>\n\nSelect a chat:",
}

_RULES_LIST_TITLE: dict[str, str] = {
    "ru": "<b>Правила чата</b>",
    "en": "<b>Chat Rules</b>",
}

_NO_RULES: dict[str, str] = {
    "ru": "<b>Правила чата</b>\n\nНет правил.",
    "en": "<b>Chat Rules</b>\n\nNo rules.",
}

_NO_CHATS: dict[str, str] = {
    "ru": "<b>Управление правилами</b>\n\nНет чатов в whitelist.",
    "en": "<b>Rules Management</b>\n\nNo whitelisted chats.",
}

_NOT_ADMIN: dict[str, str] = {
    "ru": "У вас нет доступа.",
    "en": "Access denied.",
}

_TYPE_SELECTED: dict[str, str] = {
    "ru": (
        "<b>Создание правила</b>\n\n"
        "Тип: <code>{rule_type}</code>\n\n"
        "Отправьте JSON-конфиг правила.\n\n"
        "Пример для keyword_trigger:\n"
        '<code>{{"name": "spam-words", "keywords": ["spam", "buy"], '
        '"match_type": "contains", "action": "warn_user", '
        '"warning_message": "No spam!"}}</code>\n\n'
        "Действия: <code>notify_admin</code>, <code>warn_user</code>, "
        '<code>custom_response</code>, <code>set_reaction</code> (+ "emoji": "💊" — '
        "только из набора реакций Telegram). Для keyword_trigger опционально "
        '"target_users": [id].'
    ),
    "en": (
        "<b>Create Rule</b>\n\n"
        "Type: <code>{rule_type}</code>\n\n"
        "Send JSON config for the rule.\n\n"
        "Example for keyword_trigger:\n"
        '<code>{{"name": "spam-words", "keywords": ["spam", "buy"], '
        '"match_type": "contains", "action": "warn_user", '
        '"warning_message": "No spam!"}}</code>\n\n'
        "Actions: <code>notify_admin</code>, <code>warn_user</code>, "
        '<code>custom_response</code>, <code>set_reaction</code> (+ "emoji": "💊" — '
        "Telegram's reaction set only). For keyword_trigger, optionally "
        '"target_users": [id].'
    ),
}

_RULE_CREATED: dict[str, str] = {
    "ru": "Правило #{rule_id} создано.",
    "en": "Rule #{rule_id} created.",
}

_RULE_DELETED: dict[str, str] = {
    "ru": "Правило удалено.",
    "en": "Rule deleted.",
}

_RULE_NOT_FOUND: dict[str, str] = {
    "ru": "Правило не найдено.",
    "en": "Rule not found.",
}

_RULE_DEL_CONFIRM_TITLE: dict[str, str] = {
    "ru": "<b>Удалить правило?</b>",
    "en": "<b>Delete rule?</b>",
}

_RULE_DEL_CONFIRM_BODY: dict[str, str] = {
    "ru": "Это действие необратимо.",
    "en": "This action cannot be undone.",
}

_RULE_TOGGLED: dict[str, str] = {
    "ru": "Правило {status}.",
    "en": "Rule {status}.",
}

_INVALID_JSON: dict[str, str] = {
    "ru": "Невалидный JSON. Попробуйте ещё раз.",
    "en": "Invalid JSON. Please try again.",
}

_INVALID_ACTION: dict[str, str] = {
    "ru": "Неизвестное действие <code>{action}</code>. Доступные: {valid}.",
    "en": "Unknown action <code>{action}</code>. Available: {valid}.",
}

_INVALID_EMOJI: dict[str, str] = {
    "ru": (
        "Для <code>set_reaction</code> поле <code>emoji</code> должно быть "
        "одним из стандартных эмодзи-реакций Telegram (например 💊, 🔥, 🤡). "
        "Попробуйте ещё раз."
    ),
    "en": (
        "For <code>set_reaction</code>, <code>emoji</code> must be one of "
        "Telegram's standard reaction emoji (e.g. 💊, 🔥, 🤡). "
        "Please try again."
    ),
}


def _get_lang(raw: str | None) -> str:
    return raw if raw in ("ru", "en") else "ru"


def _check_admin(data: dict[str, Any]) -> bool:
    return bool(data.get("is_admin", False))


def _is_private(callback: CallbackQuery) -> bool:
    msg = callback.message
    if isinstance(msg, Message):
        return msg.chat.type == "private"
    return False


async def _leave_config_prompt(state: FSMContext) -> None:
    """Drop `awaiting_rule_config` when the admin navigates away from it.

    Without this the state outlives the screen that created it. Concrete
    misfire: open the type prompt for chat A, tap «Назад», wander off, and
    paste any valid JSON dict into the DM an hour later -- a rule is silently
    created in chat A with the type chosen back then. `/cancel` and the cancel
    button do not cover it, because the admin never realises a dialog is still
    open.

    Only OUR state is cleared. A rules screen has no business ending someone
    else's dialog, and `state.clear()` is indiscriminate.
    """
    if await state.get_state() == AdminStates.awaiting_rule_config.state:
        await state.clear()


# ---------------------------------------------------------------------------
# Entry: chat selection
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("adm_rules:"))
async def handle_rules_menu(
    callback: CallbackQuery,
    state: FSMContext,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """Show paginated list of chats to manage rules for."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    await _leave_config_prompt(state)

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    await callback.answer()

    # Get enabled chats
    rows = await chat_settings_repo._pool.fetch(
        """
        SELECT chat_id, chat_title, chat_type
        FROM chat_settings WHERE enabled = true
        ORDER BY chat_title NULLS LAST, chat_id
        LIMIT $1 OFFSET $2
        """,
        _PER_PAGE,
        page * _PER_PAGE,
    )
    total = (
        await chat_settings_repo._pool.fetchval(
            "SELECT COUNT(*) FROM chat_settings WHERE enabled = true"
        )
        or 0
    )
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    chats = [dict(r) for r in rows]

    # Resolve missing titles via Telegram API and persist
    for chat in chats:
        if not chat.get("chat_title") and callback.bot:
            try:
                chat_info = await callback.bot.get_chat(chat["chat_id"])
                title = chat_info.title or chat_info.full_name
                if title:
                    chat["chat_title"] = title
                    await chat_settings_repo.upsert(
                        chat["chat_id"],
                        chat_title=title,
                    )
            except Exception:
                logger.debug(
                    "Could not resolve chat title via Telegram API",
                    chat_id=chat["chat_id"],
                    exc_info=True,
                )

    msg = callback.message
    if isinstance(msg, Message):
        text = _NO_CHATS[lang] if not chats else _RULES_TITLE[lang]
        await safe_edit_text(
            msg,
            text,
            parse_mode="HTML",
            reply_markup=rules_chat_list_keyboard(lang, chats, page, total_pages),
        )


# ---------------------------------------------------------------------------
# Rule list for a chat
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("ar_list:"))
async def handle_rules_list(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    **kwargs: Any,
) -> None:
    """Show paginated rules for a specific chat."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    await _leave_config_prompt(state)

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    chat_id = int(parts[2]) if len(parts) > 2 else 0
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0

    await callback.answer()

    rules, total = await rules_repo.get_rules_page(chat_id, page, _PER_PAGE)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    msg = callback.message
    if isinstance(msg, Message):
        text = _NO_RULES[lang] if not rules else _RULES_LIST_TITLE[lang]
        await safe_edit_text(
            msg,
            text,
            parse_mode="HTML",
            reply_markup=rules_list_keyboard(lang, rules, page, total_pages, chat_id),
        )


# ---------------------------------------------------------------------------
# View rule details
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("ar_view:"))
async def handle_rule_view(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    **kwargs: Any,
) -> None:
    """Show details for a single rule."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    await _leave_config_prompt(state)

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    rule_id = int(parts[2]) if len(parts) > 2 else 0

    await callback.answer()

    rule = await rules_repo.get(rule_id)
    msg = callback.message
    if not isinstance(msg, Message):
        return

    if rule is None:
        await safe_edit_text(
            msg,
            {"ru": "Правило не найдено.", "en": "Rule not found."}.get(lang, ""),
            parse_mode="HTML",
        )
        return

    config = rule.get("config", {})
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            config = {}

    name = escape(config.get("name", f"Rule #{rule_id}"))
    enabled_icon = "✅" if rule.get("enabled") else "⏸"
    config_str = escape(json.dumps(config, ensure_ascii=False, indent=2))

    text = (
        f"<b>{name}</b> {enabled_icon}\n\n"
        f"<b>ID:</b> {rule_id}\n"
        f"<b>Type:</b> {escape(str(rule.get('rule_type', '')))}\n"
        f"<b>Weight:</b> {rule.get('weight', 1)}\n"
        f"<b>Mandatory:</b> {rule.get('mandatory', False)}\n"
        f"<b>Triggers:</b> {rule.get('trigger_count', 0)}\n\n"
        f"<b>Config:</b>\n<pre>{config_str}</pre>"
    )

    chat_id = rule.get("chat_id", 0)
    await safe_edit_text(
        msg,
        text,
        parse_mode="HTML",
        reply_markup=rule_detail_keyboard(lang, rule_id, chat_id),
    )


# ---------------------------------------------------------------------------
# Toggle rule
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("ar_tog:"))
async def handle_rule_toggle(
    callback: CallbackQuery,
    rules_repo: FromDishka[RulesRepository],
    **kwargs: Any,
) -> None:
    """Toggle a rule on/off."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    rule_id = int(parts[2]) if len(parts) > 2 else 0
    chat_id = int(parts[3]) if len(parts) > 3 else 0
    page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0

    rule = await rules_repo.get(rule_id)
    if rule is None:
        await callback.answer("Not found", show_alert=True)
        return

    new_enabled = not rule.get("enabled", True)
    await rules_repo.toggle(rule_id, enabled=new_enabled)

    status = (
        {"ru": "включено", "en": "enabled"}
        if new_enabled
        else {"ru": "выключено", "en": "disabled"}
    )
    await callback.answer(_RULE_TOGGLED[lang].format(status=status.get(lang, "")))

    # Refresh list
    rules, total = await rules_repo.get_rules_page(chat_id, page, _PER_PAGE)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    msg = callback.message
    if isinstance(msg, Message):
        text = _NO_RULES[lang] if not rules else _RULES_LIST_TITLE[lang]
        await safe_edit_text(
            msg,
            text,
            parse_mode="HTML",
            reply_markup=rules_list_keyboard(lang, rules, page, total_pages, chat_id),
        )


# ---------------------------------------------------------------------------
# Ask confirmation before deleting a rule
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("ar_del_ask:"))
async def handle_rule_delete_ask(
    callback: CallbackQuery,
    rules_repo: FromDishka[RulesRepository],
    **kwargs: Any,
) -> None:
    """Show confirmation prompt before deleting a rule."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    rule_id = int(parts[2]) if len(parts) > 2 else 0
    chat_id = int(parts[3]) if len(parts) > 3 else 0
    page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0

    await callback.answer()

    rule = await rules_repo.get(rule_id)
    msg = callback.message
    if not isinstance(msg, Message):
        return

    if rule is None:
        await safe_edit_text(
            msg,
            _RULE_NOT_FOUND[lang],
            parse_mode="HTML",
        )
        return

    config = rule.get("config", {})
    if isinstance(config, str):
        try:
            import json as _json

            config = _json.loads(config)
        except (ValueError, KeyError):
            config = {}
    name = escape(config.get("name", f"#{rule_id}"))

    text = f"{_RULE_DEL_CONFIRM_TITLE[lang]}\n\n{name}\n\n{_RULE_DEL_CONFIRM_BODY[lang]}"
    await safe_edit_text(
        msg,
        text,
        parse_mode="HTML",
        reply_markup=confirm_delete_rule_keyboard(lang, rule_id, chat_id, page),
    )


# ---------------------------------------------------------------------------
# Delete rule
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("ar_del:"))
async def handle_rule_delete(
    callback: CallbackQuery,
    rules_repo: FromDishka[RulesRepository],
    **kwargs: Any,
) -> None:
    """Delete a rule and refresh the list."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    rule_id = int(parts[2]) if len(parts) > 2 else 0
    chat_id = int(parts[3]) if len(parts) > 3 else 0
    page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0

    await rules_repo.delete(rule_id)
    await callback.answer(_RULE_DELETED[lang])

    # Refresh list
    rules, total = await rules_repo.get_rules_page(chat_id, page, _PER_PAGE)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    msg = callback.message
    if isinstance(msg, Message):
        text = _NO_RULES[lang] if not rules else _RULES_LIST_TITLE[lang]
        await safe_edit_text(
            msg,
            text,
            parse_mode="HTML",
            reply_markup=rules_list_keyboard(lang, rules, page, total_pages, chat_id),
        )


# ---------------------------------------------------------------------------
# Add rule: select type
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("ar_add:"))
async def handle_add_rule(
    callback: CallbackQuery,
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Show rule type selection."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    await _leave_config_prompt(state)

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    chat_id = int(parts[2]) if len(parts) > 2 else 0

    await callback.answer()

    msg = callback.message
    if isinstance(msg, Message):
        await safe_edit_text(
            msg,
            {"ru": "<b>Тип правила:</b>", "en": "<b>Rule type:</b>"}.get(lang, ""),
            parse_mode="HTML",
            reply_markup=rule_type_keyboard(lang, chat_id),
        )


# ---------------------------------------------------------------------------
# Add rule: type selected → await JSON config
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("ar_type:"))
async def handle_type_selected(
    callback: CallbackQuery,
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Rule type selected — ask for JSON config via FSM."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    chat_id = int(parts[2]) if len(parts) > 2 else 0
    rule_type = parts[3] if len(parts) > 3 else ""

    if rule_type not in _VALID_RULE_TYPES:
        await callback.answer("Invalid type", show_alert=True)
        return

    await callback.answer()

    # Save context in FSM
    await state.set_state(AdminStates.awaiting_rule_config)
    await state.update_data(rule_chat_id=chat_id, rule_type=rule_type, lang=lang)

    msg = callback.message
    if isinstance(msg, Message):
        text = _TYPE_SELECTED[lang].format(rule_type=escape(rule_type))
        await safe_edit_text(
            msg,
            text,
            parse_mode="HTML",
            reply_markup=rule_config_cancel_keyboard(lang, chat_id),
        )


@router.callback_query(F.data.startswith("ar_cancel:"))
async def handle_rule_config_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    rules_repo: FromDishka[RulesRepository],
    **kwargs: Any,
) -> None:
    """Leave the config prompt without creating anything, and show the list.

    Gated exactly like every other callback in this file: a cancel screen
    re-renders a chat's rule list, so it is not "harmless navigation".
    """
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    chat_id = int(parts[2]) if len(parts) > 2 else 0

    # NOT a bare `state.clear()`. These cancel buttons ride on standalone reply
    # messages (the three invalid-input re-prompts) that nothing ever edits, so
    # they stay tappable indefinitely — an admin who later opens a DIFFERENT
    # dialog and scrolls up to an old «✖️ Отмена» would otherwise have that
    # dialog silently wiped, state and data. Same rule the sibling helper
    # states: a rules screen has no business ending someone else's dialog.
    await _leave_config_prompt(state)
    await callback.answer()

    rules, total = await rules_repo.get_rules_page(chat_id, 0, _PER_PAGE)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    msg = callback.message
    if isinstance(msg, Message):
        text = _NO_RULES[lang] if not rules else _RULES_LIST_TITLE[lang]
        await safe_edit_text(
            msg,
            text,
            parse_mode="HTML",
            reply_markup=rules_list_keyboard(lang, rules, 0, total_pages, chat_id),
        )


# ---------------------------------------------------------------------------
# FSM: receive JSON config
# ---------------------------------------------------------------------------


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
) -> None:
    """Receive JSON config and create the rule.

    Four filters, and each one closes a hole the body could not (TD-049):

    ``IsAdmin()`` -- this handler writes a rule for whatever ``chat_id`` the
    FSM data carries, and until now it did so with **no authority check at
    all**, unlike both of its siblings. The FSM key binds the state to one
    user, which is exactly the TOCTOU the security register records as S-11:
    it binds it to the user who *was* an admin when the dialog opened.

    ``F.chat.type == "private"`` -- the dialog is only ever opened from a DM
    callback, so this costs nothing and stops the state from ever applying
    anywhere else.

    The two slash guards are one guard. aiogram resolves a command from
    ``message.text or message.caption``, so ``/help`` sent as a photo caption
    is a real command -- and ``~F.text.startswith("/")`` returns True for it,
    because ``.text`` is None. Measured: with the text-only guard, a captioned
    ``/help`` was still eaten by this handler and answered "Невалидный JSON".
    The same shape is why the escape hatches live on the keyboard and on
    ``/cancel`` rather than in this handler's body: a filtered-out update is
    one this handler never sees.
    """
    data = await state.get_data()
    lang = _get_lang(data.get("lang"))
    chat_id = data.get("rule_chat_id", 0)
    rule_type = data.get("rule_type", "")

    text = message.text or ""
    try:
        config = json.loads(text)
        if not isinstance(config, dict):
            raise ValueError  # noqa: TRY301
    except (json.JSONDecodeError, ValueError):
        await message.reply(
            _INVALID_JSON[lang],
            parse_mode="HTML",
            reply_markup=rule_config_cancel_keyboard(lang, chat_id),
        )
        return

    # Validate what the engine would otherwise drop silently at evaluation
    # time: an unknown action, or a set_reaction emoji outside Telegram's
    # reaction set, both yield "Rule created" and a permanent no-op whose
    # only trace is a server-side log line.
    action = config.get("action")
    if action is not None and action not in _VALID_RULE_ACTIONS:
        await message.reply(
            _INVALID_ACTION[lang].format(
                action=escape(str(action)),
                valid=", ".join(f"<code>{a}</code>" for a in RuleAction),
            ),
            parse_mode="HTML",
            reply_markup=rule_config_cancel_keyboard(lang, chat_id),
        )
        return
    if action == RuleAction.SET_REACTION:
        emoji = config.get("emoji")
        if not isinstance(emoji, str) or ReactionSelector.select(emoji) is None:
            await message.reply(
                _INVALID_EMOJI[lang],
                parse_mode="HTML",
                reply_markup=rule_config_cancel_keyboard(lang, chat_id),
            )
            return

    # Create rule
    rule_id = await rules_repo.create(
        chat_id=chat_id,
        rule_type=rule_type,
        config=config,
        weight=config.pop("weight", 1),
        mandatory=config.pop("mandatory", False),
    )

    await state.clear()
    await message.reply(
        _RULE_CREATED[lang].format(rule_id=rule_id),
        parse_mode="HTML",
    )
