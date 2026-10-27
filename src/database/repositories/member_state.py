"""Repository for `chat_member_state` -- joins, leaves and returns (migration 036).

Every transition is ONE statement, because the same event arrives twice -- the
`chat_member` update and the service message -- in either order, and updates are
handled concurrently. A statement that changed nothing means "duplicate or
stale", and the caller stays silent.

Two rules carry the ordering, and both are measured by the integration tests:

- **A tie goes to the leave.** Telegram timestamps have one-second resolution.
  A join that arrives after a leave stamped the same second (a spam account
  banned the moment it joined) is a late duplicate, not a return: the join
  needs `at > last_left_at`, the leave only `at >= last_joined_at`. The price
  is a genuine rejoin within the same second as the leave, which no person
  manages.
- **Every comparison goes through COALESCE.** A seeded `seen` row and a row
  created by a leave carry NULL timestamps, and a bare `at > NULL` is NULL --
  the transition would silently not happen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg

# A pending greeting older than this is dropped rather than delivered: someone
# who joined two months ago and speaks for the first time is not a newcomer.
GREETING_WINDOW_DAYS = 30


@dataclass(frozen=True)
class JoinOutcome:
    """A join that actually changed the state."""

    kind: str  # "new" | "back"
    join_count: int
    leave_count: int


@dataclass(frozen=True)
class LeaveOutcome:
    """A leave that actually changed the state."""

    status: str  # "left" | "kicked"
    leave_count: int
    joined_at: datetime | None
    """When this stay began, as far as the bot knows: the last join, else the
    first message seen. None for someone the bot never saw at all."""


class ChatMemberStateRepository:
    """Atomic membership transitions for one chat at a time."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_join(
        self, chat_id: int, user_id: int, at: datetime, *, greet: bool
    ) -> JoinOutcome | None:
        """Record a join; None when it was a duplicate or older than the last leave.

        An updated row (`left`, `kicked` or `seen`) is a return. So is a NEW row
        for someone who wrote here before `at`: the bot cannot list members, and
        a regular who left before the mode existed has no row -- without this
        they would be greeted as a stranger. Messages from `at` on are excluded
        on purpose: a join update that arrives late, after the person's first
        message was saved, must still read as a newcomer.

        `greet` decides whether the join leaves a greeting to claim -- False
        when the community mode is off, so switching it on later does not greet
        a month of joins in a burst.
        """
        row = await self._pool.fetchrow(
            """
            WITH prior AS (
                SELECT EXISTS (
                    SELECT 1 FROM chat_messages
                     WHERE chat_id = $1 AND user_id = $2 AND created_at < $3
                ) AS wrote
            )
            INSERT INTO chat_member_state AS s
                   (chat_id, user_id, status, first_seen_at, last_joined_at,
                    join_count, pending_greeting)
            SELECT $1, $2, 'member', $3, $3, 1,
                   CASE WHEN $4::boolean THEN
                        CASE WHEN prior.wrote THEN 'back' ELSE 'new' END
                   END
              FROM prior
            ON CONFLICT (chat_id, user_id) DO UPDATE
               SET status = 'member',
                   last_joined_at = EXCLUDED.last_joined_at,
                   join_count = s.join_count + 1,
                   pending_greeting = CASE WHEN $4::boolean THEN 'back' END,
                   updated_at = now()
             WHERE s.status <> 'member'
               AND EXCLUDED.last_joined_at > COALESCE(s.last_left_at, '-infinity'::timestamptz)
            RETURNING (xmax = 0) AS inserted, (SELECT wrote FROM prior) AS wrote,
                      join_count, leave_count
            """,
            chat_id,
            user_id,
            at,
            greet,
        )
        if row is None:
            return None
        return JoinOutcome(
            kind="new" if row["inserted"] and not row["wrote"] else "back",
            join_count=row["join_count"],
            leave_count=row["leave_count"],
        )

    async def record_leave(
        self, chat_id: int, user_id: int, at: datetime, *, status: str
    ) -> LeaveOutcome | None:
        """Record a leave; None when it was a duplicate or older than the last join.

        Someone the bot has no row for (present before it arrived, never wrote)
        gets a `left` row, so coming back later is recognised as a return.
        """
        if status not in ("left", "kicked"):
            raise ValueError(f"not a leave status: {status!r}")
        row = await self._pool.fetchrow(
            """
            INSERT INTO chat_member_state AS s
                   (chat_id, user_id, status, last_left_at, leave_count)
            VALUES ($1, $2, $4, $3, 1)
            ON CONFLICT (chat_id, user_id) DO UPDATE
               SET status = EXCLUDED.status,
                   last_left_at = EXCLUDED.last_left_at,
                   leave_count = s.leave_count + 1,
                   pending_greeting = NULL,
                   updated_at = now()
             WHERE s.status IN ('member', 'seen')
               AND EXCLUDED.last_left_at >= COALESCE(s.last_joined_at, '-infinity'::timestamptz)
            RETURNING leave_count, COALESCE(last_joined_at, first_seen_at) AS joined_at
            """,
            chat_id,
            user_id,
            at,
            status,
        )
        if row is None:
            return None
        return LeaveOutcome(
            status=status, leave_count=row["leave_count"], joined_at=row["joined_at"]
        )

    async def claim_greeting(self, chat_id: int, user_id: int) -> str | None:
        """Take the pending greeting, at most once: `new`, `back` or None.

        The row is locked in the subquery, so of two concurrent claims the
        second re-reads it after the first commits, finds nothing pending and
        returns None. For anyone not awaiting a greeting -- nearly every message
        -- this is a primary-key lookup that changes no row.
        """
        kind = await self._pool.fetchval(
            """
            UPDATE chat_member_state AS s
               SET pending_greeting = NULL, greeted_at = now(), updated_at = now()
              FROM (SELECT chat_id, user_id, pending_greeting
                      FROM chat_member_state
                     WHERE chat_id = $1 AND user_id = $2
                       AND pending_greeting IS NOT NULL
                       AND last_joined_at > now() - make_interval(days => $3)
                       FOR UPDATE) AS old
             WHERE s.chat_id = old.chat_id AND s.user_id = old.user_id
            RETURNING old.pending_greeting
            """,
            chat_id,
            user_id,
            GREETING_WINDOW_DAYS,
        )
        return str(kind) if kind is not None else None

    async def message_span(
        self, chat_id: int, user_id: int
    ) -> tuple[datetime | None, datetime | None]:
        """First and last time this person wrote here, for the farewell summary."""
        row = await self._pool.fetchrow(
            "SELECT MIN(created_at) AS first, MAX(created_at) AS last "
            "FROM chat_messages WHERE chat_id = $1 AND user_id = $2",
            chat_id,
            user_id,
        )
        return (row["first"], row["last"]) if row else (None, None)
