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
    ('default_save_messages',                'true',            'Save message history by default'),
    ('admin_ids',                            '""',              'Comma-separated Telegram user IDs of bot admins'),
    ('allowed_chats',                        '""',              'Comma-separated chat IDs allowed to use the bot (empty = use per-chat enabled flag)')
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

-- =============================================================================
-- chat_messages — Message history per chat
-- =============================================================================

CREATE TABLE IF NOT EXISTS chat_messages (
    id                      BIGSERIAL PRIMARY KEY,
    chat_id                 BIGINT NOT NULL,
    message_id              BIGINT NOT NULL,
    user_id                 BIGINT,
    username                TEXT,
    first_name              TEXT,
    message_type            VARCHAR(20) NOT NULL DEFAULT 'text',
    content                 TEXT,
    raw_data                JSONB,
    reply_to_message_id     BIGINT,
    is_bot_message          BOOLEAN NOT NULL DEFAULT false,
    sticker_file_id         TEXT,
    sticker_file_unique_id  TEXT,
    sticker_set_name        TEXT,
    sticker_emoji           TEXT,

    -- Edit tracking
    original_content        TEXT,
    edited_at               TIMESTAMPTZ,
    edit_count              INTEGER NOT NULL DEFAULT 0,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_created
    ON chat_messages (chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_user
    ON chat_messages (chat_id, user_id);

-- =============================================================================
-- user_activity — Activity tracking per user per chat
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_activity (
    id              BIGSERIAL PRIMARY KEY,
    chat_id         BIGINT NOT NULL,
    user_id         BIGINT NOT NULL,
    username        TEXT,
    first_name      TEXT,
    activity_type   VARCHAR(20) NOT NULL DEFAULT 'message',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_activity_chat_user_created
    ON user_activity (chat_id, user_id, created_at DESC);

-- =============================================================================
-- response_log — AI response metadata logging
-- =============================================================================

CREATE TABLE IF NOT EXISTS response_log (
    id                BIGSERIAL PRIMARY KEY,
    chat_id           BIGINT NOT NULL,
    user_id           BIGINT,
    message_id        BIGINT,
    trigger_type      VARCHAR(20),
    provider          VARCHAR(50),
    model             VARCHAR(100),
    tokens_input      INTEGER,
    tokens_output     INTEGER,
    response_time_ms  INTEGER,
    was_fallback      BOOLEAN NOT NULL DEFAULT false,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_response_log_chat_created
    ON response_log (chat_id, created_at DESC);

-- =============================================================================
-- sticker_knowledge — Sticker intelligence catalog
-- =============================================================================

CREATE TABLE IF NOT EXISTS sticker_knowledge (
    id                          SERIAL PRIMARY KEY,
    file_unique_id              VARCHAR(255) UNIQUE NOT NULL,
    file_id                     VARCHAR(255) NOT NULL,
    set_name                    VARCHAR(255),
    emoji                       VARCHAR(50),
    is_animated                 BOOLEAN DEFAULT false,
    is_video                    BOOLEAN DEFAULT false,

    -- AI-generated descriptions
    visual_description          TEXT,
    original_vision_description TEXT,
    emotion                     VARCHAR(100),
    suggested_contexts          TEXT[],
    style_tags                  TEXT[],
    character_or_meme           VARCHAR(255),

    -- Embedding for semantic search
    description_embedding       vector(768),

    -- Accumulated usage contexts (max 10, FIFO)
    usage_contexts              TEXT[] DEFAULT ARRAY[]::TEXT[],

    -- Admin data
    admin_notes                 TEXT,

    -- Usage statistics
    total_uses                  INTEGER DEFAULT 0,
    bot_uses                    INTEGER DEFAULT 0,
    last_used_at                TIMESTAMPTZ,

    -- Metadata
    analyzed_at                 TIMESTAMPTZ,
    analysis_failed             BOOLEAN DEFAULT false,
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sticker_knowledge_set
    ON sticker_knowledge(set_name);
CREATE INDEX IF NOT EXISTS idx_sticker_knowledge_emotion
    ON sticker_knowledge(emotion);
CREATE INDEX IF NOT EXISTS idx_sticker_knowledge_uses
    ON sticker_knowledge(total_uses DESC);
CREATE INDEX IF NOT EXISTS idx_sticker_knowledge_embedding
    ON sticker_knowledge USING ivfflat (description_embedding vector_cosine_ops)
    WITH (lists = 10);

DROP TRIGGER IF EXISTS sticker_knowledge_updated_at ON sticker_knowledge;
CREATE TRIGGER sticker_knowledge_updated_at
    BEFORE UPDATE ON sticker_knowledge
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =============================================================================
-- sticker_sets — Sticker set metadata cache
-- =============================================================================

CREATE TABLE IF NOT EXISTS sticker_sets (
    set_name            VARCHAR(255) PRIMARY KEY,
    set_title           VARCHAR(255),
    total_count         INTEGER NOT NULL DEFAULT 0,
    thumbnail_file_id   VARCHAR(255),
    is_animated         BOOLEAN DEFAULT false,
    is_video            BOOLEAN DEFAULT false,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

DROP TRIGGER IF EXISTS sticker_sets_updated_at ON sticker_sets;
CREATE TRIGGER sticker_sets_updated_at
    BEFORE UPDATE ON sticker_sets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
