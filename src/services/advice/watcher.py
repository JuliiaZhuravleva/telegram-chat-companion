"""Advice watcher -- background task that classifies blog comments and sends the digest.

Two jobs on one timer. **Classify:** every closed series of a member's messages
in a watched chat gets its context (`context.build`), the model answers the
fact questions (`prompts`), and the owner's rules make the verdict
(`verdict`). **Report:** once a day, after `digest_hour` in the display time
zone, everything found since the last digest goes to the bot's admins.

**Which chats.** `advice_watch_enabled` AND a linked channel. The flag is a
normal setting, so it also appears on the global defaults screen; switched on
there, it would otherwise run the blog's rules -- "don't advise the author on
her own posts" -- over every group chat the bot is in, and spend the daily cap
on chats where the rule means nothing. A discussion group of a channel is the
only shape the rules were written for, so it is also the gate.

**A series is classified once it is closed** -- its last message older than
`SERIES_GAP_SECONDS` -- for the same reason the chunk indexer waits for a
session to close: a verdict on a remark still being typed would be a verdict on
half of it, and the natural key `(chat_id, first_message_id)` would then keep
the half.

**Cost bound.** Workers AI's free allocation is shared with another project, so
the daily cap counts what this worker actually spent (`response_log` rows with
`task_type='advice_watch'`) from midnight in the display zone, and stops the
pass at the cap. A model that fails on one series again and again is given up
on after `max_attempts` (`advice_checked` without a verdict); like the chunk
indexer's parking, the count is per process.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import structlog
from aiogram import Bot

from src.config import AdviceWatchSettings
from src.database.repositories.advice import AdviceRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.messages import MessageRepository
from src.services.advice import context as ctx_mod
from src.services.advice.digest import render_digest
from src.services.advice.prompts import render_comment_prompt, render_post_prompt
from src.services.advice.verdict import FactsParseError, parse_facts, verdict
from src.services.ai.base import AIProviderError, RateLimitError
from src.services.ai.router import AIRouter
from src.services.chat_config import ChatConfigService
from src.utils.display_tz import DISPLAY_TZ
from src.utils.telegram_text import split_html

logger = structlog.get_logger(__name__)

TASK_TYPE = "advice_watch"
DIGEST_KEY = "advice_digest_last_sent"

_INITIAL_DELAY = 300  # after the chunk indexer; nothing here is user-facing
# How far before the oldest candidate the timeline starts: enough for the
# "four messages before" fallback and for a reply chain's recent ancestors.
_TIMELINE_LEAD = timedelta(days=1)
# Older posts and replies a chain reaches are fetched hop by hop; a blog
# thread is rarely deeper than this.
_CHAIN_HOPS = 6
_MAX_TOKENS = 4096  # a thinking model spends most of it before answering
_TEMPERATURE = 0.2


class _CapReached(Exception):
    pass


class AdviceWatcher:
    """Background task: blog comments -> verdicts -> the owner's daily digest.

    Process-lifetime, not Dishka-managed (CLAUDE.md's "Process-lifetime
    singletons" ADR), and not in `dp[]` either -- no handler calls it.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        ai_router: AIRouter,
        chat_config: ChatConfigService,
        bot: Bot,
        admin_ids: Sequence[int],
        config: AdviceWatchSettings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._pool = pool
        self._ai_router = ai_router
        self._chat_config = chat_config
        self._bot = bot
        self._admin_ids = list(admin_ids)
        self._config = config
        self._clock = clock
        self._repo = AdviceRepository(pool)
        self._task: asyncio.Task[None] | None = None
        self._failures: dict[tuple[int, int], int] = defaultdict(int)
        self._warned_unlinked: set[int] = set()
        self._warned_no_admins = False
        self._budget = 0

    async def start(self) -> None:
        """Start the loop (no-op when disabled by config)."""
        if not self._config.enabled:
            logger.info("Advice watcher disabled by config")
            return
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Advice watcher started",
            interval_seconds=self._config.interval_seconds,
            daily_call_cap=self._config.daily_call_cap,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Advice watcher stopped")

    async def _run_loop(self) -> None:
        await asyncio.sleep(_INITIAL_DELAY)
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Advice watcher pass failed")
            await asyncio.sleep(self._config.interval_seconds)

    async def run_once(self) -> dict[str, int]:
        """One pass: classify what closed, then send the digest if it is due."""
        now = self._clock()
        day_start = (
            now.astimezone(DISPLAY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        ).astimezone(UTC)
        self._budget = self._config.daily_call_cap - await self._repo.calls_since(day_start)

        stats = {"chats": 0, "verdicts": 0, "failed": 0, "digest_items": 0}
        try:
            for chat_id in await MessageRepository(self._pool).list_chat_ids():
                if not await self._watched(chat_id):
                    continue
                stats["chats"] += 1
                try:
                    verdicts, failed = await self._classify_chat(chat_id, now)
                except _CapReached:
                    raise
                except RateLimitError:
                    logger.warning("Advice watcher rate-limited, pass ends", chat_id=chat_id)
                    break
                except Exception:
                    logger.exception("Advice classification failed for chat", chat_id=chat_id)
                    continue
                stats["verdicts"] += verdicts
                stats["failed"] += failed
        except _CapReached:
            logger.warning(
                "Advice watcher reached its daily call cap", cap=self._config.daily_call_cap
            )

        stats["digest_items"] = await self._maybe_send_digest(now)
        if any(stats[k] for k in ("verdicts", "failed", "digest_items")):
            logger.info("Advice watcher pass complete", **stats)
        return stats

    async def _watched(self, chat_id: int) -> bool:
        try:
            config = await self._chat_config.get_config(chat_id)
        except Exception:
            logger.exception("Advice watcher could not resolve chat config", chat_id=chat_id)
            return False
        if not config.advice_watch_enabled:
            return False
        if config.linked_channel_id is None:
            if chat_id not in self._warned_unlinked:
                self._warned_unlinked.add(chat_id)
                logger.warning(
                    "Advice watch is on for a chat with no linked channel -- skipped",
                    chat_id=chat_id,
                )
            return False
        return True

    # ── Classification ────────────────────────────────────────────────────

    async def _classify_chat(self, chat_id: int, now: datetime) -> tuple[int, int]:
        since = now - timedelta(hours=self._config.lookback_hours)
        candidates = await self._repo.unchecked_candidates(
            chat_id, since, exclude_user_ids=self._admin_ids
        )
        if not candidates:
            return 0, 0
        timeline = await self._repo.timeline(chat_id, candidates[0].created_at - _TIMELINE_LEAD)
        by_id = {x.message_id: x for x in timeline}
        await self._load_chains(chat_id, by_id)

        verdicts = failed = 0
        settled: set[int] = set()
        for line in candidates:
            if line.message_id in settled:
                continue
            if line.message_id not in by_id or not line.body:
                # Whitespace the SQL trim does not strip: nothing to judge.
                await self._repo.mark_checked(chat_id, [line.message_id])
                continue
            context = ctx_mod.build(line, timeline, by_id)
            settled.update(context.message_ids)
            closed_at = context.series[-1].created_at
            if (now - closed_at).total_seconds() <= ctx_mod.SERIES_GAP_SECONDS:
                continue  # still being written; the next pass gets it whole
            try:
                saved = await self._classify(chat_id, context)
            except (AIProviderError, FactsParseError) as exc:
                if isinstance(exc, RateLimitError):
                    raise
                failed += 1
                await self._record_failure(chat_id, context, exc)
                continue
            verdicts += int(saved)
        return verdicts, failed

    async def _load_chains(self, chat_id: int, by_id: dict[int, ctx_mod.Line]) -> None:
        """Fetch the older rows the reply chains reach, posts included."""
        frontier = {x.reply_to for x in by_id.values() if x.reply_to and x.reply_to not in by_id}
        for _ in range(_CHAIN_HOPS):
            if not frontier:
                return
            fetched = await self._repo.lines_by_id(chat_id, sorted(frontier))
            for x in fetched:
                by_id[x.message_id] = x
            frontier = {x.reply_to for x in fetched if x.reply_to and x.reply_to not in by_id}

    async def _classify(self, chat_id: int, context: ctx_mod.Context) -> bool:
        post_facts: dict[str, Any] = {}
        if context.post is not None:
            post_facts = await self._post_facts(chat_id, context.post)
        prompt = render_comment_prompt(
            post_text=context.post.body if context.post is not None else None,
            thread=context.thread,
            who=ctx_mod.who(context.series[0]),
            comment=context.comment,
        )
        facts, model = await self._ask(chat_id, prompt)
        return await self._repo.save_verdict(
            chat_id,
            user_id=context.series[0].user_id,
            message_ids=context.message_ids,
            post_message_id=context.post.message_id if context.post is not None else None,
            facts=facts,
            verdict=verdict(facts, post_facts),
            model=model,
        )

    async def _post_facts(self, chat_id: int, post: ctx_mod.Line) -> dict[str, Any]:
        cached = await self._repo.get_post_facts(chat_id, post.message_id)
        if cached is not None:
            return cached
        facts, _model = await self._ask(chat_id, render_post_prompt(post.body))
        await self._repo.put_post_facts(chat_id, post.message_id, facts)
        return facts

    async def _ask(self, chat_id: int, prompt: str) -> tuple[dict[str, Any], str]:
        if self._budget <= 0:
            raise _CapReached
        self._budget -= 1
        result = await self._ai_router.generate_text(
            prompt,
            model=self._config.model,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            response_mime_type="application/json",
        )
        # Logged before parsing: a call whose answer is unusable was still
        # made, and the cap must see it.
        await self._ai_router.log_usage(result, chat_id=chat_id, task_type=TASK_TYPE)
        return parse_facts(result.text), result.model

    async def _record_failure(self, chat_id: int, context: ctx_mod.Context, exc: Exception) -> None:
        key = (chat_id, context.message_ids[0])
        self._failures[key] += 1
        attempts = self._failures[key]
        logger.warning(
            "Advice classification failed",
            chat_id=chat_id,
            message_id=key[1],
            attempts=attempts,
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        if attempts >= self._config.max_attempts:
            await self._repo.mark_checked(chat_id, context.message_ids)
            del self._failures[key]
            logger.warning("Advice classification given up", chat_id=chat_id, message_id=key[1])

    # ── Digest ────────────────────────────────────────────────────────────

    async def _maybe_send_digest(self, now: datetime) -> int:
        local = now.astimezone(DISPLAY_TZ)
        if local.hour < self._config.digest_hour:
            return 0
        today = local.date().isoformat()
        config_repo = BotConfigRepository(self._pool)
        if await config_repo.get(DIGEST_KEY) == today:
            return 0
        items = await self._repo.digest_items(now)
        if items:
            if not self._admin_ids:
                if not self._warned_no_admins:
                    self._warned_no_admins = True
                    logger.warning("Advice digest has findings and no admin to send them to")
                return 0
            if not await self._deliver(render_digest(items, local.date())):
                # Nothing reached anyone: keep the findings for the next pass.
                return 0
        await self._repo.mark_notified(now)
        await config_repo.set(DIGEST_KEY, today, description="Date of the last advice digest")
        return len(items)

    async def _deliver(self, text: str) -> bool:
        """True once any part reached anyone.

        Counted per part, not per whole digest: a failure on part two would
        otherwise leave the day open, and the next pass, five minutes later,
        would send part one again -- every five minutes for the rest of the
        evening. A lost tail is logged; a loop of repeats is spam.
        """
        parts = split_html(text)
        delivered = False
        for admin_id in self._admin_ids:
            try:
                for part in parts:
                    await self._bot.send_message(admin_id, part, parse_mode="HTML")
                    delivered = True
            except Exception as exc:
                logger.warning(
                    "advice_digest_dm_failed", admin_id=admin_id, error_type=type(exc).__name__
                )
        return delivered
