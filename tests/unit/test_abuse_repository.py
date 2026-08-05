"""Tests for src.database.repositories.abuse — read-only cooldown probe.

The probe exists for the R-5 reaction path (ADR-0004 Decision 4), which emits a
user-visible bot action from outside `TextProcessingPipeline` and so never
reaches the pipeline's Stage 1 abuse gate.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.database.repositories.abuse import COOLDOWN_SECONDS, AbuseRepository
from src.services.abuse.checker import AntiAbuseChecker

_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "004_anti_abuse.py"


def _make_pool(fetchval_result: int | None) -> MagicMock:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=fetchval_result)
    return pool


# ---------------------------------------------------------------------------
# Drift guard
# ---------------------------------------------------------------------------


class TestCooldownConstantMatchesSql:
    def test_python_constant_equals_sql_variable(self) -> None:
        """`COOLDOWN_SECONDS` duplicates `v_cooldown_seconds` from the SQL
        function so the read-only probe answers exactly what check_anti_abuse()
        would. Nothing in Postgres enforces that, so assert it here — if the
        SQL window changes, the probe would silently disagree with the gate it
        mirrors."""
        source = _MIGRATION.read_text(encoding="utf-8")
        match = re.search(r"v_cooldown_seconds\s+INTEGER\s*:=\s*(\d+)", source)

        assert match is not None, (
            "could not find v_cooldown_seconds in 004_anti_abuse.py — "
            "the anchor moved, so this guard was silently passing on nothing"
        )
        assert int(match.group(1)) == COOLDOWN_SECONDS


# ---------------------------------------------------------------------------
# get_cooldown_remaining_seconds
# ---------------------------------------------------------------------------


class TestGetCooldownRemainingSeconds:
    @pytest.mark.asyncio
    async def test_no_row_means_no_cooldown(self) -> None:
        """No `user_response_cooldown` row -> fetchval returns None, not 0."""
        repo = AbuseRepository(_make_pool(None))
        assert await repo.get_cooldown_remaining_seconds(-100, 7) == 0

    @pytest.mark.asyncio
    async def test_returns_remaining_seconds(self) -> None:
        repo = AbuseRepository(_make_pool(4))
        assert await repo.get_cooldown_remaining_seconds(-100, 7) == 4

    @pytest.mark.asyncio
    async def test_passes_window_and_keys_to_sql(self) -> None:
        pool = _make_pool(0)
        repo = AbuseRepository(pool)

        await repo.get_cooldown_remaining_seconds(-100, 7)

        args = pool.fetchval.call_args.args
        assert args[1:] == (-100, 7, COOLDOWN_SECONDS)

    @pytest.mark.asyncio
    async def test_probe_is_read_only(self) -> None:
        """The whole point of this method: it must not run the side-effecting
        check_anti_abuse() function, which advances spam counters and penalty
        multipliers."""
        pool = _make_pool(0)
        pool.execute = AsyncMock()
        repo = AbuseRepository(pool)

        await repo.get_cooldown_remaining_seconds(-100, 7)

        pool.execute.assert_not_awaited()
        assert "check_anti_abuse" not in pool.fetchval.call_args.args[0]


# ---------------------------------------------------------------------------
# AntiAbuseChecker.is_in_cooldown
# ---------------------------------------------------------------------------


class TestIsInCooldown:
    @pytest.mark.asyncio
    async def test_true_when_seconds_remain(self) -> None:
        repo = AsyncMock()
        repo.get_cooldown_remaining_seconds = AsyncMock(return_value=3)
        assert await AntiAbuseChecker(repo).is_in_cooldown(-100, 7) is True

    @pytest.mark.asyncio
    async def test_false_when_expired(self) -> None:
        repo = AsyncMock()
        repo.get_cooldown_remaining_seconds = AsyncMock(return_value=0)
        assert await AntiAbuseChecker(repo).is_in_cooldown(-100, 7) is False
