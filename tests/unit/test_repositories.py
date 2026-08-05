"""Tests for database repositories with mocked asyncpg pool."""

import json
from unittest.mock import AsyncMock

import pytest

from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository

# ---------------------------------------------------------------------------
# BotConfigRepository
# ---------------------------------------------------------------------------


class TestBotConfigRepository:
    """Tests with mocked asyncpg pool."""

    @pytest.fixture
    def repo(self):
        pool = AsyncMock()
        return BotConfigRepository(pool), pool

    @pytest.mark.asyncio
    async def test_get_returns_parsed_json(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"value": '"hello"'}
        result = await repo_.get("test_key")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing_key(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = None
        result = await repo_.get("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_list(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"value": '["bot", "бот"]'}
        result = await repo_.get("default_trigger_words")
        assert result == ["bot", "бот"]

    @pytest.mark.asyncio
    async def test_get_returns_number(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"value": "0.05"}
        result = await repo_.get("default_random_response_chance")
        assert result == 0.05

    @pytest.mark.asyncio
    async def test_set_calls_execute(self, repo):
        repo_, pool = repo
        await repo_.set("my_key", {"foo": "bar"}, description="test")
        pool.execute.assert_awaited_once()
        args = pool.execute.call_args[0]
        # args[0] = SQL, args[1] = key, args[2] = JSON value, args[3] = description
        assert args[1] == "my_key"
        assert json.loads(args[2]) == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_get_all_returns_dict(self, repo):
        repo_, pool = repo
        pool.fetch.return_value = [
            {"key": "k1", "value": '"v1"'},
            {"key": "k2", "value": "42"},
        ]
        result = await repo_.get_all()
        assert result == {"k1": "v1", "k2": 42}

    @pytest.mark.asyncio
    async def test_get_defaults_strips_prefix(self, repo):
        repo_, pool = repo
        pool.fetch.return_value = [
            {"key": "default_language", "value": '"ru"'},
            {"key": "default_trigger_words", "value": '["bot"]'},
        ]
        result = await repo_.get_defaults()
        assert result == {"language": "ru", "trigger_words": ["bot"]}


# ---------------------------------------------------------------------------
# ChatSettingsRepository
# ---------------------------------------------------------------------------


class TestChatSettingsRepository:
    """Tests with mocked asyncpg pool."""

    @pytest.fixture
    def repo(self):
        pool = AsyncMock()
        return ChatSettingsRepository(pool), pool

    @pytest.mark.asyncio
    async def test_get_returns_dict(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"chat_id": 123, "enabled": True}
        result = await repo_.get(123)
        assert result == {"chat_id": 123, "enabled": True}

    @pytest.mark.asyncio
    async def test_get_returns_none_for_unknown_chat(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = None
        result = await repo_.get(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_with_fields(self, repo):
        repo_, pool = repo
        await repo_.upsert(123, enabled=True, language="en")
        pool.execute.assert_awaited_once()
        sql = pool.execute.call_args[0][0]
        assert "INSERT INTO chat_settings" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_upsert_empty_fields(self, repo):
        repo_, pool = repo
        await repo_.upsert(123)
        pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_rejects_unknown_columns(self, repo):
        repo_, pool = repo
        with pytest.raises(ValueError, match="Unknown chat_settings columns"):
            await repo_.upsert(123, nonexistent_column="bad")

    @pytest.mark.asyncio
    async def test_set_field_valid(self, repo):
        repo_, pool = repo
        await repo_.set_field(123, "language", "en")
        pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_field_rejects_unknown(self, repo):
        repo_, pool = repo
        with pytest.raises(ValueError, match="Unknown chat_settings column"):
            await repo_.set_field(123, "bad_column", "value")

    @pytest.mark.asyncio
    async def test_set_field_kb_enabled(self, repo):
        """kb_enabled (A3: chat_facts KB opt-in toggle) is writable."""
        repo_, pool = repo
        await repo_.set_field(123, "kb_enabled", True)
        pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_kb_enabled(self, repo):
        repo_, pool = repo
        await repo_.upsert(123, kb_enabled=True)
        pool.execute.assert_awaited_once()
        sql = pool.execute.call_args[0][0]
        assert "kb_enabled" in sql

    @pytest.mark.asyncio
    async def test_set_field_reactions_enabled(self, repo):
        """reactions_enabled (R-1, ADR-0004) is writable."""
        repo_, pool = repo
        await repo_.set_field(123, "reactions_enabled", True)
        pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_field_reactions_history_enabled(self, repo):
        repo_, pool = repo
        await repo_.set_field(123, "reactions_history_enabled", False)
        pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_reactions_columns(self, repo):
        repo_, pool = repo
        await repo_.upsert(123, reactions_enabled=True, reactions_history_enabled=False)
        pool.execute.assert_awaited_once()
        sql = pool.execute.call_args[0][0]
        assert "reactions_enabled" in sql
        assert "reactions_history_enabled" in sql

    @pytest.mark.asyncio
    async def test_list_enabled(self, repo):
        repo_, pool = repo
        pool.fetch.return_value = [
            {"chat_id": 100},
            {"chat_id": 200},
        ]
        result = await repo_.list_enabled()
        assert result == [100, 200]
