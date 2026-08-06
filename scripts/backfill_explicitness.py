"""One-off maintenance script: backfill ``explicitness_score`` for the
existing sticker catalog (ADR-0008 Decision 5).

Run manually, out of band — never from the bot process, never from an admin
UI action (ADR-0003's "no bulk re-analyze with progress tracking" boundary,
Julia's own [D-1] answer):

    python -m scripts.backfill_explicitness

This is NOT a full re-analysis. It only scores the one new
``explicitness_score`` column for rows that already have a working
``visual_description`` — it never touches ``visual_description``, ``emotion``,
``style_tags``, ``character_or_meme``, ``description_embedding``, or
``analyzed_at``. Doubling Vision spend and risking perturbation of
already-good descriptions for a need that is only "score one new axis" is
exactly what this script avoids (see ADR-0008 Decision 5 for the full
reasoning).

No admin-facing progress UI by design — the log below is the only feedback
channel (mirrors ADR-0007 Decision 3's "the log is the only feedback loop"
precedent for this module).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

import asyncpg
import structlog
from aiogram import Bot

from src.config import Settings
from src.database.connection import close_pool, create_pool
from src.database.repositories.response_log import ResponseLogRepository
from src.database.repositories.stickers import StickerRepository
from src.services.ai.base import AIProviderError
from src.services.ai.router import AIRouter
from src.services.modules.sticker.learning import StickerLearningService
from src.services.modules.sticker.models import StickerRenderError
from src.services.modules.sticker.renderer import render_tgs, render_webm

logger = structlog.get_logger(__name__)

# Narrow, single-field prompt (ADR-0008 Decision 5) — deliberately NOT the
# full _build_vision_prompt schema; this script only needs one number.
_EXPLICITNESS_PROMPT = (
    "Оцени, насколько этот стикер откровенный/пошлый, числом от 0.0 до 1.0: "
    "0.0 = совершенно безобидный, 1.0 = максимально откровенный/18+. "
    'Ответь только JSON: {"explicit": 0.0-1.0}'
)

_Outcome = Literal["scored", "skipped", "failed"]


@dataclass
class BackfillSummary:
    """Final counts logged at the end of a backfill run."""

    scored: int = 0
    skipped: int = 0
    failed: int = 0


async def _score_one(
    *,
    bot: Bot,
    ai_router: AIRouter,
    repo: StickerRepository,
    row: asyncpg.Record,
) -> _Outcome:
    """Score a single catalog row and persist the result.

    Returns:
        "scored" on success. "skipped" when the sticker's file is no longer
        retrievable from Telegram (expected for an old catalog — not a bug
        to investigate). "failed" when rendering or the Vision call itself
        errored, or Vision returned no usable score (worth investigating).
    """
    file_unique_id: str = row["file_unique_id"]

    try:
        file = await bot.get_file(row["file_id"])
        if not file.file_path:
            logger.warning(
                "Backfill: sticker file no longer resolvable on Telegram",
                file_unique_id=file_unique_id,
            )
            return "skipped"
        buf = await bot.download_file(file.file_path)
        if buf is None:
            logger.warning(
                "Backfill: sticker download returned no data",
                file_unique_id=file_unique_id,
            )
            return "skipped"
        raw = buf.read()
    except Exception:
        logger.warning(
            "Backfill: failed to download sticker from Telegram",
            file_unique_id=file_unique_id,
            exc_info=True,
        )
        return "skipped"

    # Reuse the existing render path's anchor frame (ADR-0007 Decision 2) —
    # do not invent a second "which frame represents this sticker" answer.
    is_video = bool(row["is_video"])
    is_animated = bool(row["is_animated"])
    try:
        if is_video:
            rendered = await render_webm(raw)
            image_data = rendered.hash_frame
            mime_type = "image/png"
        elif is_animated:
            rendered = await render_tgs(raw)
            image_data = rendered.hash_frame
            mime_type = "image/png"
        else:
            image_data = raw
            mime_type = "image/webp"
    except StickerRenderError:
        logger.warning(
            "Backfill: rendering failed",
            file_unique_id=file_unique_id,
            exc_info=True,
        )
        return "failed"

    try:
        result = await ai_router.analyze_image(
            image_data=image_data,
            prompt=_EXPLICITNESS_PROMPT,
            mime_type=mime_type,
            response_mime_type="application/json",
        )
    except AIProviderError:
        logger.warning(
            "Backfill: Vision call failed",
            file_unique_id=file_unique_id,
            exc_info=True,
        )
        return "failed"

    # Reuse the same reject-not-clamp validation the main learn() pipeline
    # uses (ADR-0008 Decision 4) — one implementation of "what counts as a
    # trustworthy score", not a second one for this script.
    parsed = StickerLearningService._parse_vision_response(result.text)
    score = parsed.get("explicit")
    if score is None:
        logger.warning(
            "Backfill: Vision returned no usable explicitness score",
            file_unique_id=file_unique_id,
            raw_response=result.text[:200],
        )
        return "failed"

    await repo.update_explicitness_score(file_unique_id, score)
    return "scored"


async def run_backfill(
    *,
    bot: Bot,
    ai_router: AIRouter,
    repo: StickerRepository,
) -> BackfillSummary:
    """Score every catalog row eligible for backfill (ADR-0008 Decision 5).

    Log-and-continue per row (mirrors ADR-0003's existing pattern for this
    table) — one bad file must never abort the whole run.
    """
    candidates = await repo.get_explicitness_backfill_candidates()
    summary = BackfillSummary()
    logger.info("Backfill: starting", candidate_count=len(candidates))

    for row in candidates:
        try:
            outcome = await _score_one(bot=bot, ai_router=ai_router, repo=repo, row=row)
        except Exception:
            logger.warning(
                "Backfill: unexpected error scoring sticker, skipping",
                file_unique_id=row["file_unique_id"],
                exc_info=True,
            )
            outcome = "failed"

        if outcome == "scored":
            summary.scored += 1
        elif outcome == "skipped":
            summary.skipped += 1
        else:
            summary.failed += 1

    logger.info(
        "Backfill: complete",
        scored=summary.scored,
        skipped=summary.skipped,
        failed=summary.failed,
        total=len(candidates),
    )
    return summary


async def main() -> None:
    """Bootstrap Bot/AIRouter/StickerRepository/pool directly (no Dishka
    request scope — there is no request to scope to, ADR-0008 Decision 5)."""
    settings = Settings()
    pool = await create_pool(settings.database_url)
    bot = Bot(token=settings.telegram_bot_token)

    try:
        repo = StickerRepository(pool)
        response_log_repo = ResponseLogRepository(pool)
        ai_router = AIRouter(settings, response_log_repo)
        await run_backfill(bot=bot, ai_router=ai_router, repo=repo)
    finally:
        await bot.session.close()
        await close_pool(pool)


if __name__ == "__main__":
    asyncio.run(main())
