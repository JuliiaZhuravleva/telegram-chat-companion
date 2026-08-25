"""
Integration tests: `chunks_enabled` end-to-end against real Postgres (S5b).

The four points this flag has to travel through — migration 032's column,
``ChatSettingsRepository._WRITABLE_COLUMNS``,
``ChatConfigService._CHAT_CONFIG_FIELDS`` and the ``ChatConfig`` field — are
each unit-tested in isolation with mocked repositories. None of those tests can
see the failure this file exists for: a name that is right in three of the four
places degrades silently. A per-chat override on a column that does not exist
is simply not read, an unwritable column raises only at runtime, and a field
missing from the merge set falls back to the dataclass default — in every case
the bot keeps working with the chat's choice quietly discarded.

That matters more here than for its siblings. This is the switch that turns the
chunk index from write-only into the bot's memory: a chat that turns it on and
is silently ignored looks exactly like a chat where retrieval found nothing,
which is precisely the state the explicit-empty notice was written to describe
honestly.

Modelled on ``test_kb_enabled_toggle.py`` deliberately — same four points, same
shape, so drift between the two per-chat opt-ins is visible.
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.config import BotSettings
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService


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


class TestChunksEnabledDefault:
    @pytest.mark.asyncio
    async def test_defaults_false_with_no_settings_row(self, service: ChatConfigService) -> None:
        """Merging this slice must change no chat's behaviour on its own."""
        config = await service.get_config(-930001)
        assert config.chunks_enabled is False

    @pytest.mark.asyncio
    async def test_onboarding_does_not_materialize_a_value(
        self,
        service: ChatConfigService,
        chat_settings_repo: ChatSettingsRepository,
        db_conn: asyncpg.Connection,
    ) -> None:
        """Nullable, no DEFAULT — so NULL still means "inherited" afterwards.

        A SQL DEFAULT here would be written by the first ``ensure_exists`` the
        chat ever triggers, permanently shadowing ``default_chunks_enabled``
        for every chat the bot has already seen. That is the trap the 13
        migration-001 columns are still stuck in, and it would make the global
        rollout lever useless on exactly the chats that matter.
        """
        chat_id = -930002
        await chat_settings_repo.ensure_exists(chat_id)

        stored = await db_conn.fetchval(
            "SELECT chunks_enabled FROM chat_settings WHERE chat_id = $1", chat_id
        )
        assert stored is None


class TestChunksEnabledChatOverride:
    @pytest.mark.asyncio
    async def test_per_chat_true_reaches_the_effective_config(
        self,
        service: ChatConfigService,
        chat_settings_repo: ChatSettingsRepository,
    ) -> None:
        chat_id = -930003
        await chat_settings_repo.upsert(chat_id, chunks_enabled=True)

        assert (await service.get_config(chat_id)).chunks_enabled is True

    @pytest.mark.asyncio
    async def test_toggling_back_off_flows_through(
        self,
        service: ChatConfigService,
        chat_settings_repo: ChatSettingsRepository,
    ) -> None:
        """The rollback path, exercised: one panel toggle, no redeploy."""
        chat_id = -930004
        await chat_settings_repo.upsert(chat_id, chunks_enabled=True)
        assert (await service.get_config(chat_id)).chunks_enabled is True

        await chat_settings_repo.set_field(chat_id, "chunks_enabled", False)
        service.invalidate(chat_id)

        assert (await service.get_config(chat_id)).chunks_enabled is False


class TestChunksEnabledGlobalDefault:
    @pytest.mark.asyncio
    async def test_global_default_turns_it_on_everywhere(
        self,
        service: ChatConfigService,
        bot_config_repo: BotConfigRepository,
    ) -> None:
        """The second step of the rollout: one row, every chat, no restart."""
        await bot_config_repo.set("default_chunks_enabled", True)

        assert (await service.get_config(-930005)).chunks_enabled is True

    @pytest.mark.asyncio
    async def test_a_chat_that_opted_out_is_not_swept_up_by_the_global_flip(
        self,
        service: ChatConfigService,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
    ) -> None:
        """An explicit per-chat False outranks the global default.

        This is what makes a staged rollout reversible per chat: if one chat's
        answers get worse, it opts out and keeps its choice through the global
        flip, instead of the choice being silently overwritten.
        """
        chat_id = -930006
        await chat_settings_repo.ensure_exists(chat_id)
        await bot_config_repo.set("default_chunks_enabled", True)
        assert (await service.get_config(chat_id)).chunks_enabled is True

        await chat_settings_repo.set_field(chat_id, "chunks_enabled", False)
        service.invalidate(chat_id)

        assert (await service.get_config(chat_id)).chunks_enabled is False
