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
from src.database.repositories.admin import AdminRepository
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
async def admin_repo(db_conn: asyncpg.Connection) -> AdminRepository:
    return AdminRepository(db_conn)  # type: ignore[arg-type]


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
        admin_repo: AdminRepository,
    ) -> None:
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag")  # rag_enabled

        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, service, admin_repo
        )

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
        admin_repo: AdminRepository,
    ) -> None:
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        await handle_chat_panel_toggle(
            _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag"),
            chat_settings_repo,
            bot_config_repo,
            service,
            admin_repo,
        )
        await handle_chat_panel_toggle(
            _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag"),
            chat_settings_repo,
            bot_config_repo,
            service,
            admin_repo,
        )

        row = await chat_settings_repo.get(CHAT_ID)
        assert row is not None
        assert row["rag_enabled"] is True

    @pytest.mark.asyncio
    async def test_flip_derives_from_stored_value_not_from_callers_read(
        self,
        chat_settings_repo: ChatSettingsRepository,
    ) -> None:
        """This is what makes a double-tap harmless.

        aiogram runs updates concurrently, so on a laggy client the second tap
        reads the same value as the first and both would write the same
        negation -- two taps, one flip. The negation now happens inside the
        UPDATE, so the result depends on what is *stored* when the statement
        runs, not on what the caller read earlier.

        Simulated deterministically rather than with real concurrency: the
        integration harness hands every test ONE asyncpg connection inside a
        transaction, and a single connection cannot run two statements at once
        (`another operation is in progress`) -- an `asyncio.gather` test there
        fails on the harness, not on the defect, and would pass vacuously.
        Here the caller passes a deliberately stale ``effective=True`` while
        the column already holds False, exactly the state the losing tap is in.
        """
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        await chat_settings_repo.set_field(CHAT_ID, "rag_enabled", False)

        result = await chat_settings_repo.toggle_bool_field(CHAT_ID, "rag_enabled", True)

        # Old read-modify-write would have written `not True` = False again.
        assert result is True
        row = await chat_settings_repo.get(CHAT_ID)
        assert row is not None and row["rag_enabled"] is True

    @pytest.mark.asyncio
    async def test_inherited_null_column_flips_from_the_effective_value(
        self,
        chat_settings_repo: ChatSettingsRepository,
    ) -> None:
        """NULL means "inherited", so the flip starts from the merged value the
        caller supplies -- otherwise `NOT NULL` would yield NULL and the tap
        would appear to do nothing."""
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        await chat_settings_repo.set_field(CHAT_ID, "kb_enabled", None)

        result = await chat_settings_repo.toggle_bool_field(CHAT_ID, "kb_enabled", False)

        assert result is True

    @pytest.mark.asyncio
    async def test_toggle_on_absent_row_reports_failure(
        self,
        chat_settings_repo: ChatSettingsRepository,
    ) -> None:
        """The repository contract the handler relies on: no row -> None, so a
        caller can refuse to claim success instead of silently no-opping."""
        result = await chat_settings_repo.toggle_bool_field(-999_000_111, "rag_enabled", True)
        assert result is None

    @pytest.mark.asyncio
    async def test_a_tap_for_a_chat_without_a_row_lands_on_the_real_chat_list(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
        admin_repo: AdminRepository,
    ) -> None:
        """The handler's side of that contract: nothing is written, and the
        admin sees the chat list read from the real database, with the line
        that the chat has gone, as every other panel screen does."""
        await chat_settings_repo.ensure_exists(OTHER_CHAT_ID, "Other", "group")
        await chat_settings_repo.upsert(OTHER_CHAT_ID, enabled=True)  # the list shows enabled ones
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag")

        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, service, admin_repo
        )

        assert await chat_settings_repo.get(CHAT_ID) is None
        text = callback.message.edit_text.await_args.args[0]
        assert text.startswith("<i>Этого чата нет в настройках")
        assert "Other" in str(callback.message.edit_text.await_args.kwargs["reply_markup"])


# ---------------------------------------------------------------------------
# Cache invalidation
#
# NB: `service` here is one long-lived ChatConfigService, which is what makes
# these assertions meaningful. In production the service is Dishka
# Scope.REQUEST (src/di.py), so no cache survives an update and the
# invalidation is defensive rather than corrective -- see TD-046 and the
# ADR-0006 addendum. These tests pin the invariant, not a live bug.
# ---------------------------------------------------------------------------


class TestToggleInvalidatesCache:
    @pytest.mark.asyncio
    async def test_effective_value_flips_immediately_no_stale_read(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
        admin_repo: AdminRepository,
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
            admin_repo,
        )

        config = await service.get_config(CHAT_ID)
        assert config.rag_enabled is False

    @pytest.mark.asyncio
    async def test_does_not_invalidate_other_chats_cache(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
        admin_repo: AdminRepository,
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
            admin_repo,
        )

        assert service.is_cached(OTHER_CHAT_ID)


# ---------------------------------------------------------------------------
# The knowledge base switch, now a panel toggle, against a real repo
# ---------------------------------------------------------------------------


class TestKnowledgeBaseTogglesThroughThePanelAgainstRealRepo:
    @pytest.mark.asyncio
    async def test_kb_enabled_code_now_reaches_chat_settings_write(
        self,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        service: ChatConfigService,
        admin_repo: AdminRepository,
    ) -> None:
        """Inverted on purpose. This used to prove the panel could never write
        ``kb_enabled`` (ADR-0010: the switch lived only in admin_kb.py). The
        switch now sits in the chat's memory section and toggles through the
        same ``toggle_bool_field`` as every other switch; the superseding note
        is in docs/admin-dm-internals.md. What must hold against a real
        database: a fresh row carries NULL (the column has no DEFAULT), the
        panel flips it from the value the chat actually runs with, and the
        service serves the new value after the write."""
        await chat_settings_repo.ensure_exists(CHAT_ID, "Chat", "group")
        before = await chat_settings_repo.get(CHAT_ID)
        assert before is not None
        assert before["kb_enabled"] is None
        assert (await service.get_config(CHAT_ID)).kb_enabled is False

        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:kb")
        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, service, admin_repo
        )

        after = await chat_settings_repo.get(CHAT_ID)
        assert after is not None
        assert after["kb_enabled"] is True
        assert (await service.get_config(CHAT_ID)).kb_enabled is True
        text = callback.message.edit_text.await_args.args[0]
        assert text.startswith("<i>✓ Включено: База знаний</i>")
