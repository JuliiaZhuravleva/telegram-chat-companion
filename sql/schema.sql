-- Telegram Chat Companion — Database Schema
-- Run: psql $DATABASE_URL -f sql/schema.sql
-- Safe to run multiple times (all statements are idempotent).

-- Required extension for RAG memory
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- Schema version tracking
-- =============================================================================

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    description TEXT,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_version (version, description)
VALUES (1, 'Initial schema — bot_config + chat_settings')
ON CONFLICT (version) DO NOTHING;

-- =============================================================================
-- Auto-update trigger for updated_at columns
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- bot_config — Global key-value settings
-- =============================================================================

CREATE TABLE IF NOT EXISTS bot_config (
    key         VARCHAR(100) PRIMARY KEY,
    value       JSONB NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS bot_config_updated_at ON bot_config;
CREATE TRIGGER bot_config_updated_at
    BEFORE UPDATE ON bot_config
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Seed global defaults (these override YAML when present)
INSERT INTO bot_config (key, value, description) VALUES
    ('default_trigger_words',                '["bot", "бот"]',  'Default trigger words for new chats'),
    ('default_random_response_chance',       '0.05',            'Default random response probability (0.0–1.0)'),
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
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- chat_settings — Per-chat configuration overrides
-- =============================================================================

CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id                      BIGINT PRIMARY KEY,
    chat_title                   TEXT,
    chat_type                    VARCHAR(20) DEFAULT 'group',

    -- Whitelist
    enabled                      BOOLEAN NOT NULL DEFAULT false,

    -- Behavior
    trigger_words                TEXT[] DEFAULT ARRAY['bot', 'бот'],
    random_response_chance       FLOAT DEFAULT 0.05,
    random_response_min_interval INTEGER DEFAULT 300,
    system_prompt                TEXT DEFAULT '',
    language                     VARCHAR(10) DEFAULT 'ru',

    -- Module toggles
    rag_enabled                  BOOLEAN DEFAULT true,
    transcribe_voice             BOOLEAN DEFAULT true,
    transcribe_video_notes       BOOLEAN DEFAULT true,
    abuse_filter_enabled         BOOLEAN DEFAULT false,
    sticker_learning_enabled     BOOLEAN DEFAULT false,
    sticker_response_chance      FLOAT DEFAULT 0.15,
    image_analysis_enabled       BOOLEAN DEFAULT true,
    save_messages                BOOLEAN DEFAULT true,

    -- Timestamps
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity_at             TIMESTAMPTZ DEFAULT now()
);

DROP TRIGGER IF EXISTS chat_settings_updated_at ON chat_settings;
CREATE TRIGGER chat_settings_updated_at
    BEFORE UPDATE ON chat_settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Index for listing enabled chats
CREATE INDEX IF NOT EXISTS idx_chat_settings_enabled
    ON chat_settings (enabled) WHERE enabled = true;
