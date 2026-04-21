"""Initial schema — bot_config + chat_settings + schema_version.

Revision ID: 001
Revises:
Create Date: 2026-02-04

All statements are idempotent.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Schema version tracking
    op.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            description TEXT,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        INSERT INTO schema_version (version, description)
        VALUES (1, 'Initial schema — bot_config + chat_settings')
        ON CONFLICT (version) DO NOTHING
    """)

    # Auto-update trigger function
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    # bot_config — Global key-value settings
    op.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            key         VARCHAR(100) PRIMARY KEY,
            value       JSONB NOT NULL,
            description TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("DROP TRIGGER IF EXISTS bot_config_updated_at ON bot_config")
    op.execute("""
        CREATE TRIGGER bot_config_updated_at
            BEFORE UPDATE ON bot_config
            FOR EACH ROW EXECUTE FUNCTION update_updated_at()
    """)

    # Seed global defaults
    op.execute("""
        INSERT INTO bot_config (key, value, description) VALUES
            ('default_trigger_words',                '["bot", "бот"]',  'Default trigger words for new chats'),
            ('default_random_response_chance',       '0.05',            'Default random response probability (0.0-1.0)'),
            ('default_random_response_min_interval', '300',             'Minimum seconds between random responses'),
            ('default_language',                     '"ru"',            'Default response language'),
            ('default_system_prompt',                '""',              'Default AI personality / system prompt'),
            ('default_rag_enabled',                  'true',            'Enable RAG memory by default'),
            ('default_transcribe_voice',             'true',            'Enable voice transcription by default'),
            ('default_transcribe_video_notes',       'true',            'Enable video note transcription by default'),
            ('default_abuse_filter_enabled',         'false',           'Enable abuse filter by default'),
            ('default_sticker_learning_enabled',     'false',           'Enable sticker learning by default'),
            ('default_sticker_response_chance',      '0.15',            'Default sticker response probability'),
            ('default_image_analysis_enabled',       'true',            'Enable image analysis by default'),
            ('default_save_messages',                'true',            'Save message history by default')
        ON CONFLICT (key) DO NOTHING
    """)

    # chat_settings — Per-chat configuration overrides
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id                      BIGINT PRIMARY KEY,
            chat_title                   TEXT,
            chat_type                    VARCHAR(20) DEFAULT 'group',

            enabled                      BOOLEAN NOT NULL DEFAULT false,

            trigger_words                TEXT[] DEFAULT ARRAY['bot', 'бот'],
            random_response_chance       FLOAT DEFAULT 0.05,
            random_response_min_interval INTEGER DEFAULT 300,
            system_prompt                TEXT DEFAULT '',
            language                     VARCHAR(10) DEFAULT 'ru',

            rag_enabled                  BOOLEAN DEFAULT true,
            transcribe_voice             BOOLEAN DEFAULT true,
            transcribe_video_notes       BOOLEAN DEFAULT true,
            abuse_filter_enabled         BOOLEAN DEFAULT false,
            sticker_learning_enabled     BOOLEAN DEFAULT false,
            sticker_response_chance      FLOAT DEFAULT 0.15,
            image_analysis_enabled       BOOLEAN DEFAULT true,
            save_messages                BOOLEAN DEFAULT true,

            created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_activity_at             TIMESTAMPTZ DEFAULT now()
        )
    """)

    op.execute("DROP TRIGGER IF EXISTS chat_settings_updated_at ON chat_settings")
    op.execute("""
        CREATE TRIGGER chat_settings_updated_at
            BEFORE UPDATE ON chat_settings
            FOR EACH ROW EXECUTE FUNCTION update_updated_at()
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_settings_enabled
            ON chat_settings (enabled) WHERE enabled = true
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chat_settings_enabled")
    op.execute("DROP TRIGGER IF EXISTS chat_settings_updated_at ON chat_settings")
    op.execute("DROP TABLE IF EXISTS chat_settings")
    op.execute("DROP TRIGGER IF EXISTS bot_config_updated_at ON bot_config")
    op.execute("DROP TABLE IF EXISTS bot_config")
    op.execute("DROP TABLE IF EXISTS schema_version")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at()")
