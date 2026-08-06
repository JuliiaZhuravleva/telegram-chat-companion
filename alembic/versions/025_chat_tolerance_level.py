"""Per-chat tolerance_level (ADR-0008 Decision 1/6/8).

Revision ID: 025
Revises: 024
Create Date: 2026-08-06

ADR-0008 (docs/decisions/ADR-0008-sticker-explicitness-tolerance.md, Decision
1/6/8): adds the per-chat "decency ceiling" the tolerance-gating feature (D
block) compares against each sticker's `explicitness_score` (migration 024).

- `tolerance_level` FLOAT, nullable, **no SQL DEFAULT**. NULL means "not
  overridden for this chat" — the three-layer merge falls back to
  `bot_config.default_tolerance_level` (if set) then `ChatConfig`'s dataclass
  default (`0.5`). Adding a SQL DEFAULT here would repeat the exact bug
  `020_rules_columns_drop_default.py` already had to fix: a concrete value on
  every row shadows the global/dataclass default forever the moment
  `ensure_exists()` touches the row.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "025"
down_revision: str = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS tolerance_level FLOAT
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS tolerance_level")
