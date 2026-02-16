"""Chat messages table with edit tracking.

Revision ID: 002
Revises: 001
Create Date: 2026-02-04

Tables: chat_messages, response_log, user_activity
"""

from collections.abc import Sequence

from alembic import op

revision: str = "002"
down_revision: str = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            user_id BIGINT,
            username VARCHAR(255),
            first_name VARCHAR(255),
            message_type VARCHAR(50) NOT NULL,
            content TEXT,
            raw_data JSONB,
            sticker_file_id VARCHAR(255),
            sticker_file_unique_id VARCHAR(255),
            sticker_set_name VARCHAR(255),
            sticker_emoji VARCHAR(50),
            reply_to_message_id BIGINT,
            is_bot_message BOOLEAN DEFAULT false,
            edited_at TIMESTAMPTZ,
            edit_count INTEGER DEFAULT 0,
            original_content TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(chat_id, message_id)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_created
        ON chat_messages(chat_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_user
        ON chat_messages(chat_id, user_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_type
        ON chat_messages(chat_id, message_type, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_edited
        ON chat_messages(chat_id, edited_at) WHERE edited_at IS NOT NULL
    """)

    # Response log for tracking AI responses
    op.execute("""
        CREATE TABLE IF NOT EXISTS response_log (
            id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            user_id BIGINT,
            message_id BIGINT,
            trigger_type VARCHAR(50),
            provider VARCHAR(50),
            model VARCHAR(100),
            tokens_input INTEGER,
            tokens_output INTEGER,
            response_time_ms INTEGER,
            was_fallback BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_response_log_chat
        ON response_log(chat_id, created_at DESC)
    """)

    # User activity tracking
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_activity (
            id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            username VARCHAR(255),
            first_name VARCHAR(255),
            activity_type VARCHAR(50) DEFAULT 'message',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_activity_lookup
        ON user_activity(chat_id, user_id, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_activity CASCADE")
    op.execute("DROP TABLE IF EXISTS response_log CASCADE")
    op.execute("DROP TABLE IF EXISTS chat_messages CASCADE")
