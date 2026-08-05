"""Anti-abuse SQL function wrapper."""

from __future__ import annotations

import structlog

from src.database.repositories.abuse import AbuseRepository, AntiAbuseResult

logger = structlog.get_logger(__name__)


class AntiAbuseChecker:
    """Thin wrapper around the ``check_anti_abuse()`` SQL function.

    Runs all abuse checks in a single database call:
    jailbreak patterns, message blacklist, cooldown, fatigue, penalties.
    """

    def __init__(self, abuse_repo: AbuseRepository) -> None:
        self._repo = abuse_repo

    async def check(
        self,
        chat_id: int,
        user_id: int,
        content: str,
        *,
        is_addressed_to_bot: bool = False,
    ) -> AntiAbuseResult:
        """Run the full anti-abuse check.

        Returns an ``AntiAbuseResult`` with disposition and metadata.
        """
        return await self._repo.check_anti_abuse(
            chat_id=chat_id,
            user_id=user_id,
            content=content,
            is_addressed_to_bot=is_addressed_to_bot,
        )

    async def update_cooldown(self, chat_id: int, user_id: int) -> None:
        """Update response cooldown after the bot sends a response."""
        await self._repo.update_cooldown(chat_id, user_id)

    async def is_in_cooldown(self, chat_id: int, user_id: int) -> bool:
        """Whether the bot is still inside its response cooldown for this user.

        Side-effect free, unlike :meth:`check`. Used by the R-5 reaction path
        (`bot/handlers/message.py`), which emits a user-visible bot action from
        outside `TextProcessingPipeline` and therefore never passes through the
        pipeline's Stage 1 abuse gate.
        """
        return await self._repo.get_cooldown_remaining_seconds(chat_id, user_id) > 0
