"""Image analysis service using Vision AI providers.

Analyzes photos sent in chats using the configured vision model.
"""

from __future__ import annotations

import structlog

from src.services.ai.base import AIProviderError
from src.services.ai.router import AIRouter

logger = structlog.get_logger(__name__)

IMAGE_ANALYSIS_PROMPT = (
    "Опиши это изображение кратко (2-3 предложения на русском языке). "
    "Укажи основные объекты, действия и настроение. "
    "Если это мем или скриншот — опиши контекст. "
    "Если ты не уверен в идентификации объекта, символа или персонажа — "
    'так и напиши (например: "возможно", "похоже на"). '
    "Не придумывай названия и факты — лучше описать внешний вид, чем угадать неправильно."
)


class ImageAnalysisService:
    """Analyze images using Vision AI providers."""

    def __init__(self, ai_router: AIRouter) -> None:
        self._ai = ai_router

    async def analyze(
        self,
        image_data: bytes,
        *,
        mime_type: str = "image/jpeg",
    ) -> str | None:
        """Analyze an image and return a text description.

        Args:
            image_data: Raw image bytes (JPEG, PNG, WebP, GIF).
            mime_type: MIME type of the image.

        Returns:
            Description string on success, None on failure.
        """
        try:
            result = await self._ai.analyze_image(
                image_data=image_data,
                prompt=IMAGE_ANALYSIS_PROMPT,
                mime_type=mime_type,
            )
            description = result.text.strip()
            logger.info(
                "Vision analysis complete",
                provider=result.provider,
                text_length=len(description),
                text_preview=description[:80] if description else None,
            )
            if not description:
                return None
            return description
        except AIProviderError:
            logger.exception("Image analysis failed")
            return None
