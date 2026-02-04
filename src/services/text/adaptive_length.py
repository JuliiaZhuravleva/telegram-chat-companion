"""Adaptive response length based on median message length.

ADR-009: Computes median of recent non-bot messages to calibrate
how long the bot's response should be.
"""

from __future__ import annotations

from statistics import median


def compute_length_instruction(message_lengths: list[int]) -> str | None:
    """Return a length instruction based on median message length.

    Args:
        message_lengths: Character lengths of recent non-bot messages (pre-filtered ≥5 chars).

    Returns:
        A natural-language length instruction, or ``None`` when no constraint is needed.
    """
    if not message_lengths:
        return None

    med = median(message_lengths)

    if med <= 30:
        return (
            "Respond MAXIMALLY BRIEFLY: 1-2 sentences or even one word/phrase. "
            "Match the laconic style of the chat."
        )
    if med <= 80:
        return "Respond briefly: 1-3 sentences."
    if med <= 150:
        return "Respond moderately: 2-4 sentences."

    # Long messages — no constraint
    return None
