"""Reactions: record event_date and reject redelivered duplicates.

Revision ID: 019
Revises: 018
Create Date: 2026-08-03

`handle_message_reaction` guarded against "a duplicate update" by checking that
`diff(old_reaction, new_reaction)` is non-empty. That guard cannot do the job it
claimed: a redelivered update carries the *same* old/new pair as the first
delivery (e.g. old=[], new=[👍]), so `diff()` returns the same ADDED event again
and a second identical row is written. Telegram redelivers whenever the polling
offset was not confirmed -- a crash or restart mid-batch is enough.

`message_reactions` had a BIGSERIAL PK and no unique constraint, and stored no
value that survives redelivery, so nothing could deduplicate after the fact
either: `created_at DEFAULT NOW()` differs between the two writes by
construction.

Adds:
- `event_date TIMESTAMPTZ` -- `MessageReactionUpdated.date`, supplied by the Bot
  API and identical across redeliveries of the same update (unlike created_at,
  which is our own clock).
- a unique index over the full logical identity of an event, so the second write
  is rejected and `insert_events` can ON CONFLICT DO NOTHING.

Nullable columns participate via COALESCE sentinels: in a plain UNIQUE index
NULL never equals NULL, so rows with an anonymous reactor (user_id NULL) or a
custom emoji (emoji NULL) would each be treated as distinct and duplicate
freely -- exactly the rows this is meant to protect.

`event_date` is nullable and the index tolerates NULL via COALESCE so rows
written before this migration keep working; they simply share the epoch
sentinel, which is why the backfill below is a no-op rather than a guess.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "019"
down_revision: str = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Sentinels for the nullable parts of an event's identity. Chosen to be values
# Telegram cannot produce: chat/user ids are never 0, and an empty string is not
# a valid emoji or custom_emoji_id.
_UNIQUE_INDEX = """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_message_reactions_event
    ON message_reactions (
        chat_id,
        message_id,
        COALESCE(user_id, 0),
        COALESCE(actor_chat_id, 0),
        action,
        reaction_type,
        COALESCE(emoji, ''),
        COALESCE(custom_emoji_id, ''),
        COALESCE(event_date, TIMESTAMPTZ 'epoch')
    )
"""


def upgrade() -> None:
    op.execute("""
        ALTER TABLE message_reactions
        ADD COLUMN IF NOT EXISTS event_date TIMESTAMPTZ
    """)

    # Pre-existing rows (recorded before event_date was stored) can contain
    # genuine duplicates from an earlier redelivery. Drop the extras, keeping
    # the lowest id of each identical group, or the unique index below cannot
    # be created.
    op.execute("""
        DELETE FROM message_reactions a
        USING message_reactions b
        WHERE a.id > b.id
          AND a.chat_id = b.chat_id
          AND a.message_id = b.message_id
          AND COALESCE(a.user_id, 0) = COALESCE(b.user_id, 0)
          AND COALESCE(a.actor_chat_id, 0) = COALESCE(b.actor_chat_id, 0)
          AND a.action = b.action
          AND a.reaction_type = b.reaction_type
          AND COALESCE(a.emoji, '') = COALESCE(b.emoji, '')
          AND COALESCE(a.custom_emoji_id, '') = COALESCE(b.custom_emoji_id, '')
          AND COALESCE(a.event_date, TIMESTAMPTZ 'epoch')
              = COALESCE(b.event_date, TIMESTAMPTZ 'epoch')
    """)

    op.execute(_UNIQUE_INDEX)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_message_reactions_event")
    op.execute("ALTER TABLE message_reactions DROP COLUMN IF EXISTS event_date")
