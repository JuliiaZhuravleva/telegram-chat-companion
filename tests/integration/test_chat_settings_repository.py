"""
Integration tests: ChatSettingsRepository against real Postgres.

Covers the A-1 acceptance criteria for chat_settings:
  - ensure_exists: insert-or-COALESCE-update (idempotent, title update semantics)
  - upsert: multi-field writes, ON CONFLICT updates
  - get: returns dict or None
  - list_enabled: filters by enabled=true
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.chat_settings import ChatSettingsRepository

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def repo(db_conn: asyncpg.Connection) -> ChatSettingsRepository:
    return ChatSettingsRepository(db_conn)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ensure_exists
# ---------------------------------------------------------------------------


class TestEnsureExists:
    """ensure_exists creates the row if absent and applies COALESCE semantics."""

    @pytest.mark.asyncio
    async def test_creates_row_if_absent(self, repo: ChatSettingsRepository) -> None:
        row = await repo.ensure_exists(chat_id=-100111, chat_title="Test Group")
        assert row["chat_id"] == -100111
        assert row["chat_title"] == "Test Group"

    @pytest.mark.asyncio
    async def test_row_has_default_enabled_false(self, repo: ChatSettingsRepository) -> None:
        row = await repo.ensure_exists(chat_id=-100222)
        assert row["enabled"] is False

    @pytest.mark.asyncio
    async def test_idempotent_second_call_preserves_existing_title(
        self, repo: ChatSettingsRepository
    ) -> None:
        """Calling ensure_exists twice: second call with None title keeps the original title."""
        await repo.ensure_exists(chat_id=-100333, chat_title="Original Title")
        # Second call with no title — COALESCE keeps the original
        row = await repo.ensure_exists(chat_id=-100333, chat_title=None)
        assert row["chat_title"] == "Original Title"

    @pytest.mark.asyncio
    async def test_updates_title_when_new_value_provided(
        self, repo: ChatSettingsRepository
    ) -> None:
        """Second call with a non-NULL title should overwrite the old value."""
        await repo.ensure_exists(chat_id=-100444, chat_title="Old Title")
        row = await repo.ensure_exists(chat_id=-100444, chat_title="New Title")
        assert row["chat_title"] == "New Title"

    @pytest.mark.asyncio
    async def test_stores_chat_type(self, repo: ChatSettingsRepository) -> None:
        row = await repo.ensure_exists(chat_id=-100555, chat_type="supergroup")
        assert row["chat_type"] == "supergroup"

    @pytest.mark.asyncio
    async def test_is_forum_defaults_to_null_and_coalesces(
        self, repo: ChatSettingsRepository
    ) -> None:
        """TD-102: NULL means "not yet observed"; a caller that doesn't know
        (None) must not erase what an informed caller wrote."""
        row = await repo.ensure_exists(chat_id=-100666)
        assert row["is_forum"] is None

        row = await repo.ensure_exists(chat_id=-100666, is_forum=True)
        assert row["is_forum"] is True

        # None (caller doesn't know) keeps the stored value
        row = await repo.ensure_exists(chat_id=-100666, is_forum=None)
        assert row["is_forum"] is True

    @pytest.mark.asyncio
    async def test_is_forum_false_overwrites_true(self, repo: ChatSettingsRepository) -> None:
        """A chat that switched forum mode off must be corrected — False is a
        real observation, not an absence (the middleware coerces with bool())."""
        await repo.ensure_exists(chat_id=-100777, is_forum=True)
        row = await repo.ensure_exists(chat_id=-100777, is_forum=False)
        assert row["is_forum"] is False


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


class TestUpsert:
    """upsert writes arbitrary allowed columns; rejects unknown columns."""

    @pytest.mark.asyncio
    async def test_creates_row_with_enabled_true(self, repo: ChatSettingsRepository) -> None:
        await repo.upsert(-100601, enabled=True, chat_title="Whitelisted")
        row = await repo.get(-100601)
        assert row is not None
        assert row["enabled"] is True
        assert row["chat_title"] == "Whitelisted"

    @pytest.mark.asyncio
    async def test_updates_existing_row(self, repo: ChatSettingsRepository) -> None:
        await repo.upsert(-100602, enabled=False)
        await repo.upsert(-100602, enabled=True)
        row = await repo.get(-100602)
        assert row is not None
        assert row["enabled"] is True

    @pytest.mark.asyncio
    async def test_no_fields_creates_row_with_defaults(self, repo: ChatSettingsRepository) -> None:
        await repo.upsert(-100603)
        row = await repo.get(-100603)
        assert row is not None
        assert row["enabled"] is False  # DB default

    @pytest.mark.asyncio
    async def test_rejects_unknown_column(self, repo: ChatSettingsRepository) -> None:
        with pytest.raises(ValueError, match="Unknown chat_settings columns"):
            await repo.upsert(-100604, nonexistent_column=True)

    @pytest.mark.asyncio
    async def test_updates_trigger_words(self, repo: ChatSettingsRepository) -> None:
        await repo.upsert(-100605, trigger_words=["hey", "yo"])
        row = await repo.get(-100605)
        assert row is not None
        assert list(row["trigger_words"]) == ["hey", "yo"]


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


class TestGet:
    @pytest.mark.asyncio
    async def test_returns_none_for_missing_chat(self, repo: ChatSettingsRepository) -> None:
        result = await repo.get(-999999999)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_dict_for_existing_chat(self, repo: ChatSettingsRepository) -> None:
        await repo.ensure_exists(-100701)
        result = await repo.get(-100701)
        assert isinstance(result, dict)
        assert result["chat_id"] == -100701


# ---------------------------------------------------------------------------
# list_enabled
# ---------------------------------------------------------------------------


class TestListEnabled:
    @pytest.mark.asyncio
    async def test_returns_only_enabled_chats(self, repo: ChatSettingsRepository) -> None:
        # One enabled, one disabled
        await repo.upsert(-100801, enabled=True)
        await repo.upsert(-100802, enabled=False)

        enabled = await repo.list_enabled()
        assert -100801 in enabled
        assert -100802 not in enabled

    @pytest.mark.asyncio
    async def test_empty_when_none_enabled(self, repo: ChatSettingsRepository) -> None:
        await repo.upsert(-100901, enabled=False)
        await repo.upsert(-100902, enabled=False)

        enabled = await repo.list_enabled()
        # Neither of our test chats should appear
        assert -100901 not in enabled
        assert -100902 not in enabled

    @pytest.mark.asyncio
    async def test_multiple_enabled_chats(self, repo: ChatSettingsRepository) -> None:
        await repo.upsert(-101001, enabled=True)
        await repo.upsert(-101002, enabled=True)
        await repo.upsert(-101003, enabled=True)

        enabled = await repo.list_enabled()
        assert {-101001, -101002, -101003}.issubset(set(enabled))


# ---------------------------------------------------------------------------
# set_field
# ---------------------------------------------------------------------------


class TestSetField:
    @pytest.mark.asyncio
    async def test_updates_single_field(self, repo: ChatSettingsRepository) -> None:
        await repo.ensure_exists(-110001)
        await repo.set_field(-110001, "language", "en")

        row = await repo.get(-110001)
        assert row is not None
        assert row["language"] == "en"

    @pytest.mark.asyncio
    async def test_rejects_unknown_field(self, repo: ChatSettingsRepository) -> None:
        await repo.ensure_exists(-110002)
        with pytest.raises(ValueError, match="Unknown chat_settings column"):
            await repo.set_field(-110002, "bogus_field", "value")
