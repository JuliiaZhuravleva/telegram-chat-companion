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
    ('allowed_chats',                        '""',              'Comma-separated chat IDs allowed to use the bot (empty = use per-chat enabled flag)'),
    ('default_rules_mode',                   '"all"',           'Default rules execution mode (all/highest_weight/weighted_random)'),
    ('default_rules_enabled',                'false',           'Enable custom rules by default'),
    ('default_link_comments_enabled',        'false',           'Enable video link comments by default'),
    ('health_check_enabled',                 'true',            'Enable periodic health monitoring'),
    ('default_sticker_reply_to_sticker_enabled', 'true',       'Reply to stickers with stickers by default'),
    ('default_sticker_reply_to_sticker_chance', '0.5',         'Default sticker-to-sticker reply probability'),
    ('default_image_comment_sticker_enabled', 'true',          'Comment on images with stickers by default'),
    ('default_image_comment_sticker_chance', '0.3',            'Default image comment sticker probability')
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
    sticker_reply_to_sticker_enabled BOOLEAN DEFAULT true,
    sticker_reply_to_sticker_chance  FLOAT DEFAULT 0.5,
    image_comment_sticker_enabled    BOOLEAN DEFAULT true,
    image_comment_sticker_chance     FLOAT DEFAULT 0.3,
    image_analysis_enabled       BOOLEAN DEFAULT true,
    save_messages                BOOLEAN DEFAULT true,

    -- Rules engine
    rules_enabled                BOOLEAN DEFAULT false,
    rules_mode                   VARCHAR(20) DEFAULT 'all',

    -- Link comments
    link_comments_enabled        BOOLEAN DEFAULT false,

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

-- Schema v2: Cost monitoring columns
ALTER TABLE response_log ADD COLUMN IF NOT EXISTS task_type VARCHAR(20) DEFAULT 'text';
ALTER TABLE response_log ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(12, 8) DEFAULT 0;
ALTER TABLE response_log ADD COLUMN IF NOT EXISTS duration_seconds FLOAT;

CREATE INDEX IF NOT EXISTS idx_response_log_task_type
    ON response_log (task_type);
CREATE INDEX IF NOT EXISTS idx_response_log_created_at
    ON response_log (created_at DESC);

INSERT INTO schema_version (version, description)
VALUES (2, 'Add task_type, cost_usd, duration_seconds to response_log')
ON CONFLICT (version) DO NOTHING;

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
-- sticker_analysis_debug — Debug artifacts for Vision API analysis
-- =============================================================================

CREATE TABLE IF NOT EXISTS sticker_analysis_debug (
    id                      SERIAL PRIMARY KEY,
    file_unique_id          VARCHAR(255) NOT NULL REFERENCES sticker_knowledge(file_unique_id) ON DELETE CASCADE,
    rendered_collage        BYTEA,
    vision_prompt           TEXT NOT NULL,
    vision_raw_response     TEXT NOT NULL,
    model_used              VARCHAR(100),
    analysis_duration_ms    INTEGER,
    motion_metadata         JSONB,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sticker_debug_file_id
    ON sticker_analysis_debug(file_unique_id);
CREATE INDEX IF NOT EXISTS idx_sticker_debug_created_at
    ON sticker_analysis_debug(created_at DESC);

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

-- =============================================================================
-- custom_rules — Per-chat automation rules
-- =============================================================================

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

CREATE INDEX IF NOT EXISTS idx_custom_rules_chat_enabled
    ON custom_rules(chat_id, enabled) WHERE enabled = true;

DROP TRIGGER IF EXISTS custom_rules_updated_at ON custom_rules;
CREATE TRIGGER custom_rules_updated_at
    BEFORE UPDATE ON custom_rules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =============================================================================
-- health_log — Periodic health check results
-- =============================================================================

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

CREATE INDEX IF NOT EXISTS idx_health_log_checked_at
    ON health_log (checked_at DESC);

-- =============================================================================
-- admin_sticker_notifications — Track sticker notifications sent to admins
-- =============================================================================

CREATE TABLE IF NOT EXISTS admin_sticker_notifications (
    id              SERIAL PRIMARY KEY,
    file_unique_id  VARCHAR(255) NOT NULL,
    admin_id        BIGINT NOT NULL,
    message_id      BIGINT,
    sticker_msg_id  BIGINT,
    chat_id         BIGINT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_sticker_notif_chat_msg
    ON admin_sticker_notifications (chat_id, message_id);
CREATE INDEX IF NOT EXISTS idx_admin_sticker_notif_chat_stk
    ON admin_sticker_notifications (chat_id, sticker_msg_id);

-- Schema v3: Sticker intelligence — response settings + admin notifications
ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS sticker_reply_to_sticker_enabled BOOLEAN DEFAULT true;
ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS sticker_reply_to_sticker_chance FLOAT DEFAULT 0.5;
ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS image_comment_sticker_enabled BOOLEAN DEFAULT true;
ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS image_comment_sticker_chance FLOAT DEFAULT 0.3;

INSERT INTO schema_version (version, description)
VALUES (3, 'Sticker intelligence — response settings + admin notifications')
ON CONFLICT (version) DO NOTHING;

-- Schema v4: Relevancy gate — natural response filtering for random triggers
ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS relevancy_gate_enabled BOOLEAN DEFAULT true;

INSERT INTO bot_config (key, value, description) VALUES
    ('default_relevancy_gate_enabled', 'true', 'Enable relevancy gate for random responses')
ON CONFLICT (key) DO NOTHING;

INSERT INTO schema_version (version, description)
VALUES (4, 'Relevancy gate — per-chat toggle')
ON CONFLICT (version) DO NOTHING;
