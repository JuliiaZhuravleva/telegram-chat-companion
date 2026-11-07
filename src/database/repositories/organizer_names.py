"""The names knowledge-base organizers had when they were added (``kb_organizer_names``).

The organizer list asks Telegram first (``getChatMember``). For someone who is not
in the group, Telegram answers with an error and no name, and that happens often: the
person picker offers anyone. Then the list reads the name stored here, and after
that the latest name the person used in this chat's history.

Migration 038 explains why this is a table of its own and not a ``chat_settings``
column.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True, slots=True)
class KnownName:
    """The parts of a person's name the bot was given; any of them may be missing."""

    first_name: str | None
    last_name: str | None
    username: str | None


def _clean(value: object) -> str | None:
    """A name part as stored: stripped text, or None for a missing or blank part."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


_REMEMBER = """
    INSERT INTO kb_organizer_names (chat_id, user_id, first_name, last_name, username)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (chat_id, user_id) DO UPDATE SET
        first_name = EXCLUDED.first_name,
        last_name  = EXCLUDED.last_name,
        username   = EXCLUDED.username,
        updated_at = now()
"""

# The newest message that carries a name. `created_at` is nullable (rows from the
# n8n-era import), so NULLS LAST: a DESC sort puts NULLs first by default, and
# a NULL-dated row would then count as the newest.
_REMEMBER_SEEN = """
    INSERT INTO kb_organizer_names (chat_id, user_id, first_name, last_name, username)
    SELECT chat_id, user_id, NULLIF(btrim(first_name), ''), NULL, NULLIF(btrim(username), '')
    FROM chat_messages
    WHERE chat_id = $1
      AND user_id = $2
      AND (NULLIF(btrim(first_name), '') IS NOT NULL OR NULLIF(btrim(username), '') IS NOT NULL)
    ORDER BY created_at DESC NULLS LAST, id DESC
    LIMIT 1
    ON CONFLICT (chat_id, user_id) DO UPDATE SET
        first_name = EXCLUDED.first_name,
        last_name  = EXCLUDED.last_name,
        username   = EXCLUDED.username,
        updated_at = now()
"""

_NAMES = """
    SELECT u.user_id,
           n.user_id IS NOT NULL AS stored,
           n.first_name, n.last_name, n.username,
           s.first_name AS seen_first_name, s.username AS seen_username
    FROM unnest($2::bigint[]) AS u(user_id)
    LEFT JOIN kb_organizer_names n
           ON n.chat_id = $1 AND n.user_id = u.user_id
    LEFT JOIN LATERAL (
        SELECT NULLIF(btrim(m.first_name), '') AS first_name,
               NULLIF(btrim(m.username), '') AS username
        FROM chat_messages m
        WHERE m.chat_id = $1
          AND m.user_id = u.user_id
          AND (NULLIF(btrim(m.first_name), '') IS NOT NULL
               OR NULLIF(btrim(m.username), '') IS NOT NULL)
        ORDER BY m.created_at DESC NULLS LAST, m.id DESC
        LIMIT 1
    ) s ON true
"""


class OrganizerNameRepository:
    """Names of knowledge-base organizers, per chat."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def remember(
        self,
        chat_id: int,
        user_id: int,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        username: str | None = None,
    ) -> bool:
        """Store the name an add path was given, replacing an older one.

        Returns False and writes nothing when every part is missing: an empty row
        would hide the name from the chat history behind it.
        """
        parts = (_clean(first_name), _clean(last_name), _clean(username))
        if not any(parts):
            return False
        await self._pool.execute(_REMEMBER, chat_id, user_id, *parts)
        return True

    async def remember_seen(self, chat_id: int, user_id: int) -> bool:
        """Store the latest name ``user_id`` posted under in this chat.

        For the add from the participant list, where the name comes from the
        chat's history. Returns False when that history holds no name for them.
        """
        status = await self._pool.execute(_REMEMBER_SEEN, chat_id, user_id)
        return str(status).split()[-1] != "0"

    async def names(self, chat_id: int, user_ids: Iterable[int]) -> dict[int, KnownName]:
        """The best known name per id: the stored one, else the latest seen in the chat.

        An id with neither is left out of the result.
        """
        wanted = list(dict.fromkeys(user_ids))
        if not wanted:
            return {}
        rows = await self._pool.fetch(_NAMES, chat_id, wanted)
        found: dict[int, KnownName] = {}
        for row in rows:
            stored = KnownName(row["first_name"], row["last_name"], row["username"])
            if row["stored"] and any((stored.first_name, stored.last_name, stored.username)):
                found[int(row["user_id"])] = stored
            elif row["seen_first_name"] or row["seen_username"]:
                found[int(row["user_id"])] = KnownName(
                    row["seen_first_name"], None, row["seen_username"]
                )
        return found
