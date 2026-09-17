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
- **Half-open, not closed:** when the deadline passes, one call is let through
  and the window is immediately re-armed, so the caller after it waits again
  until the probe's outcome is known. A success clears the state -- which is
  what makes the bot recover on its own once someone tops up the account, with
  no restart and no operator.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

_INITIAL_DELAY = 60.0
_MAX_DELAY = 1800.0
# How many consecutive rate limits a provider's own retry hint is taken at face
# value for, before the escalation ladder becomes a floor under it.
_TRUST_HINT_UNTIL = 3


@dataclass
class _State:
    """One provider's backoff state for one task."""

    open_until: float
    delay: float
    reason: str
    consecutive: int
    # Identifies THIS window. A success carrying an older generation is a
    # success that started before the window existed, and must not clear it.
    generation: int = 0
    # How many half-open probes this window has let through, for the log only.
    probes: int = 0


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
        # Starts at 1, so a real token is never falsy. `record_success` treats
        # 0 as "no token, clear unconditionally", and a counter starting at 0
        # handed that very value to the first caller -- defeating the check in
        # the one interleaving it exists for.
        self._generation = 1

    def gate(self, task: str, provider: str) -> tuple[str | None, int]:
        """May this provider be called? Returns (reason-to-skip, generation).

        `None` means go ahead, and the generation must be handed back to
        `record_success` so a late success cannot clear a window that a
        concurrent failure opened in the meantime (see `record_success`).

        **Passing the deadline re-arms the window rather than removing it.**
        That is what makes this half-open: the probe caller goes through, the
        next caller is blocked again until the new deadline, and only the
        probe's OUTCOME decides what happens next. The first version merely
        flipped a flag that nothing read, so after the deadline every call went
        through for ever -- and since a probe that fails with something other
        than a rate limit re-arms nothing, that restored the original defect of
        hammering a dead quota. Verified by execution before the fix: three
        consecutive asks after the deadline all returned None.
        """
        state = self._states.get((task, provider))
        if state is None:
            # The CURRENT counter, not 0. With 0 the token compared equal to
            # nothing and `record_success` fell back to clearing
            # unconditionally -- in exactly the common case: no window yet, two
            # calls in flight, the first comes back 429 and opens one, the
            # second comes back 200 and pops it. Handing out the counter means
            # any window opened after this call started carries a higher
            # generation and survives that success. Caught by its own test,
            # which is why it is not in the commit.
            return None, self._generation
        if self._now() >= state.open_until:
            state.open_until = self._now() + state.delay
            state.probes += 1
            logger.info(
                "Provider backoff expired, letting one call through",
                task=task,
                provider=provider,
                consecutive_rate_limits=state.consecutive,
                probe=state.probes,
                next_deadline_in=round(state.delay, 1),
                reason=state.reason,
            )
            return None, state.generation
        return state.reason, state.generation

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
        escalated = (
            self._initial_delay if previous is None else min(previous.delay * 2, self._max_delay)
        )

        if retry_after is not None and retry_after > 0 and consecutive <= _TRUST_HINT_UNTIL:
            # The provider's hint is worth honouring while the condition still
            # looks temporary. After a few repetitions it plainly is not: a
            # daily quota answers "retry in 60s" all day, which would pin the
            # window at a minute and still spend ~1300 doomed calls a day.
            delay = min(retry_after, self._max_delay)
        elif retry_after is not None and retry_after > 0:
            delay = min(max(retry_after, escalated), self._max_delay)
        else:
            delay = escalated

        self._generation += 1
        self._states[key] = _State(
            open_until=self._now() + delay,
            delay=delay,
            reason=reason,
            consecutive=consecutive,
            generation=self._generation,
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

    def record_success(self, task: str, provider: str, generation: int = 0) -> None:
        """Clear the backoff this caller was cleared against — nothing newer.

        `generation` is what `gate` returned. Without it, this sequence loses
        the window: two calls are in flight, the first comes back 429 and opens
        the window, the second comes back 200 and pops it. That is not an edge
        case under a per-minute quota -- "the limit trips partway through a
        batch, earlier rows succeed and later ones fail" is the measured
        production behaviour that parked 58 healthy chunks. The breaker would
        then hold only in passes where not a single call got through.
        """
        key = (task, provider)
        state = self._states.get(key)
        if state is None:
            return
        if generation and state.generation != generation:
            logger.info(
                "Ignoring a success against a superseded backoff window",
                task=task,
                provider=provider,
                success_generation=generation,
                current_generation=state.generation,
            )
            return
        del self._states[key]
        logger.info("Provider recovered, backoff cleared", task=task, provider=provider)
