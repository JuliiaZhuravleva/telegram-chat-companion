"""DI provider lifecycle: things that open connections must also close them.

Dishka only registers a teardown for a provider that *yields*. A provider that
``return``s is constructed and then forgotten, so a ``close()`` the class
defines is never reached — and nothing fails, which is why this went unnoticed:
the process exits and the sockets go with it. The cost is real only in the
long-running bot and in scripts, and it is invisible either way.

These tests drive the provider function itself rather than a full container:
building one needs a live pool, and the property under test — "the generator
resumes after the yield and closes what it made" — is fully observable here.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.di import AppProvider
from src.services.ai.router import AIRouter


def _settings() -> MagicMock:
    s = MagicMock()
    s.openai_api_key = "sk-test"
    s.gemini_api_key = "test-gemini"
    s.grok_api_key = None
    s.deepseek_api_key = None
    s.ai.default_provider = "gemini"
    s.ai.tasks = {}
    return s


@pytest.mark.parametrize("name", ["get_pool", "get_ai_router"])
def test_resource_providers_are_async_generators(name: str) -> None:
    """The structural precondition, stated once.

    ``isasyncgenfunction`` is exactly the property Dishka keys off to register
    a finalizer. ``get_pool`` is the reference implementation and is included
    deliberately: if a future refactor breaks the assumption for both, this
    test says so rather than silently testing nothing.
    """
    factory = getattr(AppProvider, name)
    assert inspect.isasyncgenfunction(factory.origin), (
        f"{name} must yield, not return — a returning provider gets no teardown"
    )


@pytest.mark.asyncio
async def test_ai_router_provider_closes_the_router_on_teardown() -> None:
    """TD-068: ``AIRouter.close()`` existed and was never called.

    Asserts the call site, not the method: ``close()`` had its own passing
    test the whole time it was unreachable in production. What was missing was
    a provider that resumes after the yield.
    """
    provider_obj = AppProvider()
    agen = AppProvider.get_ai_router.origin(provider_obj, _settings(), MagicMock())

    router = await agen.__anext__()
    assert isinstance(router, AIRouter)

    # Providers are created lazily, so a router nobody used has nothing to
    # close. Plant two so the teardown has something observable to do.
    gemini, openai = AsyncMock(), AsyncMock()
    router._providers = {"gemini": gemini, "openai": openai}

    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()

    gemini.close.assert_awaited_once()
    openai.close.assert_awaited_once()
    assert router._providers == {}, "close() must also drop the references"
