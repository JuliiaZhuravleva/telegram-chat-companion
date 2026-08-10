"""Shared reply mechanics for the text and photo handlers.

`handlers/media.py` grew as a partial copy of `handlers/message.py`, and the
copy drifted: three things present in the text path never reached it — the
relevancy gate, the spend-limit warning and the AI-chosen sticker. The first
was a live defect (random replies to captioned photos bypassed the relevance
check entirely), the other two were silent feature gaps.

The lesson recorded in TD-028 is that patching media.py would fix the symptom
and leave the mechanism: two copies of "decide → reply → bookkeeping" drift
again the next time either is touched. So the parts that actually drifted live
here, called by both handlers, and there is one place to change them.

Deliberately NOT a full merge of the two handlers. They differ in ways that
matter — a photo must be downloaded and described before anything can be
decided, and it has a no-caption branch with no equivalent in text. Forcing
those into one function would trade duplication for a flag-driven mega-handler
on the bot's most critical path.
"""

from __future__ import annotations

import structlog
from aiogram.types import Message

from src.models.chat_config import ChatConfig
from src.models.enums import TriggerType
from src.services.abuse.checker import AntiAbuseChecker
from src.services.costs.spend_limit import SpendLimitService
from src.services.modules.reactions.responder import set_reaction
from src.services.modules.reactions.selector import ReactionSelector
from src.services.relevancy.gate import GateDecision, RelevancyGate
from src.services.text.pipeline import PipelineResult, TextProcessingPipeline

logger = structlog.get_logger(__name__)


async def react_to_silence(
    message: Message,
    config: ChatConfig,
    gate_decision: GateDecision,
    abuse_checker: AntiAbuseChecker,
) -> None:
    """R-5: tier-3 silence -> optionally react instead of a text reply.

    ``gate_decision.suggested_emoji`` is only ever populated on the tier-3
    ``llm_judge`` path (ADR-0004 Decision 4) — every other tier leaves it
    ``None``, so this is a no-op there. Gated on ``reactions_enabled`` only,
    never ``reactions_history_enabled`` (Decision 3): R-5 needs no history at
    all, ``llm_judge`` decides live, per-call.

    Anti-abuse: this path returns before ``TextProcessingPipeline.process()``,
    so the pipeline's Stage 1 abuse gate never runs for it. Setting a reaction
    is an outbound, user-visible action, so it must respect the same "be quiet
    toward this user" signal a text reply would. The cooldown is probed
    read-only and *after* the cheap guards, so a message the bot was never
    going to react to costs no query at all.
    """
    if not config.reactions_enabled or message.bot is None:
        return

    emoji = ReactionSelector.select(gate_decision.suggested_emoji)
    if emoji is None:
        return

    user = message.from_user
    if user is not None and await abuse_checker.is_in_cooldown(message.chat.id, user.id):
        logger.debug(
            "Skipping silence reaction: user in cooldown",
            chat_id=message.chat.id,
            user_id=user.id,
        )
        return

    try:
        await set_reaction(
            message.bot,
            chat_id=message.chat.id,
            message_id=message.message_id,
            emoji=emoji,
        )
    except Exception as exc:
        # `set_reaction` swallows TelegramAPIError; this is a last-resort net
        # for anything else (e.g. a programming error after a signature change)
        # so a suppressed text reply never turns into an unhandled crash.
        logger.warning(
            "Failed to set reaction on silence",
            chat_id=message.chat.id,
            message_id=message.message_id,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )


async def relevancy_allows_reply(
    *,
    message: Message,
    chat_config: ChatConfig,
    trigger_type: TriggerType,
    message_text: str,
    relevancy_gate: RelevancyGate,
    abuse_checker: AntiAbuseChecker,
) -> bool:
    """Whether an unprompted (RANDOM) reply has earned the right to be sent.

    Returns ``True`` unchanged for every non-RANDOM trigger — a mention, a
    trigger word or a direct reply is an explicit invitation and is never
    gated.

    The photo handler did not call this at all, so the bot could interject on
    any captioned photo regardless of relevance. That was the defect, not the
    duplication.
    """
    if trigger_type != TriggerType.RANDOM:
        return True

    gate_decision = await relevancy_gate.evaluate(
        chat_id=message.chat.id,
        message_text=message_text,
        config=chat_config,
        message_id=message.message_id,
        user_id=message.from_user.id if message.from_user else None,
    )
    if gate_decision.should_respond:
        return True

    await react_to_silence(message, chat_config, gate_decision, abuse_checker)
    return False


async def finish_reply(
    *,
    message: Message,
    result: PipelineResult,
    sent_message_id: int,
    chat_config: ChatConfig,
    pipeline: TextProcessingPipeline,
    spend_limit_svc: SpendLimitService,
) -> None:
    """Everything that must happen after a reply is sent, in the right order.

    Order is load-bearing: ``post_send`` is what writes the cost row, so the
    spend check has to run after it or it reads a stale total and the warning
    is always one message late.

    The photo path previously did only ``post_send``, so an AI-chosen sticker
    was computed and dropped, and the daily spend warning never fired for a
    photo reply — a limit that silently does not apply to part of the traffic
    is worse than no limit, because it still reads as enforced.
    """
    if result.sticker_file_id:
        try:
            await message.answer_sticker(result.sticker_file_id)
        except Exception as exc:
            logger.warning(
                "Failed to send AI-chosen sticker",
                sticker_file_id=result.sticker_file_id,
                error_type=type(exc).__name__,
            )

    await pipeline.post_send(result, bot_message_id=sent_message_id)

    warning = await spend_limit_svc.get_warning_if_exceeded(chat_config.language)
    if warning:
        try:
            await message.answer(warning)
        except Exception as exc:
            logger.warning(
                "Failed to send spend limit warning",
                chat_id=message.chat.id,
                error_type=type(exc).__name__,
            )
