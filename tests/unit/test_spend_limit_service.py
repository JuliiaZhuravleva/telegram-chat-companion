"""Unit tests for SpendLimitService (B-3 — daily spend limit, the admins' spend card)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.chat_config import ChatConfig
from src.services.costs.spend_limit import _DAILY_LIMIT_KEY, SENT_DAY_KEY, SpendLimitService

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
# notify_admins_if_exceeded
# ---------------------------------------------------------------------------


class _Config:
    """An in-memory ``bot_config``: ``get`` reads what ``set`` wrote."""

    def __init__(self, **values: object) -> None:
        self.values: dict[str, object] = dict(values)
        self.sets: list[tuple[str, object]] = []

    async def get(self, key: str) -> object:
        # A real read suspends; without this, "concurrent" calls never interleave
        # and the concurrency test below would pass with no lock at all.
        await asyncio.sleep(0)
        return self.values.get(key)

    async def set(self, key: str, value: object, description: str | None = None) -> None:
        self.sets.append((key, value))
        self.values[key] = value


_NOW = datetime(2026, 11, 3, 14, 5, tzinfo=UTC)


def _notifier(
    *, total: str = "1.27", limit: object = 1.0, admins: object = "111,222", **extra: object
) -> tuple[SpendLimitService, _Config]:
    config = _Config(
        **{_DAILY_LIMIT_KEY: limit, "admin_ids": admins, **extra}  # type: ignore[arg-type]
    )
    log = AsyncMock()
    log.get_total_cost = AsyncMock(return_value=Decimal(total))
    return SpendLimitService(log, config), config  # type: ignore[arg-type]


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


class TestNotifyAdminsIfExceeded:
    @pytest.mark.asyncio
    async def test_every_admin_gets_the_card_in_the_admin_language(self) -> None:
        svc, config = _notifier(admin_settings={"lang": "en"})
        bot = _bot()

        sent = await svc.notify_admins_if_exceeded(bot, now=_NOW)

        assert sent == 2
        assert [c.args[0] for c in bot.send_message.await_args_list] == [111, 222]
        call = bot.send_message.await_args
        assert call.args[1].startswith("💰 <b>The daily spend limit is exceeded</b>")
        assert "$1.27 against a limit of $1.00" in call.args[1]
        assert call.kwargs["parse_mode"] == "HTML"
        buttons = [
            b.callback_data for row in call.kwargs["reply_markup"].inline_keyboard for b in row
        ]
        assert buttons == ["adm_costs:en:24h"]
        assert config.values[SENT_DAY_KEY] == "2026-11-03"

    @pytest.mark.asyncio
    async def test_once_per_utc_day(self) -> None:
        svc, config = _notifier()
        bot = _bot()

        await svc.notify_admins_if_exceeded(bot, now=_NOW)
        again = await svc.notify_admins_if_exceeded(bot, now=_NOW.replace(hour=23, minute=59))
        next_day = await svc.notify_admins_if_exceeded(bot, now=datetime(2026, 11, 4, tzinfo=UTC))

        assert again == 0
        assert next_day == 2
        assert bot.send_message.await_count == 4
        assert [v for k, v in config.sets if k == SENT_DAY_KEY] == ["2026-11-03", "2026-11-04"]

    @pytest.mark.asyncio
    async def test_a_day_already_claimed_skips_the_cost_query(self) -> None:
        """After the card is out, each reply costs one bot_config read, no aggregate."""
        svc, _ = _notifier(**{SENT_DAY_KEY: "2026-11-03"})
        bot = _bot()

        assert await svc.notify_admins_if_exceeded(bot, now=_NOW) == 0

        svc._response_log.get_total_cost.assert_not_awaited()  # type: ignore[attr-defined]
        bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_concurrent_replies_send_it_once(self) -> None:
        svc, _ = _notifier()
        bot = _bot()

        results = await asyncio.gather(
            *(svc.notify_admins_if_exceeded(bot, now=_NOW) for _ in range(5))
        )

        assert sorted(results) == [0, 0, 0, 0, 2]
        assert bot.send_message.await_count == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("total", "limit"),
        [("0.99", 1.0), ("1.00", 1.0), ("99", None)],
        ids=["under", "exactly-at", "no-limit"],
    )
    async def test_nothing_when_not_over(self, total: str, limit: object) -> None:
        svc, config = _notifier(total=total, limit=limit)
        bot = _bot()

        assert await svc.notify_admins_if_exceeded(bot, now=_NOW) == 0

        bot.send_message.assert_not_awaited()
        assert config.sets == [], "a day with nothing sent must not be claimed"

    @pytest.mark.asyncio
    async def test_no_admins_claims_nothing(self) -> None:
        svc, config = _notifier(admins=None)

        assert await svc.notify_admins_if_exceeded(_bot(), now=_NOW) == 0
        assert config.sets == []

    @pytest.mark.asyncio
    async def test_a_failed_send_still_reaches_the_other_admins_and_is_not_retried(self) -> None:
        svc, _ = _notifier()
        bot = _bot()
        bot.send_message = AsyncMock(side_effect=[RuntimeError("blocked"), None])

        assert await svc.notify_admins_if_exceeded(bot, now=_NOW) == 1
        assert await svc.notify_admins_if_exceeded(bot, now=_NOW) == 0
        assert bot.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_a_database_error_is_swallowed(self) -> None:
        config = AsyncMock()
        config.get = AsyncMock(side_effect=RuntimeError("DB down"))
        svc = SpendLimitService(AsyncMock(), config)
        bot = _bot()

        assert await svc.notify_admins_if_exceeded(bot, now=_NOW) == 0
        bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_bot_no_card(self) -> None:
        svc, config = _notifier()

        assert await svc.notify_admins_if_exceeded(None, now=_NOW) == 0
        assert config.sets == []


class TestFinishReplyCallSite:
    """The reply path is where the card used to go wrong: into the chat, every reply."""

    @staticmethod
    def _message() -> MagicMock:
        message = MagicMock()
        message.chat.id = -1009000000001
        message.answer = AsyncMock()
        message.answer_sticker = AsyncMock()
        message.bot = _bot()
        return message

    @pytest.mark.asyncio
    async def test_an_overrun_goes_to_the_admins_not_into_the_chat(self) -> None:
        from src.bot.reply_flow import finish_reply
        from src.services.text.pipeline import PipelineResult

        svc, _ = _notifier()
        pipeline = MagicMock()
        order: list[str] = []
        pipeline.post_send = AsyncMock(side_effect=lambda *_a, **_k: order.append("post_send"))
        message = self._message()
        message.bot.send_message = AsyncMock(side_effect=lambda *_a, **_k: order.append("card"))

        for _ in range(3):
            await finish_reply(
                message=message,
                result=PipelineResult(should_respond=True),
                sent_message_id=77,
                chat_config=ChatConfig(chat_id=-1009000000001),
                pipeline=pipeline,
                spend_limit_svc=svc,
            )

        message.answer.assert_not_awaited()
        assert [c.args[0] for c in message.bot.send_message.await_args_list] == [111, 222]
        # post_send writes the cost row the check reads, so it comes first.
        assert order[:3] == ["post_send", "card", "card"]

    @pytest.mark.asyncio
    async def test_a_raising_service_does_not_break_the_reply(self) -> None:
        from src.bot.reply_flow import finish_reply
        from src.services.text.pipeline import PipelineResult

        svc = MagicMock()
        svc.notify_admins_if_exceeded = AsyncMock(side_effect=RuntimeError("boom"))
        message = self._message()

        await finish_reply(
            message=message,
            result=PipelineResult(should_respond=True),
            sent_message_id=77,
            chat_config=ChatConfig(chat_id=-1009000000001),
            pipeline=MagicMock(post_send=AsyncMock()),
            spend_limit_svc=svc,
        )

        message.answer.assert_not_awaited()


@pytest.mark.parametrize(
    ("total", "limit", "shown"),
    [
        # Over by less than half a cent: plain rounding printed "$1.00 / $1.00".
        ("1.004", "1.00", "$1.01 при лимите $1.00"),
        # A limit with sub-cent digits is never shown above what it is.
        ("1.0101", "1.009", "$1.02 при лимите $1.00"),
        # Whole cents are shown as they are.
        ("1.27", "1.00", "$1.27 при лимите $1.00"),
    ],
)
def test_an_exceeded_card_never_shows_the_total_at_or_under_the_limit(
    total: str, limit: str, shown: str
) -> None:
    from src.bot.screens import cards
    from src.bot.screens.render import render_text

    text = render_text(cards.spend_card("ru", total=Decimal(total), limit=Decimal(limit)))

    assert shown in text
