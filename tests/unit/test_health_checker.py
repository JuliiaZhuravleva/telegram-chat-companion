"""Tests for HealthChecker service."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.services.health.checker import HealthChecker
from src.services.health.models import HealthCheckResult, HealthIssue, HealthStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pool():
    """Mocked asyncpg pool."""
    return AsyncMock()


@pytest.fixture
def bot():
    """Mocked aiogram Bot."""
    b = AsyncMock()
    b.send_message = AsyncMock()
    return b


@pytest.fixture
def checker(pool, bot):
    """HealthChecker instance with mocked deps."""
    return HealthChecker(pool=pool, bot=bot)


# ---------------------------------------------------------------------------
# _run_check tests
# ---------------------------------------------------------------------------


class TestRunCheck:
    @pytest.mark.asyncio
    async def test_healthy_when_all_checks_pass(self, checker, pool):
        pool.fetchval.side_effect = [
            1,  # SELECT 1 (db check)
            0,  # message count 30m
            0,  # fallback count 15m
        ]

        result = await checker._run_check()

        assert result.status == HealthStatus.HEALTHY
        assert result.db_ok is True
        assert result.messages_30m == 0
        assert result.fallbacks_15m == 0
        assert len(result.issues) == 0

    @pytest.mark.asyncio
    async def test_critical_when_db_fails(self, checker, pool):
        pool.fetchval.side_effect = ConnectionError("connection refused")

        result = await checker._run_check()

        assert result.status == HealthStatus.CRITICAL
        assert result.db_ok is False
        assert any(
            i.severity == HealthStatus.CRITICAL and "Database" in i.message for i in result.issues
        )

    @pytest.mark.asyncio
    async def test_warning_when_fallbacks_detected(self, checker, pool):
        pool.fetchval.side_effect = [
            1,  # SELECT 1
            5,  # message count
            3,  # fallback count > 0
        ]

        result = await checker._run_check()

        assert result.status == HealthStatus.WARNING
        assert result.fallbacks_15m == 3
        assert any(
            i.severity == HealthStatus.WARNING and "fallback" in i.message.lower()
            for i in result.issues
        )


# ---------------------------------------------------------------------------
# _format_alert tests
# ---------------------------------------------------------------------------


class TestFormatAlert:
    def test_critical_alert_format(self):
        result = HealthCheckResult(
            status=HealthStatus.CRITICAL,
            checked_at=datetime(2026, 2, 10, 15, 30, tzinfo=UTC),
            db_ok=False,
            messages_30m=12,
            fallbacks_15m=3,
            issues=[
                HealthIssue(
                    severity=HealthStatus.CRITICAL,
                    message="Database connectivity failed: connection refused",
                ),
                HealthIssue(
                    severity=HealthStatus.WARNING,
                    message="AI fallback activated 3 time(s) in last 15 min",
                ),
            ],
        )

        text = HealthChecker._format_alert(result)

        assert "CRITICAL" in text
        assert "Bot Health Alert" in text
        assert "Database connectivity failed" in text
        assert "fallback" in text.lower()
        assert "Messages (30m): 12" in text

    def test_warning_alert_format(self):
        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            checked_at=datetime(2026, 2, 10, 15, 30, tzinfo=UTC),
            fallbacks_15m=1,
            issues=[
                HealthIssue(
                    severity=HealthStatus.WARNING,
                    message="AI fallback activated 1 time(s) in last 15 min",
                ),
            ],
        )

        text = HealthChecker._format_alert(result)

        assert "WARNING" in text


# ---------------------------------------------------------------------------
# Alert cooldown tests
# ---------------------------------------------------------------------------


class TestPersistResult:
    @pytest.mark.asyncio
    async def test_alert_sent_after_cooldown(self, checker, pool, bot):
        """Alert should be sent if cooldown has expired."""
        # Mock health repo methods
        pool.fetchrow.side_effect = [
            {"ts": time.time() - 3600},  # last alert 1h ago (get_last_alert_time)
            {"id": 1},  # insert_log
        ]
        pool.fetchval.side_effect = [
            0,  # cleanup_old_logs
        ]

        config = {"admin_ids": "12345", "health_alert_cooldown_seconds": 1800}
        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[
                HealthIssue(severity=HealthStatus.WARNING, message="test issue"),
            ],
        )

        await checker._persist_result(result, config)

        assert result.alert_sent is True
        bot.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_alert_not_sent_during_cooldown(self, checker, pool, bot):
        """Alert should NOT be sent if within cooldown period."""
        pool.fetchrow.side_effect = [
            {"ts": time.time() - 60},  # last alert 1 min ago
            {"id": 2},  # insert_log
        ]
        pool.fetchval.side_effect = [
            0,  # cleanup_old_logs
        ]

        config = {"admin_ids": "12345", "health_alert_cooldown_seconds": 1800}
        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[
                HealthIssue(severity=HealthStatus.WARNING, message="test issue"),
            ],
        )

        await checker._persist_result(result, config)

        assert result.alert_sent is False
        bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_alert_when_healthy(self, checker, pool, bot):
        """No alert for healthy status (no issues)."""
        pool.fetchrow.return_value = {"id": 3}
        pool.fetchval.return_value = 0

        config = {"admin_ids": "12345"}
        result = HealthCheckResult(status=HealthStatus.HEALTHY, issues=[])

        await checker._persist_result(result, config)

        assert result.alert_sent is False
        bot.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Alert target tests
# ---------------------------------------------------------------------------


class TestSendAlert:
    @pytest.mark.asyncio
    async def test_sends_to_first_admin_only(self, checker, bot):
        """Only the first admin in the list should receive the alert."""
        config = {"admin_ids": "111,222,333"}
        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[
                HealthIssue(severity=HealthStatus.WARNING, message="test"),
            ],
        )

        await checker._send_alert(result, config)

        bot.send_message.assert_awaited_once()
        call_args = bot.send_message.call_args
        assert call_args[0][0] == 111  # first admin

    @pytest.mark.asyncio
    async def test_handles_empty_admin_ids(self, checker, bot):
        """No crash when admin_ids is empty."""
        config = {"admin_ids": ""}

        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[HealthIssue(severity=HealthStatus.WARNING, message="test")],
        )

        await checker._send_alert(result, config)

        bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_admin_ids_as_list(self, checker, bot):
        """admin_ids can be a JSON list from bot_config."""
        config = {"admin_ids": [111, 222]}

        result = HealthCheckResult(
            status=HealthStatus.WARNING,
            issues=[HealthIssue(severity=HealthStatus.WARNING, message="test")],
        )

        await checker._send_alert(result, config)

        bot.send_message.assert_awaited_once()
        assert bot.send_message.call_args[0][0] == 111


# ---------------------------------------------------------------------------
# Healthcheck file
# ---------------------------------------------------------------------------


class TestHealthcheckFile:
    def test_writes_timestamp(self, checker, tmp_path):
        """Healthcheck file should contain a recent timestamp."""
        hc_file = tmp_path / "healthcheck"

        with patch(
            "src.services.health.checker._HEALTHCHECK_FILE",
            hc_file,
        ):
            checker._write_healthcheck_file()

        content = hc_file.read_text()
        ts = float(content)
        assert abs(time.time() - ts) < 5


# ---------------------------------------------------------------------------
# Health check disabled
# ---------------------------------------------------------------------------


class TestDisabledCheck:
    @pytest.mark.asyncio
    async def test_load_config_reads_keys(self, checker, pool):
        """Config loading should read expected keys."""
        pool.fetchrow.return_value = None  # no config found

        config = await checker._load_config()

        assert isinstance(config, dict)
