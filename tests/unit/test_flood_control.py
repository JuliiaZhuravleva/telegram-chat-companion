"""Telegram's "retry after N" must be obeyed, not raised into a handler.

Before this middleware existed, `TelegramRetryAfter` was caught nowhere in
`src/` — and because it is a *sibling* of `TelegramBadRequest` rather than a
subclass, every carefully written `except TelegramBadRequest` stepped straight
past it. It reached the handler, killed it, and the global error handler
answers only CallbackQuery events, so the chat saw nothing.

The tests that matter here are the ones about what is NOT retried and what is
NOT waited for. A retry layer that is too eager posts a message to a group
twice; one that sleeps before failing turns a fast failure into a slow one
while holding a pooled database connection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter, TelegramServerError
from aiogram.methods import SendMessage

from src.bot.middleware.flood_control import FloodControlMiddleware


def _method() -> SendMessage:
    return SendMessage(chat_id=-100123, text="hello")


def _flood(seconds: int) -> TelegramRetryAfter:
    return TelegramRetryAfter(
        method=_method(),
        message=f"Too Many Requests: retry after {seconds}",
        retry_after=seconds,
    )


class _Recorder:
    """Records the delays asked for without spending them."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.slept.append(delay)


def _middleware(recorder: _Recorder, **kwargs) -> FloodControlMiddleware:
    # jitter pinned to zero so delay assertions are exact rather than ranged;
    # the jitter itself is exercised by test_the_wait_is_never_shorter.
    return FloodControlMiddleware(sleep=recorder, jitter=lambda: 0.0, **kwargs)


class TestPassThrough:
    @pytest.mark.asyncio
    async def test_a_successful_call_is_not_delayed_or_repeated(self) -> None:
        recorder = _Recorder()
        make_request = AsyncMock(return_value="ok")

        result = await _middleware(recorder)(make_request, MagicMock(), _method())

        assert result == "ok"
        assert make_request.await_count == 1
        assert recorder.slept == []


class TestRetriesOnlyFlood:
    """The scoping decision, and the one most likely to be widened by mistake."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            TelegramBadRequest(method=_method(), message="Bad Request: message is too long"),
            TelegramServerError(method=_method(), message="Bad Gateway"),
            RuntimeError("connection reset"),
        ],
        ids=["bad_request", "server_error", "network"],
    )
    async def test_an_ambiguous_failure_is_never_re_sent(self, error: Exception) -> None:
        """A 5xx or a dropped connection may mean the message WAS delivered.

        Re-sending it would post the same transcription to the group twice,
        which is worse than a visible failure the caller already degrades on.
        Only 429 carries Telegram's own statement that it did nothing.
        """
        recorder = _Recorder()
        make_request = AsyncMock(side_effect=error)

        with pytest.raises(type(error)):
            await _middleware(recorder)(make_request, MagicMock(), _method())

        assert make_request.await_count == 1, "an ambiguous failure must not be repeated"
        assert recorder.slept == []

    @pytest.mark.asyncio
    async def test_a_flood_limit_is_waited_out_and_the_call_succeeds(self) -> None:
        recorder = _Recorder()
        make_request = AsyncMock(side_effect=[_flood(3), "ok"])

        result = await _middleware(recorder)(make_request, MagicMock(), _method())

        assert result == "ok"
        assert make_request.await_count == 2
        assert recorder.slept == [3.5], "Telegram's 3s plus the fixed padding"


class TestBounds:
    @pytest.mark.asyncio
    async def test_a_wait_longer_than_the_cap_fails_immediately(self) -> None:
        """Sleeping and THEN failing is the worst of both.

        The handler holds a pooled database connection for the duration, and a
        reply four minutes into a conversation is not a reply.
        """
        recorder = _Recorder()
        make_request = AsyncMock(side_effect=_flood(300))

        with pytest.raises(TelegramRetryAfter):
            await _middleware(recorder, max_wait_seconds=30.0)(make_request, MagicMock(), _method())

        assert recorder.slept == [], "it must refuse the wait, not take it and then give up"
        assert make_request.await_count == 1

    @pytest.mark.asyncio
    async def test_attempts_are_bounded_and_the_error_reaches_the_caller(self) -> None:
        recorder = _Recorder()
        make_request = AsyncMock(side_effect=_flood(1))

        with pytest.raises(TelegramRetryAfter):
            await _middleware(recorder, max_attempts=3)(make_request, MagicMock(), _method())

        assert make_request.await_count == 3
        # Two sleeps for three attempts: the last failure is raised, not slept on.
        assert recorder.slept == [1.5, 1.5]

    @pytest.mark.asyncio
    async def test_the_wait_is_never_shorter_than_telegram_asked(self) -> None:
        """Waking early is what produced the flood in the first place."""
        recorder = _Recorder()
        middleware = FloodControlMiddleware(sleep=recorder, jitter=lambda: 0.75)
        make_request = AsyncMock(side_effect=[_flood(2), "ok"])

        await middleware(make_request, MagicMock(), _method())

        assert recorder.slept and all(delay >= 2.0 for delay in recorder.slept)

    def test_a_zero_attempt_budget_is_rejected_at_construction(self) -> None:
        """Otherwise the loop body never runs and every call returns nothing."""
        with pytest.raises(ValueError):
            FloodControlMiddleware(max_attempts=0)


class TestObservability:
    """A guard that leaves no trace cannot be told from one that never fires."""

    @pytest.mark.asyncio
    async def test_every_retry_is_logged_with_the_method_and_chat(self) -> None:
        recorder = _Recorder()
        make_request = AsyncMock(side_effect=[_flood(2), "ok"])

        with structlog.testing.capture_logs() as logs:
            await _middleware(recorder)(make_request, MagicMock(), _method())

        retries = [line for line in logs if line["event"] == "Flood limited; waiting and retrying"]
        assert len(retries) == 1
        assert retries[0]["method"] == "SendMessage"
        assert retries[0]["chat_id"] == -100123
        assert retries[0]["retry_after"] == 2.0

    @pytest.mark.asyncio
    async def test_giving_up_is_logged_too(self) -> None:
        recorder = _Recorder()
        make_request = AsyncMock(side_effect=_flood(1))

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(TelegramRetryAfter):
                await _middleware(recorder, max_attempts=2)(make_request, MagicMock(), _method())

        assert any("giving up" in line["event"] for line in logs)

    @pytest.mark.asyncio
    async def test_refusing_an_over_long_wait_is_logged(self) -> None:
        recorder = _Recorder()
        make_request = AsyncMock(side_effect=_flood(600))

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(TelegramRetryAfter):
                await _middleware(recorder, max_wait_seconds=30.0)(
                    make_request, MagicMock(), _method()
                )

        assert any("longer than we are willing to wait" in line["event"] for line in logs)


class TestItIsActuallyWired:
    """Registered is not firing, and a correct helper is not a used helper.

    Both of the previous classes drive `FloodControlMiddleware.__call__`
    directly, which proves the logic and nothing about whether aiogram ever
    calls it. These two close that gap from both ends: one runs a real `Bot`'s
    session chain, the other pins the registration in `main.py` so deleting
    that single line fails CI instead of silently removing flood handling.
    """

    @pytest.mark.asyncio
    async def test_a_real_session_chain_retries_a_flood_limited_send(self) -> None:
        import asyncio

        from aiogram import Bot

        bot = Bot(token="123456789:AABBccddEEffGGhhIIjjKKllMMnnOOppQQr")
        try:
            slept: list[float] = []

            async def _sleep(delay: float) -> None:
                slept.append(delay)
                await asyncio.sleep(0)

            bot.session.middleware(FloodControlMiddleware(sleep=_sleep, jitter=lambda: 0.0))

            attempts = {"n": 0}

            async def _fake_make_request(bot_, method, timeout=None):  # noqa: ANN001, ARG001
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise _flood(2)
                return "DELIVERED"

            bot.session.make_request = _fake_make_request  # type: ignore[method-assign]

            result = await bot.session(bot, SendMessage(chat_id=-100123, text="hi"))

            assert result == "DELIVERED"
            assert attempts["n"] == 2, "aiogram never routed the call through the middleware"
            assert slept == [2.5]
        finally:
            await bot.session.close()

    def test_main_registers_it_on_the_session(self) -> None:
        """The one line that makes any of this reachable in production."""
        import ast
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2] / "src" / "main.py").read_text()
        tree = ast.parse(source)

        registrations = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "middleware"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "session"
            and any(
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Name)
                and arg.func.id == "FloodControlMiddleware"
                for arg in node.args
            )
        ]
        assert registrations, (
            "src/main.py must call bot.session.middleware(FloodControlMiddleware(...)) — "
            "on the SESSION, not the dispatcher: a dispatcher middleware sees incoming "
            "updates and would never see an outgoing flood limit at all"
        )


class TestTimeBoundMethodsAreNotRetried:
    """A retry that lands after the value expires is worse than no retry.

    `sendChatAction` is the one that bites. Telegram expires a chat action
    client-side after ~5s (which is why `typing_indicator` re-sends every 4s),
    so a "typing" delivered 30s late is not late, it is wrong — and aiogram's
    `ChatActionSender._stop()` waits on its worker with no timeout, so a worker
    parked in our sleep holds `async with typing_indicator(...)` open. Measured
    before the fix: ~61s of dead wait on the voice path, beginning the instant
    transcription returned, with the transcript already in hand.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method_name", ["SendChatAction", "AnswerCallbackQuery"])
    async def test_it_fails_fast_instead_of_sleeping(self, method_name: str) -> None:
        from src.bot.middleware.flood_control import _NEVER_RETRY

        assert method_name in _NEVER_RETRY

        recorder = _Recorder()
        method = MagicMock()
        type(method).__name__ = method_name
        method.chat_id = -100123
        make_request = AsyncMock(side_effect=_flood(30))

        with pytest.raises(TelegramRetryAfter):
            await _middleware(recorder)(make_request, MagicMock(), method)

        assert recorder.slept == [], f"{method_name} must not be waited on"
        assert make_request.await_count == 1

    @pytest.mark.asyncio
    async def test_an_ordinary_send_is_still_retried(self) -> None:
        """Control: the exclusion list must not have disabled retrying wholesale."""
        recorder = _Recorder()
        make_request = AsyncMock(side_effect=[_flood(3), "ok"])

        result = await _middleware(recorder)(make_request, MagicMock(), _method())

        assert result == "ok"
        assert recorder.slept == [3.5]
