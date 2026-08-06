"""
Integration tests: settings-by-default toggle (C-1, ADR-0006) against real Postgres.

C-1's own unit tests (``test_admin_defaults_handler.py``) mock
``bot_config_repo`` and ``chat_config_service`` and only assert ``set`` /
``invalidate_all`` were *called* with the right arguments. They can't catch
the real ``invalidate(chat_id)`` vs. ``invalidate_all()`` regression the ADR
explicitly flags as "easy to get backwards by analogy" -- a mocked
``ChatConfigService`` has no real cache to accidentally leave stale for
every *other* chat. This file drives ``handle_defaults_toggle`` against a
real ``BotConfigRepository`` + a real ``ChatConfigService`` (real cache)
over a real Postgres container.

G-1 routing hint (C-1's own note, execution.md): "integration coverage
should assert the default write reaches bot_config via a real repo/DB and
that get_config() reflects it post invalidate_all() for a chat with no
per-chat override."
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio
from aiogram.types import Message

from src.bot.handlers.admin_defaults import handle_defaults_toggle
from src.config import BotSettings
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService

ADMIN_ID = 111
CHAT_ID = -930101
OTHER_CHAT_ID = -930102


def _make_callback(data: str, user_id: int = ADMIN_ID) -> MagicMock:
    """Fakes only the CallbackQuery boundary. Mirrors
    ``test_admin_defaults_handler.py``'s ``_make_callback``."""
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.type = "private"
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    return callback


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def chat_settings_repo(db_conn: asyncpg.Connection) -> ChatSettingsRepository:
    return ChatSettingsRepository(db_conn)


@pytest_asyncio.fixture
async def bot_config_repo(db_conn: asyncpg.Connection) -> BotConfigRepository:
    return BotConfigRepository(db_conn)


@pytest_asyncio.fixture
async def service(
    chat_settings_repo: ChatSettingsRepository, bot_config_repo: BotConfigRepository
) -> ChatConfigService:
    return ChatConfigService(
        yaml_settings=BotSettings(),
        bot_config_repo=bot_config_repo,
        chat_settings_repo=chat_settings_repo,
        cache_ttl=60.0,
    )


@pytest_asyncio.fixture(autouse=True)
async def _admin(bot_config_repo: BotConfigRepository) -> None:
    await bot_config_repo.set("admin_ids", [ADMIN_ID])


# ---------------------------------------------------------------------------
# Write reaches the real repository
# ---------------------------------------------------------------------------


class TestDefaultsToggleWriteReachesRepository:
    @pytest.mark.asyncio
    async def test_flip_lands_in_bot_config_default_key(
        self, bot_config_repo: BotConfigRepository, service: ChatConfigService
    ) -> None:
        # migration 008 seeds 'default_rules_enabled': false.
        callback = _make_callback("adm_defs_tgl:ru:re")  # rules_enabled

        await handle_defaults_toggle(callback, bot_config_repo, service)

        assert await bot_config_repo.get("default_rules_enabled") is True


# ---------------------------------------------------------------------------
# Cache invalidation: invalidate_all(), not a single chat (ADR-0006 gotcha)
# ---------------------------------------------------------------------------


class TestDefaultsToggleInvalidatesAllChatsNotOne:
    @pytest.mark.asyncio
    async def test_chat_with_no_override_sees_flip_post_invalidate(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
    ) -> None:
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        warm = await service.get_config(CHAT_ID)
        assert warm.rules_enabled is False  # seeded default_rules_enabled=false
        assert service.is_cached(CHAT_ID)

        await handle_defaults_toggle(_make_callback("adm_defs_tgl:ru:re"), bot_config_repo, service)

        config = await service.get_config(CHAT_ID)
        assert config.rules_enabled is True

    @pytest.mark.asyncio
    async def test_multiple_already_cached_chats_all_see_the_flip(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
    ) -> None:
        """The regression the ADR calls out: a per-chat ``invalidate(chat_id)``
        used here by analogy with B-1 would leave every OTHER already-cached
        chat showing the stale default forever (no TTL expiry within the
        test). ``invalidate_all()`` must clear the shared cache dict feeding
        every chat's next ``get_config()``."""
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        await chat_settings_repo.ensure_exists(OTHER_CHAT_ID, "Other", "group")
        await service.get_config(CHAT_ID)
        await service.get_config(OTHER_CHAT_ID)
        assert service.is_cached(CHAT_ID)
        assert service.is_cached(OTHER_CHAT_ID)

        await handle_defaults_toggle(_make_callback("adm_defs_tgl:ru:re"), bot_config_repo, service)

        assert (await service.get_config(CHAT_ID)).rules_enabled is True
        assert (await service.get_config(OTHER_CHAT_ID)).rules_enabled is True


# ---------------------------------------------------------------------------
# Layering: explicit per-chat override still outranks the global default
# ---------------------------------------------------------------------------


class TestDefaultsToggleRespectsExplicitPerChatOverride:
    @pytest.mark.asyncio
    async def test_chat_with_explicit_override_is_unaffected_by_default_flip(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
    ) -> None:
        """A default-screen toggle must not clobber a chat's own explicit
        choice -- layer 3 (per-chat) still outranks layer 2 (global default)
        after the global flip, same as before it."""
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        await chat_settings_repo.set_field(CHAT_ID, "rules_enabled", False)  # explicit choice
        service.invalidate(CHAT_ID)
        assert (await service.get_config(CHAT_ID)).rules_enabled is False

        await handle_defaults_toggle(_make_callback("adm_defs_tgl:ru:re"), bot_config_repo, service)

        # Global default flipped to True, but this chat's explicit False wins.
        assert (await service.get_config(CHAT_ID)).rules_enabled is False
