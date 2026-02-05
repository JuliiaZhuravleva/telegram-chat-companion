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

from src.database.repositories.messages import MessageRepository
from src.database.repositories.response_log import ResponseLogRepository
from src.models.chat_config import ChatConfig
from src.models.enums import ResponseType, TriggerType
from src.services.abuse.checker import AntiAbuseChecker
from src.services.ai.base import AIProviderError
from src.services.ai.router import AIRouter
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
    ) -> None:
        self._ai = ai_router
        self._abuse = abuse_checker
        self._messages = message_repo
        self._response_log = response_log_repo
        self._rag = rag_service

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
        image_context: str | None = None,
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
        if response_type == ResponseType.COOLDOWN:
            return PipelineResult(should_respond=False, response_type=response_type)

        # --- Stage 2: Gather context in parallel ---
        recent_msgs_task = self._messages.get_recent(chat_id, limit=30)
        lengths_task = self._messages.get_recent_lengths(chat_id)

        rag_task: asyncio.Task[list[dict[str, Any]]] | None = None
        if config.rag_enabled and self._rag:
            rag_task = asyncio.ensure_future(self._rag.search(chat_id, message_text))

        recent_msgs, message_lengths = await asyncio.gather(
            recent_msgs_task, lengths_task
        )

        rag_memories: list[dict[str, Any]] = []
        if rag_task:
            rag_memories = await rag_task

        # Convert Record rows to dicts for prompt builder
        history = [dict(r) for r in reversed(recent_msgs)]

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
            rag_memories=rag_memories,
            reply_author=reply_author,
            reply_text=reply_text,
            reply_is_bot=reply_is_bot,
            image_context=image_context,
            user_name=user_name,
            user_message=message_text,
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
        html_text = markdown_to_html(ai_result.text)

        result = PipelineResult(
            should_respond=True,
            html_text=html_text,
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
            "ai_text": ai_result.text,
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
        tasks.append(
            asyncio.ensure_future(
                self._safe_update_cooldown(chat_id, user_id)
            )
        )

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

        # 3. Save bot message
        if bot_message_id is not None:
            tasks.append(
                asyncio.ensure_future(
                    self._safe_save_bot_message(
                        chat_id=chat_id,
                        message_id=bot_message_id,
                        content=ctx["ai_text"],
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
            )
        except Exception:
            logger.warning("Failed to log response", chat_id=chat_id)

    async def _safe_save_bot_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        content: str,
    ) -> None:
        try:
            await self._messages.save(
                chat_id=chat_id,
                message_id=message_id,
                message_type="text",
                content=content,
                is_bot_message=True,
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
