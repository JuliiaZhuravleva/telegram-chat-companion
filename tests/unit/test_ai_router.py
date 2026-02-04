"""Tests for src.services.ai.router — AIRouter fallback chain logic."""

from unittest.mock import MagicMock

import pytest

from src.services.ai.base import (
    AIProviderError,
    RateLimitError,
    TextGenerationResult,
)
from src.services.ai.router import AIRouter


@pytest.fixture
def mock_router_settings():
    """Create a mock settings object for AIRouter tests."""
    s = MagicMock()
    s.openai_api_key = "sk-test-openai"
    s.gemini_api_key = "test-gemini-key"
    s.grok_api_key = None
    s.deepseek_api_key = None
    s.ai.default_provider = "gemini"
    s.ai.tasks = {}
    return s


@pytest.fixture
def make_router(mock_router_settings):
    """Factory: create AIRouter with mock settings (no patching needed)."""

    def _factory(settings_override=None):
        s = settings_override or mock_router_settings
        router = AIRouter(s)
        return router, s

    return _factory


class TestAIRouterInit:
    """Test router initialization."""

    def test_creates_empty_providers_dict(self, make_router):
        router, _ = make_router()
        assert router._providers == {}


class TestIsProviderAvailable:
    """Test _is_provider_available()."""

    def test_available_when_api_key_set(self, make_router):
        router, _ = make_router()
        assert router._is_provider_available("openai") is True
        assert router._is_provider_available("gemini") is True

    def test_unavailable_when_no_api_key(self, make_router):
        router, _ = make_router()
        assert router._is_provider_available("grok") is False
        assert router._is_provider_available("deepseek") is False

    def test_unknown_provider_unavailable(self, make_router):
        router, _ = make_router()
        assert router._is_provider_available("unknown") is False


class TestGetProviderChain:
    """Test _get_provider_chain() fallback resolution."""

    def test_uses_task_config_when_present(self, make_router, mock_router_settings):
        task_config = MagicMock()
        task_config.provider = "gemini"
        task_config.fallback = ["openai"]
        mock_router_settings.ai.tasks = {"text_generation": task_config}

        router, _ = make_router(mock_router_settings)
        chain = router._get_provider_chain("text_generation")
        assert chain == ["gemini", "openai"]

    def test_filters_out_unavailable_providers(self, make_router, mock_router_settings):
        task_config = MagicMock()
        task_config.provider = "gemini"
        task_config.fallback = ["grok", "openai"]
        mock_router_settings.ai.tasks = {"text_generation": task_config}

        router, _ = make_router(mock_router_settings)
        chain = router._get_provider_chain("text_generation")
        assert "grok" not in chain
        assert chain == ["gemini", "openai"]


class TestGenerateText:
    """Test generate_text() with mock providers."""

    @pytest.mark.asyncio
    async def test_success_on_first_provider(
        self, mock_provider, make_router, mock_router_settings,
    ):
        expected = TextGenerationResult(
            text="hello world", model="mock", provider="gemini",
            tokens_input=5, tokens_output=3,
        )
        provider = mock_provider(name="gemini", text_result=expected)

        task_config = MagicMock()
        task_config.provider = "gemini"
        task_config.fallback = []
        task_config.model = "gemini-3-flash"
        task_config.max_tokens = 500
        task_config.temperature = 0.9
        mock_router_settings.ai.tasks = {"text_generation": task_config}

        router, _ = make_router(mock_router_settings)
        router._providers["gemini"] = provider

        result = await router.generate_text("test prompt")

        assert result.text == "hello world"
        assert "generate_text" in provider.call_log

    @pytest.mark.asyncio
    async def test_fallback_on_rate_limit(
        self, mock_provider, make_router, mock_router_settings,
    ):
        failing_provider = mock_provider(
            name="gemini",
            error=RateLimitError("rate limited", provider="gemini", retry_after=60.0),
        )
        fallback_result = TextGenerationResult(
            text="fallback response", model="gpt-5-nano", provider="openai",
        )
        fallback_provider = mock_provider(name="openai", text_result=fallback_result)

        task_config = MagicMock()
        task_config.provider = "gemini"
        task_config.fallback = ["openai"]
        task_config.model = "gemini-3-flash"
        task_config.max_tokens = 500
        task_config.temperature = 0.9
        mock_router_settings.ai.tasks = {"text_generation": task_config}

        router, _ = make_router(mock_router_settings)
        router._providers["gemini"] = failing_provider
        router._providers["openai"] = fallback_provider

        result = await router.generate_text("test")

        assert result.text == "fallback response"
        assert result.provider == "openai"

    @pytest.mark.asyncio
    async def test_raises_when_all_providers_fail(
        self, mock_provider, make_router, mock_router_settings,
    ):
        failing1 = mock_provider(
            name="gemini",
            error=AIProviderError("gemini down", provider="gemini"),
        )
        failing2 = mock_provider(
            name="openai",
            error=AIProviderError("openai down", provider="openai"),
        )

        task_config = MagicMock()
        task_config.provider = "gemini"
        task_config.fallback = ["openai"]
        task_config.model = None
        task_config.max_tokens = 500
        task_config.temperature = 0.9
        mock_router_settings.ai.tasks = {"text_generation": task_config}

        router, _ = make_router(mock_router_settings)
        router._providers["gemini"] = failing1
        router._providers["openai"] = failing2

        with pytest.raises(AIProviderError, match="All providers failed"):
            await router.generate_text("test")

    @pytest.mark.asyncio
    async def test_raises_when_no_providers_available(self):
        no_keys = MagicMock()
        no_keys.openai_api_key = None
        no_keys.gemini_api_key = None
        no_keys.grok_api_key = None
        no_keys.deepseek_api_key = None
        no_keys.ai.default_provider = "gemini"
        no_keys.ai.tasks = {}

        router = AIRouter(no_keys)
        with pytest.raises(AIProviderError, match="No providers available"):
            await router.generate_text("test")


class TestClose:
    """Test router cleanup."""

    @pytest.mark.asyncio
    async def test_close_calls_all_providers(self, mock_provider, make_router):
        p1 = mock_provider(name="gemini")
        p2 = mock_provider(name="openai")

        router, _ = make_router()
        router._providers = {"gemini": p1, "openai": p2}

        await router.close()

        assert "close" in p1.call_log
        assert "close" in p2.call_log
        assert router._providers == {}
