"""Repository for sticker_knowledge, sticker_sets, and admin_sticker_notifications tables."""

from __future__ import annotations

from typing import Any

import asyncpg


class StickerRepository:
    """Data access layer for sticker intelligence."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ── sticker_knowledge ─────────────────────────────────────────────

    async def get_by_file_unique_id(
        self, file_unique_id: str
    ) -> asyncpg.Record | None:
        """Look up a sticker by its unique ID."""
        return await self._pool.fetchrow(
            "SELECT * FROM sticker_knowledge WHERE file_unique_id = $1",
            file_unique_id,
        )

    async def get_by_file_id(self, file_id: str) -> asyncpg.Record | None:
        """Look up a sticker by its (non-unique) file_id."""
        return await self._pool.fetchrow(
            "SELECT * FROM sticker_knowledge WHERE file_id = $1",
            file_id,
        )

    async def save_sticker(
        self,
        *,
        file_unique_id: str,
        file_id: str,
        set_name: str | None = None,
        emoji: str | None = None,
        is_animated: bool = False,
        is_video: bool = False,
        visual_description: str | None = None,
        original_vision_description: str | None = None,
        emotion: str | None = None,
        suggested_contexts: list[str] | None = None,
        style_tags: list[str] | None = None,
        character_or_meme: str | None = None,
        usage_contexts: list[str] | None = None,
        analysis_failed: bool = False,
    ) -> int:
        """Insert or update a sticker. Returns sticker ID.

        On conflict (file_unique_id): increments total_uses and updates file_id.
        """
        row = await self._pool.fetchrow(
            """
            INSERT INTO sticker_knowledge (
                file_unique_id, file_id, set_name, emoji,
                is_animated, is_video,
                visual_description, original_vision_description,
                emotion, suggested_contexts, style_tags, character_or_meme,
                usage_contexts, analysis_failed, analyzed_at, total_uses
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                CASE WHEN $7::TEXT IS NOT NULL THEN NOW() END,
                1
            )
            ON CONFLICT (file_unique_id) DO UPDATE
            SET file_id = EXCLUDED.file_id,
                total_uses = sticker_knowledge.total_uses + 1,
                last_used_at = NOW()
            RETURNING id
            """,
            file_unique_id,
            file_id,
            set_name,
            emoji,
            is_animated,
            is_video,
            visual_description,
            original_vision_description,
            emotion,
            suggested_contexts,
            style_tags,
            character_or_meme,
            usage_contexts or [],
            analysis_failed,
        )
        assert row is not None
        return int(row["id"])

    async def update_embedding(
        self,
        file_unique_id: str,
        embedding: list[float],
    ) -> None:
        """Store the semantic embedding for a sticker."""
        await self._pool.execute(
            """
            UPDATE sticker_knowledge
            SET description_embedding = $2
            WHERE file_unique_id = $1
            """,
            file_unique_id,
            embedding,
        )

    async def increment_usage(
        self,
        file_unique_id: str,
        *,
        is_bot_use: bool = False,
    ) -> None:
        """Increment usage counters for an existing sticker."""
        await self._pool.execute(
            """
            UPDATE sticker_knowledge
            SET total_uses = total_uses + 1,
                bot_uses = bot_uses + CASE WHEN $2 THEN 1 ELSE 0 END,
                last_used_at = NOW()
            WHERE file_unique_id = $1
            """,
            file_unique_id,
            is_bot_use,
        )

    async def search_by_embedding(
        self,
        query_embedding: list[float],
        *,
        limit: int = 5,
        min_similarity: float = 0.6,
    ) -> list[asyncpg.Record]:
        """Semantic search using cosine similarity.

        Returns stickers with similarity >= min_similarity,
        ordered by descending similarity.
        """
        result: list[asyncpg.Record] = await self._pool.fetch(
            """
            SELECT
                file_id, file_unique_id, visual_description, emotion,
                character_or_meme, suggested_contexts, usage_contexts,
                1 - (description_embedding <=> $1) AS similarity,
                total_uses, bot_uses
            FROM sticker_knowledge
            WHERE description_embedding IS NOT NULL
              AND visual_description IS NOT NULL
              AND analysis_failed = false
              AND 1 - (description_embedding <=> $1) >= $2
            ORDER BY description_embedding <=> $1
            LIMIT $3
            """,
            query_embedding,
            min_similarity,
            limit,
        )
        return result

    async def get_pack_context(
        self,
        set_name: str,
        *,
        exclude_file_unique_id: str | None = None,
        limit: int = 5,
    ) -> list[asyncpg.Record]:
        """Get descriptions of other stickers in the same set.

        Used to provide context in the Vision API prompt.
        """
        if exclude_file_unique_id:
            result: list[asyncpg.Record] = await self._pool.fetch(
                """
                SELECT visual_description, emotion, character_or_meme
                FROM sticker_knowledge
                WHERE set_name = $1
                  AND file_unique_id != $2
                  AND visual_description IS NOT NULL
                ORDER BY total_uses DESC
                LIMIT $3
                """,
                set_name,
                exclude_file_unique_id,
                limit,
            )
            return result
        rows: list[asyncpg.Record] = await self._pool.fetch(
            """
            SELECT visual_description, emotion, character_or_meme
            FROM sticker_knowledge
            WHERE set_name = $1
              AND visual_description IS NOT NULL
            ORDER BY total_uses DESC
            LIMIT $2
            """,
            set_name,
            limit,
        )
        return rows

    async def accumulate_context(
        self,
        file_unique_id: str,
        context_text: str,
        *,
        max_contexts: int = 10,
    ) -> None:
        """Add a usage context string, FIFO capped at max_contexts."""
        # Dedup: skip if this exact text already exists
        # FIFO: trim from the front if over max
        await self._pool.execute(
            """
            UPDATE sticker_knowledge
            SET usage_contexts = (
                SELECT array_agg(ctx)
                FROM (
                    SELECT unnest(
                        CASE
                            WHEN $2 = ANY(usage_contexts) THEN usage_contexts
                            ELSE usage_contexts || $2
                        END
                    ) AS ctx
                    OFFSET GREATEST(0,
                        array_length(
                            CASE
                                WHEN $2 = ANY(usage_contexts) THEN usage_contexts
                                ELSE usage_contexts || $2
                            END,
                            1
                        ) - $3
                    )
                ) sub
            )
            WHERE file_unique_id = $1
            """,
            file_unique_id,
            context_text,
            max_contexts,
        )

    # ── Admin description editing ─────────────────────────────────────

    async def update_description_and_fields(
        self,
        *,
        file_unique_id: str,
        visual_description: str,
        emotion: str | None = None,
        suggested_contexts: list[str] | None = None,
        character_or_meme: str | None = None,
        admin_notes: str | None = None,
    ) -> None:
        """Update description and related fields after admin merge."""
        await self._pool.execute(
            """
            UPDATE sticker_knowledge
            SET visual_description = $2,
                emotion = COALESCE($3::TEXT, emotion),
                suggested_contexts = COALESCE($4::TEXT[], suggested_contexts),
                character_or_meme = COALESCE($5::TEXT, character_or_meme),
                admin_notes = COALESCE($6::TEXT, admin_notes),
                updated_at = NOW()
            WHERE file_unique_id = $1
            """,
            file_unique_id,
            visual_description,
            emotion,
            suggested_contexts,
            character_or_meme,
            admin_notes,
        )

    async def append_admin_note(self, file_unique_id: str, note: str) -> None:
        """Append a note to admin_notes. Simple — no nullable array params."""
        await self._pool.execute(
            """
            UPDATE sticker_knowledge
            SET admin_notes = CASE
                    WHEN admin_notes IS NOT NULL THEN admin_notes || E'\n' || $2
                    ELSE $2
                END,
                updated_at = NOW()
            WHERE file_unique_id = $1
            """,
            file_unique_id,
            note,
        )

    async def clear_for_reanalysis(self, file_unique_id: str) -> None:
        """Clear analysis fields to force re-analysis on next encounter."""
        await self._pool.execute(
            """
            UPDATE sticker_knowledge
            SET visual_description = NULL,
                description_embedding = NULL,
                analysis_failed = false,
                analyzed_at = NULL,
                updated_at = NOW()
            WHERE file_unique_id = $1
            """,
            file_unique_id,
        )

    # ── Set browsing (admin wizard) ──────────────────────────────────

    async def get_all_sets_with_stats(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[asyncpg.Record]:
        """Get all sticker sets with learned sticker counts."""
        return await self._pool.fetch(  # type: ignore[no-any-return]
            """
            SELECT
                ss.set_name, ss.set_title, ss.total_count,
                ss.is_animated, ss.is_video, ss.updated_at,
                COUNT(sk.id) AS learned_count,
                COUNT(sk.description_embedding) AS with_embedding
            FROM sticker_sets ss
            LEFT JOIN sticker_knowledge sk ON sk.set_name = ss.set_name
            GROUP BY ss.set_name, ss.set_title, ss.total_count,
                     ss.is_animated, ss.is_video, ss.updated_at
            ORDER BY learned_count DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )

    async def count_sets(self) -> int:
        """Count all known sticker sets."""
        val = await self._pool.fetchval("SELECT COUNT(*) FROM sticker_sets")
        return int(val or 0)

    async def get_stickers_in_set(
        self,
        set_name: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[asyncpg.Record]:
        """Get stickers in a specific set (paginated)."""
        return await self._pool.fetch(  # type: ignore[no-any-return]
            """
            SELECT file_unique_id, file_id, emoji, visual_description,
                   emotion, character_or_meme, total_uses, bot_uses,
                   is_animated, is_video, analysis_failed
            FROM sticker_knowledge
            WHERE set_name = $1
            ORDER BY total_uses DESC
            LIMIT $2 OFFSET $3
            """,
            set_name,
            limit,
            offset,
        )

    async def count_stickers_in_set(self, set_name: str) -> int:
        """Count stickers in a specific set."""
        val = await self._pool.fetchval(
            "SELECT COUNT(*) FROM sticker_knowledge WHERE set_name = $1",
            set_name,
        )
        return int(val or 0)

    # ── Admin notifications ──────────────────────────────────────────

    async def save_notification(
        self,
        *,
        file_unique_id: str,
        admin_id: int,
        message_id: int,
        sticker_msg_id: int,
        chat_id: int,
    ) -> int:
        """Save admin sticker notification for reply tracking."""
        row = await self._pool.fetchrow(
            """
            INSERT INTO admin_sticker_notifications
                (file_unique_id, admin_id, message_id, sticker_msg_id, chat_id)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            file_unique_id,
            admin_id,
            message_id,
            sticker_msg_id,
            chat_id,
        )
        assert row is not None
        return int(row["id"])

    async def get_latest_sticker_msg(
        self,
        admin_id: int,
        chat_id: int,
    ) -> int | None:
        """Get the most recent sticker_msg_id for an admin in a chat."""
        val = await self._pool.fetchval(
            """
            SELECT sticker_msg_id FROM admin_sticker_notifications
            WHERE admin_id = $1 AND chat_id = $2 AND sticker_msg_id > 0
            ORDER BY created_at DESC
            LIMIT 1
            """,
            admin_id,
            chat_id,
        )
        return int(val) if val else None

    async def get_notification_by_reply(
        self,
        chat_id: int,
        reply_to_message_id: int,
    ) -> asyncpg.Record | None:
        """Look up notification by the reply message ID in admin's DM."""
        return await self._pool.fetchrow(
            """
            SELECT * FROM admin_sticker_notifications
            WHERE chat_id = $1 AND (message_id = $2 OR sticker_msg_id = $2)
            """,
            chat_id,
            reply_to_message_id,
        )

    # ── sticker_sets ──────────────────────────────────────────────────

    async def upsert_sticker_set(
        self,
        *,
        set_name: str,
        set_title: str | None = None,
        total_count: int = 0,
        thumbnail_file_id: str | None = None,
        is_animated: bool = False,
        is_video: bool = False,
    ) -> None:
        """Insert or update sticker set metadata cache."""
        await self._pool.execute(
            """
            INSERT INTO sticker_sets (
                set_name, set_title, total_count,
                thumbnail_file_id, is_animated, is_video
            ) VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (set_name) DO UPDATE
            SET set_title = EXCLUDED.set_title,
                total_count = EXCLUDED.total_count,
                thumbnail_file_id = EXCLUDED.thumbnail_file_id,
                is_animated = EXCLUDED.is_animated,
                is_video = EXCLUDED.is_video
            """,
            set_name,
            set_title,
            total_count,
            thumbnail_file_id,
            is_animated,
            is_video,
        )

    async def get_sticker_set(self, set_name: str) -> asyncpg.Record | None:
        """Get cached set metadata."""
        return await self._pool.fetchrow(
            "SELECT * FROM sticker_sets WHERE set_name = $1",
            set_name,
        )

    async def get_stale_sets(
        self,
        *,
        staleness_days: int = 30,
        limit: int = 10,
    ) -> list[asyncpg.Record]:
        """Get sets needing refresh (stale or missing from cache)."""
        return await self._pool.fetch(  # type: ignore[no-any-return]
            """
            SELECT DISTINCT sk.set_name
            FROM sticker_knowledge sk
            LEFT JOIN sticker_sets ss ON ss.set_name = sk.set_name
            WHERE sk.set_name IS NOT NULL
              AND (
                  ss.set_name IS NULL
                  OR ss.updated_at < NOW() - INTERVAL '1 day' * $1
              )
            LIMIT $2
            """,
            staleness_days,
            limit,
        )

    # ── sticker_analysis_debug ────────────────────────────────────────

    async def save_debug_artifacts(
        self,
        *,
        file_unique_id: str,
        rendered_collage: bytes | None,
        vision_prompt: str,
        vision_raw_response: str,
        model_used: str | None,
        analysis_duration_ms: int | None,
        motion_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Save Vision API analysis artifacts for debugging.

        Args:
            file_unique_id: Unique sticker identifier
            rendered_collage: Collage PNG bytes
            vision_prompt: Prompt sent to Vision API
            vision_raw_response: Raw AI response
            model_used: Model identifier
            analysis_duration_ms: Analysis duration in milliseconds
            motion_metadata: Motion analysis data (avg_motion, peak_motion_time, etc.)
        """
        import json

        motion_json = json.dumps(motion_metadata) if motion_metadata else None

        await self._pool.execute(
            """
            INSERT INTO sticker_analysis_debug (
                file_unique_id,
                rendered_collage,
                vision_prompt,
                vision_raw_response,
                model_used,
                analysis_duration_ms,
                motion_metadata
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            file_unique_id,
            rendered_collage,
            vision_prompt,
            vision_raw_response,
            model_used,
            analysis_duration_ms,
            motion_json,
        )

    async def get_debug_artifacts(
        self, file_unique_id: str
    ) -> asyncpg.Record | None:
        """Get latest debug artifacts for a sticker."""
        return await self._pool.fetchrow(
            """
            SELECT * FROM sticker_analysis_debug
            WHERE file_unique_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            file_unique_id,
        )

    async def cleanup_old_debug_artifacts(self, retention_days: int) -> int:
        """Delete debug records older than retention_days. Returns count deleted."""
        result = await self._pool.execute(
            """
            DELETE FROM sticker_analysis_debug
            WHERE created_at < NOW() - INTERVAL '1 day' * $1
            """,
            retention_days,
        )
        # Parse "DELETE N" to get count
        return int(result.split()[-1]) if result else 0
