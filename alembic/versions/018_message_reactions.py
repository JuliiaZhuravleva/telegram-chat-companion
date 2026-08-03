"""Reactions: message_reactions table + reactions_enabled/reactions_history_enabled
on chat_settings.

Revision ID: 018
Revises: 017
Create Date: 2026-08-03

Foundation for the reactions feature (docs/decisions/ADR-0004, R-1/Phase 1):
persist who added/removed which reaction on which message. Denormalized row
per (emoji, action) -- the diff between `old_reaction`/`new_reaction` is
computed once, at write time, in the handler (`modules/reactions/models.py::diff`),
not re-derived on every read.

Adds:
- message_reactions table (one row per changed reaction key per update)
- chat_settings.reactions_enabled (BOOLEAN, nullable, no default -- master
  module toggle, defaults to off like sticker_intelligence/kb_enabled)
- chat_settings.reactions_history_enabled (BOOLEAN, nullable, no default --
  separate, more granular toggle gating only the INSERT; an owner can keep
  R-5's bot-initiated reactions while opting out of behavioral logging)

No foreign key to chat_messages(chat_id, message_id): no table in this
codebase FKs onto chat_messages (a reacted-to message can be older than
chat_messages_days retention, or never saved at all when save_messages is
false), so (chat_id, message_id) is a soft join key, resolved best-effort at
read time -- same convention as chat_facts.source_message_id (ADR-0003).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "018"
down_revision: str = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS message_reactions (
            id               BIGSERIAL PRIMARY KEY,
            chat_id          BIGINT NOT NULL,
            message_id       BIGINT NOT NULL,
            user_id          BIGINT,              -- NULL when actor_chat_id is set (anonymous reactor)
            actor_chat_id    BIGINT,              -- NULL when user_id is set
            action           VARCHAR(10) NOT NULL,  -- 'added' | 'removed'
            reaction_type    VARCHAR(20) NOT NULL,  -- 'emoji' | 'custom_emoji' | 'paid' (Bot API's own vocabulary)
            emoji            VARCHAR(50),           -- set iff reaction_type = 'emoji'
            custom_emoji_id  VARCHAR(64),           -- set iff reaction_type = 'custom_emoji'; raw id, never resolved in Phase 1
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_message_reactions_chat_message
        ON message_reactions(chat_id, message_id)
    """)

    # Retention sweep (RetentionCleaner) and future analytics (R-9) both scan
    # by recency.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_message_reactions_chat_created
        ON message_reactions(chat_id, created_at DESC)
    """)

    # Both nullable, no DEFAULT: a chat_settings row leaves these NULL until
    # the chat/admin explicitly opts in/out, so the three-layer merge's global
    # layer applies until then (see migration 014's kb_enabled, ADR-0003).
    # Brand-new columns, so -- unlike kb_enabled's historical DROP DEFAULT
    # dance -- a single nullable ADD COLUMN is enough.
    op.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS reactions_enabled BOOLEAN
    """)
    op.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS reactions_history_enabled BOOLEAN
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS reactions_history_enabled;")
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS reactions_enabled;")
    op.execute("DROP INDEX IF EXISTS idx_message_reactions_chat_created;")
    op.execute("DROP INDEX IF EXISTS idx_message_reactions_chat_message;")
    op.execute("DROP TABLE IF EXISTS message_reactions CASCADE;")
