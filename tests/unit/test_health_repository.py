"""Tests for HealthRepository with mocked asyncpg pool."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from src.database.repositories.health import HealthRepository


@pytest.fixture
def repo():
    """HealthRepository with mocked pool."""
    pool = AsyncMock()
    return HealthRepository(pool), pool


class TestInsertLog:
    @pytest.mark.asyncio
    async def test_inserts_and_returns_id(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"id": 7}

        result = await repo_.insert_log(
            status="healthy",
            db_ok=True,
            messages_30m=42,
            fallbacks_15m=0,
            ai_provider="gemini",
            issues=[],
            alert_sent=False,
        )

        assert result == 7
        pool.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_issues_as_json(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"id": 1}

        issues = [{"severity": "warning", "message": "fallback active"}]
        await repo_.insert_log(
            status="warning",
            db_ok=True,
            messages_30m=0,
            fallbacks_15m=2,
            ai_provider="gemini",
            issues=issues,
            alert_sent=True,
        )

        call_args = pool.fetchrow.call_args[0]
        # issues is 6th positional arg (after sql string)
        assert '"severity": "warning"' in call_args[6]


class TestGetLatest:
    @pytest.mark.asyncio
    async def test_returns_dict_when_row_exists(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"id": 5, "status": "healthy"}

        result = await repo_.get_latest()

        assert result == {"id": 5, "status": "healthy"}

    @pytest.mark.asyncio
    async def test_returns_none_when_no_rows(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = None

        result = await repo_.get_latest()

        assert result is None


class TestGetLastAlertTime:
    @pytest.mark.asyncio
    async def test_returns_float_when_alert_exists(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"ts": 1707580000.0}

        result = await repo_.get_last_alert_time()

        assert result == 1707580000.0

    @pytest.mark.asyncio
    async def test_returns_none_when_no_alerts(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = None

        result = await repo_.get_last_alert_time()

        assert result is None


class TestGetFallbackCount:
    @pytest.mark.asyncio
    async def test_returns_count(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 3

        result = await repo_.get_fallback_count(timedelta(minutes=15))

        assert result == 3

    @pytest.mark.asyncio
    async def test_returns_zero_on_none(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = None

        result = await repo_.get_fallback_count(timedelta(minutes=15))

        assert result == 0


class TestGetMessageCount30m:
    @pytest.mark.asyncio
    async def test_returns_count(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 12

        result = await repo_.get_message_count_30m()

        assert result == 12


class TestCleanupOldLogs:
    @pytest.mark.asyncio
    async def test_returns_deleted_count(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 5

        result = await repo_.cleanup_old_logs(keep_days=30)

        assert result == 5
