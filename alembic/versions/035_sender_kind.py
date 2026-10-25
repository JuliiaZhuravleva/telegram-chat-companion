"""chat_messages.sender_kind -- record WHO is speaking when it is not a person.

Revision ID: 035
Revises: 034
Create Date: 2026-10-25

A message sent "on behalf of a chat" reaches a bot with a placeholder in
`from` and the real speaker in `sender_chat`:

- a channel post auto-forwarded into its discussion group: `from` is
  "Telegram" (777000, `is_bot=false`), `is_automatic_forward=true`;
- an anonymous group admin: `from` is GroupAnonymousBot (1087968824,
  `is_bot=true`), `sender_chat` is the group itself, plus `author_signature`;
- a member posting as a channel: `from` is Channel_Bot (136817688,
  `is_bot=true`), `sender_chat` is that channel.

MessageSaver stored `from` only, so every surface that renders history drew
the two `is_bot=true` placeholders as the bot ITSELF (`Bot: ...`) -- in a
channel's discussion group the model would read the author's own comments as
its own past replies -- and the auto-forward as a person named "Telegram".

`is_bot_message` keeps its meaning ("an account flagged as a bot", fakes
included), so every existing `is_bot_message = false` filter behaves exactly
as before. `sender_kind` is the separate answer to "who actually speaks":
`channel_post | anon_admin | channel`, NULL for an ordinary account.

The backfill classifies old rows by the placeholder ids, which is all they
carry -- `raw_data` was never populated, so the channel title and the admin
signature of an old row are gone and the rendering degrades to a bare
"канал" / "админ". Only `WHERE sender_kind IS NULL`: it fills, never
overwrites. `idx_chat_messages_user` makes it an index lookup.

`linked_channel_*` are chat metadata, NOT part of the settings merge -- the
same arrangement as `is_forum` (030): only Telegram decides which channel a
group is linked to. `transcribe_expandable` IS merged: nullable, no DEFAULT
(a DEFAULT would be materialized by `ensure_exists()` and shadow the global
layer -- CLAUDE.md, migrations 014/015).
"""

from __future__ import annotations

from alembic import op

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None

_TELEGRAM_SERVICE_ID = 777000
_GROUP_ANONYMOUS_BOT_ID = 1087968824
_CHANNEL_BOT_ID = 136817688


def upgrade() -> None:
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS sender_kind TEXT")
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS sender_chat_id BIGINT")
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS sender_chat_title TEXT")
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS author_signature TEXT")
    op.execute(
        f"""
        UPDATE chat_messages
           SET sender_kind = CASE user_id
                   WHEN {_TELEGRAM_SERVICE_ID} THEN 'channel_post'
                   WHEN {_GROUP_ANONYMOUS_BOT_ID} THEN 'anon_admin'
                   WHEN {_CHANNEL_BOT_ID} THEN 'channel'
               END
         WHERE user_id IN ({_TELEGRAM_SERVICE_ID}, {_GROUP_ANONYMOUS_BOT_ID}, {_CHANNEL_BOT_ID})
           AND sender_kind IS NULL
        """  # noqa: S608 -- module constants, no input
    )
    op.execute("ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS linked_channel_id BIGINT")
    op.execute("ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS linked_channel_title TEXT")
    op.execute("ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS transcribe_expandable BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS transcribe_expandable")
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS linked_channel_title")
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS linked_channel_id")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS author_signature")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS sender_chat_title")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS sender_chat_id")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS sender_kind")
