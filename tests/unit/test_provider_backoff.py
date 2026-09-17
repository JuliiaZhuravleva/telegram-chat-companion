"""Backoff after a rate limit — asserted at the call site, not in the helper.

The defect this guards is not in the arithmetic. It is that the bot made
131-155 embedding requests an hour, for twenty-four hours, into a quota that
could not serve any of them (measured on production 2026-09-17). A helper that
computes a perfect delay while the router never consults it would pass every
test of the helper, so the tests that matter here count the calls the PROVIDER
received.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.services.ai.backoff import ProviderBackoff
from src.services.ai.base import AIProviderError, EmbeddingResult, RateLimitError


class _Clock:
    """Hand-cranked monotonic clock, so a 30-minute window costs no seconds."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestProviderBackoff:
    def test_blocks_until_the_deadline(self):
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock)

        backoff.record_rate_limit("embeddings", "gemini", retry_after=None, reason="429")

        assert backoff.gate("embeddings", "gemini")[0] == "429"
        clock.advance(59)
        assert backoff.gate("embeddings", "gemini")[0] == "429"
        clock.advance(2)
        assert backoff.gate("embeddings", "gemini")[0] is None

    def test_honours_the_providers_own_retry_hint(self):
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock)

        backoff.record_rate_limit("embeddings", "gemini", retry_after=5.0, reason="429")

        clock.advance(6)
        assert backoff.gate("embeddings", "gemini")[0] is None

    def test_delay_escalates_when_no_hint_is_given(self):
        """A permanent condition must not be re-probed every minute for ever.

        With depleted credits there is no hint to honour, and the old provider
        layer invented 65 seconds -- which is how 24 hours of retrying at a
        fixed rate happened in the first place.
        """
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock)

        first = backoff.record_rate_limit("embeddings", "g", retry_after=None, reason="r")
        clock.advance(first + 1)
        backoff.gate("embeddings", "g")  # the probe goes out
        second = backoff.record_rate_limit("embeddings", "g", retry_after=None, reason="r")

        assert second == first * 2

    def test_escalation_is_capped(self):
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock, initial_delay=600.0, max_delay=1800.0)

        delays = []
        for _ in range(5):
            delay = backoff.record_rate_limit("embeddings", "g", retry_after=None, reason="r")
            delays.append(delay)
            clock.advance(delay + 1)
            backoff.gate("embeddings", "g")

        assert max(delays) == 1800.0

    def test_success_clears_the_window(self):
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock)
        backoff.record_rate_limit("embeddings", "gemini", retry_after=3600.0, reason="429")

        backoff.record_success("embeddings", "gemini")

        assert backoff.gate("embeddings", "gemini")[0] is None

    def test_tasks_are_independent(self):
        """A dead embedding quota says nothing about text generation.

        Quotas are granted per model and task; keying by provider alone would
        mute a working capability because a different one is exhausted.
        """
        backoff = ProviderBackoff(time_source=_Clock())

        backoff.record_rate_limit("embeddings", "gemini", retry_after=None, reason="429")

        assert backoff.gate("embeddings", "gemini")[0] is not None
        assert backoff.gate("text_generation", "gemini")[0] is None

    def test_only_one_call_passes_after_the_deadline(self):
        """Half-open means ONE probe, then blocked again until it reports.

        The first version only flipped an `expired` flag that nothing read, so
        every call after the deadline went through -- and a probe failing with
        anything other than a rate limit re-arms nothing, which restored the
        original defect of hammering a dead quota for ever. Verified by
        execution at the time: three consecutive asks all returned None.
        """
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock)
        backoff.record_rate_limit("embeddings", "gemini", retry_after=None, reason="429")
        clock.advance(61)

        assert backoff.gate("embeddings", "gemini")[0] is None  # the probe
        assert backoff.gate("embeddings", "gemini")[0] == "429"  # everyone else waits
        assert backoff.gate("embeddings", "gemini")[0] == "429"

    def test_a_late_success_does_not_clear_a_newer_window(self):
        """Two calls in flight: one 429 opens a window, one 200 must not pop it.

        Under a per-minute quota this is the NORMAL shape -- earlier rows in a
        batch succeed and later ones fail (the measured behaviour that parked
        58 healthy chunks). Without the generation check the breaker would hold
        only in passes where not a single call got through.
        """
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock)

        # B passes the gate first and is still in flight.
        _, b_generation = backoff.gate("embeddings", "gemini")
        # A comes back rate-limited and opens a window.
        backoff.record_rate_limit("embeddings", "gemini", retry_after=None, reason="429")
        # Now B's 200 arrives, carrying a generation from before that window.
        backoff.record_success("embeddings", "gemini", b_generation)

        assert backoff.gate("embeddings", "gemini")[0] == "429"

    def test_a_success_from_the_current_window_does_clear_it(self):
        """Control for the test above: the probe's own success must still work.

        Otherwise the window could never close and the bot would not recover
        without a restart -- worse than the bug being guarded against.
        """
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock)
        backoff.record_rate_limit("embeddings", "gemini", retry_after=None, reason="429")
        clock.advance(61)

        _, generation = backoff.gate("embeddings", "gemini")  # the probe
        backoff.record_success("embeddings", "gemini", generation)

        assert backoff.gate("embeddings", "gemini")[0] is None

    def test_a_repeated_retry_hint_stops_pinning_the_window(self):
        """A daily quota answers "retry in 60s" all day.

        Honoured for ever, that is ~1300 doomed calls a day instead of 3500 --
        better than the incident and still a loop. After a few repetitions the
        escalation ladder becomes a floor under the provider's hint.
        """
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock)

        delays = []
        for _ in range(6):
            delay = backoff.record_rate_limit(
                "embeddings", "g", retry_after=60.0, reason="retry in 60s"
            )
            delays.append(delay)
            clock.advance(delay + 1)
            backoff.gate("embeddings", "g")

        assert delays[0] == 60.0
        assert delays[-1] > 60.0


# ---------------------------------------------------------------------------
# The call site: does the router actually stop calling?
# ---------------------------------------------------------------------------


@pytest.fixture
def router():
    """Same mock-settings shape the rest of the router tests use."""
    from unittest.mock import MagicMock

    from src.services.ai.router import AIRouter

    settings = MagicMock()
    settings.openai_api_key = None
    settings.gemini_api_key = "test-gemini-key"
    settings.grok_api_key = None
    settings.deepseek_api_key = None
    settings.ai.default_provider = "gemini"
    # Embeddings have no fallback leg in production config, and that is
    # precisely why a rate limit there is a total outage.
    task_config = MagicMock()
    task_config.provider = "gemini"
    task_config.fallback = []
    task_config.model = "gemini-embedding-001"
    settings.ai.tasks = {"embeddings": task_config}
    return AIRouter(settings)


def _rate_limited_provider() -> AsyncMock:
    provider = AsyncMock()
    provider.generate_embedding = AsyncMock(
        side_effect=RateLimitError(
            "Gemini rate limit exceeded (no retry hint): Your prepayment credits are depleted.",
            provider="gemini",
        )
    )
    return provider


class TestRouterStopsCalling:
    @pytest.mark.asyncio
    async def test_second_request_does_not_reach_the_provider(self, router, monkeypatch):
        """The measured defect: every doomed call was actually sent.

        One call per backoff window, not one per row per pass.
        """
        provider = _rate_limited_provider()
        monkeypatch.setattr(router, "_get_provider", AsyncMock(return_value=provider))

        for _ in range(5):
            with pytest.raises(AIProviderError):
                await router.generate_embedding("текст")

        assert provider.generate_embedding.await_count == 1

    @pytest.mark.asyncio
    async def test_the_skipped_call_still_reports_failure(self, router, monkeypatch):
        """A muted provider must not read as a working one.

        `ai_failure_log` is what the health check counts, so a skipped call has
        to raise and be logged -- otherwise silencing the API calls would also
        silence the outage, which is the worse bug of the two.
        """
        provider = _rate_limited_provider()
        monkeypatch.setattr(router, "_get_provider", AsyncMock(return_value=provider))
        logged: list[str] = []

        async def _capture(*, task_type, error):  # noqa: ARG001 -- signature must match
            logged.append(task_type)

        monkeypatch.setattr(router, "_log_failure", _capture)

        with pytest.raises(AIProviderError):
            await router.generate_embedding("текст")
        with pytest.raises(AIProviderError) as second:
            await router.generate_embedding("текст")
        # `_log_failure` is dispatched through `fire_and_forget`, so the write
        # happens on a task the caller never awaits. Drained explicitly --
        # without this the list is empty and the assertion would "pass" for a
        # version that logs nothing at all.
        await asyncio.sleep(0)

        assert "backing off" in str(second.value)
        assert logged == ["embeddings", "embeddings"]

    @pytest.mark.asyncio
    async def test_recovery_needs_no_restart(self, router, monkeypatch):
        """Top up the credits and the bot heals by itself.

        Parked rows in the workers need a process restart to be retried; this
        window must not add a second such trap.
        """
        provider = _rate_limited_provider()
        monkeypatch.setattr(router, "_get_provider", AsyncMock(return_value=provider))
        clock = _Clock()
        from src.services.ai.backoff import ProviderBackoff

        monkeypatch.setattr(router, "_backoff", ProviderBackoff(time_source=clock))

        with pytest.raises(AIProviderError):
            await router.generate_embedding("текст")

        # Credits topped up while the window is open.
        provider.generate_embedding = AsyncMock(
            return_value=EmbeddingResult(
                embedding=[0.1] * 768,
                model="gemini-embedding-001",
                provider="gemini",
                dimensions=768,
                tokens_input=3,
            )
        )
        clock.advance(61)
        result = await router.generate_embedding("текст")

        assert len(result.embedding) == 768
        # And the window is closed, so the next call goes straight through.
        await router.generate_embedding("текст")
        assert provider.generate_embedding.await_count == 2


class TestTheFailureReallyReachesTheTable:
    @pytest.mark.asyncio
    async def test_log_failure_writes_the_row_with_the_providers_words(self, monkeypatch):
        """Asserted on the REPOSITORY, not on the router's own method.

        Monkeypatching `_log_failure` proves the router calls something; it
        cannot notice that the body writes nothing. `ai_failure_log` is the
        only signal the health check has for a terminal AI failure, so an
        empty write is a silent outage — the exact hole this table was added
        to close.
        """
        from unittest.mock import MagicMock

        from src.services.ai.router import AIRouter

        settings = MagicMock()
        settings.openai_api_key = None
        settings.gemini_api_key = "test-gemini-key"
        settings.grok_api_key = None
        settings.deepseek_api_key = None
        settings.ai.default_provider = "gemini"
        task = MagicMock()
        task.provider, task.fallback, task.model = "gemini", [], "gemini-embedding-001"
        settings.ai.tasks = {"embeddings": task}

        repo = AsyncMock()
        router = AIRouter(settings, response_log_repo=repo)
        monkeypatch.setattr(
            router, "_get_provider", AsyncMock(return_value=_rate_limited_provider())
        )

        with pytest.raises(AIProviderError):
            await router.generate_embedding("текст")
        await asyncio.sleep(0)

        repo.log_failure.assert_awaited_once()
        kwargs = repo.log_failure.await_args.kwargs
        assert kwargs["task_type"] == "embeddings"
        assert "prepayment credits are depleted" in kwargs["error_message"]

    @pytest.mark.asyncio
    async def test_a_non_provider_exception_is_still_logged(self, router, monkeypatch):
        """A malformed 200 (bad JSON, `embedding` not a dict) used to escape.

        It left the method without reaching `_log_failure`: no row, no backoff
        armed, a failure that exists for the caller and not for the health
        check.
        """
        provider = AsyncMock()
        provider.generate_embedding = AsyncMock(side_effect=ValueError("not json"))
        monkeypatch.setattr(router, "_get_provider", AsyncMock(return_value=provider))
        logged: list[object] = []

        async def _capture(*, task_type, error):
            logged.append((task_type, str(error)))

        monkeypatch.setattr(router, "_log_failure", _capture)

        with pytest.raises(AIProviderError) as exc:
            await router.generate_embedding("текст")
        await asyncio.sleep(0)

        assert logged and logged[0][0] == "embeddings"
        assert "ValueError" in str(exc.value)


class TestConcurrentCallers:
    @pytest.mark.asyncio
    async def test_a_partially_served_quota_does_not_pop_the_window(self, router, monkeypatch):
        """The measured production shape: some rows succeed, later ones 429.

        Two calls in flight; the 429 opens the window and the 200 arrives
        afterwards carrying a token from before it existed. Without the
        generation check the breaker would survive only in passes where not a
        single call got through — i.e. almost never under a per-minute quota.
        """
        provider = AsyncMock()

        async def _slow_success(**_kwargs):
            await asyncio.sleep(0.02)
            return EmbeddingResult(
                embedding=[0.1] * 768,
                model="gemini-embedding-001",
                provider="gemini",
                dimensions=768,
                tokens_input=3,
            )

        async def _fast_rate_limit(**_kwargs):
            await asyncio.sleep(0.01)
            raise RateLimitError("Gemini rate limit exceeded (no retry hint)", provider="gemini")

        calls = [_fast_rate_limit, _slow_success]

        async def _dispatch(**kw):
            return await calls.pop(0)(**kw)

        provider.generate_embedding = AsyncMock(side_effect=_dispatch)
        monkeypatch.setattr(router, "_get_provider", AsyncMock(return_value=provider))

        results = await asyncio.gather(
            router.generate_embedding("a"),
            router.generate_embedding("b"),
            return_exceptions=True,
        )

        assert any(isinstance(r, AIProviderError) for r in results)
        assert any(not isinstance(r, Exception) for r in results)
        # The window opened by the 429 must still be standing.
        assert router._backoff.gate("embeddings", "gemini")[0] is not None
