"""The two counts the redesigned panel shows on entry buttons (F item 5).

SQL semantics — that the count agrees with the list behind it — are checked
against real Postgres in ``tests/integration/test_screen_counts.py``; here only
the contract of each method: argument passing, the None → 0 edge, and the
refusal of a status the table never holds.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.database.repositories.admin import AdminRepository
from src.database.repositories.rules import RulesRepository


class TestCountAttempts:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["pending", "rejected", "approved"])
    async def test_passes_status_and_returns_int(self, status: str):
        pool = AsyncMock()
        pool.fetchval.return_value = 3
        assert await AdminRepository(pool).count_attempts(status) == 3
        sql, arg = pool.fetchval.await_args.args
        assert arg == status
        assert "ua.status = $1" in sql
        assert "cs.enabled = true" in sql, "enabled chats are not listed, so not counted"

    @pytest.mark.asyncio
    async def test_none_is_zero(self):
        pool = AsyncMock()
        pool.fetchval.return_value = None
        assert await AdminRepository(pool).count_attempts("pending") == 0

    @pytest.mark.asyncio
    async def test_unknown_status_is_refused_before_sql(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="Invalid attempt status"):
            await AdminRepository(pool).count_attempts("Pending")
        pool.fetchval.assert_not_awaited()


class TestCountForChat:
    @pytest.mark.asyncio
    async def test_passes_chat_id(self):
        pool = AsyncMock()
        pool.fetchval.return_value = 7
        assert await RulesRepository(pool).count_for_chat(-100) == 7
        sql, arg = pool.fetchval.await_args.args
        assert arg == -100
        assert "custom_rules" in sql and "chat_id = $1" in sql

    @pytest.mark.asyncio
    async def test_none_is_zero(self):
        pool = AsyncMock()
        pool.fetchval.return_value = None
        assert await RulesRepository(pool).count_for_chat(-100) == 0
