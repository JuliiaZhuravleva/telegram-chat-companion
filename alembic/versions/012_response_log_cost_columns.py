"""Add task_type, cost_usd, duration_seconds to response_log.

Revision ID: 012
Revises: 011
Create Date: 2026-06-05

The response_log table was created in migration 002 without the cost-tracking
columns that ResponseLogRepository.log() already accepts.  This migration
adds the three missing columns and a cost-aggregation index so that
get_total_cost() / get_cost_by_model() queries are efficient.

All ALTER statements use ADD COLUMN IF NOT EXISTS for idempotency.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "012"
down_revision: str = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add cost-tracking columns (idempotent)
    op.execute("""
        ALTER TABLE response_log
            ADD COLUMN IF NOT EXISTS task_type      VARCHAR(50)    DEFAULT 'text',
            ADD COLUMN IF NOT EXISTS cost_usd       NUMERIC(12, 8),
            ADD COLUMN IF NOT EXISTS duration_seconds FLOAT
    """)

    # Partial index for the aggregation queries that filter on created_at and
    # sum cost_usd — only rows that actually have a cost value are indexed.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_response_log_cost_date
        ON response_log(created_at DESC)
        WHERE cost_usd IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_response_log_cost_date")
    op.execute("ALTER TABLE response_log DROP COLUMN IF EXISTS duration_seconds")
    op.execute("ALTER TABLE response_log DROP COLUMN IF EXISTS cost_usd")
    op.execute("ALTER TABLE response_log DROP COLUMN IF EXISTS task_type")
