"""RAG memory with pgvector embeddings.

Revision ID: 003
Revises: 002
Create Date: 2026-02-04

Tables: chat_memory with vector(768) + IVFFlat index
"""

from collections.abc import Sequence

from alembic import op

revision: str = "003"
down_revision: str = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_memory (
            id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            content TEXT NOT NULL,
            metadata JSONB DEFAULT '{}',
            embedding vector(768),
            source_message_id BIGINT,
            importance_score FLOAT DEFAULT 0.5,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_memory_chat
        ON chat_memory(chat_id)
    """)

    # IVFFlat index for fast cosine similarity search
    # lists=100 is appropriate for up to ~100k vectors
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_memory_embedding
        ON chat_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_memory CASCADE")
