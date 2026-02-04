"""Pattern + embedding abuse filter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

from src.database.repositories.abuse import AbuseRepository
from src.services.ai.router import AIRouter

logger = structlog.get_logger(__name__)

# Severity levels for abuse patterns
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

# Embedding similarity threshold for MEDIUM severity verification
_EMBEDDING_SIMILARITY_THRESHOLD = 0.75


@dataclass
class AbuseFilterResult:
    """Result from abuse pattern/embedding filter."""

    is_abusive: bool
    severity: str | None = None
    matched_pattern: str | None = None
    embedding_similarity: float | None = None
    action: str = "allow"  # allow, block, log


class AbuseFilter:
    """Two-stage abuse filter: regex patterns + embedding verification.

    - HIGH severity patterns: block immediately (zero API cost)
    - MEDIUM severity: verify with embedding similarity
    - LOW severity: log only
    """

    def __init__(
        self,
        abuse_repo: AbuseRepository,
        ai_router: AIRouter,
        *,
        patterns: list[dict[str, Any]] | None = None,
    ) -> None:
        self._repo = abuse_repo
        self._ai_router = ai_router
        self._patterns = patterns or []

    async def check(self, text: str) -> AbuseFilterResult:
        """Check text against abuse patterns and embeddings."""
        text_lower = text.lower()

        # Stage 1: Pattern matching
        for pattern_def in self._patterns:
            pattern = pattern_def.get("pattern", "")
            severity = pattern_def.get("severity", SEVERITY_LOW)

            try:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    if severity == SEVERITY_HIGH:
                        return AbuseFilterResult(
                            is_abusive=True,
                            severity=severity,
                            matched_pattern=pattern,
                            action="block",
                        )
                    elif severity == SEVERITY_MEDIUM:
                        # Verify with embedding
                        similarity = await self._check_embedding(text)
                        if similarity and similarity >= _EMBEDDING_SIMILARITY_THRESHOLD:
                            return AbuseFilterResult(
                                is_abusive=True,
                                severity=severity,
                                matched_pattern=pattern,
                                embedding_similarity=similarity,
                                action="block",
                            )
                        # False positive — not abusive
                        return AbuseFilterResult(is_abusive=False)
                    else:
                        # LOW severity — log only
                        return AbuseFilterResult(
                            is_abusive=False,
                            severity=severity,
                            matched_pattern=pattern,
                            action="log",
                        )
            except re.error:
                logger.warning("Invalid abuse pattern regex", pattern=pattern)

        return AbuseFilterResult(is_abusive=False)

    async def _check_embedding(self, text: str) -> float | None:
        """Check text against abuse embeddings using cosine similarity."""
        try:
            embedding_result = await self._ai_router.generate_embedding(text)
        except Exception:
            logger.warning("Failed to generate embedding for abuse check")
            return None

        rows = await self._repo.search_abuse_embeddings(
            query_embedding=embedding_result.embedding,
            min_similarity=_EMBEDDING_SIMILARITY_THRESHOLD,
            limit=1,
        )

        if rows:
            return float(rows[0]["similarity"])
        return None
