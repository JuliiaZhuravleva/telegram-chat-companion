"""
Integration tests: kb_enabled 4-point consistency (YAML -> ChatConfig ->
_CHAT_CONFIG_FIELDS -> _WRITABLE_COLUMNS) against real Postgres.

A3's unit tests already cover each of the 4 points in isolation with mocked
repos (``test_config.py`` for the YAML leg, ``test_chat_config_service.py`` for
the merge, ``test_repositories.py`` for ``_WRITABLE_COLUMNS``). This file is
A6's real-DB, end-to-end complement: writes actually land in migration 014's
``chat_settings.kb_enabled`` column via the real ``ChatSettingsRepository``, and
``ChatConfigService`` (real merge logic, real ``BotConfigRepository``) resolves
the effective ``kb_enabled`` value exactly as the running bot would.
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.config import BotSettings
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService

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
    # kb_enabled has no YAML leg (it's a per-chat opt-in, no site-wide default in
    # config/default.yml -- A3's YAML point only registers the module, not a
    # BotSettings field) so cache_ttl is irrelevant; keep it short for safety.
    return ChatConfigService(
        yaml_settings=BotSettings(),
        bot_config_repo=bot_config_repo,
        chat_settings_repo=chat_settings_repo,
        cache_ttl=60.0,
    )


# ---------------------------------------------------------------------------
# Default (no overrides anywhere)
# ---------------------------------------------------------------------------


class TestKbEnabledDefault:
    @pytest.mark.asyncio
    async def test_defaults_false_with_no_chat_settings_row(
        self, service: ChatConfigService
    ) -> None:
        config = await service.get_config(-920001)
        assert config.kb_enabled is False


# ---------------------------------------------------------------------------
# Per-chat override (the real-world path: adm_kb_toggle handler)
# ---------------------------------------------------------------------------


class TestKbEnabledChatOverride:
    @pytest.mark.asyncio
    async def test_chat_level_true_flows_into_effective_config(
        self,
        service: ChatConfigService,
        chat_settings_repo: ChatSettingsRepository,
    ) -> None:
        chat_id = -920002
        await chat_settings_repo.upsert(chat_id, kb_enabled=True)

        config = await service.get_config(chat_id)
        assert config.kb_enabled is True

    @pytest.mark.asyncio
    async def test_toggle_off_flows_through(
        self,
        service: ChatConfigService,
        chat_settings_repo: ChatSettingsRepository,
    ) -> None:
        chat_id = -920003
        await chat_settings_repo.upsert(chat_id, kb_enabled=True)
        assert (await service.get_config(chat_id)).kb_enabled is True

        # Simulates adm_kb_toggle flipping it back off.
        await chat_settings_repo.set_field(chat_id, "kb_enabled", False)
        service.invalidate(chat_id)  # same invalidation the real handler performs

        config = await service.get_config(chat_id)
        assert config.kb_enabled is False


# ---------------------------------------------------------------------------
# Global default (bot_config `default_kb_enabled`) interaction
# ---------------------------------------------------------------------------


class TestKbEnabledGlobalDefaultInteraction:
    @pytest.mark.asyncio
    async def test_global_default_applies_when_no_chat_settings_row(
        self,
        service: ChatConfigService,
        bot_config_repo: BotConfigRepository,
    ) -> None:
        await bot_config_repo.set("default_kb_enabled", True)
        config = await service.get_config(-920004)
        assert config.kb_enabled is True

    @pytest.mark.asyncio
    async def test_existing_chat_row_shadows_global_default_even_at_column_default(
        self,
        service: ChatConfigService,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
    ) -> None:
        """FINDING (flagged for backend-dev, not a blocker for this item):
        every sibling per-chat boolean toggle (`rag_enabled`, `abuse_filter_enabled`,
        `sticker_learning_enabled`, `save_messages`, `rules_enabled`) is declared
        nullable (`BOOLEAN DEFAULT ...`) in its migration, so a chat_settings row
        can exist with that column still NULL -- letting the global `bot_config`
        default (layer 2) take effect until a chat explicitly opts in/out.
        `kb_enabled` (migration 014) is declared `BOOLEAN NOT NULL DEFAULT false`
        -- the ONLY per-chat toggle column with a NOT NULL constraint. That means
        as soon as *any* chat_settings row exists for a chat (e.g. via
        `ensure_exists`, which every chat gets on first bot interaction,
        unrelated to KB), `chat_row["kb_enabled"]` is always present (never NULL)
        and `ChatConfigService._merge()`'s per-chat layer unconditionally wins --
        silently making `default_kb_enabled` in `bot_config` dead for any chat
        that has ever interacted with the bot before an admin sets a global
        default. This test documents the CURRENT (surprising) behavior; it is
        not asserting this is correct. Recommend backend-dev align the column
        to `BOOLEAN DEFAULT false` (nullable) to match the other 5 toggles'
        precedent if a global rollout default is ever intended to work for
        already-onboarded chats.
        """
        chat_id = -920005
        await chat_settings_repo.ensure_exists(chat_id)  # unrelated onboarding write
        await bot_config_repo.set("default_kb_enabled", True)

        config = await service.get_config(chat_id)
        # Documents the shadowing: global True is silently overridden by the
        # NOT-NULL column's implicit False, unlike every other toggle.
        assert config.kb_enabled is False
