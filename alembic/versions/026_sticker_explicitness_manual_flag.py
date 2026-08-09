"""Sticker explicitness manual-override marker (ADR-0009 Decision 3).

Revision ID: 026
Revises: 025
Create Date: 2026-08-09

ADR-0009 (docs/decisions/ADR-0009-manual-explicitness-override.md, Decision
3): adds the per-sticker marker that lets `save_sticker()`'s upsert (Decision
4) protect an admin-set `explicitness_score` from being silently overwritten
by the next re-analysis.

- `explicitness_is_manual` BOOLEAN, **NOT NULL DEFAULT false**. Unlike
  `explicitness_score`/`tolerance_level` (migrations 024/025: nullable, no
  SQL DEFAULT, because those two participate in `ChatConfigService`'s
  three-layer merge, where a materialized default would permanently shadow a
  higher layer -- migration 020's own bug), this column is a flat,
  always-meaningful per-row fact with no merge semantics: every row either
  was or wasn't hand-set, and a pre-migration row was, unambiguously, not.
  Same shape as `sticker_knowledge.analysis_failed` (migration 001). The
  migration-020 pitfall does not apply here -- `sticker_knowledge` is not
  part of the `ChatConfig` three-layer merge, so there is no higher layer
  this column could shadow.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "026"
down_revision: str = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE sticker_knowledge
        ADD COLUMN IF NOT EXISTS explicitness_is_manual BOOLEAN NOT NULL DEFAULT false
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE sticker_knowledge DROP COLUMN IF EXISTS explicitness_is_manual")
