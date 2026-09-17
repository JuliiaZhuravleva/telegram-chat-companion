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

        assert backoff.blocked_reason("embeddings", "gemini") == "429"
        clock.advance(59)
        assert backoff.blocked_reason("embeddings", "gemini") == "429"
        clock.advance(2)
        assert backoff.blocked_reason("embeddings", "gemini") is None

    def test_honours_the_providers_own_retry_hint(self):
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock)

        backoff.record_rate_limit("embeddings", "gemini", retry_after=5.0, reason="429")

        clock.advance(6)
        assert backoff.blocked_reason("embeddings", "gemini") is None

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
        backoff.blocked_reason("embeddings", "g")  # expire it
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
            backoff.blocked_reason("embeddings", "g")

        assert max(delays) == 1800.0

    def test_success_clears_the_window(self):
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock)
        backoff.record_rate_limit("embeddings", "gemini", retry_after=3600.0, reason="429")

        backoff.record_success("embeddings", "gemini")

        assert backoff.blocked_reason("embeddings", "gemini") is None

    def test_tasks_are_independent(self):
        """A dead embedding quota says nothing about text generation.

        Quotas are granted per model and task; keying by provider alone would
        mute a working capability because a different one is exhausted.
        """
        backoff = ProviderBackoff(time_source=_Clock())

        backoff.record_rate_limit("embeddings", "gemini", retry_after=None, reason="429")

        assert backoff.blocked_reason("embeddings", "gemini") is not None
        assert backoff.blocked_reason("text_generation", "gemini") is None

    def test_active_does_not_consume_the_window(self):
        """The health check must be able to look without changing the state."""
        clock = _Clock()
        backoff = ProviderBackoff(time_source=clock)
        backoff.record_rate_limit("embeddings", "gemini", retry_after=None, reason="429")

        assert backoff.active() == {"embeddings/gemini": "429"}
        assert backoff.active() == {"embeddings/gemini": "429"}
        # Still blocked afterwards: asking is not probing.
        assert backoff.blocked_reason("embeddings", "gemini") == "429"


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
