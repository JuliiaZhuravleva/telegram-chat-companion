"""Conversation-session chunks -- the RAG index that covers the whole chat.

Revision ID: 029
Revises: 028
Create Date: 2026-08-19

`chat_memory` stores `"Q: ...\nA: ..."` pairs written only on turns where the
bot replied. Measured on production 2026-08-18, that is 4-8% of a live chat's
history -- and not a random 4-8%: it is exactly the part where people addressed
the bot, so the bot's memory is a record of conversations *about itself*.
`chat_chunks` indexes the conversation instead: sessions of real messages, over
the whole of what `chat_messages` already holds (plan §4.1, §5.1).

Design notes that are one-way doors, so they land here rather than later:

- **Natural key `(chat_id, thread_id, msg_from, msg_to, part)`** with `NULLS
  NOT DISTINCT` (PG15+; we run pg16). Without that clause a NULL `thread_id`
  compares distinct from itself and the unique index silently permits
  duplicates -- and today *every* row has a NULL there, because chunks are
  chat-wide: measured on production 2026-08-19, `chat_messages.
  message_thread_id` identifies reply chains rather than forum topics (2.0-2.7
  messages per value, ~70% NULL, 3737 distinct values in one chat). The column
  is kept for forum-aware chunking, which needs a way to recognise a forum
  first (`chat.is_forum` is not stored). `part` is the chunk's index inside its session, so a
  re-run over the same messages produces the same keys and `ON CONFLICT DO
  NOTHING` becomes a real idempotency guarantee.
- **No ANN index.** At hundreds to a couple of thousand chunks per chat an
  exact scan is both faster and complete, and it removes the ivfflat failure
  mode that R1 had to defuse in `chat_memory`: the ivfflat index carries no
  `chat_id`, so an approximate plan takes the globally nearest rows and only
  then filters the chat. Revisit near ~50k chunks/chat.
- **Generated `tsv`** rather than an application-side column: the FTS leg of
  S5's hybrid retrieval must never disagree with `content`, and a generated
  column cannot drift. The `translate()` normalising ё→е is symmetry insurance,
  **not** the mechanism: measured on pg16 in S5, PostgreSQL's `russian`
  configuration already folds ё→е unconditionally, including in words its
  stemmer cannot know ("зёшка" → 'зешк'), so the call is a no-op here. It is
  kept because the query side applies the identical expression and the pair
  must move together -- swap the configuration for one without that folding
  (`simple`) and the `translate()` becomes load-bearing on both sides at once.
  The original claim here -- that without it "ёлка" and "елка" stop matching --
  was wrong, and the test written to guard it could not fail.
- **`emb_model` and `emb_task_type` per row.** Embeddings here are asymmetric
  (`RETRIEVAL_DOCUMENT` at index time, `RETRIEVAL_QUERY` at query time), which
  is only valid because this index is built from scratch -- `task_type`
  changes the embedding space, so it can never be flipped on `chat_memory`'s
  existing vectors. Recording both per row is what makes a future model or
  task-type migration a query (`WHERE emb_model <> ...`) instead of an
  archaeology exercise.
- **Out of retention, deliberately.** `chat_messages` has a 365-day window;
  chunks must outlive it or the bot's memory develops a hole exactly one year
  wide with no observable trace. This is the policy ADR-0011 already applies
  to `chat_memory`, now at 100% coverage instead of ~7% -- see plan §5.1.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "029"
down_revision: str = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One statement per op.execute(): migrations run online through asyncpg,
    # which PREPAREs every statement, and PostgreSQL rejects a prepared
    # statement holding more than one command (CLAUDE.md).
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_chunks (
            id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            thread_id BIGINT,
            msg_from BIGINT NOT NULL,
            msg_to BIGINT NOT NULL,
            part SMALLINT NOT NULL DEFAULT 0,
            content TEXT NOT NULL,
            msg_count INTEGER NOT NULL,
            senders BIGINT[] NOT NULL DEFAULT '{}',
            started_at TIMESTAMPTZ NOT NULL,
            ended_at TIMESTAMPTZ NOT NULL,
            embedding vector(768),
            emb_model TEXT,
            emb_task_type TEXT,
            tsv tsvector GENERATED ALWAYS AS (
                to_tsvector('russian', translate(content, 'ёЁ', 'еЕ'))
            ) STORED,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT chat_chunks_natural_key
                UNIQUE NULLS NOT DISTINCT (chat_id, thread_id, msg_from, msg_to, part)
        )
    """)

    # The indexer's watermark query (`MAX(msg_to)` per chat/thread) and every
    # future time-bounded retrieval read this.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_chunks_chat_ended
        ON chat_chunks(chat_id, ended_at DESC)
    """)

    # FTS leg of S5's hybrid retrieval. Without it every text query is a
    # sequential scan; with it the leg is a bitmap lookup from day one.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_chunks_tsv
        ON chat_chunks USING GIN (tsv)
    """)

    # The embedding backfill queue. Partial, so it holds only the rows that
    # are actually pending -- which is zero rows in steady state.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_chunks_pending
        ON chat_chunks(id) WHERE embedding IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_chunks CASCADE")
