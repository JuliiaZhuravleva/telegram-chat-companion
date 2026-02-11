"""Add sticker_analysis_debug table for Vision API artifacts.

Revision ID: 010
Revises: 009
Create Date: 2026-02-11

Adds:
- sticker_analysis_debug table for storing Vision API debug artifacts
- Collage images (BYTEA), prompts, raw responses, timing metrics
- Automatic cleanup via scheduler (default 7-day retention)
"""

from collections.abc import Sequence

from alembic import op

revision: str = "010"
down_revision: str = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS sticker_analysis_debug (
            id                      SERIAL PRIMARY KEY,
            file_unique_id          VARCHAR(255) NOT NULL REFERENCES sticker_knowledge(file_unique_id) ON DELETE CASCADE,
            rendered_collage        BYTEA,
            vision_prompt           TEXT NOT NULL,
            vision_raw_response     TEXT NOT NULL,
            model_used              VARCHAR(100),
            analysis_duration_ms    INTEGER,
            created_at              TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sticker_debug_file_id
        ON sticker_analysis_debug(file_unique_id);
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sticker_debug_created_at
        ON sticker_analysis_debug(created_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_sticker_debug_created_at;")
    op.execute("DROP INDEX IF EXISTS idx_sticker_debug_file_id;")
    op.execute("DROP TABLE IF EXISTS sticker_analysis_debug;")
