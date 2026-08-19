"""Repository for chat_messages table."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


class MessageRepository:
    """Data access layer for chat messages."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(
        self,
        chat_id: int,
        message_id: int,
        message_type: str,
        *,
        user_id: int | None = None,
        username: str | None = None,
        first_name: str | None = None,
        content: str | None = None,
        raw_data: dict[str, Any] | None = None,
        reply_to_message_id: int | None = None,
        is_bot_message: bool = False,
        sticker_file_id: str | None = None,
        sticker_file_unique_id: str | None = None,
        sticker_set_name: str | None = None,
        sticker_emoji: str | None = None,
        message_thread_id: int | None = None,
        quote_text: str | None = None,
        quote_is_manual: bool | None = None,
        transcribed_message_id: int | None = None,
    ) -> None:
        """Save a chat message.

        `transcribed_message_id` (migration 028): set ONLY on a message the bot
        posted that carries a voice transcription, and holds the message_id of
        the audio it transcribes. It is the sole marker of "this bot message is
        a relayed transcription" — see `get_transcription_source`.

        Note the ON CONFLICT branch does NOT touch that column, so re-saving an
        existing row preserves the link rather than clearing it. That is the
        behaviour we want, but it is currently load-bearing only in theory:
        Telegram never delivers the bot's own outgoing message back as an
        update, so the transcription row is written exactly once and never
        re-saved. If a future path (message editing, business connections) can
        re-save it, this is the line that decides whether the link survives.
        """
        await self._pool.execute(
            """
            INSERT INTO chat_messages (
                chat_id, message_id, user_id, username, first_name,
                message_type, content, raw_data, reply_to_message_id,
                is_bot_message, sticker_file_id, sticker_file_unique_id,
                sticker_set_name, sticker_emoji, message_thread_id,
                quote_text, quote_is_manual, transcribed_message_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
            ON CONFLICT (chat_id, message_id) DO UPDATE
            SET transcribed_message_id = COALESCE(
                    EXCLUDED.transcribed_message_id, chat_messages.transcribed_message_id
                ),
                content = EXCLUDED.content,
                edited_at = NOW(),
                edit_count = chat_messages.edit_count + 1,
                original_content = COALESCE(
                    chat_messages.original_content, chat_messages.content
                )
            """,
            chat_id,
            message_id,
            user_id,
            username,
            first_name,
            message_type,
            content,
            None if raw_data is None else json.dumps(raw_data),
            reply_to_message_id,
            is_bot_message,
            sticker_file_id,
            sticker_file_unique_id,
            sticker_set_name,
            sticker_emoji,
            message_thread_id,
            quote_text,
            quote_is_manual,
            transcribed_message_id,
        )

    async def get_transcription_source(
        self,
        chat_id: int,
        message_id: int,
    ) -> asyncpg.Record | None:
        """Resolve a bot transcription message back to the person who spoke.

        Returns None unless `message_id` is a message the bot posted carrying a
        transcription (i.e. its `transcribed_message_id` is set). On a hit, the
        row carries the source audio's id plus the speaker's name and the
        transcript itself, read from the *source* row — the transcription row
        deliberately stores no content of its own (migration 028).

        This replaces matching the rendered header text. A user can make the
        bot echo any string, so a text marker on a bot-authored message proves
        nothing; this column is written by exactly one code path and cannot be
        forged from the chat.

        `source_first_name` / `transcript` are LEFT JOINed and may be NULL if
        the source row was pruned by retention while the transcription row
        survived — callers must treat "recognised as a transcription" and
        "know who said what" as separate facts.
        """
        return await self._pool.fetchrow(
            """
            SELECT t.transcribed_message_id       AS source_message_id,
                   src.user_id                    AS source_user_id,
                   src.first_name                 AS source_first_name,
                   src.username                   AS source_username,
                   src.content                    AS transcript
            FROM chat_messages t
            LEFT JOIN chat_messages src
                   ON src.chat_id = t.chat_id
                  AND src.message_id = t.transcribed_message_id
            WHERE t.chat_id = $1
              AND t.message_id = $2
              AND t.transcribed_message_id IS NOT NULL
            """,
            chat_id,
            message_id,
        )

    async def get_recent(
        self,
        chat_id: int,
        limit: int = 20,
        *,
        exclude_bot: bool = False,
    ) -> list[asyncpg.Record]:
        """Get recent messages for a chat, ordered by newest first.

        Transcription bookkeeping rows are excluded (migration 028). This is
        NOT the same as `exclude_bot`: the relevancy gate's tier-3 judge calls
        this with `exclude_bot=False` on purpose, because it needs to see the
        bot's real replies to judge the conversation — it just must not see a
        content-free row. It rendered as a bare `Bot: ` line in the judge's
        prompt and, with a window of only 5 messages, pushed a real turn out of
        view every time someone sent a voice note.
        """
        query = """
            SELECT id, chat_id, message_id, user_id, username, first_name,
                   message_type, content, is_bot_message, created_at
            FROM chat_messages
            WHERE chat_id = $1
              AND message_type <> 'transcription'
        """
        if exclude_bot:
            query += " AND is_bot_message = false"
        query += " ORDER BY created_at DESC LIMIT $2"
        result: list[asyncpg.Record] = await self._pool.fetch(query, chat_id, limit)
        return result

    async def get_recent_with_topic_context(
        self,
        chat_id: int,
        message_thread_id: int | None,
        *,
        current_topic_limit: int = 20,
        other_topics_limit: int = 10,
    ) -> list[asyncpg.Record]:
        """Get topic-weighted message context for AI.

        When message_thread_id is provided (forum mode):
        - Returns up to current_topic_limit messages from current topic
        - Plus up to other_topics_limit messages from other topics
        - Each row has 'topic_scope' column: 'current', 'other', or NULL

        When message_thread_id is None (non-forum):
        - Falls back to standard get_recent() behavior with NULL topic_scope

        Each row also carries `quote_text` / `quote_is_manual` (migration 021):
        the manually-highlighted reply quote persisted for that message, if
        any. Consumers must gate on `quote_is_manual is True` before treating
        `quote_text` as the user's deliberate focus -- same rule Q-1 applies
        on the live (non-historical) path; a server-attached quote carries no
        such intent.
        """
        if message_thread_id is None:
            # Non-forum: standard query with NULL topic_scope
            result: list[asyncpg.Record] = await self._pool.fetch(
                """
                SELECT id, chat_id, message_id, user_id, username, first_name,
                       message_type, content, is_bot_message, created_at,
                       quote_text, quote_is_manual,
                       NULL::text AS topic_scope
                FROM chat_messages
                WHERE chat_id = $1
                  AND message_type <> 'transcription'
                ORDER BY created_at DESC
                LIMIT $2
                """,
                chat_id,
                current_topic_limit + other_topics_limit,
            )
            return result

        # Forum mode: UNION ALL for topic-weighted context
        result = await self._pool.fetch(
            """
            (SELECT id, chat_id, message_id, user_id, username, first_name,
                    message_type, content, is_bot_message, created_at,
                    quote_text, quote_is_manual,
                    'current' AS topic_scope
               FROM chat_messages
              WHERE chat_id = $1 AND message_thread_id = $2
                AND message_type <> 'transcription'
              ORDER BY created_at DESC LIMIT $3)
            UNION ALL
            (SELECT id, chat_id, message_id, user_id, username, first_name,
                    message_type, content, is_bot_message, created_at,
                    quote_text, quote_is_manual,
                    'other' AS topic_scope
               FROM chat_messages
              WHERE chat_id = $1 AND message_thread_id IS DISTINCT FROM $2
                AND message_type <> 'transcription'
              ORDER BY created_at DESC LIMIT $4)
            """,
            chat_id,
            message_thread_id,
            current_topic_limit,
            other_topics_limit,
        )
        return result

    async def get_recent_lengths(
        self,
        chat_id: int,
        limit: int = 15,
        min_length: int = 5,
    ) -> list[int]:
        """Get lengths of recent non-bot messages for adaptive response length."""
        rows = await self._pool.fetch(
            """
            SELECT LENGTH(content) as msg_length
            FROM chat_messages
            WHERE chat_id = $1
              AND is_bot_message = false
              AND content IS NOT NULL
              AND LENGTH(content) >= $3
            ORDER BY created_at DESC
            LIMIT $2
            """,
            chat_id,
            limit,
            min_length,
        )
        return [row["msg_length"] for row in rows]

    async def get_bot_message_stats(
        self,
        chat_id: int,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get bot message statistics for the relevancy gate.

        Returns aggregated signals from the last `limit` messages in one query:
        total_count, bot_count, bot_ratio, seconds_since_last_bot,
        velocity_per_minute, consecutive_bot_at_end.
        """
        # Single query computes all engagement signals in one round-trip.
        # CTE 'recent' fetches last N messages; 'stats' aggregates counts;
        # 'consecutive' counts trailing bot messages at the end of history.
        row = await self._pool.fetchrow(
            """
            WITH recent AS (
                SELECT is_bot_message, created_at,
                       ROW_NUMBER() OVER (ORDER BY created_at DESC) AS rn
                FROM chat_messages
                WHERE chat_id = $1
                  AND message_type <> 'transcription'
                ORDER BY created_at DESC
                LIMIT $2
            ),
            stats AS (
                SELECT
                    COUNT(*)::int AS total_count,
                    COUNT(*) FILTER (WHERE is_bot_message)::int AS bot_count,
                    MAX(created_at) FILTER (WHERE is_bot_message) AS last_bot_at,
                    COUNT(*) FILTER (
                        WHERE created_at > NOW() - INTERVAL '5 minutes'
                    )::int AS recent_5min_count
                FROM recent
            ),
            first_non_bot AS (
                SELECT MIN(rn) AS rn
                FROM recent
                WHERE NOT is_bot_message
            ),
            consecutive AS (
                SELECT COUNT(*)::int AS consecutive_bot
                FROM recent
                WHERE is_bot_message = true
                  AND rn < COALESCE((SELECT rn FROM first_non_bot), $2 + 1)
            )
            SELECT
                s.total_count,
                s.bot_count,
                CASE WHEN s.total_count > 0
                     THEN s.bot_count::float / s.total_count
                     ELSE 0.0 END AS bot_ratio,
                EXTRACT(EPOCH FROM (NOW() - s.last_bot_at)) AS seconds_since_last_bot,
                CASE WHEN s.recent_5min_count > 0
                     THEN s.recent_5min_count / 5.0
                     ELSE 0.0 END AS velocity_per_minute,
                c.consecutive_bot AS consecutive_bot_at_end
            FROM stats s, consecutive c
            """,
            chat_id,
            limit,
        )
        if row is None:
            return {
                "total_count": 0,
                "bot_count": 0,
                "bot_ratio": 0.0,
                "seconds_since_last_bot": None,
                "velocity_per_minute": 0.0,
                "consecutive_bot_at_end": 0,
            }
        return dict(row)

    async def find_by_username(self, chat_id: int, username: str) -> asyncpg.Record | None:
        """Find the most recent user_id seen posting under `username` in this chat.

        Case-insensitive (Telegram usernames are case-insensitive). Used to
        resolve a plain ``@username`` reply into a concrete user id when
        adding a KB organizer (B-1 stage 2) — the Bot API has no
        username-to-user lookup, so this chat-scoped message history is the
        only available index. Returns ``None`` if this chat's history has no
        record of that username (caller should then check
        ``username_seen_elsewhere`` to phrase the right not-found copy).
        """
        return await self._pool.fetchrow(
            """
            SELECT user_id, first_name
            FROM chat_messages
            WHERE chat_id = $1
              AND user_id IS NOT NULL
              AND username IS NOT NULL
              AND LOWER(username) = LOWER($2)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            chat_id,
            username,
        )

    async def username_seen_elsewhere(self, chat_id: int, username: str) -> bool:
        """Check whether `username` has posted in a chat other than `chat_id`.

        Lets the organizer-add flow distinguish "don't know this username at
        all" from "know them, but not in this chat" (B-1 stage 2).
        """
        row = await self._pool.fetchrow(
            """
            SELECT 1
            FROM chat_messages
            WHERE chat_id != $1
              AND user_id IS NOT NULL
              AND username IS NOT NULL
              AND LOWER(username) = LOWER($2)
            LIMIT 1
            """,
            chat_id,
            username,
        )
        return row is not None

    async def get_top_active_users(
        self, chat_id: int, page: int, per_page: int = 5
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated distinct posters in a chat, ranked by message count desc.

        Powers the KB organizer picker (B-2): lets an admin pick a candidate
        from chat participants instead of guessing a forward/@username.
        Excludes bot messages and rows with no `user_id` (e.g. service
        messages). `username`/`first_name` reflect that user's most recent
        message in this chat -- same chat-scoped-history constraint as
        `find_by_username` (no live Bot API lookup here). The existing
        `idx_chat_messages_user(chat_id, user_id, created_at DESC)` serves the
        per-chat grouping; the final `ORDER BY message_count DESC` still sorts,
        because it orders by an aggregate no index can precompute -- acceptable
        for a low-frequency admin action. `user_id ASC` is a stable tiebreaker
        so page contents don't reshuffle across requests when counts are equal.
        Note the count and the page are two separate statements, so under
        concurrent writes `total` may disagree slightly with the rows returned;
        harmless for a picker, worth knowing before reusing this elsewhere.
        Returns (candidates, total_distinct_posters).
        """
        total = (
            await self._pool.fetchval(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM chat_messages
                WHERE chat_id = $1 AND user_id IS NOT NULL AND is_bot_message = false
                """,
                chat_id,
            )
            or 0
        )
        rows = await self._pool.fetch(
            """
            SELECT user_id,
                   (array_agg(username ORDER BY created_at DESC))[1] AS username,
                   (array_agg(first_name ORDER BY created_at DESC))[1] AS first_name,
                   COUNT(*)::int AS message_count
            FROM chat_messages
            WHERE chat_id = $1 AND user_id IS NOT NULL AND is_bot_message = false
            GROUP BY user_id
            ORDER BY message_count DESC, user_id ASC
            LIMIT $2 OFFSET $3
            """,
            chat_id,
            per_page,
            page * per_page,
        )
        return [dict(r) for r in rows], int(total)

    async def get_for_summary(
        self,
        chat_id: int,
        limit: int = 100,
        *,
        message_thread_id: int | None = None,
    ) -> list[asyncpg.Record]:
        """Get messages for summary generation.

        When message_thread_id is provided, filters to only that topic.
        When None, returns messages from entire chat.
        """
        if message_thread_id is not None:
            result: list[asyncpg.Record] = await self._pool.fetch(
                """
                SELECT user_id, username, first_name, content,
                       is_bot_message, created_at
                FROM chat_messages
                WHERE chat_id = $1
                  AND content IS NOT NULL
                  AND message_thread_id = $3
                ORDER BY created_at DESC
                LIMIT $2
                """,
                chat_id,
                limit,
                message_thread_id,
            )
        else:
            result = await self._pool.fetch(
                """
                SELECT user_id, username, first_name, content,
                       is_bot_message, created_at
                FROM chat_messages
                WHERE chat_id = $1 AND content IS NOT NULL
                ORDER BY created_at DESC
                LIMIT $2
                """,
                chat_id,
                limit,
            )
        return result

    async def list_chat_ids(self) -> list[int]:
        """Every chat that has a message -- which is what "a chat to index" means.

        The chunk indexer used to enumerate `chat_settings` instead, and that
        was a bug with a deadline (TD-104). Telegram assigns a **new**
        `chat_id` when a group becomes a supergroup; `ChatMigrationRepository`
        moves the settings row onto the new id and deliberately leaves
        `chat_messages` on the old one. Everything not yet chunked at that
        moment therefore disappeared from the enumeration for ever -- and the
        loss is never zero, because `_closed_sessions` always defers the
        session that can still grow and a chat is by definition active at the
        moment it upgrades. A year later retention deletes those rows;
        `chat_chunks` is outside retention (ADR-0011), so chunking is the only
        thing standing between that conversation and deletion. One production
        chat was already in exactly that state on 2026-08-19 -- 2 messages, no
        settings row, zero chunks.

        Enumerating here changes *which* chats are considered, not whether the
        owner's toggle is honoured: the indexer still resolves `save_messages`
        per chat through the ordinary three-layer merge, which for a chat with
        no settings row of its own is the global default.

        What this does **not** fix: the chunks are written under the old
        `chat_id`, so retrieval asking as the new supergroup still will not see
        them (TD-111). That is a recoverable state -- a re-key can be applied
        to a chunk table at any time -- whereas deleted messages are not, and
        the two problems have different sizes.

        Cost, measured on production 2026-08-19: a sequential scan, 16 ms over
        39 975 rows to return 19 chats, once per indexer pass (default 15
        minutes). It is linear in the table, not in the number of chats, so it
        grows with history -- at a million rows expect a few hundred
        milliseconds. That is still nothing for a background pass; if it ever
        stops being nothing, the fix is a recursive loose index scan over the
        `chat_id`-leading index rather than a different source of truth.
        """
        rows = await self._pool.fetch("SELECT DISTINCT chat_id FROM chat_messages ORDER BY chat_id")
        return [int(row["chat_id"]) for row in rows]

    async def get_for_chunking(
        self,
        chat_id: int,
        *,
        after_message_id: int,
        limit: int,
    ) -> list[asyncpg.Record]:
        """Messages the chunk indexer has not seen yet, oldest first (S4).

        **The whole chat, not per `message_thread_id`.** The plan assumed that
        column identifies a forum topic; measured on production 2026-08-19 it
        does not. Every chat averages 2.0-2.7 messages per "thread" and ~70%
        of messages have none at all, because Telegram also sets
        `message_thread_id` on ordinary *reply chains* in a supergroup -- one
        chat had 3737 of them. Sessioning by it would shatter a conversation
        into two-message fragments and, worse, split a reply from the message
        it replies to: the reply carries the thread id and the original does
        not. Chunks stay chat-wide until there is a way to tell a real forum
        topic from a reply chain (`chat.is_forum` is not stored).

        Bot messages are included on purpose. A chunk is the conversation, and
        a question whose answer is missing reads as an unanswered question;
        `chat_memory`'s Q&A pairs exist precisely because the bot's own side
        is worth retrieving. `transcription` bookkeeping rows are excluded
        here as everywhere (migration 028) -- they are content-free.

        The three exclusions are the same ones `source_messages` applies, and
        they are here as well as there on purpose. `LIMIT` counts rows, not
        usable rows: a stretch of stickers, photos or content-free rows longer
        than one batch would fill the whole window, yield no chunk, leave the
        watermark where it was -- and be re-read on every pass for ever, with
        the real messages behind it never reached. Filtering in SQL makes the
        batch a batch of *chunkable* messages; the checks in `source_messages`
        stay as the guarantee for any other caller.

        **Ordered by `message_id`, and that is not a detail.** The watermark
        is an id, so the fetch has to advance along the same axis or it can
        step over rows: a batch taken in *time* order spans a wider id range
        than it contains, and every id inside that range but outside the
        batch is then excluded for ever by `message_id > $2`. Measured on
        production 2026-08-19, before this was fixed: the largest chat's first
        2000-message batch spanned 2792 ids, so **792 messages would never
        have been indexed** -- and the second chat 352. Nothing would have
        reported it; the index would simply have been missing a fourteenth of
        the conversation.

        Ordering by id also happens to be the truer conversation order.
        Telegram assigns `message_id` in send order, while `created_at` is
        only as good as whatever wrote the row -- the n8n-era import left
        1.8% of adjacent pairs with time and id disagreeing. Session
        boundaries still come from the timestamps, because a pause is a fact
        about time; `split_sessions` measures each gap against the latest
        moment seen so far, so one stale timestamp cannot invent a pause.
        """
        result: list[asyncpg.Record] = await self._pool.fetch(
            # Raw: the class below is written with `\u` escapes, and a plain
            # triple-quoted string would hand Postgres the characters instead
            # -- which is also how a "\n" inside a comment once ended the
            # comment early and broke the whole query.
            r"""
            SELECT message_id, user_id, username, first_name,
                   message_type, content, is_bot_message, created_at
            FROM chat_messages
            WHERE chat_id = $1
              AND message_id > $2
              AND message_type <> 'transcription'
              AND created_at IS NOT NULL
              AND content IS NOT NULL
              -- The class is `str.strip()`'s, spelled out. `btrim(content)`
              -- trims only U+0020, and `[[:space:]]` adds no more than the
              -- ASCII controls -- while `source_messages` drops everything
              -- Python calls whitespace, so a row of NBSP or U+2028 passed
              -- here and was dropped only after the LIMIT had counted it.
              -- That is the starvation this predicate exists to prevent: a
              -- full batch of such rows yields no chunk, leaves the
              -- watermark where it was, and is re-read on every pass for
              -- ever. Parity is checked against Python's own set, not
              -- against a list someone typed: see the integration test.
              AND content ~ '[^[:space:]\u001c-\u001f\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]'
            ORDER BY message_id ASC
            LIMIT $3
            """,
            chat_id,
            after_message_id,
            limit,
        )
        return result
