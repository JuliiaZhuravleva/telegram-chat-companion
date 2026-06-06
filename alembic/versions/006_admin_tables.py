"""Admin panel: admin_sticker_session and unauthorized_attempts tables.

Revision ID: 006
Revises: 005
Create Date: 2026-02-05

Tables: admin_sticker_session, unauthorized_attempts
"""

from collections.abc import Sequence

from alembic import op

revision: str = "006"
down_revision: str = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS admin_sticker_session (
            admin_user_id BIGINT PRIMARY KEY,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # unauthorized_attempts was first created in migration 004 without admin
    # columns (chat_type, user_first_name, user_username, message_text, status).
    # CREATE TABLE IF NOT EXISTS is idempotent on a fresh install; the ALTER TABLE
    # statements below bring an existing 004-era table up to the full admin schema.
    op.execute("""
        CREATE TABLE IF NOT EXISTS unauthorized_attempts (
            id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            chat_title TEXT,
            chat_type VARCHAR(20),
            user_id BIGINT,
            user_first_name TEXT,
            user_username TEXT,
            message_text TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # Idempotent column additions — no-op if columns already exist (fresh install)
    # or if upgrading from the migration-004 version of the table.
    op.execute("""
        ALTER TABLE unauthorized_attempts
            ADD COLUMN IF NOT EXISTS chat_type      VARCHAR(20),
            ADD COLUMN IF NOT EXISTS user_first_name TEXT,
            ADD COLUMN IF NOT EXISTS user_username  TEXT,
            ADD COLUMN IF NOT EXISTS message_text   TEXT,
            ADD COLUMN IF NOT EXISTS status         VARCHAR(20) NOT NULL DEFAULT 'pending';
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_unauth_status
        ON unauthorized_attempts(status) WHERE status = 'pending';
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_unauth_created
        ON unauthorized_attempts(created_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS unauthorized_attempts;")
    op.execute("DROP TABLE IF EXISTS admin_sticker_session;")
