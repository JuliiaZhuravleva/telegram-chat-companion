"""Tests for image analysis service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.ai.base import AIProviderError, VisionResult
from src.services.modules.image.analysis import (
    IMAGE_ANALYSIS_PROMPT,
    ImageAnalysisService,
)


@pytest.fixture
def image_service():
    ai_router = MagicMock()
    return ImageAnalysisService(ai_router)


@pytest.mark.asyncio
async def test_analyze_success(image_service):
    image_service._ai.analyze_image = AsyncMock(
        return_value=VisionResult(
            text="На фото кот на подоконнике.",
            model="gemini-3-flash",
            provider="gemini",
        )
    )

    result = await image_service.analyze(b"fake-image")

    assert result == "На фото кот на подоконнике."
    image_service._ai.analyze_image.assert_awaited_once()
    call_args = image_service._ai.analyze_image.call_args
    assert call_args.kwargs["prompt"] == IMAGE_ANALYSIS_PROMPT


@pytest.mark.asyncio
async def test_analyze_ai_error_returns_none(image_service):
    image_service._ai.analyze_image = AsyncMock(
        side_effect=AIProviderError("Vision failed", provider="gemini")
    )

    result = await image_service.analyze(b"fake-image")

    assert result is None


@pytest.mark.asyncio
async def test_analyze_empty_result_returns_none(image_service):
    image_service._ai.analyze_image = AsyncMock(
        return_value=VisionResult(
            text="  ",
            model="gemini-3-flash",
            provider="gemini",
        )
    )

    result = await image_service.analyze(b"fake-image")

    assert result is None


@pytest.mark.asyncio
async def test_analyze_passes_mime_type(image_service):
    image_service._ai.analyze_image = AsyncMock(
        return_value=VisionResult(
            text="description",
            model="gemini-3-flash",
            provider="gemini",
        )
    )

    await image_service.analyze(b"fake-image", mime_type="image/png")

    call_kwargs = image_service._ai.analyze_image.call_args.kwargs
    assert call_kwargs["mime_type"] == "image/png"


def test_prompt_is_in_russian():
    assert "русском" in IMAGE_ANALYSIS_PROMPT
    assert "изображение" in IMAGE_ANALYSIS_PROMPT
