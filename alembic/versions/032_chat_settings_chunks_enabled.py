"""chat_settings.chunks_enabled -- the read side of the chunk index gets a gate.

Revision ID: 032
Revises: 031
Create Date: 2026-08-25

`chat_chunks` has been filling since migration 029 and nothing has ever read
it: `ChatChunkIndexer` writes every 15 minutes while `ChunkRetrievalService`
was called from two eval scripts and nowhere in the bot (TD-143). This column
is the switch that turns the read side on, per chat.

**Nullable, no DEFAULT** -- the three-layer merge reads NULL as "not
overridden" (CLAUDE.md; migration 014/015). A DEFAULT here would be
materialized by `ensure_exists()` on first contact and would permanently
shadow `bot_config.default_chunks_enabled` for every chat the bot has already
seen, which is exactly the legacy trap the 13 migration-001 columns are still
stuck in.

**Off by default**, and deliberately not seeded into `bot_config`: merging to
main is a production release, so the slice that adds the read path must not
also switch it on. The rollout is a separate, instantly reversible DB write --
one chat first (`chat_settings.chunks_enabled = true`), then the global
`bot_config.default_chunks_enabled`. Rolling back is the same write inverted;
the indexer is unaffected either way, because writing and reading are
independent halves.

Note the gate this does NOT duplicate: `save_messages` decides whether a chat
has anything to chunk (the write side), `rag_enabled` decides whether the Q&A
store is searched. This one decides whether the chunk store is searched, so
all four combinations are expressible -- including "chunks only", which is
what the store swap eventually looks like.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "032"
down_revision: str = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS chunks_enabled BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS chunks_enabled")
