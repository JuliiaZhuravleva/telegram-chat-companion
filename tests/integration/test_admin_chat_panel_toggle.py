"""
Integration tests: chat settings panel toggle (B-1, ADR-0006) against real Postgres.

B-1's own unit tests (``test_admin_chat_panel_handler.py``) fully mock
``chat_settings_repo`` / ``bot_config_repo`` / ``chat_config_service`` and
assert only that ``set_field`` / ``invalidate`` were *called* with the right
arguments. A mocked ``ChatConfigService`` has no real cache to accidentally
leave stale, so those tests cannot catch a wiring bug where the write lands
on the wrong column, or where the cache genuinely still serves the old value
after the "invalidation" call. This file drives ``handle_chat_panel_toggle``
against a real ``ChatSettingsRepository`` + a real ``ChatConfigService``
(real in-memory cache, not mocked) over a real Postgres container -- the
same pattern ``test_kb_enabled_toggle.py`` established for the KB module's
own toggle before this generic panel existed.

G-1 routing hint (B-1's own note, execution.md): "integration coverage
should assert the toggle write reaches chat_settings via a real repo/DB and
that chat_config_service.get_config() reflects the flip post-invalidate --
this item's [unit] tests mock both repos and the service."
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio
from aiogram.types import Message

from src.bot.handlers.admin_chat_panel import handle_chat_panel_toggle
from src.config import BotSettings
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService

ADMIN_ID = 111
CHAT_ID = -930001
OTHER_CHAT_ID = -930002


def _make_callback(data: str, user_id: int = ADMIN_ID) -> MagicMock:
    """Fakes only the CallbackQuery boundary -- everything downstream
    (repos, service, DB) is real. Mirrors
    ``test_admin_chat_panel_handler.py``'s ``_make_callback``."""
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.type = "private"
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = None
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


class TestToggleWriteReachesRepository:
    @pytest.mark.asyncio
    async def test_flip_lands_in_chat_settings_column(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
    ) -> None:
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag")  # rag_enabled

        await handle_chat_panel_toggle(callback, chat_settings_repo, bot_config_repo, service)

        row = await chat_settings_repo.get(CHAT_ID)
        assert row is not None
        # migration 001: rag_enabled BOOLEAN DEFAULT true -> toggle flips False.
        assert row["rag_enabled"] is False

    @pytest.mark.asyncio
    async def test_second_flip_toggles_back(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
    ) -> None:
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        await handle_chat_panel_toggle(
            _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag"),
            chat_settings_repo,
            bot_config_repo,
            service,
        )
        await handle_chat_panel_toggle(
            _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag"),
            chat_settings_repo,
            bot_config_repo,
            service,
        )

        row = await chat_settings_repo.get(CHAT_ID)
        assert row is not None
        assert row["rag_enabled"] is True


# ---------------------------------------------------------------------------
# Cache invalidation (the PRD's documented "up to a minute delay" bug)
# ---------------------------------------------------------------------------


class TestToggleInvalidatesCache:
    @pytest.mark.asyncio
    async def test_effective_value_flips_immediately_no_stale_read(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
    ) -> None:
        """Warm the cache first -- exactly what opening the panel does just
        before a tap -- then assert the very next read reflects the flip
        instead of the 60s-TTL stale entry."""
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        warm = await service.get_config(CHAT_ID)
        assert warm.rag_enabled is True  # dataclass/SQL default, now cached
        assert service.is_cached(CHAT_ID)

        await handle_chat_panel_toggle(
            _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag"),
            chat_settings_repo,
            bot_config_repo,
            service,
        )

        config = await service.get_config(CHAT_ID)
        assert config.rag_enabled is False

    @pytest.mark.asyncio
    async def test_does_not_invalidate_other_chats_cache(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
    ) -> None:
        """Per-chat ``invalidate(chat_id)`` must be scoped -- toggling one
        chat's field must not evict another chat's already-cached entry
        (that's C-1's ``invalidate_all()`` job, not B-1's)."""
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        await chat_settings_repo.ensure_exists(OTHER_CHAT_ID, "Other", "group")
        await service.get_config(OTHER_CHAT_ID)
        assert service.is_cached(OTHER_CHAT_ID)

        await handle_chat_panel_toggle(
            _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag"),
            chat_settings_repo,
            bot_config_repo,
            service,
        )

        assert service.is_cached(OTHER_CHAT_ID)


# ---------------------------------------------------------------------------
# Decision 2 boundary, re-verified against a real (non-mocked) repo
# ---------------------------------------------------------------------------


class TestToggleRejectsLinkOnlyFieldAgainstRealRepo:
    @pytest.mark.asyncio
    async def test_kb_enabled_code_never_reaches_chat_settings_write(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
    ) -> None:
        """KB's own toggle handler (admin_kb.py) owns this write, not the
        generic panel toggle -- the row must stay untouched even though this
        is a real repository, not a mock asserting call args."""
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        before = await chat_settings_repo.get(CHAT_ID)

        await handle_chat_panel_toggle(
            _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:kb"),
            chat_settings_repo,
            bot_config_repo,
            service,
        )

        after = await chat_settings_repo.get(CHAT_ID)
        assert after == before
