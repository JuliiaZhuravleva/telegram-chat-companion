"""Tests for scripts/backfill_explicitness.py (ADR-0008 Decision 5).

Mirrors the mocking style of TestReanalyze in test_sticker_learning.py
(bot.get_file/download_file), since the same Telegram-download pattern is
reused here.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.backfill_explicitness import BackfillSummary, _score_one, run_backfill
from src.services.ai.base import AIProviderError, VisionResult
from src.services.modules.sticker.models import StickerRenderError
from src.services.modules.sticker.renderer import RenderedSticker


def _make_row(
    file_unique_id: str = "uid-1",
    file_id: str = "file-1",
    is_animated: bool = False,
    is_video: bool = False,
) -> dict:
    return {
        "file_unique_id": file_unique_id,
        "file_id": file_id,
        "is_animated": is_animated,
        "is_video": is_video,
    }


def _make_bot_mock(
    file_path: str | None = "stickers/file.webp",
    buf_data: bytes = b"fake-bytes",
) -> MagicMock:
    bot = MagicMock()
    file_obj = MagicMock()
    file_obj.file_path = file_path

    buf = MagicMock()
    buf.read = MagicMock(return_value=buf_data)

    bot.get_file = AsyncMock(return_value=file_obj)
    bot.download_file = AsyncMock(return_value=buf)
    return bot


def _make_ai_router(explicit: float | str = 0.5) -> MagicMock:
    ai_router = MagicMock()
    ai_router.analyze_image = AsyncMock(
        return_value=VisionResult(
            text=f'{{"explicit": {explicit}}}' if not isinstance(explicit, str) else explicit,
            model="gemini-3-flash",
            provider="gemini",
        )
    )
    return ai_router


def _make_repo() -> MagicMock:
    repo = MagicMock()
    repo.update_explicitness_score = AsyncMock()
    return repo


class TestScoreOne:
    @pytest.mark.asyncio
    async def test_static_sticker_scored(self):
        bot = _make_bot_mock()
        ai_router = _make_ai_router(0.5)
        repo = _make_repo()

        outcome = await _score_one(bot=bot, ai_router=ai_router, repo=repo, row=_make_row())

        assert outcome == "scored"
        repo.update_explicitness_score.assert_awaited_once_with("uid-1", 0.5)
        call_kwargs = ai_router.analyze_image.call_args.kwargs
        assert call_kwargs["mime_type"] == "image/webp"
        assert call_kwargs["image_data"] == b"fake-bytes"

    @pytest.mark.asyncio
    @patch("scripts.backfill_explicitness.render_tgs", new_callable=AsyncMock)
    async def test_animated_sticker_uses_render_hash_frame(self, mock_render_tgs):
        mock_render_tgs.return_value = RenderedSticker(
            collage_png=b"fake-collage",
            duration=1.0,
            frame_times=[0.0],
            hash_frame=b"anchor-frame-png",
        )
        bot = _make_bot_mock(buf_data=b"raw-tgs-bytes")
        ai_router = _make_ai_router(0.2)
        repo = _make_repo()

        outcome = await _score_one(
            bot=bot,
            ai_router=ai_router,
            repo=repo,
            row=_make_row(is_animated=True),
        )

        assert outcome == "scored"
        mock_render_tgs.assert_awaited_once_with(b"raw-tgs-bytes")
        call_kwargs = ai_router.analyze_image.call_args.kwargs
        # The single deterministic anchor frame, NOT the 6-frame Vision
        # collage — a narrow prompt only needs one image (Decision 5).
        assert call_kwargs["image_data"] == b"anchor-frame-png"
        assert call_kwargs["mime_type"] == "image/png"

    @pytest.mark.asyncio
    @patch("scripts.backfill_explicitness.render_webm", new_callable=AsyncMock)
    async def test_video_sticker_uses_render_hash_frame(self, mock_render_webm):
        mock_render_webm.return_value = RenderedSticker(
            collage_png=b"fake-collage",
            duration=1.0,
            frame_times=[0.0],
            hash_frame=b"anchor-frame-png",
        )
        bot = _make_bot_mock(buf_data=b"raw-webm-bytes")
        ai_router = _make_ai_router(0.2)
        repo = _make_repo()

        outcome = await _score_one(
            bot=bot,
            ai_router=ai_router,
            repo=repo,
            row=_make_row(is_video=True),
        )

        assert outcome == "scored"
        mock_render_webm.assert_awaited_once_with(b"raw-webm-bytes")
        call_kwargs = ai_router.analyze_image.call_args.kwargs
        assert call_kwargs["image_data"] == b"anchor-frame-png"
        assert call_kwargs["mime_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_missing_file_path_is_skipped(self):
        bot = _make_bot_mock(file_path=None)
        ai_router = _make_ai_router()
        repo = _make_repo()

        outcome = await _score_one(bot=bot, ai_router=ai_router, repo=repo, row=_make_row())

        assert outcome == "skipped"
        ai_router.analyze_image.assert_not_awaited()
        repo.update_explicitness_score.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_download_error_is_skipped_not_failed(self):
        """An old catalog sticker whose file has expired on Telegram's side
        is an EXPECTED condition, not a code fault — counted separately from
        'failed'."""
        bot = _make_bot_mock()
        bot.get_file = AsyncMock(side_effect=Exception("file not found"))
        ai_router = _make_ai_router()
        repo = _make_repo()

        outcome = await _score_one(bot=bot, ai_router=ai_router, repo=repo, row=_make_row())

        assert outcome == "skipped"

    @pytest.mark.asyncio
    @patch("scripts.backfill_explicitness.render_tgs", new_callable=AsyncMock)
    async def test_render_failure_is_failed(self, mock_render_tgs):
        mock_render_tgs.side_effect = StickerRenderError("render failed")
        bot = _make_bot_mock()
        ai_router = _make_ai_router()
        repo = _make_repo()

        outcome = await _score_one(
            bot=bot,
            ai_router=ai_router,
            repo=repo,
            row=_make_row(is_animated=True),
        )

        assert outcome == "failed"
        ai_router.analyze_image.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_vision_provider_error_is_failed(self):
        bot = _make_bot_mock()
        ai_router = MagicMock()
        ai_router.analyze_image = AsyncMock(side_effect=AIProviderError("boom", provider="gemini"))
        repo = _make_repo()

        outcome = await _score_one(bot=bot, ai_router=ai_router, repo=repo, row=_make_row())

        assert outcome == "failed"
        repo.update_explicitness_score.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_score_is_failed_not_persisted(self):
        """Reject-not-clamp (ADR-0008 Decision 4) applies here too, via the
        same shared _parse_vision_response validation."""
        bot = _make_bot_mock()
        ai_router = _make_ai_router(explicit='{"explicit": "not-a-number"}')
        repo = _make_repo()

        outcome = await _score_one(bot=bot, ai_router=ai_router, repo=repo, row=_make_row())

        assert outcome == "failed"
        repo.update_explicitness_score.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_score_key_is_failed(self):
        bot = _make_bot_mock()
        ai_router = _make_ai_router(explicit='{"note": "no explicit key here"}')
        repo = _make_repo()

        outcome = await _score_one(bot=bot, ai_router=ai_router, repo=repo, row=_make_row())

        assert outcome == "failed"


class TestRunBackfill:
    @pytest.mark.asyncio
    async def test_summary_counts_each_outcome(self):
        bot = _make_bot_mock()
        ai_router = _make_ai_router(0.5)
        repo = _make_repo()
        repo.get_explicitness_backfill_candidates = AsyncMock(
            return_value=[_make_row("uid-1"), _make_row("uid-2")]
        )

        summary = await run_backfill(bot=bot, ai_router=ai_router, repo=repo)

        assert summary == BackfillSummary(scored=2, skipped=0, failed=0)

    @pytest.mark.asyncio
    async def test_one_bad_row_does_not_abort_the_run(self):
        """Log-and-continue (ADR-0003's existing pattern for this table):
        an unexpected exception scoring one row (here: the final DB write
        itself, the one call in _score_one() not already wrapped in its own
        try/except) must not stop the rest — caught by run_backfill()'s own
        outer guard and counted as 'failed'."""
        bot = _make_bot_mock()
        ai_router = _make_ai_router(0.5)
        repo = _make_repo()
        repo.get_explicitness_backfill_candidates = AsyncMock(
            return_value=[_make_row("uid-bad"), _make_row("uid-good")]
        )

        call_count = 0
        real_update = repo.update_explicitness_score

        async def _flaky_update(file_unique_id, score):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("unexpected DB error")
            return await real_update(file_unique_id, score)

        repo.update_explicitness_score = _flaky_update

        summary = await run_backfill(bot=bot, ai_router=ai_router, repo=repo)

        assert summary.scored == 1
        assert summary.skipped == 0
        assert summary.failed == 1
