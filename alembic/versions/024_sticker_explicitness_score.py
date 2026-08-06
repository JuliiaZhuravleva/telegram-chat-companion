"""Sticker explicitness score (ADR-0008 Decision 8).

Revision ID: 024
Revises: 023
Create Date: 2026-08-06

ADR-0008 (docs/decisions/ADR-0008-sticker-explicitness-tolerance.md, Decision
1/4/8): adds the per-sticker, Vision-derived explicitness score the
tolerance-gating feature (D block) needs.

- `explicitness_score` FLOAT, nullable, **no SQL DEFAULT**. NULL means
  "unscored" (not-yet-analyzed, or a pre-migration catalog row awaiting the
  one-off backfill script) and is Decision 3's fail-closed sentinel — never
  0.0 (would look "safe") and never a materialized default. Adding a SQL
  DEFAULT here would repeat the exact bug `020_rules_columns_drop_default.py`
  already had to fix: a concrete value on every row shadows the
  dataclass/`bot_config` default forever the moment `ensure_exists()` touches
  the row.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "024"
down_revision: str = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE sticker_knowledge
        ADD COLUMN IF NOT EXISTS explicitness_score FLOAT
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE sticker_knowledge DROP COLUMN IF EXISTS explicitness_score")
