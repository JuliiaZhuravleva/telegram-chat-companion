"""Repository for the chat_settings per-chat configuration table."""

from __future__ import annotations

from typing import Any

import asyncpg

from src.utils import parse_user_id_list

# Columns that can be written via upsert / set_field.
# Maps Python field names to SQL column names (they happen to match).
_WRITABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "chat_title",
        "chat_type",
        "enabled",
        "trigger_words",
        "random_response_chance",
        "random_response_min_interval",
        "system_prompt",
        "language",
        "rag_enabled",
        "transcribe_voice",
        "transcribe_video_notes",
        "transcribe_expandable",
        "abuse_filter_enabled",
        "sticker_learning_enabled",
        "sticker_response_chance",
        "image_analysis_enabled",
        "save_messages",
        "chunks_enabled",
        "rules_enabled",
        "rules_mode",
        "sticker_reply_to_sticker_enabled",
        "sticker_reply_to_sticker_chance",
        "image_comment_sticker_enabled",
        "image_comment_sticker_chance",
        "tolerance_level",
        "link_comments_enabled",
        "relevancy_gate_enabled",
        "kb_enabled",
        "kb_organizer_ids",
        "last_activity_at",
        "reactions_enabled",
        "reactions_history_enabled",
        "community_enabled",
        "greet_on_first_message",
        "welcome_back_enabled",
        "farewell_public",
        "farewell_dm",
        "returner_joke_enabled",
        "advice_watch_enabled",
        "community_templates",
    }
)


# `kb_organizer_ids` as the writers below build on it: the stored list when it is
# a JSON array of numbers, else an empty one. `parse_user_id_list` reads anything
# else as "nobody", so an add on top of a malformed value would store a list that
# still reads as nobody; starting from an empty one stores the person.
_ORGANIZERS = (
    "(CASE WHEN jsonb_typeof(kb_organizer_ids) = 'array'"
    " AND NOT jsonb_path_exists(kb_organizer_ids, '$[*] ? (@.type() != \"number\")')"
    " THEN kb_organizer_ids ELSE '[]'::jsonb END)"
)

# Each writer is ONE UPDATE that computes the new list from the row it locks, so
# two writes that race (aiogram runs one admin's taps concurrently) both land:
# the second waits for the first and reads its result. A read in Python followed
# by a write lost one of them. Each RETURNs the list now stored; no row -> None.
_ADD_ORGANIZER = f"""
    UPDATE chat_settings
       SET kb_organizer_ids = CASE
           WHEN {_ORGANIZERS} @> jsonb_build_array($2::bigint) THEN {_ORGANIZERS}
           ELSE {_ORGANIZERS} || jsonb_build_array($2::bigint)
       END
     WHERE chat_id = $1
    RETURNING kb_organizer_ids
"""  # noqa: S608 -- local constant

_REMOVE_ORGANIZER = f"""
    UPDATE chat_settings
       SET kb_organizer_ids = COALESCE(
           (SELECT jsonb_agg(e ORDER BY n)
              FROM jsonb_array_elements({_ORGANIZERS}) WITH ORDINALITY AS t(e, n)
             WHERE e <> to_jsonb($2::bigint)),
           '[]'::jsonb)
     WHERE chat_id = $1
    RETURNING kb_organizer_ids
"""  # noqa: S608 -- local constant

# The position is clamped to the list as it is now; NULL appends.
_RESTORE_ORGANIZER = f"""
    UPDATE chat_settings
       SET kb_organizer_ids = CASE
           WHEN {_ORGANIZERS} @> jsonb_build_array($2::bigint) THEN {_ORGANIZERS}
           ELSE jsonb_insert(
               {_ORGANIZERS},
               ARRAY[LEAST(COALESCE($3::int, 2147483647),
                           jsonb_array_length({_ORGANIZERS}))::text],
               to_jsonb($2::bigint))
       END
     WHERE chat_id = $1
    RETURNING kb_organizer_ids
"""  # noqa: S608 -- local constant

_INT4_MAX = 2_147_483_647


class ChatSettingsRepository:
    """Read/write access to per-chat settings."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, chat_id: int) -> dict[str, Any] | None:
        """Get settings for a chat. Returns column dict or None."""
        row = await self._pool.fetchrow(
            "SELECT * FROM chat_settings WHERE chat_id = $1",
            chat_id,
        )
        if row is None:
            return None
        return dict(row)

    async def upsert(self, chat_id: int, **fields: Any) -> None:
        """Insert or update chat settings.

        Only columns in _WRITABLE_COLUMNS are accepted; unknown keys raise ValueError.
        """
        bad_keys = set(fields) - _WRITABLE_COLUMNS
        if bad_keys:
            raise ValueError(f"Unknown chat_settings columns: {bad_keys}")

        if not fields:
            # Just ensure the row exists with defaults
            await self._pool.execute(
                """
                INSERT INTO chat_settings (chat_id)
                VALUES ($1)
                ON CONFLICT (chat_id) DO NOTHING
                """,
                chat_id,
            )
            return

        columns = list(fields.keys())
        values = list(fields.values())

        # Build SET clause: "col1 = $2, col2 = $3, ..."
        set_clause = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(columns))
        # Build INSERT columns and value placeholders
        insert_cols = "chat_id, " + ", ".join(columns)
        insert_vals = "$1, " + ", ".join(f"${i + 2}" for i in range(len(columns)))

        await self._pool.execute(
            f"""
            INSERT INTO chat_settings ({insert_cols})
            VALUES ({insert_vals})
            ON CONFLICT (chat_id) DO UPDATE SET {set_clause}
            """,
            chat_id,
            *values,
        )

    async def set_field(self, chat_id: int, field: str, value: Any) -> bool:
        """Update a single field for a chat; ``False`` means no row matched.

        The bool lets a caller that reports success to a person check it
        first, instead of announcing a write that touched nothing.
        """
        if field not in _WRITABLE_COLUMNS:
            raise ValueError(f"Unknown chat_settings column: {field}")

        status = await self._pool.execute(
            f"UPDATE chat_settings SET {field} = $2 WHERE chat_id = $1",  # noqa: S608
            chat_id,
            value,
        )
        return str(status).split()[-1] != "0"

    async def toggle_bool_field(self, chat_id: int, field: str, effective: bool) -> bool | None:
        """Flip a bool column atomically, returning the value now stored.

        ``None`` means no row matched — the caller must not report success.

        Read-modify-write in Python loses a flip when an admin double-taps a
        toggle: aiogram runs the two updates concurrently, both read the same
        value across their awaits, and both write the same negation, so two
        taps produce one flip. Negating inside the UPDATE removes the window.

        ``effective`` supplies the merged value (YAML/global layers) used only
        when the column is NULL, i.e. "inherited": flipping must start from
        what the chat actually behaves like, not from SQL NULL. When the column
        is non-NULL, COALESCE keeps the stored value and ``effective`` equals
        it anyway, so the negation is the same either way.
        """
        if field not in _WRITABLE_COLUMNS:
            raise ValueError(f"Unknown chat_settings column: {field}")

        stored = await self._pool.fetchval(
            f"UPDATE chat_settings SET {field} = NOT COALESCE({field}, $2) "  # noqa: S608
            f"WHERE chat_id = $1 RETURNING {field}",
            chat_id,
            effective,
        )
        return None if stored is None else bool(stored)

    async def add_kb_organizer(self, chat_id: int, user_id: int) -> list[int] | None:
        """Add ``user_id`` to the chat's organizers unless already there.

        Returns the list now stored, ``None`` when the chat has no row (it
        was never seen, or a group-to-supergroup migration re-keyed it): the
        caller must not report an add then.
        """
        raw = await self._pool.fetchval(_ADD_ORGANIZER, chat_id, user_id)
        return None if raw is None else parse_user_id_list(raw)

    async def remove_kb_organizer(self, chat_id: int, user_id: int) -> list[int] | None:
        """Take ``user_id`` out of the chat's organizers; the list now stored, or ``None``."""
        raw = await self._pool.fetchval(_REMOVE_ORGANIZER, chat_id, user_id)
        return None if raw is None else parse_user_id_list(raw)

    async def restore_kb_organizer(
        self, chat_id: int, user_id: int, position: int | None
    ) -> list[int] | None:
        """Put ``user_id`` back at ``position`` (the undo after a remove).

        The position is clamped to the list as it is now, ``None`` appends, and
        someone already back stays where they are. Returns the list now stored,
        or ``None`` when the chat has no row.
        """
        if position is not None:
            position = min(max(position, 0), _INT4_MAX)
        raw = await self._pool.fetchval(_RESTORE_ORGANIZER, chat_id, user_id, position)
        return None if raw is None else parse_user_id_list(raw)

    async def ensure_exists(
        self,
        chat_id: int,
        chat_title: str | None = None,
        chat_type: str = "group",
        is_forum: bool | None = None,
    ) -> dict[str, Any]:
        """Ensure a chat_settings row exists, return it.

        ``is_forum`` follows the same COALESCE rule as the title: None means
        "the caller doesn't know", a bool overwrites. Callers holding a real
        Chat object should pass ``bool(chat.is_forum)`` rather than the raw
        attribute — Telegram omits the field for non-forums, and passing that
        None through would let a chat that switched forum mode off keep its
        stale True forever (TD-102).
        """
        await self._pool.execute(
            """
            INSERT INTO chat_settings (chat_id, chat_title, chat_type, is_forum)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (chat_id) DO UPDATE
                SET chat_title = COALESCE(EXCLUDED.chat_title, chat_settings.chat_title),
                    chat_type = COALESCE(EXCLUDED.chat_type, chat_settings.chat_type),
                    is_forum = COALESCE(EXCLUDED.is_forum, chat_settings.is_forum)
            """,
            chat_id,
            chat_title,
            chat_type,
            is_forum,
        )
        row = await self.get(chat_id)
        assert row is not None  # just inserted
        return row

    async def set_linked_channel(
        self, chat_id: int, channel_id: int, channel_title: str | None
    ) -> int | None:
        """Record the group's linked channel; returns the id now stored.

        Fills an empty slot or refreshes the title of the SAME channel, and
        never replaces a different one: a mismatch is something to log and look
        at (a group relinked to a new channel), not to overwrite silently on
        whatever post happens to arrive. The caller compares the returned id
        with the one it offered. None means the chat has no row yet.
        """
        stored: int | None = await self._pool.fetchval(
            """
            UPDATE chat_settings
               SET linked_channel_id = COALESCE(linked_channel_id, $2),
                   linked_channel_title = CASE
                       WHEN linked_channel_id IS NULL OR linked_channel_id = $2
                       THEN COALESCE($3, linked_channel_title)
                       ELSE linked_channel_title
                   END
             WHERE chat_id = $1
            RETURNING linked_channel_id
            """,
            chat_id,
            channel_id,
            channel_title,
        )
        return stored

    async def list_enabled(self) -> list[int]:
        """Get all enabled (whitelisted) chat IDs."""
        rows = await self._pool.fetch("SELECT chat_id FROM chat_settings WHERE enabled = true")
        return [row["chat_id"] for row in rows]

    async def list_groups(self) -> list[asyncpg.Record]:
        """Every group the bot has a settings row for -- whether or not it is
        still there, which only Telegram can say (`scripts/community_rollout.py`)."""
        rows: list[asyncpg.Record] = await self._pool.fetch(
            "SELECT chat_id, chat_title FROM chat_settings "
            "WHERE chat_type IN ('group', 'supergroup') ORDER BY chat_id"
        )
        return rows

    async def list_all(self) -> list[asyncpg.Record]:
        """Every chat the bot has a settings row for, with its title.

        The chunk indexer's title lookup. It used to be that worker's list of
        *chats* as well, which is what TD-104 was: a settings row can move away
        from its messages (a group becoming a supergroup re-keys it), and the
        messages left behind then had no entry here and were never indexed
        again. Chats are enumerated from `MessageRepository.list_chat_ids()`
        now; this stays the answer to "what is that chat called", for which a
        missing row simply means "no title".

        Deliberately not `list_enabled()`, for the reason that still applies to
        the title as much as it did to the list: a chat disabled after years of
        saved history still owns that history.
        """
        rows: list[asyncpg.Record] = await self._pool.fetch(
            "SELECT chat_id, chat_title FROM chat_settings ORDER BY chat_id"
        )
        return rows
