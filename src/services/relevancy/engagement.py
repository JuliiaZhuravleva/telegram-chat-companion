"""Tier 2: Engagement probability based on conversation signals.

Uses one SQL query (via ``MessageRepository.get_bot_message_stats``) to
decide if the bot should stay silent based on conversation dynamics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.database.repositories.messages import MessageRepository

# If the bot authored more than this fraction of recent messages, suppress.
_BOT_RATIO_CAP = 0.25

# Messages per minute threshold — bot backs off in fast conversations.
_VELOCITY_CAP = 5.0

# Max consecutive bot messages at the tail of the conversation.
_CONSECUTIVE_BOT_CAP = 2

# Minimum number of messages before ratio-based rules kick in.
_MIN_MESSAGES_FOR_RATIO = 10


@dataclass(frozen=True)
class EngagementSignals:
    """Raw signals computed from recent chat history."""

    bot_message_ratio: float
    seconds_since_last_bot: float | None
    velocity_per_minute: float
    consecutive_bot_at_end: int
    total_messages: int


@dataclass(frozen=True)
class EngagementResult:
    """Outcome of Tier 2 engagement check."""

    should_pass: bool
    reason: str = ""
    signals: EngagementSignals | None = None


async def compute_engagement(
    chat_id: int,
    message_repo: MessageRepository,
) -> EngagementResult:
    """Compute engagement signals and apply rules.

    Returns ``should_pass=False`` when conversation dynamics indicate the
    bot would be intrusive.
    """
    stats: dict[str, Any] = await message_repo.get_bot_message_stats(chat_id, limit=20)

    signals = EngagementSignals(
        bot_message_ratio=stats["bot_ratio"],
        seconds_since_last_bot=stats["seconds_since_last_bot"],
        velocity_per_minute=stats["velocity_per_minute"],
        consecutive_bot_at_end=stats["consecutive_bot_at_end"],
        total_messages=stats["total_count"],
    )

    # Rule: bot is dominating the conversation
    if (
        signals.bot_message_ratio > _BOT_RATIO_CAP
        and signals.total_messages >= _MIN_MESSAGES_FOR_RATIO
    ):
        return EngagementResult(False, "bot_ratio_exceeded", signals)

    # Rule: conversation is moving fast — people are talking among themselves
    if signals.velocity_per_minute > _VELOCITY_CAP:
        return EngagementResult(False, "high_velocity", signals)

    # Rule: bot already responded to the last N messages randomly — avoid monologue
    if signals.consecutive_bot_at_end >= _CONSECUTIVE_BOT_CAP:
        return EngagementResult(False, "consecutive_bot_responses", signals)

    return EngagementResult(True, "", signals)
