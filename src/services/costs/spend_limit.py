"""Configurable daily AI spend limit — check, and tell the bot admins on overrun.

The daily limit is stored in ``bot_config`` under the key
``daily_spend_limit_usd`` as a JSON number (e.g. ``1.5``).  When the key
is absent or ``null`` the limit is off and nobody is told anything.

An overrun goes to the bot admins as a card in their DM (``card.spend``), at
most once per UTC day, and never into the chat whose message happened to tip
the total over: the members there can do nothing about it, and the warning
used to repeat under every reply for the rest of the day. The day the card
went out is remembered in ``bot_config`` under ``spend_card_sent_day``, so a
restart does not send it again. The total it compares is the same day's: the
spend since 00:00 UTC, not a rolling 24 h — a rolling total still holds last
evening's overrun after midnight, which claimed a new day and sent the same
card twice.

Example — set a $2/day limit via the admin bot-config API::

    await bot_config_repo.set(
        "daily_spend_limit_usd", 2.0, description="Daily AI spend cap (USD)"
    )
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog

from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.response_log import ResponseLogRepository
from src.utils import parse_admin_ids

logger = structlog.get_logger(__name__)

_DAILY_LIMIT_KEY = "daily_spend_limit_usd"
SENT_DAY_KEY = "spend_card_sent_day"
"""``bot_config`` key: the UTC date (``YYYY-MM-DD``) the spend card last went out."""

# One process, many concurrent replies: without this, two replies finishing
# together both read "not sent today" and every admin gets the card twice. A
# module-level lock because the service itself is built per request.
_CARD_LOCK = asyncio.Lock()


def _utc(now: datetime | None) -> datetime:
    """``now`` (default: the clock) on the UTC axis; a naive value is taken as UTC."""
    moment = now or datetime.now(UTC)
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


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

    async def get_today_total(self, now: datetime | None = None) -> Decimal:
        """Return today's total AI spend from ``response_log``: since 00:00 UTC.

        ``now`` fixes the moment (default: the clock); the window is the UTC
        day it falls in, the same day `notify_admins_if_exceeded` claims.
        """
        moment = _utc(now)
        midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        # get_total_cost counts ``created_at > NOW() - interval``: the interval
        # since midnight puts the window's start at midnight.
        since_midnight: timedelta = moment - midnight
        return await self._response_log.get_total_cost(since_midnight)

    async def check(self, now: datetime | None = None) -> tuple[Decimal | None, Decimal, bool]:
        """Return ``(limit, today_total, is_exceeded)``.

        If no limit is configured, ``is_exceeded`` is always ``False``.
        Queries are issued concurrently for efficiency.
        """
        import asyncio

        limit_task = asyncio.ensure_future(self.get_daily_limit())
        total_task = asyncio.ensure_future(self.get_today_total(now))

        limit, today_total = await asyncio.gather(limit_task, total_task)

        if limit is None:
            return None, today_total, False
        return limit, today_total, today_total > limit

    async def notify_admins_if_exceeded(self, bot: Any, *, now: datetime | None = None) -> int:
        """Send every bot admin the spend card if today's total is over the limit.

        At most once per UTC day. Returns how many admins received it, 0 when
        nothing was due. Never raises: it runs after every AI reply.

        The day is claimed BEFORE sending. If every send then fails (an admin
        blocked the bot, Telegram is down), the card is lost for the day rather
        than retried under every following reply — a retry on each message is
        how one failure becomes a hundred attempts.

        Cheap on the common path: once today's card is out, each call is one
        ``bot_config`` read and no ``response_log`` aggregate.

        The claim is the last read-or-write before sending: every read the card
        needs comes first, so a failed read leaves the day unclaimed, to be
        tried again on the next reply, instead of claimed with nothing sent.
        """
        if bot is None:
            return 0
        moment = _utc(now)
        today = moment.date().isoformat()
        try:
            async with _CARD_LOCK:
                if await self._bot_config.get(SENT_DAY_KEY) == today:
                    return 0
                limit, total, exceeded = await self.check(moment)
                if not exceeded or limit is None:
                    return 0
                admin_ids = parse_admin_ids(await self._bot_config.get("admin_ids"))
                if not admin_ids:
                    logger.warning("spend_card_no_admins", total=str(total), limit=str(limit))
                    return 0
                raw_settings = await self._bot_config.get("admin_settings")
                await self._bot_config.set(
                    SENT_DAY_KEY, today, description="UTC day the spend card was last sent"
                )
        except Exception:
            logger.warning("spend_card_check_failed", exc_info=True)
            return 0

        # Imported here: the screen package imports render, render imports
        # reply_flow, and reply_flow imports this module.
        from src.bot.screens import cards
        from src.bot.screens.render import render_text, to_markup

        screen = cards.spend_card(cards.admin_lang(raw_settings), total=total, limit=limit)
        sent = 0
        for admin_id in admin_ids:
            try:
                await bot.send_message(
                    admin_id,
                    render_text(screen),
                    parse_mode="HTML",
                    reply_markup=to_markup(screen),
                )
                sent += 1
            except Exception as exc:
                logger.warning(
                    "spend_card_send_failed", admin_id=admin_id, error_type=type(exc).__name__
                )
        logger.info(
            "spend_card_sent",
            day=today,
            sent=sent,
            admins=len(admin_ids),
            total=str(total),
            limit=str(limit),
        )
        return sent
