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
from src.database.repositories.response_log import ResponseLogRepository
from src.models.chat_config import ChatConfig
from src.models.enums import ResponseType, TriggerType
from src.services.abuse.checker import AntiAbuseChecker
from src.services.ai.base import AIProviderError
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
)

logger = structlog.get_logger(__name__)

# Base max tokens for text generation
_BASE_MAX_TOKENS = 2000

# Max KB facts to retrieve per turn (ADR-0003 Part 2: KB_BUDGET_TOKENS=300
# fits roughly 4-5 short facts; over-fetching just gets trimmed downstream by
# trim_facts_to_budget(), so this is a DB round-trip cost cap, not a budget).
_KB_SEARCH_LIMIT = 5


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
    ) -> None:
        self._ai = ai_router
        self._abuse = abuse_checker
        self._messages = message_repo
        self._response_log = response_log_repo
        self._rag = rag_service
        self._links = link_service
        self._sticker = sticker_service
        self._knowledge = knowledge_repo

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

        # Blocked dispositions
        if response_type == ResponseType.BLACKLISTED:
            return PipelineResult(should_respond=False, response_type=response_type)
        # Direct replies to the bot bypass cooldown — the user is explicitly
        # continuing a conversation, not spamming.
        if response_type == ResponseType.COOLDOWN and trigger_type != TriggerType.REPLY:
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

        rag_task: asyncio.Task[list[dict[str, Any]]] | None = None
        if config.rag_enabled and self._rag:
            rag_task = asyncio.ensure_future(self._rag.search(chat_id, message_text))

        kb_task: asyncio.Task[list[dict[str, Any]]] | None = None
        if config.kb_enabled and self._knowledge:
            kb_task = asyncio.ensure_future(self._safe_get_kb_facts(chat_id, message_text))

        link_task: asyncio.Task[str | None] | None = None
        if config.link_comments_enabled and self._links:
            link_task = asyncio.ensure_future(self._safe_extract_links(message_text))

        sticker_task: asyncio.Task[str | None] | None = None
        if config.sticker_learning_enabled and self._sticker:
            sticker_task = asyncio.ensure_future(self._safe_get_sticker_candidates(message_text))

        recent_msgs, message_lengths = await asyncio.gather(recent_msgs_task, lengths_task)

        rag_memories: list[dict[str, Any]] = []
        if rag_task:
            rag_memories = await rag_task

        kb_facts: list[dict[str, Any]] = []
        if kb_task:
            kb_facts = await kb_task

        link_context_str: str | None = None
        if link_task:
            link_context_str = await link_task

        sticker_candidates_str: str | None = None
        if sticker_task:
            sticker_candidates_str = await sticker_task

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
            kb_facts=kb_facts,
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

    async def _safe_get_sticker_candidates(self, message_text: str) -> str | None:
        """Get sticker candidates for prompt injection (non-blocking on failure)."""
        if not self._sticker:
            return None
        try:
            candidates = await self._sticker.get_sticker_candidates(message_text)
            if not candidates:
                return None
            return StickerResponderService.format_candidates_for_prompt(candidates)
        except Exception:
            logger.warning("Sticker candidate search failed")
            return None

    async def _safe_get_kb_facts(self, chat_id: int, message_text: str) -> list[dict[str, Any]]:
        """Retrieve KB facts relevant to the current message (non-blocking on failure).

        `AIRouter.generate_embedding()` self-logs cost (router-level
        `ensure_future(self._log_usage(...))`) so no separate `log_usage`
        call is needed here (cross-cutting constraint applies to
        `generate_text`, which does not self-log -- embeddings already do).
        """
        if not self._knowledge:
            return []
        try:
            embedding_result = await self._ai.generate_embedding(message_text)
        except Exception:
            logger.warning("KB embedding generation failed", chat_id=chat_id)
            return []
        try:
            return await self._knowledge.search_by_similarity(
                chat_id, embedding_result.embedding, limit=_KB_SEARCH_LIMIT
            )
        except Exception:
            logger.warning("KB fact search failed", chat_id=chat_id)
            return []

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
