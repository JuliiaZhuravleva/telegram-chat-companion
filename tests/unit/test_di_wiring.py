"""DI wiring: values that must travel from config into a constructed service.

A correct filter is not a used filter. `TextProcessingPipeline` applies the
knowledge-base similarity floor correctly (tests/unit/test_text_pipeline.py) —
but every one of those tests hands it the number directly, so all of them stay
green if the DI provider stops passing the configured value and the pipeline
falls back to something else. This file asserts the *call site*.

Driving the provider function itself rather than a container is deliberate and
matches test_di_lifecycle.py: building a real container needs a live pool, while
the property under test ("the provider reads this field and passes it on") is
fully observable from the returned object.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from dishka import Scope

from src.bot.access_requests import NotifyCooldown
from src.di import AppProvider, ServiceProvider


def _built_pipeline(floor: float = 0.7, alias_repo: object | None = None):
    settings = MagicMock()
    settings.knowledge_base.min_similarity = floor
    return ServiceProvider().text_pipeline(
        settings=settings,
        ai_router=MagicMock(),
        abuse_checker=MagicMock(),
        message_repo=MagicMock(),
        response_log_repo=MagicMock(),
        rag_service=MagicMock(),
        chunk_service=MagicMock(),
        link_service=MagicMock(),
        sticker_service=MagicMock(),
        knowledge_repo=MagicMock(),
        observability_repo=MagicMock(),
        alias_repo=alias_repo if alias_repo is not None else MagicMock(),
    )


def _pipeline_with_configured_floor(floor: float):
    return _built_pipeline(floor)


def test_alias_repo_reaches_the_pipeline_from_the_provider() -> None:
    """The provider must actually hand the repository over.

    `TextProcessingPipeline` declares `alias_repo: AliasRepository | None =
    None`, and `_safe_load_aliases` returns an empty view when it is None --
    which is correct behaviour for a hand-built test instance and a silent
    catastrophe here. A provider that stopped passing it would keep every chat
    on account names for ever: no exception, no log line, no failing unit test,
    because every other test constructs the pipeline itself.
    """
    sentinel = MagicMock()

    assert _built_pipeline(alias_repo=sentinel)._aliases is sentinel


def test_kb_floor_reaches_the_pipeline_from_settings() -> None:
    """The configured floor, not a value the pipeline chose for itself."""
    pipeline = _pipeline_with_configured_floor(0.83)

    assert pipeline._kb_min_similarity == 0.83


def test_kb_floor_is_read_per_construction_not_captured_once() -> None:
    """A second pipeline built from different settings gets the different value.

    Guards against the floor being frozen at import time (a module-level default
    or a mutable-default capture), which would make the YAML authoritative only
    for whichever process happened to start first.
    """
    assert _pipeline_with_configured_floor(0.0)._kb_min_similarity == 0.0
    assert _pipeline_with_configured_floor(0.7)._kb_min_similarity == 0.7


def test_alias_repo_reaches_the_summary_service_from_the_provider() -> None:
    """Same call-site argument as the pipeline's, and it bites the same way.

    `SummaryService` carries `alias_repo: AliasRepository | None = None` so a
    hand-built instance still works, and `_load_aliases` returns an empty view
    when it is None. A provider that stopped passing it would put account names
    back into every summary with nothing raising -- and every other summary
    test constructs the service itself, so all of them stay green.
    """
    sentinel = MagicMock()

    service = ServiceProvider().summary_service(
        message_repo=MagicMock(), ai_router=MagicMock(), alias_repo=sentinel
    )

    assert service._aliases is sentinel


def _chunk_service(**knobs: float | int):
    settings = MagicMock()
    for name, value in knobs.items():
        setattr(settings.chunk_retrieval, name, value)
    return ServiceProvider().chunk_retrieval_service(
        settings=settings,
        chunk_repo=MagicMock(),
        ai_router=MagicMock(),
    )


def test_chunk_retrieval_knobs_reach_the_service_from_settings() -> None:
    """Same call-site argument as the KB floor above, and it bites harder here.

    `ChunkRetrievalService` carries a default for every one of these, so a
    provider that stopped passing them would construct a service that works,
    retrieves, and logs — just with numbers nobody configured. Every unit test
    of the service hands them in directly, so all of them stay green in exactly
    that state, and `retrieval_log.params` would then record the defaults as if
    they were the configuration S6 is about to sweep.
    """
    service = _chunk_service(
        max_results=9,
        min_similarity=0.42,
        rrf_k=77,
        vector_weight=2.5,
        fts_weight=0.25,
        depth_multiplier=4,
    )

    assert service.max_results == 9
    assert service.min_similarity == 0.42
    assert service.params == {
        "backend": "chunks",
        "max_results": 9,
        "min_similarity": 0.42,
        "rrf_k": 77,
        "vector_weight": 2.5,
        "fts_weight": 0.25,
        "depth_multiplier": 4,
    }


def test_chunk_retrieval_knobs_are_read_per_construction() -> None:
    """Not frozen at import time — a second service sees different settings."""
    assert _chunk_service(min_similarity=0.0).min_similarity == 0.0
    assert _chunk_service(min_similarity=0.61).min_similarity == 0.61


def test_the_notify_cooldown_is_process_wide_not_per_request() -> None:
    """The whole reason `NotifyCooldown` was lifted out of the middleware (TD-025).

    It used to be `self._last_notify` on the single `AccessControlMiddleware`
    instance. With the `my_chat_member` handler filing access requests too,
    there are now two callers — and if each got its own cooldown, adding the
    bot and then posting in the chat would send the admin two cards for one
    chat. `Scope.REQUEST` would do exactly that: this project has already been
    bitten by a service assumed to be shared that is rebuilt per update
    (`ChatConfigService`, whose 60s cache never spans two updates).

    Asserts the DECLARATION, which is what the container acts on; a runtime
    assertion would need a live pool.
    """
    factories = [
        f
        for f in AppProvider().factories
        if getattr(f.provides, "type_hint", None) is NotifyCooldown
    ]
    assert factories, "NotifyCooldown is not provided at all — DI will fail at startup"
    assert factories[0].scope is Scope.APP, (
        f"NotifyCooldown must be Scope.APP so the middleware and the my_chat_member "
        f"handler share one instance; it is {factories[0].scope}"
    )


def test_the_access_request_service_is_handed_that_shared_cooldown() -> None:
    """A correct APP-scoped provider is not a used one: assert the call site."""
    cooldown = NotifyCooldown()

    service = ServiceProvider().access_request_service(
        admin_repo=MagicMock(),
        bot_config_repo=MagicMock(),
        notifier=MagicMock(),
        cooldown=cooldown,
    )

    assert service._cooldown is cooldown
