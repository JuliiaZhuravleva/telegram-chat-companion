"""Configurable daily AI spend limit — check and warn on overrun.

The daily limit is stored in ``bot_config`` under the key
``daily_spend_limit_usd`` as a JSON number (e.g. ``1.5``).  When the key
is absent or ``null`` the service is disabled and always returns ``None``.

Example — set a $2/day limit via the admin bot-config API::

    await bot_config_repo.set(
        "daily_spend_limit_usd", 2.0, description="Daily AI spend cap (USD)"
    )
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import structlog

from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.response_log import ResponseLogRepository

logger = structlog.get_logger(__name__)

_DAILY_LIMIT_KEY = "daily_spend_limit_usd"

# Localised warning templates.  Keys must match ChatConfig.language values.
# Dynamic parts: {total} and {limit} — both plain floats, no HTML special chars.
_WARNING_TEXT: dict[str, str] = {
    "ru": "⚠️ Дневной лимит расходов на AI превышён: ${total:.4f} / ${limit:.4f} USD.",
    "en": "⚠️ Daily AI spend limit exceeded: ${total:.4f} / ${limit:.4f} USD.",
}


class SpendLimitService:
    """Checks today's AI spend against the configurable daily limit.

    All public methods are non-critical: any internal error is logged as
    a warning and a safe default (``None`` / ``False``) is returned so
    callers are never disrupted.
    """

    def __init__(
        self,
        response_log_repo: ResponseLogRepository,
        bot_config_repo: BotConfigRepository,
    ) -> None:
        self._response_log = response_log_repo
        self._bot_config = bot_config_repo

    async def get_daily_limit(self) -> Decimal | None:
        """Return the configured daily spend cap in USD, or ``None`` if unset."""
        raw = await self._bot_config.get(_DAILY_LIMIT_KEY)
        if raw is None:
            return None
        try:
            return Decimal(str(raw))
        except Exception:
            logger.warning("Invalid daily_spend_limit_usd in bot_config", value=raw)
            return None

    async def get_today_total(self) -> Decimal:
        """Return today's total AI spend (last 24 h) from ``response_log``."""
        return await self._response_log.get_total_cost(timedelta(hours=24))

    async def check(self) -> tuple[Decimal | None, Decimal, bool]:
        """Return ``(limit, today_total, is_exceeded)``.

        If no limit is configured, ``is_exceeded`` is always ``False``.
        Queries are issued concurrently for efficiency.
        """
        import asyncio

        limit_task = asyncio.ensure_future(self.get_daily_limit())
        total_task = asyncio.ensure_future(self.get_today_total())

        limit, today_total = await asyncio.gather(limit_task, total_task)

        if limit is None:
            return None, today_total, False
        return limit, today_total, today_total > limit

    async def get_warning_if_exceeded(self, lang: str = "ru") -> str | None:
        """Return a localised warning string if the daily limit is exceeded.

        Returns ``None`` when the limit is not set, not yet exceeded, or on
        any error.  Safe to call after every AI response.
        """
        try:
            limit, today_total, is_exceeded = await self.check()
            if not is_exceeded or limit is None:
                return None
            template = _WARNING_TEXT.get(lang) or _WARNING_TEXT["ru"]
            return template.format(total=float(today_total), limit=float(limit))
        except Exception:
            logger.warning("SpendLimitService.get_warning_if_exceeded failed")
            return None
