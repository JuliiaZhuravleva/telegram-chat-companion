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
import sys
from dataclasses import dataclass
from typing import Literal

import asyncpg
import structlog
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

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

# Pacing between rows: the loop hits bot.get_file + bot.download_file +
# Vision back-to-back per row against live APIs — an unthrottled run over a
# large catalog is its own 429 storm (2026-08-07 review).
_INTER_ROW_DELAY_S = 0.2

# TelegramRetryAfter is honored (sleep exc.retry_after) and the row retried
# this many times before counting it "failed".
_RATE_LIMIT_ATTEMPTS = 3


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
        "scored" on success. "skipped" ONLY when Telegram positively says the
        file is gone (expected for an old catalog — not a bug to
        investigate). "failed" for everything worth a re-run or a look:
        transport/rate-limit errors that survived the retries, rendering
        errors, Vision errors, or no usable score. The 2026-08-07 review
        found the original classification put rate-limit storms under
        "skipped" — the one label the operator is told to ignore.
    """
    file_unique_id: str = row["file_unique_id"]

    raw: bytes | None = None
    for attempt in range(_RATE_LIMIT_ATTEMPTS):
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
            break
        except TelegramRetryAfter as exc:
            if attempt == _RATE_LIMIT_ATTEMPTS - 1:
                logger.warning(
                    "Backfill: still rate-limited after retries — counting as failed, re-run later",
                    file_unique_id=file_unique_id,
                    retry_after=exc.retry_after,
                )
                return "failed"
            logger.info(
                "Backfill: rate-limited by Telegram, honoring retry_after",
                file_unique_id=file_unique_id,
                retry_after=exc.retry_after,
                attempt=attempt + 1,
            )
            await asyncio.sleep(exc.retry_after)
        except TelegramBadRequest:
            # Telegram positively rejected the file reference — genuinely gone.
            logger.warning(
                "Backfill: Telegram rejected the file reference (gone) — skipping",
                file_unique_id=file_unique_id,
                exc_info=True,
            )
            return "skipped"
        except (TelegramNetworkError, TelegramServerError):
            logger.warning(
                "Backfill: transient Telegram transport error — counting as failed, re-run later",
                file_unique_id=file_unique_id,
                exc_info=True,
            )
            return "failed"
        except Exception:
            logger.warning(
                "Backfill: unexpected download error — counting as failed",
                file_unique_id=file_unique_id,
                exc_info=True,
            )
            return "failed"
    if raw is None:  # defensive: loop exhausted without break or return
        return "failed"

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

    for i, row in enumerate(candidates):
        if i:
            # Pace the loop — see _INTER_ROW_DELAY_S.
            await asyncio.sleep(_INTER_ROW_DELAY_S)
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
    if summary.failed:
        # Failed rows stay explicitness_score = NULL and therefore fail-closed
        # hidden from every low-tolerance chat — a "complete" log line alone
        # reads as success and nothing would prompt a re-run.
        logger.warning(
            "Backfill: %d row(s) still unscored after failures — re-run this script",
            summary.failed,
        )
    return summary


def _exit_code(summary: BackfillSummary) -> int:
    """Non-zero when any row failed — a wrapper script (or the operator)
    must be able to tell "everything scored or genuinely gone" from
    "re-run needed" without parsing logs."""
    return 1 if summary.failed else 0


async def main() -> int:
    """Bootstrap Bot/AIRouter/StickerRepository/pool directly (no Dishka
    request scope — there is no request to scope to, ADR-0008 Decision 5)."""
    settings = Settings()
    pool = await create_pool(settings.database_url)
    bot = Bot(token=settings.telegram_bot_token)
    # Built before the try so the finally can always close it. Constructing it
    # inside would leave the name unbound if construction itself raised, and
    # the teardown would fail with NameError while masking the real error.
    response_log_repo = ResponseLogRepository(pool)
    ai_router = AIRouter(settings, response_log_repo)

    try:
        repo = StickerRepository(pool)
        summary = await run_backfill(bot=bot, ai_router=ai_router, repo=repo)
        return _exit_code(summary)
    finally:
        # Each close isolated. An exception raised inside `finally` REPLACES the
        # `return` from the try block, so one failing socket teardown would turn
        # a fully successful backfill into an unhandled crash — discarding the
        # exit code this script computes precisely so a caller can tell "all
        # scored" from "re-run needed". Cleanup failures are logged, never
        # allowed to become the script's verdict.
        for label, closer in (
            ("bot_session", bot.session.close),
            ("ai_router", ai_router.close),
            ("pool", lambda: close_pool(pool)),
        ):
            try:
                await closer()
            except Exception as exc:
                logger.warning("cleanup_failed", component=label, error_type=type(exc).__name__)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
