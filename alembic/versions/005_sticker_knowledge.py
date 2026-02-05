"""Sticker intelligence: sticker_knowledge catalog and sticker_sets cache.

Revision ID: 005
Revises: 004
Create Date: 2026-02-05

Tables: sticker_knowledge, sticker_sets
"""

from collections.abc import Sequence

from alembic import op

revision: str = "005"
down_revision: str = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- sticker_knowledge: main sticker catalog --

    op.execute("""
        CREATE TABLE IF NOT EXISTS sticker_knowledge (
            id                          SERIAL PRIMARY KEY,
            file_unique_id              VARCHAR(255) UNIQUE NOT NULL,
            file_id                     VARCHAR(255) NOT NULL,
            set_name                    VARCHAR(255),
            emoji                       VARCHAR(50),
            is_animated                 BOOLEAN DEFAULT false,
            is_video                    BOOLEAN DEFAULT false,

            -- AI-generated descriptions
            visual_description          TEXT,
            original_vision_description TEXT,
            emotion                     VARCHAR(100),
            suggested_contexts          TEXT[],
            style_tags                  TEXT[],
            character_or_meme           VARCHAR(255),

            -- Embedding for semantic search
            description_embedding       vector(768),

            -- Accumulated usage contexts (max 10, FIFO)
            usage_contexts              TEXT[] DEFAULT ARRAY[]::TEXT[],

            -- Admin data
            admin_notes                 TEXT,

            -- Usage statistics
            total_uses                  INTEGER DEFAULT 0,
            bot_uses                    INTEGER DEFAULT 0,
            last_used_at                TIMESTAMPTZ,

            -- Metadata
            analyzed_at                 TIMESTAMPTZ,
            analysis_failed             BOOLEAN DEFAULT false,
            created_at                  TIMESTAMPTZ DEFAULT NOW(),
            updated_at                  TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sticker_knowledge_set
        ON sticker_knowledge(set_name)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sticker_knowledge_emotion
        ON sticker_knowledge(emotion)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sticker_knowledge_uses
        ON sticker_knowledge(total_uses DESC)
    """)
    # IVFFlat: lists=10 for small initial dataset.
    # Increase to 100 once catalog grows past ~4000 stickers.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sticker_knowledge_embedding
        ON sticker_knowledge USING ivfflat (description_embedding vector_cosine_ops)
        WITH (lists = 10)
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS sticker_knowledge_updated_at ON sticker_knowledge
    """)
    op.execute("""
        CREATE TRIGGER sticker_knowledge_updated_at
            BEFORE UPDATE ON sticker_knowledge
            FOR EACH ROW EXECUTE FUNCTION update_updated_at()
    """)

    # -- sticker_sets: set metadata cache --

    op.execute("""
        CREATE TABLE IF NOT EXISTS sticker_sets (
            set_name            VARCHAR(255) PRIMARY KEY,
            set_title           VARCHAR(255),
            total_count         INTEGER NOT NULL DEFAULT 0,
            thumbnail_file_id   VARCHAR(255),
            is_animated         BOOLEAN DEFAULT false,
            is_video            BOOLEAN DEFAULT false,
            created_at          TIMESTAMPTZ DEFAULT NOW(),
            updated_at          TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS sticker_sets_updated_at ON sticker_sets
    """)
    op.execute("""
        CREATE TRIGGER sticker_sets_updated_at
            BEFORE UPDATE ON sticker_sets
            FOR EACH ROW EXECUTE FUNCTION update_updated_at()
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sticker_sets CASCADE")
    op.execute("DROP TABLE IF EXISTS sticker_knowledge CASCADE")
