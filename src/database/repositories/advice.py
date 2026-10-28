"""Repository for the blog advice classifier (migration 037).

`advice_checked` is the worker's cursor, not `advice_verdicts`: a verdict
covers a whole series, and a message the model failed on for good must stop
being retried without producing a verdict. Saving a verdict and checking off
its messages happen in one transaction, so a crash between them cannot leave a
series that is judged but still "unread" -- that would be judged twice.

Every read of `chat_messages` here bounds `created_at` from below, and the
column is nullable (n8n-era rows): those rows silently fall out, which is
right for a window of the last days and would be wrong for anything else.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from src.services.advice.context import Line
from src.services.advice.verdict import Verdict

_LINE_COLUMNS = """
    message_id, created_at, COALESCE(content, '') AS content, user_id, first_name,
    username, reply_to_message_id, sender_kind, sender_chat_title,
    COALESCE(is_bot_message, false) AS is_bot_message
"""


def _line(row: asyncpg.Record) -> Line:
    return Line(
        message_id=row["message_id"],
        created_at=row["created_at"],
        content=row["content"],
        user_id=row["user_id"],
        first_name=row["first_name"],
        username=row["username"],
        reply_to=row["reply_to_message_id"],
        sender_kind=row["sender_kind"],
        sender_chat_title=row["sender_chat_title"],
        is_bot=row["is_bot_message"],
    )


@dataclass(frozen=True)
class DigestItem:
    """One finding for the owner's daily digest."""

    chat_id: int
    chat_title: str | None
    chat_type: str | None
    user_id: int | None
    name: str | None
    first_message_id: int
    quote: str
    what: str | None
    advice: str
    level: str | None
    support: int


class AdviceRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def unchecked_candidates(
        self, chat_id: int, since: datetime, exclude_user_ids: Sequence[int] = ()
    ) -> list[Line]:
        """Members' messages from `since` on that the worker has not finished with.

        A member is an ordinary account (`sender_kind IS NULL`, not a bot) --
        the author's own comments arrive as the anonymous admin and are never
        judged. `exclude_user_ids` takes the bot's admins out too, in case one
        of them comments from a personal account.
        """
        rows = await self._pool.fetch(
            f"""
            SELECT {_LINE_COLUMNS}
              FROM chat_messages m
             WHERE m.chat_id = $1
               AND m.created_at >= $2
               AND COALESCE(m.is_bot_message, false) = false
               AND m.sender_kind IS NULL
               AND m.user_id IS NOT NULL
               AND NOT (m.user_id = ANY($3::bigint[]))
               AND btrim(COALESCE(m.content, '')) <> ''
               AND NOT EXISTS (
                   SELECT 1 FROM advice_checked c
                    WHERE c.chat_id = m.chat_id AND c.message_id = m.message_id
               )
             ORDER BY m.created_at, m.message_id
            """,  # noqa: S608 -- local constant, no input
            chat_id,
            since,
            list(exclude_user_ids),
        )
        return [_line(r) for r in rows]

    async def timeline(self, chat_id: int, since: datetime) -> list[Line]:
        """Every row of the chat from `since` on, in time order."""
        rows = await self._pool.fetch(
            f"""
            SELECT {_LINE_COLUMNS}
              FROM chat_messages
             WHERE chat_id = $1 AND created_at >= $2
             ORDER BY created_at, message_id
            """,  # noqa: S608 -- local constant, no input
            chat_id,
            since,
        )
        return [_line(r) for r in rows]

    async def lines_by_id(self, chat_id: int, message_ids: Sequence[int]) -> list[Line]:
        """Specific rows -- the older posts and replies a reply chain reaches."""
        if not message_ids:
            return []
        rows = await self._pool.fetch(
            f"""
            SELECT {_LINE_COLUMNS}
              FROM chat_messages
             WHERE chat_id = $1 AND message_id = ANY($2::bigint[])
            """,  # noqa: S608 -- local constant, no input
            chat_id,
            list(message_ids),
        )
        return [_line(r) for r in rows]

    async def get_post_facts(self, chat_id: int, post_message_id: int) -> dict[str, Any] | None:
        raw = await self._pool.fetchval(
            "SELECT facts FROM advice_post_facts WHERE chat_id = $1 AND post_message_id = $2",
            chat_id,
            post_message_id,
        )
        return None if raw is None else dict(json.loads(raw))

    async def put_post_facts(
        self, chat_id: int, post_message_id: int, facts: dict[str, Any]
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO advice_post_facts (chat_id, post_message_id, facts)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (chat_id, post_message_id) DO NOTHING
            """,
            chat_id,
            post_message_id,
            json.dumps(facts, ensure_ascii=False),
        )

    async def save_verdict(
        self,
        chat_id: int,
        *,
        user_id: int | None,
        message_ids: Sequence[int],
        post_message_id: int | None,
        facts: dict[str, Any],
        verdict: Verdict,
        model: str | None,
    ) -> bool:
        """Store the verdict and check off its messages; False if it was already stored."""
        async with self._pool.acquire() as conn, conn.transaction():
            inserted = await conn.fetchval(
                """
                INSERT INTO advice_verdicts (
                    chat_id, user_id, first_message_id, last_message_id, message_ids,
                    post_message_id, facts, advice, level, support, constructive, model
                )
                VALUES ($1, $2, $3, $4, $5::bigint[], $6, $7::jsonb, $8, $9, $10, $11, $12)
                ON CONFLICT (chat_id, first_message_id) DO NOTHING
                RETURNING id
                """,
                chat_id,
                user_id,
                message_ids[0],
                message_ids[-1],
                list(message_ids),
                post_message_id,
                json.dumps(facts, ensure_ascii=False),
                verdict.advice,
                verdict.level,
                verdict.support,
                verdict.constructive,
                model,
            )
            await self._check(conn, chat_id, message_ids)
        return inserted is not None

    async def mark_checked(self, chat_id: int, message_ids: Sequence[int]) -> None:
        """Give up on messages without a verdict (the model failed on them for good)."""
        async with self._pool.acquire() as conn:
            await self._check(conn, chat_id, message_ids)

    @staticmethod
    async def _check(conn: asyncpg.Connection, chat_id: int, message_ids: Sequence[int]) -> None:
        await conn.execute(
            """
            INSERT INTO advice_checked (chat_id, message_id)
            SELECT $1, unnest($2::bigint[])
            ON CONFLICT (chat_id, message_id) DO NOTHING
            """,
            chat_id,
            list(message_ids),
        )

    async def digest_items(self, cutoff: datetime) -> list[DigestItem]:
        """Unreported findings up to `cutoff`: unsolicited advice and full support.

        The name and the quote come from `chat_messages`, so a message deleted
        by retention since leaves an empty quote rather than a missing finding.
        """
        rows = await self._pool.fetch(
            """
            SELECT v.chat_id, s.chat_title, s.chat_type, v.user_id,
                   v.first_message_id, v.advice, v.level, v.support,
                   v.facts ->> 'what' AS what,
                   (SELECT COALESCE(m.first_name, m.username)
                      FROM chat_messages m
                     WHERE m.chat_id = v.chat_id AND m.message_id = v.first_message_id
                   ) AS name,
                   (SELECT string_agg(m.content, E'\n' ORDER BY m.message_id)
                      FROM chat_messages m
                     WHERE m.chat_id = v.chat_id AND m.message_id = ANY(v.message_ids)
                   ) AS quote
              FROM advice_verdicts v
              LEFT JOIN chat_settings s ON s.chat_id = v.chat_id
             WHERE v.notified_at IS NULL
               AND v.created_at <= $1
               AND (v.advice = 'unsolicited' OR v.support = 2)
             ORDER BY v.chat_id, v.first_message_id
            """,
            cutoff,
        )
        return [
            DigestItem(
                chat_id=r["chat_id"],
                chat_title=r["chat_title"],
                chat_type=r["chat_type"],
                user_id=r["user_id"],
                name=r["name"],
                first_message_id=r["first_message_id"],
                quote=r["quote"] or "",
                what=r["what"],
                advice=r["advice"],
                level=r["level"],
                support=r["support"],
            )
            for r in rows
        ]

    async def mark_notified(self, cutoff: datetime) -> int:
        """Close every verdict up to `cutoff`, findings or not.

        Non-findings are closed too: the digest has covered them, and leaving
        them NULL would grow the partial index for ever.
        """
        result = await self._pool.execute(
            """
            UPDATE advice_verdicts SET notified_at = now()
             WHERE notified_at IS NULL AND created_at <= $1
            """,
            cutoff,
        )
        return int(result.split()[-1])

    async def calls_since(self, since: datetime) -> int:
        """Successful classifier calls since `since` (rows `log_usage` wrote)."""
        return int(
            await self._pool.fetchval(
                """
                SELECT COUNT(*) FROM response_log
                 WHERE task_type = 'advice_watch' AND created_at >= $1
                """,
                since,
            )
            or 0
        )
