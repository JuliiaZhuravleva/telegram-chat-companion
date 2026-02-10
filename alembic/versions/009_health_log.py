"""Add health_log table for periodic health monitoring.

Revision ID: 009
Revises: 008
Create Date: 2026-02-10

Adds:
- health_log table for storing health check results
- Seed health_check_enabled in bot_config
"""

from collections.abc import Sequence

from alembic import op

revision: str = "009"
down_revision: str = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS health_log (
            id              BIGSERIAL PRIMARY KEY,
            checked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status          VARCHAR(20) NOT NULL
                            CHECK (status IN ('healthy', 'warning', 'critical', 'skipped')),
            db_ok           BOOLEAN NOT NULL DEFAULT true,
            messages_30m    INTEGER NOT NULL DEFAULT 0,
            fallbacks_15m   INTEGER NOT NULL DEFAULT 0,
            ai_provider     VARCHAR(50),
            issues          JSONB NOT NULL DEFAULT '[]'::jsonb,
            alert_sent      BOOLEAN NOT NULL DEFAULT false
        );
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_health_log_checked_at
        ON health_log (checked_at DESC);
    """)

    op.execute("""
        INSERT INTO bot_config (key, value, description) VALUES
            ('health_check_enabled', 'true', 'Enable periodic health monitoring')
        ON CONFLICT (key) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_health_log_checked_at;")
    op.execute("DROP TABLE IF EXISTS health_log;")
    op.execute("DELETE FROM bot_config WHERE key = 'health_check_enabled';")
