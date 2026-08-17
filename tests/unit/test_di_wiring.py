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

from src.di import ServiceProvider


def _pipeline_with_configured_floor(floor: float):
    settings = MagicMock()
    settings.knowledge_base.min_similarity = floor
    return ServiceProvider().text_pipeline(
        settings=settings,
        ai_router=MagicMock(),
        abuse_checker=MagicMock(),
        message_repo=MagicMock(),
        response_log_repo=MagicMock(),
        rag_service=MagicMock(),
        link_service=MagicMock(),
        sticker_service=MagicMock(),
        knowledge_repo=MagicMock(),
        observability_repo=MagicMock(),
    )


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
