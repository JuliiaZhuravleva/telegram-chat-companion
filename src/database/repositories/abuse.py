"""Repository for anti-abuse system tables."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import asyncpg


@dataclass
class AntiAbuseResult:
    """Result from check_anti_abuse() SQL function."""

    should_respond: bool
    response_type: str
    blacklist_just_triggered: bool
    blacklist_timeout_hours: float
    blacklist_ignore_count: int
    response_multiplier: float
    penalty_triggered: bool
    cooldown_remaining_seconds: int
    fatigue_level: int
    max_tokens_adjustment: int
    jailbreak_detected: bool
    jailbreak_pattern_id: int | None
    jailbreak_description: str | None
    jailbreak_hint: str | None
    jailbreak_severity: int | None


class AbuseRepository:
    """Data access layer for anti-abuse system."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def check_anti_abuse(
        self,
        chat_id: int,
        user_id: int,
        content: str,
        *,
        is_addressed_to_bot: bool = False,
    ) -> AntiAbuseResult:
        """Run the check_anti_abuse() SQL function and return parsed result."""
        row = await self._pool.fetchrow(
            "SELECT * FROM check_anti_abuse($1, $2, $3, $4)",
            chat_id, user_id, content, is_addressed_to_bot,
        )
        if row is None:
            return AntiAbuseResult(
                should_respond=True,
                response_type="normal",
                blacklist_just_triggered=False,
                blacklist_timeout_hours=0.0,
                blacklist_ignore_count=0,
                response_multiplier=1.0,
                penalty_triggered=False,
                cooldown_remaining_seconds=0,
                fatigue_level=0,
                max_tokens_adjustment=0,
                jailbreak_detected=False,
                jailbreak_pattern_id=None,
                jailbreak_description=None,
                jailbreak_hint=None,
                jailbreak_severity=None,
            )

        return AntiAbuseResult(
            should_respond=row["should_respond"],
            response_type=row["response_type"],
            blacklist_just_triggered=row["blacklist_just_triggered"],
            blacklist_timeout_hours=float(row["blacklist_timeout_hours"] or 0),
            blacklist_ignore_count=row["blacklist_ignore_count"] or 0,
            response_multiplier=float(
                row["response_multiplier"]
                if isinstance(row["response_multiplier"], Decimal)
                else (row["response_multiplier"] or 1.0)
            ),
            penalty_triggered=row["penalty_triggered"],
            cooldown_remaining_seconds=row["cooldown_remaining_seconds"] or 0,
            fatigue_level=row["fatigue_level"] or 0,
            max_tokens_adjustment=row["max_tokens_adjustment"] or 0,
            jailbreak_detected=row["jailbreak_detected"],
            jailbreak_pattern_id=row["jailbreak_pattern_id"],
            jailbreak_description=row["jailbreak_description"],
            jailbreak_hint=row["jailbreak_hint"],
            jailbreak_severity=row["jailbreak_severity"],
        )

    async def update_cooldown(self, chat_id: int, user_id: int) -> None:
        """Update response cooldown after a bot response."""
        await self._pool.execute(
            "SELECT update_response_cooldown($1, $2)",
            chat_id, user_id,
        )

    async def search_abuse_embeddings(
        self,
        query_embedding: list[float],
        *,
        min_similarity: float = 0.75,
        limit: int = 3,
    ) -> list[asyncpg.Record]:
        """Search abuse embeddings by cosine similarity."""
        result: list[asyncpg.Record] = await self._pool.fetch(
            """
            SELECT id, text, category, description,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM abuse_embeddings
            WHERE enabled = true
              AND embedding IS NOT NULL
              AND 1 - (embedding <=> $1::vector) >= $2
            ORDER BY embedding <=> $1::vector ASC
            LIMIT $3
            """,
            str(query_embedding), min_similarity, limit,
        )
        return result

    async def log_blocked(
        self,
        chat_id: int,
        user_id: int,
        original_text: str,
        detection_method: str,
        *,
        username: str | None = None,
        first_name: str | None = None,
        message_id: int | None = None,
        matched_pattern_id: int | None = None,
        matched_pattern: str | None = None,
        pattern_severity: str | None = None,
        embedding_similarity: float | None = None,
        response_text: str | None = None,
        response_sticker_id: str | None = None,
    ) -> None:
        """Log a blocked abuse attempt."""
        await self._pool.execute(
            """
            INSERT INTO abuse_blocked_log (
                chat_id, user_id, username, first_name, original_text,
                message_id, detection_method, matched_pattern_id,
                matched_pattern, pattern_severity, embedding_similarity,
                response_text, response_sticker_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            chat_id, user_id, username, first_name, original_text,
            message_id, detection_method, matched_pattern_id,
            matched_pattern, pattern_severity, embedding_similarity,
            response_text, response_sticker_id,
        )
