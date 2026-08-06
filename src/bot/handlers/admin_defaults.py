"""Settings-by-default sub-router (C-1, ADR-0006).

Handles:
- ``adm_defs:*``      -- render the "settings by default" screen (replaces
  the Stage 3.1.4 placeholder that used to live in ``admin.py`` as
  ``handle_defaults_placeholder``)
- ``adm_defs_tgl:*``  -- toggle one bool default field

Scoped to ``settings_fields.new_fields()`` (11 fields) -- see ADR-0006's C-1
consequence to Decision 2 and the module docstring in
``src/bot/settings_fields.py`` for why the 13 legacy migration-001 columns
are excluded (C-2, deferred tech debt: the defaults screen would otherwise
lie for them, since a per-chat row already shadows ``bot_config.default_*``
for every chat the bot has already seen).

Every write goes through ``BotConfigRepository.set(f"default_{key}", value)``
and calls ``chat_config_service.invalidate_all()`` -- **not**
``invalidate(chat_id)`` -- because a default change hits the shared
``_global_cache`` (``src/services/chat_config.py``), which per-chat
``invalidate()`` never touches. This is B-1's per-chat write path's mirror
image, easy to get backwards by analogy.

See docs/decisions/ADR-0006-chat-settings-panel-architecture.md.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.admin_defaults import defaults_keyboard
from src.bot.settings_fields import FieldType, field_by_code, new_fields
from src.bot.utils import check_admin_direct, safe_edit_text
from src.database.repositories.bot_config import BotConfigRepository
from src.models.chat_config import ChatConfig
from src.services.chat_config import ChatConfigService

logger = structlog.get_logger(__name__)

router = Router(name="admin_defaults")

_DEFAULTS_TITLE = {
    "ru": "⚙️ Настройки по умолчанию для новых чатов",
    "en": "⚙️ Default settings for new chats",
}
_NOT_ADMIN = {"ru": "Нет доступа.", "en": "Access denied."}
_INVALID_FIELD = {"ru": "Некорректное поле.", "en": "Invalid field."}
_TOGGLE_ON = {"ru": "Включено", "en": "Enabled"}
_TOGGLE_OFF = {"ru": "Выключено", "en": "Disabled"}

# Fallback when bot_config has no explicit `default_<key>` row yet -- this is
# exactly the same fallback ChatConfigService._merge() applies for a chat
# with no chat_settings row and no global override: the ChatConfig dataclass
# defaults ARE layer 1 for every new_fields() field except
# relevancy_gate_enabled, whose layer 1 actually comes from YAML
# (config/default.yml: `relevancy_gate_enabled: true`, same value as
# ChatConfig's own default `True`) -- see ADR-0006 implementation notes.
_FALLBACK = ChatConfig(chat_id=0)


def _get_lang(raw: str | None) -> str:
    return raw if raw in ("ru", "en") else "ru"


def _is_private(callback: CallbackQuery) -> bool:
    return isinstance(callback.message, Message) and callback.message.chat.type == "private"


async def _resolve_values(bot_config_repo: BotConfigRepository) -> dict[str, object]:
    defaults = await bot_config_repo.get_defaults()
    return {
        field.key: defaults.get(field.key, getattr(_FALLBACK, field.key)) for field in new_fields()
    }


async def render_defaults_panel(
    bot_config_repo: BotConfigRepository, lang: str
) -> tuple[str, InlineKeyboardMarkup]:
    """Render the defaults screen's ``(text, keyboard)`` (ADR-0006, C-1)."""
    values = await _resolve_values(bot_config_repo)
    keyboard = defaults_keyboard(lang, values)
    return _DEFAULTS_TITLE[lang], keyboard


async def _render_and_show(
    callback: CallbackQuery, bot_config_repo: BotConfigRepository, lang: str
) -> None:
    text, keyboard = await render_defaults_panel(bot_config_repo, lang)
    if isinstance(callback.message, Message):
        await safe_edit_text(callback.message, text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("adm_defs:"))
async def handle_defaults_menu(
    callback: CallbackQuery,
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Show the "settings by default" screen."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(_NOT_ADMIN["en"], show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)

    await callback.answer()
    await _render_and_show(callback, bot_config_repo, lang)


@router.callback_query(F.data.startswith("adm_defs_tgl:"))
async def handle_defaults_toggle(
    callback: CallbackQuery,
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
) -> None:
    """Flip one bool default field (ADR-0006, C-1).

    Writes ``bot_config.default_<key>`` and calls ``invalidate_all()`` --
    the panel's own analogue to B-1's ``invalidate(chat_id)``, but for the
    shared global-defaults cache layer instead of a single chat's entry.
    """
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(_NOT_ADMIN["en"], show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        code = parts[2]
    except IndexError:
        await callback.answer("Invalid data", show_alert=True)
        return

    field = field_by_code(code)
    if field is None or field.type is not FieldType.BOOL or field.legacy:
        await callback.answer(_INVALID_FIELD[lang], show_alert=True)
        return

    values = await _resolve_values(bot_config_repo)
    new_value = not bool(values[field.key])
    await bot_config_repo.set(f"default_{field.key}", new_value)
    chat_config_service.invalidate_all()

    await callback.answer(_TOGGLE_ON[lang] if new_value else _TOGGLE_OFF[lang])
    await _render_and_show(callback, bot_config_repo, lang)
