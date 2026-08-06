"""Sticker duplicate detection: image_hash + duplicate_of_file_unique_id.

Revision ID: 023
Revises: 022
Create Date: 2026-08-06

ADR-0007 (docs/decisions/ADR-0007-sticker-duplicate-hash-dedup.md, Decision 4):
adds the two columns the pre-Vision perceptual-hash dedup check (A-2) needs.

- `image_hash` CHAR(16): hex-encoded 64-bit dHash of the sticker's artwork
  (Decision 1/2). NULL for every row created before this migration (no
  backfill — Decision 8) and for any row where hashing failed (fail-open).
  No index: matching is an app-side Hamming scan (Decision 5), not an
  equality lookup, so a btree index here would never be used.
- `duplicate_of_file_unique_id`: nullable self-FK, set when this sticker's
  description was copied from a near-identical canonical sticker instead of
  being independently analyzed by Vision. `ON DELETE SET NULL` — if the
  canonical row is ever deleted, duplicates keep their already-copied
  description; the pointer isn't required for them to keep working.
  Indexed (partial, non-NULL only): "how many duplicates does X have" is a
  natural future admin/debug query and the column is free to index now.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "023"
down_revision: str = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE sticker_knowledge
        ADD COLUMN IF NOT EXISTS image_hash CHAR(16)
    """)
    op.execute("""
        ALTER TABLE sticker_knowledge
        ADD COLUMN IF NOT EXISTS duplicate_of_file_unique_id VARCHAR(255)
        REFERENCES sticker_knowledge(file_unique_id) ON DELETE SET NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sticker_knowledge_duplicate_of
        ON sticker_knowledge(duplicate_of_file_unique_id)
        WHERE duplicate_of_file_unique_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_sticker_knowledge_duplicate_of")
    op.execute("ALTER TABLE sticker_knowledge DROP COLUMN IF EXISTS duplicate_of_file_unique_id")
    op.execute("ALTER TABLE sticker_knowledge DROP COLUMN IF EXISTS image_hash")
