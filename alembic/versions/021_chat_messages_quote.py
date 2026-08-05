"""Persist the manually-highlighted reply quote on chat_messages.

Revision ID: 021
Revises: 020
Create Date: 2026-08-04

Q-1 wired aiogram's `Message.quote` (`TextQuote`) into the live prompt path
(`extract_reply_context()` in `src/bot/handlers/message.py`), but that
fragment was never persisted -- `message_saver.py` only ever stored
`reply_to_message_id`, so the history in `chat_messages` loses exactly what
the user highlighted when replying. This migration adds the two columns
needed to fix that (Q-PERSIST); `message_saver.py` is updated in the same
item to write them.

Adds to `chat_messages`:
- `quote_text` (TEXT) -- `message.quote.text`, unset when the message carries
  no quote at all.
- `quote_is_manual` (BOOLEAN) -- `message.quote.is_manual`, distinguishing a
  selection the user made by hand from a quote Telegram's server attaches
  automatically. Consumers (Q-5) must gate on this being `true` before
  treating `quote_text` as the user's deliberate focus, same rule Q-1
  applies on the live path.

Both nullable, no DEFAULT. Unlike the `chat_settings` three-layer-merge case
(CLAUDE.md's "Per-chat columns: nullable, no DEFAULT", where NULL means "not
overridden"), this is a plain fact table: NULL here just means "this message
has no quote", which is the true state for most rows (every message before
this migration, and every post-migration message that isn't a manual-quote
reply). A DEFAULT would misrepresent that as a chosen empty string/false
rather than absence, and would force a rewrite of the existing table on
column add for no benefit. Brand-new columns, so a single nullable ADD
COLUMN is enough -- no DROP DEFAULT dance like migration 020's.

No index: Q-5 reads these columns as extra projections on the existing
`(chat_id, created_at)` / `(chat_id, message_thread_id, created_at)` access
paths already indexed by migrations 002/007; nothing filters or sorts on
`quote_text`/`quote_is_manual` themselves.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "021"
down_revision: str = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE chat_messages
        ADD COLUMN IF NOT EXISTS quote_text TEXT
    """)
    op.execute("""
        ALTER TABLE chat_messages
        ADD COLUMN IF NOT EXISTS quote_is_manual BOOLEAN
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS quote_is_manual;")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS quote_text;")
