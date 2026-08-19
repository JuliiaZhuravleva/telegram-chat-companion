"""chat_settings.is_forum -- distinguish real forums from reply chains (TD-102).

Revision ID: 030
Revises: 029
Create Date: 2026-08-19

Telegram sets `message_thread_id` not only on forum topics but on ordinary
reply chains in supergroups. The prompt pipeline picked its "forum mode"
history query on `message_thread_id is not None` alone, so every reply --
including a reply to the bot, the most common way to continue a conversation
-- collapsed the context window from 20 recent messages to the ~2 messages of
that reply chain (plus 10 "other topics"). Measured on production 2026-08-19:
no chat is a forum; avg 2.0-2.7 messages per thread_id, ~70% of messages have
none; the largest chat has 3737 distinct thread_ids over 33049 messages.

`Message.chat.is_forum` is what tells the two apart, and it was not stored
anywhere. This column is chat *metadata* like chat_title/chat_type, written
opportunistically by ChatConfigMiddleware -- NOT a three-layer override, so
the "nullable, no DEFAULT" rule here means NULL = "not yet observed", which
the config merge reads as False (the safe direction: a real forum degrades to
flat context until the first event after deploy; a normal supergroup stops
losing its window immediately).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "030"
down_revision: str = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS is_forum BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS is_forum")
