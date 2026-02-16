"""Add message_thread_id for Telegram forum topics support.

Revision ID: 007
Revises: 006
Create Date: 2026-02-06

Adds:
- message_thread_id BIGINT column to chat_messages (NULL for non-forum chats)
- Composite index for topic-aware context queries
"""

from collections.abc import Sequence

from alembic import op

revision: str = "007"
down_revision: str = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add nullable column (NULL = non-forum chat or old messages)
    op.execute("""
        ALTER TABLE chat_messages
        ADD COLUMN IF NOT EXISTS message_thread_id BIGINT;
    """)

    # Composite index for topic-aware context queries
    # Supports: "last N messages in topic X of chat Y"
    # Uses IS DISTINCT FROM for NULL-safe comparisons
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_thread
        ON chat_messages(chat_id, message_thread_id, created_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chat_messages_thread;")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS message_thread_id;")
