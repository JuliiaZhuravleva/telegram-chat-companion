"""Unit tests for SpendLimitService (B-3 — daily spend limit + warning)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.costs.spend_limit import SpendLimitService, _DAILY_LIMIT_KEY, _WARNING_TEXT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    *,
    daily_limit_raw: object = None,
    today_total: Decimal = Decimal("0"),
) -> SpendLimitService:
    """Build a SpendLimitService backed by async mocks.

    ``daily_limit_raw`` is what ``BotConfigRepository.get()`` returns (None
    means key not set; a number like 1.0 is the JSON-decoded value).
    ``today_total`` is what ``ResponseLogRepository.get_total_cost()`` returns.
    """
    bot_config_repo = AsyncMock()
    bot_config_repo.get = AsyncMock(return_value=daily_limit_raw)

    response_log_repo = AsyncMock()
    response_log_repo.get_total_cost = AsyncMock(return_value=today_total)

    return SpendLimitService(response_log_repo, bot_config_repo)


# ---------------------------------------------------------------------------
# get_daily_limit
# ---------------------------------------------------------------------------


class TestGetDailyLimit:
    @pytest.mark.asyncio
    async def test_returns_none_when_not_configured(self):
        svc = _make_service(daily_limit_raw=None)
        assert await svc.get_daily_limit() is None

    @pytest.mark.asyncio
    async def test_returns_decimal_for_float_value(self):
        svc = _make_service(daily_limit_raw=1.5)
        result = await svc.get_daily_limit()
        assert result == Decimal("1.5")

    @pytest.mark.asyncio
    async def test_returns_decimal_for_int_value(self):
        svc = _make_service(daily_limit_raw=2)
        result = await svc.get_daily_limit()
        assert result == Decimal("2")

    @pytest.mark.asyncio
    async def test_returns_decimal_for_string_value(self):
        """Config values may arrive as strings (e.g. from JSON deserialization)."""
        svc = _make_service(daily_limit_raw="3.0")
        result = await svc.get_daily_limit()
        assert result == Decimal("3.0")

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_value(self):
        """Non-numeric value in bot_config → logged warning + None."""
        svc = _make_service(daily_limit_raw="not-a-number")
        result = await svc.get_daily_limit()
        assert result is None

    @pytest.mark.asyncio
    async def test_queries_correct_key(self):
        svc = _make_service(daily_limit_raw=1.0)
        await svc.get_daily_limit()
        svc._bot_config.get.assert_awaited_once_with(_DAILY_LIMIT_KEY)


# ---------------------------------------------------------------------------
# get_today_total
# ---------------------------------------------------------------------------


class TestGetTodayTotal:
    @pytest.mark.asyncio
    async def test_returns_total_from_repo(self):
        svc = _make_service(today_total=Decimal("0.0250"))
        result = await svc.get_today_total()
        assert result == Decimal("0.0250")

    @pytest.mark.asyncio
    async def test_queries_24h_interval(self):
        from datetime import timedelta

        svc = _make_service(today_total=Decimal("0"))
        await svc.get_today_total()
        call_args = svc._response_log.get_total_cost.call_args.args
        assert call_args[0] == timedelta(hours=24)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


class TestCheck:
    @pytest.mark.asyncio
    async def test_no_limit_returns_false_exceeded(self):
        svc = _make_service(daily_limit_raw=None, today_total=Decimal("99.99"))
        limit, total, exceeded = await svc.check()
        assert limit is None
        assert total == Decimal("99.99")
        assert exceeded is False

    @pytest.mark.asyncio
    async def test_under_limit_returns_false_exceeded(self):
        svc = _make_service(daily_limit_raw=1.0, today_total=Decimal("0.50"))
        limit, total, exceeded = await svc.check()
        assert limit == Decimal("1.0")
        assert total == Decimal("0.50")
        assert exceeded is False

    @pytest.mark.asyncio
    async def test_exactly_at_limit_returns_false(self):
        """Equal to limit is NOT exceeded (strict >)."""
        svc = _make_service(daily_limit_raw=1.0, today_total=Decimal("1.0"))
        _, _, exceeded = await svc.check()
        assert exceeded is False

    @pytest.mark.asyncio
    async def test_over_limit_returns_true_exceeded(self):
        svc = _make_service(daily_limit_raw=1.0, today_total=Decimal("1.0001"))
        limit, total, exceeded = await svc.check()
        assert limit == Decimal("1.0")
        assert exceeded is True

    @pytest.mark.asyncio
    async def test_over_limit_by_large_margin(self):
        svc = _make_service(daily_limit_raw=0.5, today_total=Decimal("5.0"))
        _, _, exceeded = await svc.check()
        assert exceeded is True


# ---------------------------------------------------------------------------
# get_warning_if_exceeded
# ---------------------------------------------------------------------------


class TestGetWarningIfExceeded:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_limit(self):
        svc = _make_service(daily_limit_raw=None, today_total=Decimal("99.99"))
        result = await svc.get_warning_if_exceeded("en")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_under_limit(self):
        svc = _make_service(daily_limit_raw=2.0, today_total=Decimal("0.10"))
        result = await svc.get_warning_if_exceeded("en")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_at_limit(self):
        svc = _make_service(daily_limit_raw=1.0, today_total=Decimal("1.0"))
        result = await svc.get_warning_if_exceeded("en")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_english_warning_when_exceeded(self):
        svc = _make_service(daily_limit_raw=1.0, today_total=Decimal("1.5"))
        result = await svc.get_warning_if_exceeded("en")
        assert result is not None
        assert "1.5000" in result
        assert "1.0000" in result
        assert "exceeded" in result.lower()
        assert "⚠️" in result

    @pytest.mark.asyncio
    async def test_returns_russian_warning_when_exceeded(self):
        svc = _make_service(daily_limit_raw=1.0, today_total=Decimal("2.0"))
        result = await svc.get_warning_if_exceeded("ru")
        assert result is not None
        assert "2.0000" in result
        assert "1.0000" in result
        # Russian warning contains Cyrillic
        assert any(ord(c) > 127 for c in result)
        assert "⚠️" in result

    @pytest.mark.asyncio
    async def test_defaults_to_russian_for_unknown_lang(self):
        svc = _make_service(daily_limit_raw=1.0, today_total=Decimal("2.0"))
        result = await svc.get_warning_if_exceeded("de")  # unsupported
        # Falls back to Russian
        assert result == _WARNING_TEXT["ru"].format(total=2.0, limit=1.0)

    @pytest.mark.asyncio
    async def test_defaults_to_russian_when_lang_omitted(self):
        svc = _make_service(daily_limit_raw=1.0, today_total=Decimal("2.0"))
        result = await svc.get_warning_if_exceeded()
        assert result == _WARNING_TEXT["ru"].format(total=2.0, limit=1.0)

    @pytest.mark.asyncio
    async def test_returns_none_on_internal_exception(self):
        """Service never raises — errors are swallowed and None is returned."""
        bot_config_repo = AsyncMock()
        bot_config_repo.get = AsyncMock(side_effect=RuntimeError("DB down"))
        response_log_repo = AsyncMock()
        response_log_repo.get_total_cost = AsyncMock(return_value=Decimal("0"))

        svc = SpendLimitService(response_log_repo, bot_config_repo)
        result = await svc.get_warning_if_exceeded("en")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_repo_total_exception(self):
        """DB error on get_total_cost → None returned, no exception raised."""
        bot_config_repo = AsyncMock()
        bot_config_repo.get = AsyncMock(return_value=1.0)
        response_log_repo = AsyncMock()
        response_log_repo.get_total_cost = AsyncMock(side_effect=RuntimeError("DB down"))

        svc = SpendLimitService(response_log_repo, bot_config_repo)
        result = await svc.get_warning_if_exceeded("en")
        assert result is None

    @pytest.mark.asyncio
    async def test_warning_contains_usd(self):
        """Warning text includes USD to make unit clear."""
        svc = _make_service(daily_limit_raw=1.0, today_total=Decimal("1.5"))
        result_en = await svc.get_warning_if_exceeded("en")
        result_ru = await svc.get_warning_if_exceeded("ru")
        assert result_en is not None and "USD" in result_en
        assert result_ru is not None and "USD" in result_ru
