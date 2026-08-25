"""Honour Telegram's own "retry after N seconds" instead of crashing on it.

Telegram answers a flood-limited call with HTTP 429 and a `retry_after` field
saying exactly how long to wait. aiogram surfaces that as `TelegramRetryAfter`.
Nothing in `src/` caught it: the only place in the whole repository that ever
slept on one was an offline backfill script. In the bot it reached the handler,
and `TelegramRetryAfter` is a *sibling* of `TelegramBadRequest` rather than a
subclass, so every `except TelegramBadRequest` written to be careful stepped
straight past it. The handler then died and — because the global error handler
answers only `CallbackQuery` events — the chat saw nothing at all. That is the
same silence that lost four voice transcriptions, arriving through a different
door.

It also became likelier: a transcription that used to be one `sendMessage` is
now one per part plus a progress placeholder and its ticks.

Registered on the **session**, not on the dispatcher. A session request
middleware wraps every outgoing Bot API call there is — including calls made by
code written after this, and calls made outside any handler (the progress
ticker, background schedulers, startup command sync). A dispatcher middleware
sees updates coming in, which is the wrong direction entirely.

## Why only TelegramRetryAfter

Retrying a send is only safe when it is certain the first attempt did nothing,
and 429 is the one answer where Telegram says so. A 5xx or a timed-out request
is *ambiguous*: the message may well have been delivered, and a retry would
post it to the chat twice. For a bot whose job is posting transcriptions into a
group, a silent duplicate is a worse bug than a visible failure — and the
caller already has degradation for the failure. So this middleware deliberately
narrows to the one unambiguous case rather than becoming a general retryer.

## What it is not

This is reactive, not proactive: it has no token bucket and does not pace
outbound calls. Telegram's documented ceilings (~30 messages/second overall,
~20 per minute to one group) are far above this bot's traffic, so pacing would
be machinery guarding a limit nothing approaches. If that ever changes, the
place to add it is here, in front of `make_request`.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import Response, TelegramMethod
from aiogram.methods.base import TelegramType

logger = structlog.get_logger(__name__)

# How many times one call may be re-sent. Three attempts covers a burst that
# trips the limit once and clears; a call still refused after that is hitting
# something a sleep will not fix, and holding the handler open longer only
# delays the caller's own degradation.
DEFAULT_MAX_ATTEMPTS = 3

# The longest wait worth taking. Telegram occasionally answers a heavily
# rate-limited chat with a `retry_after` in the hundreds of seconds; a reply
# that arrives four minutes into a conversation is not a reply any more, and
# meanwhile the handler holds a database connection from the pool. Past this,
# fail immediately rather than sleeping and then failing.
DEFAULT_MAX_WAIT_SECONDS = 30.0

# Telegram's value is the floor, not the target: waking at exactly that instant
# is what produced the flood. A little extra, jittered, keeps several parts of
# one split message from retrying in lockstep.
_WAIT_PADDING_SECONDS = 0.5
_MAX_JITTER_SECONDS = 1.0


class FloodControlMiddleware(BaseRequestMiddleware):
    """Re-send a flood-limited call after the delay Telegram asked for."""

    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._max_attempts = max_attempts
        self._max_wait_seconds = max_wait_seconds
        # Injected so a test can assert the exact delays without spending them,
        # and so the jitter cannot make assertions flaky.
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter or (lambda: random.uniform(0.0, _MAX_JITTER_SECONDS))

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        method_name = type(method).__name__
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                # Telegram's own number, not a guess of ours.
                requested = float(exc.retry_after)
                if requested > self._max_wait_seconds:
                    logger.warning(
                        "Flood limit asks for longer than we are willing to wait",
                        method=method_name,
                        chat_id=_chat_id_of(method),
                        retry_after=requested,
                        max_wait_seconds=self._max_wait_seconds,
                    )
                    raise
                if attempt == self._max_attempts:
                    logger.warning(
                        "Flood limit persisted; giving up so the caller can degrade",
                        method=method_name,
                        chat_id=_chat_id_of(method),
                        retry_after=requested,
                        attempts=attempt,
                    )
                    raise
                delay = requested + _WAIT_PADDING_SECONDS + self._jitter()
                # Logged on EVERY retry, deliberately: a guard whose action
                # leaves no trace cannot be told apart from one that never
                # fires, and "is the bot being throttled?" should be a grep,
                # not an investigation.
                logger.info(
                    "Flood limited; waiting and retrying",
                    method=method_name,
                    chat_id=_chat_id_of(method),
                    retry_after=requested,
                    sleeping=round(delay, 2),
                    attempt=attempt,
                    max_attempts=self._max_attempts,
                )
                await self._sleep(delay)

        # Unreachable: the loop either returns or raises. Present so a future
        # edit to the bounds cannot silently fall out of the bottom returning
        # None, which would look like a successful send that never happened.
        raise AssertionError("flood-control retry loop exited without a result")


def _chat_id_of(method: TelegramMethod[Any]) -> int | str | None:
    """Best-effort chat id for the log line; most methods carry one."""
    return getattr(method, "chat_id", None)
