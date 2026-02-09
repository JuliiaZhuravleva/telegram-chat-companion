"""Repository for sticker_knowledge and sticker_sets tables."""

from __future__ import annotations

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
                CASE WHEN $7 IS NOT NULL THEN NOW() END,
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
