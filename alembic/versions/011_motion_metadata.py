"""Add motion_metadata column to sticker_analysis_debug.

Revision ID: 011
Revises: 010
Create Date: 2026-02-11

Adds:
- motion_metadata JSONB column to sticker_analysis_debug table
- Stores motion analysis results (avg_motion, peak_motion_time, keyframe_indices, etc.)
"""

from collections.abc import Sequence

from alembic import op

revision: str = "011"
down_revision: str = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE sticker_analysis_debug
        ADD COLUMN IF NOT EXISTS motion_metadata JSONB;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE sticker_analysis_debug
        DROP COLUMN IF EXISTS motion_metadata;
    """)
