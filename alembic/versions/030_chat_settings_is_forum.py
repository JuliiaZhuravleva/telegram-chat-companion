"""chat_settings.is_forum -- persist what Telegram knows about forum chats.

Revision ID: 030
Revises: 029
Create Date: 2026-08-19

Telegram sets `message_thread_id` not only on forum topics but on ordinary
reply chains in supergroups (measured on production 2026-08-19: no chat is a
forum; avg 2.0-2.7 messages per thread_id, ~70% NULL; the largest chat has
3737 distinct thread_ids over 33049 messages). At runtime the live event
already carries the disambiguator -- TopicMiddleware nulls the thread id
unless `chat.is_forum` -- but nothing *stored* records whether a chat is a
forum, which is exactly what offline consumers need: migration 029 keeps
`chat_chunks.thread_id` "for forum-aware chunking, which needs a way to
recognise a forum first". This column is that way.

It is chat *metadata* like chat_title/chat_type, written opportunistically by
ChatConfigMiddleware from the Chat object -- deliberately NOT part of the
three-layer settings merge and absent from the admin panel (deep-review
2026-08-19: a panel toggle or a bot_config `default_is_forum` could override
what only Telegram decides). NULL means "chat not yet observed since this
migration"; the middleware writes a definite bool on every cache-miss event.
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
