"""Add custom_rules table and rules columns to chat_settings.

Revision ID: 008
Revises: 007
Create Date: 2026-02-09

Adds:
- custom_rules table for per-chat automation rules
- rules_mode and rules_enabled columns to chat_settings
- Seed defaults in bot_config
"""

from collections.abc import Sequence

from alembic import op

revision: str = "008"
down_revision: str = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create custom_rules table
    op.execute("""
        CREATE TABLE IF NOT EXISTS custom_rules (
            id                  SERIAL PRIMARY KEY,
            chat_id             BIGINT NOT NULL,
            rule_type           VARCHAR(50) NOT NULL,
            config              JSONB NOT NULL DEFAULT '{}',
            weight              INT DEFAULT 1,
            mandatory           BOOLEAN DEFAULT FALSE,
            enabled             BOOLEAN DEFAULT TRUE,
            status              VARCHAR(20) DEFAULT 'active',
            trigger_count       INT DEFAULT 0,
            last_triggered_at   TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # 2. Partial index for active rules lookup
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_custom_rules_chat_enabled
        ON custom_rules(chat_id, enabled) WHERE enabled = true;
    """)

    # 3. Auto-update trigger
    op.execute("""
        DROP TRIGGER IF EXISTS custom_rules_updated_at ON custom_rules;
        CREATE TRIGGER custom_rules_updated_at
            BEFORE UPDATE ON custom_rules
            FOR EACH ROW EXECUTE FUNCTION update_updated_at();
    """)

    # 4. Add rules columns to chat_settings
    op.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS rules_mode VARCHAR(20) DEFAULT 'all';
    """)
    op.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS rules_enabled BOOLEAN DEFAULT false;
    """)

    # 5. Seed global defaults
    op.execute("""
        INSERT INTO bot_config (key, value, description) VALUES
            ('default_rules_mode',    '"all"',  'Default rules execution mode (all/highest_weight/weighted_random)'),
            ('default_rules_enabled', 'false',  'Enable custom rules by default')
        ON CONFLICT (key) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS rules_enabled;")
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS rules_mode;")
    op.execute("DROP TRIGGER IF EXISTS custom_rules_updated_at ON custom_rules;")
    op.execute("DROP INDEX IF EXISTS idx_custom_rules_chat_enabled;")
    op.execute("DROP TABLE IF EXISTS custom_rules;")
    op.execute("DELETE FROM bot_config WHERE key IN ('default_rules_mode', 'default_rules_enabled');")
