"""Per-provider backoff after a rate limit — a circuit breaker for quotas.

Why this exists, measured on production 2026-09-17: the Gemini project's
prepaid credits ran out at 18:42 UTC the previous day, and for the next
twenty-four hours the bot sent **131-155 embedding requests an hour** into a
door that could not open. Every one of them was rejected in milliseconds,
logged as a terminal failure, and retried on the next pass -- the same thirty
chunk ids, around the clock. Nothing in the chain was wrong on its own:
`RateLimitError` is flagged retriable (correct -- a rate limit describes the
provider, not the row), so the workers deliberately do not charge the row for
it, so the row is retried for ever.

What this adds is the missing piece: after a rate limit, that provider is
skipped for the task until a deadline. The failure still surfaces -- callers
get the remembered error, so `ai_failure_log` keeps a row and the health check
cannot read the silence as health -- but the HTTP call is not made.

Two deliberate choices:

- **The provider's own retry hint is honoured when it gives one, and otherwise
  the delay escalates** (60s, 120s, ... capped at 30 min). Guessing a fixed
  delay is what the provider layer used to do with its hardcoded 65s, and it
  is precisely why a permanent outage read as a momentary one.
- **Half-open, not closed:** when the deadline passes, exactly one call is let
  through. A success clears the state. This is what makes the bot recover on
  its own once someone tops up the account, with no restart and no operator.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

_INITIAL_DELAY = 60.0
_MAX_DELAY = 1800.0


@dataclass
class _State:
    """One provider's backoff state for one task."""

    open_until: float
    delay: float
    reason: str
    consecutive: int
    # True once the window has been let through once. The record survives so
    # the NEXT rate limit escalates from this delay instead of restarting at
    # the initial one; `record_success` is what removes it.
    expired: bool = False


class ProviderBackoff:
    """Remembers rate-limited providers so doomed calls are not made.

    Keyed by (task, provider): quotas are granted per model and task, so a
    depleted embedding quota says nothing about text generation on the same
    key. Process-lifetime state, held by the router.
    """

    def __init__(
        self,
        *,
        initial_delay: float = _INITIAL_DELAY,
        max_delay: float = _MAX_DELAY,
        time_source: object = None,
    ) -> None:
        self._initial_delay = initial_delay
        self._max_delay = max_delay
        # Injectable so tests can move time without sleeping. Deliberately not
        # a default-argument `time.monotonic`: this outlives one call.
        self._now = time_source if callable(time_source) else time.monotonic
        self._states: dict[tuple[str, str], _State] = {}

    def blocked_reason(self, task: str, provider: str) -> str | None:
        """Why this provider is being skipped, or None if it may be called.

        Passing the deadline consumes the block: the next call is the half-open
        probe. A caller that asks and then does not call therefore spends the
        probe -- acceptable, because every caller here calls immediately.
        """
        state = self._states.get((task, provider))
        if state is None:
            return None
        if self._now() >= state.open_until:
            if not state.expired:
                # Kept, not deleted. Deleting it lost `consecutive` and
                # `delay`, so the next rate limit started again at 60s and the
                # escalation this class exists for never happened -- found by
                # its own test, which is the only reason it is not in the
                # commit. Only a SUCCESS clears the record.
                state.expired = True
                logger.info(
                    "Provider backoff expired, letting one call through",
                    task=task,
                    provider=provider,
                    consecutive_rate_limits=state.consecutive,
                    reason=state.reason,
                )
            return None
        return state.reason

    def record_rate_limit(
        self,
        task: str,
        provider: str,
        *,
        retry_after: float | None,
        reason: str,
    ) -> float:
        """Open (or widen) the backoff window. Returns the delay applied."""
        key = (task, provider)
        previous = self._states.get(key)
        consecutive = (previous.consecutive if previous else 0) + 1

        if retry_after is not None and retry_after > 0:
            delay = min(retry_after, self._max_delay)
        elif previous is None:
            delay = self._initial_delay
        else:
            delay = min(previous.delay * 2, self._max_delay)

        self._states[key] = _State(
            open_until=self._now() + delay,
            delay=delay,
            reason=reason,
            consecutive=consecutive,
        )
        logger.warning(
            "Provider rate limited, backing off",
            task=task,
            provider=provider,
            delay_seconds=round(delay, 1),
            retry_after=retry_after,
            consecutive_rate_limits=consecutive,
            reason=reason,
        )
        return delay

    def record_success(self, task: str, provider: str) -> None:
        """Clear any backoff — the provider is serving again."""
        if self._states.pop((task, provider), None) is not None:
            logger.info("Provider recovered, backoff cleared", task=task, provider=provider)

    def active(self) -> dict[str, str]:
        """Currently blocked "task/provider" -> reason, for the health check.

        Read-only: unlike `blocked_reason` this never consumes a deadline, so
        asking about the state cannot change it.
        """
        now = self._now()
        return {
            f"{task}/{provider}": state.reason
            for (task, provider), state in self._states.items()
            if now < state.open_until
        }
