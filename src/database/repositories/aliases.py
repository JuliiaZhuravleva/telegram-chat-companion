"""Repository for chat_user_aliases -- the names a chat calls its people.

Two roles live in one table and they are read very differently. ``primary`` is
what the bot *says* and there is exactly one active row per person per chat;
``alternate`` is what the bot *understands* and there may be many. See
migration 033 for why the split exists and why automation may only ever write
the second one.

The write paths are modelled on ``KnowledgeRepository``: an advisory lock plus
supersede-before-insert inside one transaction, wrapped by a retry on the
unique race. What is deliberately *not* copied is ``append_fact``'s
"already exists in any status" pre-check. That check exists there because a
retired fact leaving the partial index let a redelivered update resurrect
something a user had removed. Here a retired alias being re-added is a person
changing their mind, which must work -- so the guard is narrower and aimed at
the case that is genuinely wrong: the *same* command arriving twice, which
would otherwise retire a row and insert an identical one, writing a
supersession record for an event that never happened.
"""

from __future__ import annotations

from enum import StrEnum

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# A generous backstop, not the real bound. Aliases render into every prompt, so
# the cap that matters is the roster renderer's; this one only stops a chat
# that has somehow accumulated thousands of rows from pulling them all into a
# request that is going to use a couple of dozen.
_MAX_ALIASES_PER_CHAT = 500

_INSERT_ALIAS = """
    INSERT INTO chat_user_aliases (
        chat_id, user_id, alias, alias_norm, role, status,
        source, source_message_id, source_user_id, confidence
    ) VALUES (
        $1, $2, $3, $4, $5, 'active', $6, $7, $8, $9
    )
    RETURNING id
"""


class AliasWriteOutcome(StrEnum):
    """What a write actually did. Maps 1:1 to a user-facing string."""

    SET = "set"
    UNCHANGED = "unchanged"
    TAKEN = "taken"


class AliasRepository:
    """Reads and writes the per-chat alias table."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def load_active(self, chat_id: int) -> list[asyncpg.Record]:
        """Every active alias in one chat: ``user_id``, ``alias``, ``role``.

        This runs on every turn, so it is one indexed read against
        ``idx_chat_user_aliases_chat_user`` and nothing else -- no join, no
        ordering the caller does not need. Primaries sort first so the name the
        bot will actually use precedes the alternates for the same person, and
        ``id`` breaks the tie so the order is stable across calls rather than
        whatever the heap hands back. Written as an explicit
        ``(role = 'primary') DESC`` and not as ``ORDER BY role``: the plain
        column sort puts ``alternate`` first, alphabetically, which is the
        exact opposite of the intent and reads as correct at a glance.
        """
        return list(
            await self._pool.fetch(
                """
                SELECT user_id, alias, role
                FROM chat_user_aliases
                WHERE chat_id = $1 AND status = 'active'
                ORDER BY (role = 'primary') DESC, id
                LIMIT $2
                """,
                chat_id,
                _MAX_ALIASES_PER_CHAT,
            )
        )

    async def active_owner(self, chat_id: int, alias_norm: str) -> int | None:
        """Which user currently answers to this name here, if anyone."""
        row = await self._pool.fetchrow(
            """
            SELECT user_id FROM chat_user_aliases
            WHERE chat_id = $1 AND alias_norm = $2 AND status = 'active'
            LIMIT 1
            """,
            chat_id,
            alias_norm,
        )
        return int(row["user_id"]) if row is not None else None

    async def set_primary(
        self,
        *,
        chat_id: int,
        user_id: int,
        alias: str,
        alias_norm: str,
        source: str,
        source_message_id: int | None = None,
        source_user_id: int | None = None,
    ) -> tuple[AliasWriteOutcome, int | None]:
        """Make ``alias`` the name the bot uses for ``user_id`` in this chat.

        Returns the outcome and, for ``TAKEN``, the user who already holds the
        name. ``TAKEN`` is checked before anything is written: the second
        partial unique would refuse the insert anyway, but a raised
        ``UniqueViolationError`` cannot tell the caller *who* holds the name,
        and "that name is already Борис's" is the only reply that helps.
        """
        for attempt in (1, 2):
            try:
                return await self._set_primary_once(
                    chat_id=chat_id,
                    user_id=user_id,
                    alias=alias,
                    alias_norm=alias_norm,
                    source=source,
                    source_message_id=source_message_id,
                    source_user_id=source_user_id,
                )
            except asyncpg.UniqueViolationError:
                if attempt == 2:
                    raise
                logger.warning(
                    "alias_set_primary_unique_race_retry",
                    chat_id=chat_id,
                    user_id=user_id,
                )
        raise AssertionError("unreachable")  # pragma: no cover

    async def _set_primary_once(
        self,
        *,
        chat_id: int,
        user_id: int,
        alias: str,
        alias_norm: str,
        source: str,
        source_message_id: int | None,
        source_user_id: int | None,
    ) -> tuple[AliasWriteOutcome, int | None]:
        async with self._pool.acquire() as conn, conn.transaction():
            # Serialize writers of this chat's alias space for the
            # transaction's duration. The lock is on the CHAT, not on
            # (chat, user): the name-ownership check below reads rows
            # belonging to other users, so locking per-user would let two
            # people claim the same name concurrently and leave the unique
            # index to reject one of them with an error nobody can explain.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                str(chat_id),
            )

            owner = await conn.fetchrow(
                """
                SELECT user_id FROM chat_user_aliases
                WHERE chat_id = $1 AND alias_norm = $2 AND status = 'active'
                FOR UPDATE
                """,
                chat_id,
                alias_norm,
            )
            if owner is not None and int(owner["user_id"]) != user_id:
                return AliasWriteOutcome.TAKEN, int(owner["user_id"])

            existing = await conn.fetchrow(
                """
                SELECT id, alias_norm FROM chat_user_aliases
                WHERE chat_id = $1 AND user_id = $2
                  AND role = 'primary' AND status = 'active'
                FOR UPDATE
                """,
                chat_id,
                user_id,
            )
            # The same command arriving twice must not retire a row and insert
            # an identical one -- that records a supersession for an event that
            # never happened, and the user reads two "renamed" confirmations
            # for one rename.
            if existing is not None and str(existing["alias_norm"]) == alias_norm:
                return AliasWriteOutcome.UNCHANGED, user_id

            # Close the old row BEFORE inserting: the unique index is checked
            # per statement, so two active primaries may not coexist even
            # transiently inside the transaction.
            if existing is not None:
                await conn.execute(
                    "UPDATE chat_user_aliases SET status = 'superseded' WHERE id = $1",
                    int(existing["id"]),
                )

            await conn.fetchrow(
                _INSERT_ALIAS,
                chat_id,
                user_id,
                alias,
                alias_norm,
                "primary",
                source,
                source_message_id,
                source_user_id,
                None,
            )
            return AliasWriteOutcome.SET, user_id

    async def add_alternate(
        self,
        *,
        chat_id: int,
        user_id: int,
        alias: str,
        alias_norm: str,
        source: str,
        source_message_id: int | None = None,
        source_user_id: int | None = None,
        confidence: float | None = None,
    ) -> tuple[AliasWriteOutcome, int | None]:
        """Add a name the bot should *understand* as referring to ``user_id``.

        Unlike :meth:`set_primary` this supersedes nothing -- alternates
        accumulate. A name already active for this same person is
        ``UNCHANGED`` rather than a second identical row, and a name active for
        somebody else is ``TAKEN``.

        A previously retired row with the same name is NOT resurrected and NOT
        treated as a conflict: it is history, and a person re-adding a name
        they once dropped gets a new row with its own provenance. The partial
        unique index cannot see the retired row, which is precisely why this
        method decides the question instead of leaving it to the index.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                str(chat_id),
            )
            owner = await conn.fetchrow(
                """
                SELECT user_id FROM chat_user_aliases
                WHERE chat_id = $1 AND alias_norm = $2 AND status = 'active'
                FOR UPDATE
                """,
                chat_id,
                alias_norm,
            )
            if owner is not None:
                held_by = int(owner["user_id"])
                if held_by == user_id:
                    return AliasWriteOutcome.UNCHANGED, held_by
                return AliasWriteOutcome.TAKEN, held_by

            await conn.fetchrow(
                _INSERT_ALIAS,
                chat_id,
                user_id,
                alias,
                alias_norm,
                "alternate",
                source,
                source_message_id,
                source_user_id,
                confidence,
            )
            return AliasWriteOutcome.SET, user_id

    async def retire(self, chat_id: int, alias_norm: str) -> int:
        """Drop a name from use, keeping the row as history.

        Returns how many rows were retired -- 0 means nobody in this chat
        answers to that name, which the caller reports rather than pretending
        a removal happened.
        """
        status = await self._pool.execute(
            """
            UPDATE chat_user_aliases SET status = 'rejected'
            WHERE chat_id = $1 AND alias_norm = $2 AND status = 'active'
            """,
            chat_id,
            alias_norm,
        )
        # asyncpg returns the command tag, e.g. "UPDATE 2".
        return int(status.rsplit(" ", 1)[-1]) if status else 0
