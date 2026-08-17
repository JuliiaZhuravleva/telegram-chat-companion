"""Text processing pipeline — the main orchestrator.

Flow:
1. Anti-abuse check (SQL function)
2. Abuse filter (pattern + embedding)
3. Gather context in parallel (recent msgs, RAG search, message lengths)
4. Build system + user prompt
5. Call AI router (provider chain with fallback)
6. Convert Markdown → Telegram HTML
7. Return result for handler to send
8. Post-response tasks run after send (cooldown, logging, RAG store)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.database.repositories.knowledge import KnowledgeRepository
from src.database.repositories.messages import MessageRepository
from src.database.repositories.observability import ObservabilityRepository
from src.database.repositories.response_log import ResponseLogRepository
from src.models.chat_config import ChatConfig
from src.models.enums import ResponseType, TriggerType
from src.services.abuse.checker import AntiAbuseChecker
from src.services.ai.base import AIProviderError, EmbeddingResult
from src.services.ai.pricing import calculate_cost
from src.services.ai.router import AIRouter
from src.services.modules.links.extractor import LinkExtractorService
from src.services.modules.links.formatters import format_link_context_section
from src.services.modules.sticker.responder import StickerResponderService
from src.services.rag.memory import RAGMemoryService
from src.services.text.formatter import markdown_to_html
from src.services.text.prompt_builder import (
    PromptContext,
    build_system_prompt,
    build_user_prompt,
    compute_max_tokens,
    trim_facts_to_budget,
)
from src.utils.background import fire_and_forget

logger = structlog.get_logger(__name__)


def _round_sim(similarity: Any) -> float | None:
    """Similarity as a compact JSON-safe float (4 places), or None."""
    if similarity is None:
        return None
    return round(float(similarity), 4)


def _facts_above_floor(facts: list[dict[str, Any]], min_similarity: float) -> list[dict[str, Any]]:
    """The facts whose cosine similarity clears the retrieval floor.

    One definition, used both to decide what reaches the prompt and to mark
    `above_floor` in `retrieval_log` -- the two must never disagree, or the
    report describes an injection that did not happen.

    ``min_similarity <= 0.0`` means *no floor* and returns the input unchanged.
    That is not the same as testing ``sim >= 0.0``: cosine similarity is defined
    on [-1, 1], so a ``>= 0.0`` test would still cut negatively-scored rows and
    the "set it to 0.0 to roll back" path documented in ``config/default.yml``
    would not actually restore the previous behaviour.

    A row with no usable ``similarity`` is treated as below any floor. It cannot
    be *shown* to clear one, and letting it through would make a malformed row
    more privileged than a genuine distant match.
    """
    if min_similarity <= 0.0:
        return list(facts)
    return [
        fact
        for fact in facts
        if isinstance(fact.get("similarity"), int | float)
        and not isinstance(fact.get("similarity"), bool)
        and float(fact["similarity"]) >= min_similarity
    ]


# Base max tokens for text generation
_BASE_MAX_TOKENS = 2000

# Max KB facts to retrieve per turn (ADR-0003 Part 2: KB_BUDGET_TOKENS=300
# fits roughly 4-5 short facts; over-fetching just gets trimmed downstream by
# trim_facts_to_budget(), so this is a DB round-trip cost cap, not a budget).
_KB_SEARCH_LIMIT = 5

# retrieval_log stores a short head of each retrieved item, not the full text —
# enough to recognize the memory in analysis without bloating JSONB rows.
_RETRIEVAL_HEAD_CHARS = 120


@dataclass
class PipelineResult:
    """Result from the text processing pipeline."""

    should_respond: bool
    html_text: str = ""
    trigger_type: TriggerType = TriggerType.NONE
    response_type: ResponseType = ResponseType.NORMAL

    # AI metadata (for logging)
    provider: str = ""
    model: str = ""
    tokens_input: int | None = None
    tokens_output: int | None = None
    response_time_ms: int = 0
    was_fallback: bool = False

    # Sticker chosen by AI
    sticker_file_id: str | None = None

    # Post-send context
    _post_send_ctx: dict[str, Any] = field(default_factory=dict)


class TextProcessingPipeline:
    """Orchestrate the full text → AI → HTML pipeline."""

    def __init__(
        self,
        ai_router: AIRouter,
        abuse_checker: AntiAbuseChecker,
        message_repo: MessageRepository,
        response_log_repo: ResponseLogRepository,
        rag_service: RAGMemoryService | None = None,
        link_service: LinkExtractorService | None = None,
        sticker_service: StickerResponderService | None = None,
        knowledge_repo: KnowledgeRepository | None = None,
        observability_repo: ObservabilityRepository | None = None,
        *,
        kb_min_similarity: float,
    ) -> None:
        self._ai = ai_router
        self._abuse = abuse_checker
        self._messages = message_repo
        self._response_log = response_log_repo
        self._rag = rag_service
        self._links = link_service
        self._sticker = sticker_service
        self._knowledge = knowledge_repo
        self._observability = observability_repo
        # No default, mirroring `RAGMemoryService.__init__` (S2-2): the YAML is
        # the single source for this number, and a dropped wiring must fail
        # loudly at construction rather than silently retrieve at some other
        # threshold than the one the config states.
        self._kb_min_similarity = kb_min_similarity
        if observability_repo is None:
            # DI always wires the repo; absence means a hand-built instance.
            # Without this line, "nobody is recording decisions/retrievals"
            # is indistinguishable from "nothing happened".
            logger.debug("Pipeline: observability persistence disabled (no repository)")

    async def process(
        self,
        *,
        chat_id: int,
        user_id: int,
        user_name: str,
        message_text: str,
        trigger_type: TriggerType,
        config: ChatConfig,
        reply_author: str | None = None,
        reply_text: str | None = None,
        reply_is_bot: bool = False,
        reply_quote_text: str | None = None,
        reply_quote_is_manual: bool = False,
        image_context: str | None = None,
        message_thread_id: int | None = None,
        message_id: int | None = None,
    ) -> PipelineResult:
        """Run the full pipeline and return the result."""
        # --- Stage 1: Anti-abuse check ---
        is_addressed = trigger_type in (TriggerType.TRIGGER, TriggerType.REPLY)
        abuse_result = await self._abuse.check(
            chat_id=chat_id,
            user_id=user_id,
            content=message_text,
            is_addressed_to_bot=is_addressed,
        )

        response_type = ResponseType(abuse_result.response_type)

        # Blocked dispositions. Fire-and-forget like every other observability
        # writer: these paths spike during abuse bursts, exactly when the DB
        # is most likely to be slow — an INSERT must not delay the return.
        if response_type == ResponseType.BLACKLISTED:
            fire_and_forget(
                self._safe_log_silence(
                    chat_id=chat_id, user_id=user_id, message_id=message_id, tier="blacklist"
                )
            )
            return PipelineResult(should_respond=False, response_type=response_type)
        # Direct replies to the bot bypass cooldown — the user is explicitly
        # continuing a conversation, not spamming.
        if response_type == ResponseType.COOLDOWN and trigger_type != TriggerType.REPLY:
            fire_and_forget(
                self._safe_log_silence(
                    chat_id=chat_id, user_id=user_id, message_id=message_id, tier="cooldown"
                )
            )
            return PipelineResult(should_respond=False, response_type=response_type)

        # --- Stage 2: Gather context in parallel ---
        # Use topic-aware context query for forum support
        recent_msgs_task = self._messages.get_recent_with_topic_context(
            chat_id,
            message_thread_id,
            current_topic_limit=20,
            other_topics_limit=10,
        )
        lengths_task = self._messages.get_recent_lengths(chat_id)

        # S2-4: one shared query embedding per turn for RAG + KB (previously
        # each called generate_embedding() independently for the same
        # message_text -- an extra network round-trip plus a doubled
        # cost-log row). embed_task is awaited by both consumers below; the
        # coroutine itself still runs exactly once.
        embed_task: asyncio.Task[tuple[EmbeddingResult | None, str | None]] | None = None
        if (config.rag_enabled and self._rag) or (config.kb_enabled and self._knowledge):
            embed_task = asyncio.ensure_future(self._safe_embed_query(chat_id, message_text))

        rag_task: asyncio.Task[tuple[list[dict[str, Any]], int, str | None]] | None = None
        if config.rag_enabled and self._rag:
            assert embed_task is not None
            rag_task = asyncio.ensure_future(
                self._timed_rag_search(chat_id, message_text, embed_task)
            )

        kb_task: asyncio.Task[tuple[list[dict[str, Any]], int, str | None]] | None = None
        if config.kb_enabled and self._knowledge:
            assert embed_task is not None
            kb_task = asyncio.ensure_future(self._timed_kb_facts(chat_id, embed_task))

        link_task: asyncio.Task[str | None] | None = None
        if config.link_comments_enabled and self._links:
            link_task = asyncio.ensure_future(self._safe_extract_links(message_text))

        sticker_task: asyncio.Task[str | None] | None = None
        if config.sticker_learning_enabled and self._sticker:
            sticker_task = asyncio.ensure_future(
                self._safe_get_sticker_candidates(message_text, config.tolerance_level)
            )

        recent_msgs, message_lengths = await asyncio.gather(recent_msgs_task, lengths_task)

        rag_memories: list[dict[str, Any]] = []
        rag_ms: int | None = None
        rag_error: str | None = None
        if rag_task:
            rag_memories, rag_ms, rag_error = await rag_task

        kb_facts: list[dict[str, Any]] = []
        kb_ms: int | None = None
        kb_error: str | None = None
        if kb_task:
            kb_facts, kb_ms, kb_error = await kb_task

        link_context_str: str | None = None
        if link_task:
            link_context_str = await link_task

        sticker_candidates_str: str | None = None
        if sticker_task:
            sticker_candidates_str = await sticker_task

        # Durable retrieval observability (migration 022): persist what each
        # active source returned and what of it survives budget trimming.
        # Fire-and-forget, and ALL payload construction happens inside the
        # guarded task — nothing here may add latency or break the reply.
        if self._observability is not None and rag_task is not None and self._rag is not None:
            fire_and_forget(
                self._safe_log_retrieval(
                    chat_id=chat_id,
                    message_id=message_id,
                    source="rag_memory",
                    query_text=message_text,
                    params={
                        "min_similarity": self._rag.min_similarity,
                        "max_results": self._rag.max_results,
                    },
                    raw=rag_memories,
                    duration_ms=rag_ms,
                    error=rag_error,
                )
            )
        if self._observability is not None and kb_task is not None:
            fire_and_forget(
                self._safe_log_retrieval(
                    chat_id=chat_id,
                    message_id=message_id,
                    source="kb",
                    query_text=message_text,
                    params={
                        "limit": _KB_SEARCH_LIMIT,
                        "min_similarity": self._kb_min_similarity,
                    },
                    # Deliberately the UNFILTERED set. The floor is applied to
                    # the prompt below, not here: once sub-floor rows stop being
                    # logged, `retrieval_log` no longer records the noise band —
                    # i.e. exactly the data any future re-tuning of the floor
                    # would need, and the data docs/kb-eval-baseline.md was
                    # derived from. Filtering at selection would make the floor
                    # unmeasurable the moment it shipped.
                    raw=kb_facts,
                    duration_ms=kb_ms,
                    error=kb_error,
                )
            )

        # What actually reaches the model. A turn where everything is below the
        # floor gets NO knowledge-base block at all (owner decision 2026-08-18:
        # answer without the base by default, attach facts only on a topical
        # match) — `_kb_section` is only rendered when `ctx.kb_facts` is
        # non-empty.
        kb_facts_for_prompt = _facts_above_floor(kb_facts, self._kb_min_similarity)

        # Convert Record rows to dicts for prompt builder
        history = [dict(r) for r in reversed(recent_msgs)]

        # Detect forum mode: any message has topic_scope (not NULL)
        is_forum_mode = any(msg.get("topic_scope") is not None for msg in history)

        # --- Stage 3: Build prompts ---
        ctx = PromptContext(
            system_prompt=config.system_prompt,
            language=config.language,
            response_type=response_type,
            fatigue_level=abuse_result.fatigue_level,
            max_tokens_adjustment=abuse_result.max_tokens_adjustment,
            jailbreak_hint=abuse_result.jailbreak_hint,
            recent_messages=history,
            message_lengths=message_lengths,
            kb_facts=kb_facts_for_prompt,
            rag_memories=rag_memories,
            reply_author=reply_author,
            reply_text=reply_text,
            reply_is_bot=reply_is_bot,
            reply_quote_text=reply_quote_text,
            reply_quote_is_manual=reply_quote_is_manual,
            image_context=image_context,
            link_context=link_context_str,
            sticker_candidates=sticker_candidates_str,
            user_name=user_name,
            user_message=message_text,
            is_forum_mode=is_forum_mode,
        )

        system_prompt = build_system_prompt(ctx)
        user_prompt = build_user_prompt(ctx)
        max_tokens = compute_max_tokens(_BASE_MAX_TOKENS, ctx)

        # --- Stage 4: AI generation ---
        start = time.monotonic()
        try:
            ai_result = await self._ai.generate_text(
                prompt=user_prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
            )
        except AIProviderError:
            logger.exception("All AI providers failed", chat_id=chat_id)
            fire_and_forget(
                self._safe_log_silence(
                    chat_id=chat_id, user_id=user_id, message_id=message_id, tier="provider_error"
                )
            )
            return PipelineResult(should_respond=False)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # --- Stage 5: Format response ---
        # Extract sticker marker from AI response
        sticker_file_id: str | None = None
        ai_text = ai_result.text
        if self._sticker and sticker_candidates_str:
            sticker_file_id, ai_text = StickerResponderService.extract_sticker_from_response(
                ai_text
            )

        html_text = markdown_to_html(ai_text)

        result = PipelineResult(
            should_respond=True,
            html_text=html_text,
            sticker_file_id=sticker_file_id,
            trigger_type=trigger_type,
            response_type=response_type,
            provider=ai_result.provider,
            model=ai_result.model,
            tokens_input=ai_result.tokens_input,
            tokens_output=ai_result.tokens_output,
            response_time_ms=elapsed_ms,
        )

        # Store context needed for post-send tasks
        result._post_send_ctx = {
            "chat_id": chat_id,
            "user_id": user_id,
            "user_name": user_name,
            "message_text": message_text,
            "trigger_type": trigger_type.value,
            "config": config,
            "ai_text": ai_text,
            "message_thread_id": message_thread_id,
        }

        return result

    async def post_send(
        self,
        result: PipelineResult,
        *,
        bot_message_id: int | None = None,
    ) -> None:
        """Run post-send tasks: cooldown update, logging, RAG store.

        Call this AFTER the response has been sent to Telegram.
        Non-critical — failures are logged but don't propagate.
        """
        ctx = result._post_send_ctx
        if not ctx:
            return

        chat_id = ctx["chat_id"]
        user_id = ctx["user_id"]
        config: ChatConfig = ctx["config"]
        trigger_type = ctx["trigger_type"]

        tasks: list[asyncio.Task[Any]] = []

        # 1. Update cooldown
        tasks.append(asyncio.ensure_future(self._safe_update_cooldown(chat_id, user_id)))

        # 2. Log response
        tasks.append(
            asyncio.ensure_future(
                self._safe_log_response(
                    chat_id=chat_id,
                    user_id=user_id,
                    message_id=bot_message_id,
                    trigger_type=trigger_type,
                    result=result,
                )
            )
        )

        # 3. Save bot message (with topic for forum support)
        if bot_message_id is not None:
            tasks.append(
                asyncio.ensure_future(
                    self._safe_save_bot_message(
                        chat_id=chat_id,
                        message_id=bot_message_id,
                        content=ctx["ai_text"],
                        message_thread_id=ctx.get("message_thread_id"),
                    )
                )
            )

        # 4. RAG store (Q&A pair)
        if config.rag_enabled and self._rag:
            importance = self._compute_importance(trigger_type)
            qa_content = f"Q: {ctx['message_text']}\nA: {ctx['ai_text']}"
            tasks.append(
                asyncio.ensure_future(
                    self._safe_rag_store(
                        chat_id=chat_id,
                        content=qa_content,
                        source_message_id=bot_message_id,
                        importance=importance,
                    )
                )
            )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _compute_importance(trigger_type: str) -> float:
        """Compute RAG importance score based on trigger type."""
        base = 0.5
        if trigger_type == TriggerType.TRIGGER:
            return base + 0.2
        if trigger_type == TriggerType.REPLY:
            return base + 0.1
        return base

    async def _safe_embed_query(
        self, chat_id: int, message_text: str
    ) -> tuple[EmbeddingResult | None, str | None]:
        """Shared query embedding for RAG + KB this turn (S2-4).

        `AIRouter.generate_embedding()` self-logs cost with `chat_id`
        (router-level `ensure_future(self._log_usage(...))`, TD-009) so no
        separate `log_usage` call is needed here (cross-cutting constraint
        applies to `generate_text`, which does not self-log -- embeddings
        already do). Never raises: failure is reported to both consumers via
        the returned error string.
        """
        try:
            result = await self._ai.generate_embedding(message_text, chat_id=chat_id)
        except Exception as exc:
            error = f"embedding: {type(exc).__name__}: {exc}"
            logger.warning("Query embedding failed", chat_id=chat_id, error=str(exc))
            return None, error
        return result, None

    async def _timed_rag_search(
        self,
        chat_id: int,
        message_text: str,
        embed_task: asyncio.Task[tuple[EmbeddingResult | None, str | None]],
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """RAG search plus wall-clock ms and failure detail for retrieval_log.

        Pre-022 a repository/DB error here propagated and killed the whole
        reply. Now it degrades to "no memories" and lands in
        retrieval_log.error — a retrieval outage must be visible, not fatal,
        and without the error field a broken source is byte-identical to a
        healthy source that matched nothing.

        Embedding failure (S2-4: shared with KB via `embed_task`) degrades to
        "no memories" the same way, and is reported through the same `error`
        field. It used to be discarded here "matching pre-S2-4 behavior", but
        pre-S2-4 the error was genuinely unavailable at this layer; after the
        shared-embedding refactor it sits in the tuple, and `_timed_kb_facts`
        already propagates it. Dropping it on the RAG side reproduced exactly
        the byte-identical-to-empty case the paragraph above says must never
        happen — during an embeddings outage (no fallback since S2-1) the KB
        row carried the error and the RAG row read as "matched nothing".
        """
        start = time.monotonic()
        memories: list[dict[str, Any]] = []
        embedding, error = await embed_task
        if self._rag and embedding is not None:
            try:
                memories = await self._rag.search(
                    chat_id, message_text, query_embedding=embedding.embedding
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.warning("RAG search failed", chat_id=chat_id, error=str(exc), exc_info=True)
        return memories, int((time.monotonic() - start) * 1000), error

    async def _timed_kb_facts(
        self,
        chat_id: int,
        embed_task: asyncio.Task[tuple[EmbeddingResult | None, str | None]],
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """KB fact search plus wall-clock ms and failure detail for retrieval_log.

        S2-4: the query embedding is computed once by `_safe_embed_query`
        and shared with RAG via `embed_task`, instead of KB calling
        `generate_embedding()` on its own.
        """
        start = time.monotonic()
        facts: list[dict[str, Any]] = []
        embedding, error = await embed_task
        if self._knowledge is not None and embedding is not None:
            try:
                facts = await self._knowledge.search_by_similarity(
                    chat_id, embedding.embedding, limit=_KB_SEARCH_LIMIT
                )
            except Exception as exc:
                error = f"search: {type(exc).__name__}: {exc}"
                logger.warning("KB fact search failed", chat_id=chat_id, error=str(exc))
        return facts, int((time.monotonic() - start) * 1000), error

    async def _safe_log_silence(
        self,
        *,
        chat_id: int,
        user_id: int | None,
        message_id: int | None,
        tier: str,
        reason: str | None = None,
    ) -> None:
        """Persist a pipeline suppression to decision_log (never raises)."""
        if self._observability is None:
            return
        try:
            await self._observability.log_decision(
                chat_id,
                stage="pipeline",
                decision="silent",
                tier=tier,
                reason=reason,
                message_id=message_id,
                # Handlers use 0 as the "no sender" sentinel; the gate writes
                # NULL for the same case. Store NULL so GROUP BY user_id never
                # invents a phantom user 0.
                user_id=user_id or None,
            )
        except Exception as exc:
            logger.warning(
                "Failed to log silence decision",
                chat_id=chat_id,
                tier=tier,
                error=str(exc),
                exc_info=True,
            )

    async def _safe_log_retrieval(
        self,
        *,
        chat_id: int,
        message_id: int | None,
        source: str,
        query_text: str,
        params: dict[str, Any],
        raw: list[dict[str, Any]],
        duration_ms: int | None,
        error: str | None = None,
    ) -> None:
        """Persist one retrieval pass to retrieval_log (never raises).

        Payload construction happens HERE, inside the guard — a malformed
        retrieval row must break the log write, never the reply.
        """
        if self._observability is None:
            return
        try:
            if source == "kb":
                # `raw` is the unfiltered top-N, so the two reductions the
                # prompt path performs are replayed here IN THE SAME ORDER:
                # floor first, budget trim second. Trimming `raw` instead would
                # mark a sub-floor fact `injected` whenever the budget happened
                # to have room for it — the budget almost never binds at these
                # lengths, so that mistake would be invisible in the data and
                # would silently corrupt the one field kb_report.py relies on.
                #
                # Same pure trim the renderer applies (agreement pinned by
                # test_kb_retrieval_logs_budget_trim_as_not_injected), so
                # `injected` states what actually reaches the prompt.
                above_floor = _facts_above_floor(raw, self._kb_min_similarity)
                above_floor_ids = {fact.get("id") for fact in above_floor}
                kept_ids = {fact.get("id") for fact in trim_facts_to_budget(above_floor)}
                items = [
                    {
                        "id": fact.get("id"),
                        "sim": _round_sim(fact.get("similarity")),
                        # Two distinct facts about one row: whether it was
                        # relevant enough, and whether it then fitted. Collapsing
                        # them would make "the floor cut it" and "the budget cut
                        # it" indistinguishable in the report.
                        "above_floor": fact.get("id") in above_floor_ids,
                        "injected": fact.get("id") in kept_ids,
                        "head": (fact.get("fact_text") or "")[:_RETRIEVAL_HEAD_CHARS],
                    }
                    for fact in raw
                ]
            else:
                items = [
                    {
                        "id": mem.get("id"),
                        "sim": _round_sim(mem.get("similarity")),
                        # RAG has no budget trim yet (TD-007 / ADR-0006
                        # pending): everything returned reaches the prompt.
                        "injected": True,
                        "head": (mem.get("content") or "")[:_RETRIEVAL_HEAD_CHARS],
                    }
                    for mem in raw
                ]
            await self._observability.log_retrieval(
                chat_id,
                source=source,
                query_text=query_text,
                params=params,
                results=items,
                n_results=len(items),
                n_injected=sum(1 for item in items if item.get("injected")),
                duration_ms=duration_ms,
                message_id=message_id,
                error=error,
            )
        except Exception as exc:
            logger.warning(
                "Failed to log retrieval",
                chat_id=chat_id,
                source=source,
                error=str(exc),
                exc_info=True,
            )

    async def _safe_get_sticker_candidates(
        self, message_text: str, tolerance_level: float
    ) -> str | None:
        """Get sticker candidates for prompt injection (non-blocking on failure).

        ``tolerance_level`` (ADR-0008 Decision 6): the calling chat's resolved
        explicitness ceiling, threaded through to the candidate search.
        """
        if not self._sticker:
            return None
        try:
            candidates = await self._sticker.get_sticker_candidates(
                message_text, tolerance_level=tolerance_level
            )
            if not candidates:
                return None
            return StickerResponderService.format_candidates_for_prompt(candidates)
        except Exception:
            logger.warning("Sticker candidate search failed")
            return None

    async def _safe_extract_links(self, text: str) -> str | None:
        """Extract link context (non-blocking on failure)."""
        if not self._links:
            return None
        try:
            result = await self._links.extract(text)
            if result is None:
                return None
            return format_link_context_section(result)
        except Exception:
            logger.warning("Link extraction failed")
            return None

    async def _safe_update_cooldown(self, chat_id: int, user_id: int) -> None:
        try:
            await self._abuse.update_cooldown(chat_id, user_id)
        except Exception:
            logger.warning("Failed to update cooldown", chat_id=chat_id)

    async def _safe_log_response(
        self,
        *,
        chat_id: int,
        user_id: int,
        message_id: int | None,
        trigger_type: str,
        result: PipelineResult,
    ) -> None:
        try:
            cost = calculate_cost(
                result.model,
                tokens_input=result.tokens_input,
                tokens_output=result.tokens_output,
            )
            await self._response_log.log(
                chat_id,
                user_id=user_id,
                message_id=message_id,
                trigger_type=trigger_type,
                provider=result.provider,
                model=result.model,
                tokens_input=result.tokens_input,
                tokens_output=result.tokens_output,
                response_time_ms=result.response_time_ms,
                was_fallback=result.was_fallback,
                task_type="text",
                cost_usd=cost,
            )
        except Exception:
            logger.warning("Failed to log response", chat_id=chat_id)

    async def _safe_save_bot_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        content: str,
        message_thread_id: int | None = None,
    ) -> None:
        try:
            await self._messages.save(
                chat_id=chat_id,
                message_id=message_id,
                message_type="text",
                content=content,
                is_bot_message=True,
                message_thread_id=message_thread_id,
            )
        except Exception:
            logger.warning("Failed to save bot message", chat_id=chat_id)

    async def _safe_rag_store(
        self,
        *,
        chat_id: int,
        content: str,
        source_message_id: int | None,
        importance: float,
    ) -> None:
        if not self._rag:
            return
        try:
            await self._rag.store(
                chat_id=chat_id,
                content=content,
                source_message_id=source_message_id,
                importance_score=importance,
            )
        except Exception:
            logger.warning("Failed to store RAG memory", chat_id=chat_id)
