"""Re-key a chat's data when Telegram migrates a group to a supergroup.

Telegram assigns a **new** `chat_id` on upgrade. Nothing in this codebase
handled that (`migrate_to_chat_id` appeared nowhere in `src/`), so the moment
a growing community upgraded its group, its `chat_settings` row and its
entire curated `chat_facts` knowledge base were orphaned -- silently, and
exactly when the chat was becoming more active, not less.

Deliberately its own module rather than a method on either repository: this
is a cross-table operation whose whole point is that both tables move in ONE
transaction, and hanging it off `ChatSettingsRepository` would put
`chat_facts` SQL in a class that has no business knowing about it.

Scope note: only `chat_settings` and `chat_facts` move here. `chat_memory`,
`chat_messages`, `chat_chunks` (S4) and the observability logs are also
chat-keyed and are NOT re-keyed -- moving history is a larger decision (retention, ADR-0011's
preservation invariant) than this slice should make on its own. The outcome
names what it moved so the gap is visible in the log rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MigrationOutcome:
    """What actually happened, so the caller can log or escalate honestly."""

    status: str  # "migrated" | "nothing_to_move" | "target_occupied"
    settings_moved: int = 0
    facts_moved: int = 0


class ChatMigrationRepository:
    """Moves per-chat rows from an old chat_id to the new one."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def migrate(self, old_chat_id: int, new_chat_id: int) -> MigrationOutcome:
        """Move `chat_settings` and `chat_facts` from old to new, atomically.

        Three outcomes, and the third is the reason this is not a bare UPDATE:

        - `nothing_to_move` -- no row for the old id. The common case when the
          same migration is announced twice (Telegram sends a service message
          in *both* the old chat and the new one), so this must be a quiet
          no-op, not an error.

        - `target_occupied` -- the new id ALREADY has settings. Reachable
          because `ChatConfigMiddleware.ensure_exists()` creates a row for
          every chat it sees, and the new supergroup's own service message can
          arrive first. Refused rather than merged: choosing which of two
          settings rows wins, and which of two facts survives a
          `(chat_id, subject, predicate)` unique collision, is a decision for
          a human. Nothing is moved and nothing is destroyed.

        - `migrated` -- the move happened.

        Idempotent by construction: after a successful move the old id has no
        rows, so a repeated announcement lands in `nothing_to_move`.
        """
        if old_chat_id == new_chat_id:
            return MigrationOutcome(status="nothing_to_move")

        async with self._pool.acquire() as conn, conn.transaction():
            # Lock the source row so a concurrent update (the middleware's
            # own ensure_exists on the same service message) cannot slip
            # between the check and the move.
            source = await conn.fetchrow(
                "SELECT chat_id FROM chat_settings WHERE chat_id = $1 FOR UPDATE",
                old_chat_id,
            )
            if source is None:
                return MigrationOutcome(status="nothing_to_move")

            target = await conn.fetchval(
                "SELECT 1 FROM chat_settings WHERE chat_id = $1", new_chat_id
            )
            if target is not None:
                logger.warning(
                    "Chat migration refused: the new chat already has settings — "
                    "merging two settings rows and resolving fact-key collisions "
                    "needs a human, so nothing was moved",
                    old_chat_id=old_chat_id,
                    new_chat_id=new_chat_id,
                )
                return MigrationOutcome(status="target_occupied")

            settings_result = await conn.execute(
                "UPDATE chat_settings SET chat_id = $2 WHERE chat_id = $1",
                old_chat_id,
                new_chat_id,
            )
            facts_result = await conn.execute(
                "UPDATE chat_facts SET chat_id = $2 WHERE chat_id = $1",
                old_chat_id,
                new_chat_id,
            )

        return MigrationOutcome(
            status="migrated",
            settings_moved=_rows_affected(settings_result),
            facts_moved=_rows_affected(facts_result),
        )


def _rows_affected(result: str | None) -> int:
    """asyncpg returns a command tag like ``UPDATE 3``."""
    if not result:
        return 0
    try:
        return int(result.rsplit(" ", 1)[-1])
    except ValueError:
        return 0
