"""Tests for StickerResponderService's tolerance_level threading (ADR-0008)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.modules.sticker.responder import StickerResponderService


@pytest.mark.asyncio
async def test_get_sticker_candidates_threads_tolerance_level():
    sticker_service = AsyncMock()
    sticker_service.search.return_value = []
    responder = StickerResponderService(sticker_service, MagicMock())

    await responder.get_sticker_candidates("context", tolerance_level=0.42)

    sticker_service.search.assert_awaited_once()
    assert sticker_service.search.call_args.kwargs["tolerance_level"] == 0.42


@pytest.mark.asyncio
async def test_find_sticker_for_sticker_reply_threads_tolerance_level():
    repo = MagicMock()
    repo.get_by_file_unique_id = AsyncMock(
        return_value={"visual_description": "a happy cat", "emotion": "joy"}
    )
    sticker_service = AsyncMock()
    sticker_service.search.return_value = []
    responder = StickerResponderService(sticker_service, repo)

    await responder.find_sticker_for_sticker_reply("incoming-uid", tolerance_level=0.9)

    sticker_service.search.assert_awaited_once()
    assert sticker_service.search.call_args.kwargs["tolerance_level"] == 0.9
