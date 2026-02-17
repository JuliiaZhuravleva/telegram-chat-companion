"""Periodic health checker — background task that monitors bot health."""

from __future__ import annotations

import asyncio
import contextlib
import html as html_lib
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import asyncpg
import structlog
from aiogram import Bot

from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.health import HealthRepository
from src.services.health.models import HealthCheckResult, HealthIssue, HealthStatus
from src.utils import parse_admin_ids

logger = structlog.get_logger(__name__)

_HEALTHCHECK_FILE = Path("/tmp/healthcheck")
_DEFAULT_INTERVAL = 300  # 5 minutes
_DEFAULT_COOLDOWN = 1800  # 30 minutes
_INITIAL_DELAY = 10  # seconds before first check


class HealthChecker:
    """Background task that runs periodic health checks.

    Not managed by Dishka — lives for the entire bot process.
    Receives pool and bot directly from main().
    """

    _FORCE_CHECK_COOLDOWN = 60.0  # seconds between on-demand checks

    def __init__(self, pool: asyncpg.Pool, bot: Bot) -> None:
        self._pool = pool
        self._bot = bot
        self._task: asyncio.Task[None] | None = None
        self._last_forced_at: float = 0.0

    async def start(self) -> None:
        """Start the health check background loop."""
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Health checker started")

    async def stop(self) -> None:
        """Stop the health check background loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Health checker stopped")

    async def run_check_now(self) -> tuple[HealthCheckResult, bool]:
        """Run an immediate health check on demand (for admin panel).

        Unlike the background loop, does NOT send alerts or write
        the Docker healthcheck file.  Enforces a 60-second cooldown
        to prevent DB flooding from rapid button clicks.

        Returns:
            (result, from_cache) — from_cache is True when cooldown was active.
        """
        now = time.monotonic()
        if now - self._last_forced_at < self._FORCE_CHECK_COOLDOWN:
            # Return the latest persisted result instead of running again
            health_repo = HealthRepository(self._pool)
            row = await health_repo.get_latest()
            if row:
                return HealthCheckResult(
                    status=HealthStatus(row["status"]),
                    db_ok=row.get("db_ok", True),
                    messages_30m=row.get("messages_30m", 0),
                    fallbacks_15m=row.get("fallbacks_15m", 0),
                    ai_provider=row.get("ai_provider"),
                    issues=[],
                ), True
        self._last_forced_at = now
        result = await self._run_check()
        health_repo = HealthRepository(self._pool)
        await health_repo.insert_log(
            status=result.status.value,
            db_ok=result.db_ok,
            messages_30m=result.messages_30m,
            fallbacks_15m=result.fallbacks_15m,
            ai_provider=result.ai_provider,
            issues=[
                {"severity": i.severity.value, "message": i.message}
                for i in result.issues
            ],
            alert_sent=False,
        )
        return result, False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Check health every N seconds."""
        await asyncio.sleep(_INITIAL_DELAY)

        while True:
            interval = _DEFAULT_INTERVAL
            try:
                config = await self._load_config()
                interval = int(config.get("health_check_interval_seconds", _DEFAULT_INTERVAL))

                if not config.get("health_check_enabled", True):
                    logger.debug("Health check disabled, skipping")
                    await asyncio.sleep(interval)
                    continue

                result = await self._run_check()
                await self._persist_result(result, config)
                self._write_healthcheck_file()

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Health check cycle failed")

            await asyncio.sleep(interval)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def _load_config(self) -> dict[str, Any]:
        """Load health-related config from bot_config table."""
        repo = BotConfigRepository(self._pool)
        config: dict[str, Any] = {}
        for key in (
            "health_check_enabled",
            "health_check_interval_seconds",
            "health_alert_cooldown_seconds",
            "admin_ids",
        ):
            val = await repo.get(key)
            if val is not None:
                config[key] = val
        return config

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    async def _run_check(self) -> HealthCheckResult:
        """Execute all health checks and build a result."""
        result = HealthCheckResult()
        health_repo = HealthRepository(self._pool)

        # 1. DB connectivity (implicit — if this fails, it's critical)
        try:
            await self._pool.fetchval("SELECT 1")
            result.db_ok = True
        except Exception as exc:
            result.db_ok = False
            result.issues.append(
                HealthIssue(
                    severity=HealthStatus.CRITICAL,
                    message=f"Database connectivity failed: {exc}",
                )
            )

        # 2. Message activity (metric only — no alert on inactivity)
        try:
            result.messages_30m = await health_repo.get_message_count_30m()
        except Exception:
            logger.warning("Failed to get message count")

        # 3. AI fallback frequency
        try:
            result.fallbacks_15m = await health_repo.get_fallback_count(
                timedelta(minutes=15),
            )
            if result.fallbacks_15m > 0:
                result.issues.append(
                    HealthIssue(
                        severity=HealthStatus.WARNING,
                        message=(
                            f"AI fallback activated {result.fallbacks_15m} "
                            f"time(s) in last 15 min"
                        ),
                    )
                )
        except Exception:
            logger.warning("Failed to get fallback count")

        # Determine overall status
        if any(i.severity == HealthStatus.CRITICAL for i in result.issues):
            result.status = HealthStatus.CRITICAL
        elif any(i.severity == HealthStatus.WARNING for i in result.issues):
            result.status = HealthStatus.WARNING
        else:
            result.status = HealthStatus.HEALTHY

        return result

    # ------------------------------------------------------------------
    # Persist & alert
    # ------------------------------------------------------------------

    async def _persist_result(
        self,
        result: HealthCheckResult,
        config: dict[str, Any],
    ) -> None:
        """Save result to DB and send alert if needed."""
        health_repo = HealthRepository(self._pool)
        should_alert = False

        if result.issues:
            cooldown = int(
                config.get("health_alert_cooldown_seconds", _DEFAULT_COOLDOWN),
            )
            last_alert = await health_repo.get_last_alert_time()
            now = time.time()

            if last_alert is None or (now - last_alert) >= cooldown:
                should_alert = True

        result.alert_sent = should_alert

        log_id = await health_repo.insert_log(
            status=result.status.value,
            db_ok=result.db_ok,
            messages_30m=result.messages_30m,
            fallbacks_15m=result.fallbacks_15m,
            ai_provider=result.ai_provider,
            issues=[
                {"severity": i.severity.value, "message": i.message}
                for i in result.issues
            ],
            alert_sent=should_alert,
        )

        if should_alert:
            await self._send_alert(result, config)

        logger.info(
            "Health check completed",
            log_id=log_id,
            status=result.status.value,
            issues=len(result.issues),
            alert_sent=should_alert,
            messages_30m=result.messages_30m,
            fallbacks_15m=result.fallbacks_15m,
        )

        # Periodic cleanup (lightweight — runs every cycle)
        try:
            deleted = await health_repo.cleanup_old_logs(keep_days=30)
            if deleted:
                logger.debug("Cleaned up old health logs", deleted=deleted)
        except Exception:
            logger.warning("Failed to clean up health logs")

    async def _send_alert(
        self,
        result: HealthCheckResult,
        config: dict[str, Any],
    ) -> None:
        """Send alert to first admin via Telegram."""
        admin_ids = parse_admin_ids(config.get("admin_ids", ""))
        if not admin_ids:
            return

        first_admin = admin_ids[0]
        text = self._format_alert(result)

        try:
            await self._bot.send_message(
                first_admin, text, parse_mode="HTML",
            )
        except Exception:
            logger.warning("Failed to send health alert", admin_id=first_admin)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_alert(result: HealthCheckResult) -> str:
        """Format HTML alert message for Telegram."""
        status_emoji = {
            HealthStatus.CRITICAL: "\U0001f6a8",
            HealthStatus.WARNING: "\u26a0\ufe0f",
            HealthStatus.HEALTHY: "\u2705",
        }
        emoji = status_emoji.get(result.status, "\u2753")

        lines = [
            f"{emoji} <b>Bot Health Alert</b>",
            f"<b>Status:</b> {result.status.value.upper()}",
            f"<b>Time:</b> {result.checked_at.strftime('%Y-%m-%d %H:%M UTC')}",
        ]

        if result.issues:
            lines.append("")
            lines.append("<b>Issues:</b>")
            for issue in result.issues:
                icon = (
                    "\U0001f525"
                    if issue.severity == HealthStatus.CRITICAL
                    else "\u26a0\ufe0f"
                )
                lines.append(f"  {icon} {html_lib.escape(issue.message)}")

        lines.append("")
        lines.append(
            f"<i>Messages (30m): {result.messages_30m}"
            f" | Fallbacks (15m): {result.fallbacks_15m}</i>",
        )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Docker healthcheck file
    # ------------------------------------------------------------------

    @staticmethod
    def _write_healthcheck_file() -> None:
        """Write timestamp to healthcheck file for Docker."""
        try:
            _HEALTHCHECK_FILE.write_text(str(time.time()))
        except OSError:
            logger.warning("Failed to write healthcheck file")
