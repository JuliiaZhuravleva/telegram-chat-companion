"""Settings for every chat (ADR-0006, C-1): the ``defs`` and ``defs.section`` screens.

Handles:
- ``adm_defs:*``      -- the seven sections
- ``adm_defs_sec:*``  -- one section: a toggle per yes/no field, a line per other value
- ``adm_defs_tgl:*``  -- flip one yes/no field and show its section again

These are ``bot_config.default_*`` values, i.e. merge layer 2, NOT a template
copied into new chats: a change here takes effect immediately for every chat
whose own column is NULL, existing chats included. The screens say so, because
the earlier "defaults for new chats" wording promised the opposite and an admin
could flip a module on fleet-wide believing they were configuring the future.

Scoped to ``settings_fields.new_fields()`` -- see ADR-0006's C-1 consequence to
Decision 2 and the module docstring in ``src/bot/settings_fields.py`` for why
the 13 legacy migration-001 columns are excluded (C-2, deferred tech debt: the
screen would otherwise lie for them, since a per-chat row already shadows
``bot_config.default_*`` for every chat the bot has already seen).

Every write goes through ``BotConfigRepository.set(f"default_{key}", value)``
and calls ``chat_config_service.invalidate_all()`` -- **not**
``invalidate(chat_id)`` -- because a default change hits the shared
``_global_cache`` (``src/services/chat_config.py``), which per-chat
``invalidate()`` never touches. This is B-1's per-chat write path's mirror
image, easy to get backwards by analogy.

Access is the filters' job (`IsAdmin` and the private-chat check); a payload
these handlers cannot read is a stale button and gets `show_stale`.

See docs/decisions/ADR-0006-chat-settings-panel-architecture.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka

from src.bot.filters.admin import IsAdmin
from src.bot.screens.i18n import Tr, lang_of
from src.bot.screens.render import show
from src.bot.screens.routes import parse
from src.bot.screens.sections import SECTIONS, Section
from src.bot.screens.settings import SECTION_TOGGLES, defs_screen, defs_section_screen
from src.bot.screens.stale import show_stale
from src.bot.settings_fields import FieldType, field_by_code, new_fields
from src.database.repositories.bot_config import BotConfigRepository
from src.models.chat_config import ChatConfig
from src.services.chat_config import ChatConfigService

logger = structlog.get_logger(__name__)

router = Router(name="admin_defaults")

_TOGGLE_ON = Tr(
    "✓ Включено для всех чатов без своего значения",
    "✓ Enabled for every chat without its own value",
)
_TOGGLE_OFF = Tr(
    "✓ Выключено для всех чатов без своего значения",
    "✓ Disabled for every chat without its own value",
)

# Fallback when bot_config has no explicit `default_<key>` row yet -- this is
# exactly the same fallback ChatConfigService._merge() applies for a chat
# with no chat_settings row and no global override: the ChatConfig dataclass
# defaults ARE layer 1 for every new_fields() field except
# relevancy_gate_enabled, whose layer 1 actually comes from YAML
# (config/default.yml: `relevancy_gate_enabled: true`, same value as
# ChatConfig's own default `True`) -- see ADR-0006 implementation notes.
_FALLBACK = ChatConfig(chat_id=0)

SECTION_OF: Final[Mapping[str, Section]] = {
    code: section for section, rows in SECTION_TOGGLES.items() for row in rows for code in row
}
"""The section a toggle lives in, so a flip re-renders the screen it was tapped on."""


async def _resolve_values(bot_config_repo: BotConfigRepository) -> dict[str, object]:
    defaults = await bot_config_repo.get_defaults()
    return {
        field.key: defaults.get(field.key, getattr(_FALLBACK, field.key)) for field in new_fields()
    }


def _lang(callback: CallbackQuery) -> str:
    parts = (callback.data or "").split(":")
    return lang_of(parts[1] if len(parts) > 1 else None)


_GUARDS = (F.message.chat.type == "private", IsAdmin())


@router.callback_query(F.data.startswith("adm_defs:"), *_GUARDS)
async def handle_defaults_menu(callback: CallbackQuery, **_kwargs: Any) -> None:
    """The seven sections."""
    await show(callback, defs_screen(_lang(callback)))


@router.callback_query(F.data.startswith("adm_defs_sec:"), *_GUARDS)
async def handle_defaults_section(
    callback: CallbackQuery,
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    """One section's values for every chat without its own."""
    fields = parse(callback.data, 2)
    if fields is None or fields[1] not in SECTIONS:
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang, section = lang_of(fields[0]), SECTIONS[SECTIONS.index(fields[1])]
    values = await _resolve_values(bot_config_repo)
    await show(callback, defs_section_screen(lang, section, values))


@router.callback_query(F.data.startswith("adm_defs_tgl:"), *_GUARDS)
async def handle_defaults_toggle(
    callback: CallbackQuery,
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    **kwargs: Any,
) -> None:
    """Flip one yes/no default (ADR-0006, C-1) and show its section again.

    Writes ``bot_config.default_<key>`` and calls ``invalidate_all()`` -- the
    panel's own analogue to B-1's ``invalidate(chat_id)``, but for the shared
    global-defaults cache layer instead of a single chat's entry.
    """
    fields = parse(callback.data, 2)
    field = field_by_code(fields[1]) if fields else None
    if (
        fields is None
        or field is None
        or field.type is not FieldType.BOOL
        or field.legacy
        or field.code not in SECTION_OF
    ):
        await show_stale(callback, _lang(callback), data=kwargs)
        return
    lang = lang_of(fields[0])

    values = await _resolve_values(bot_config_repo)
    new_value = not bool(values[field.key])
    await bot_config_repo.set(f"default_{field.key}", new_value)
    chat_config_service.invalidate_all()

    values[field.key] = new_value
    toast = (_TOGGLE_ON if new_value else _TOGGLE_OFF)(lang)
    await show(callback, defs_section_screen(lang, SECTION_OF[field.code], values), toast=toast)
