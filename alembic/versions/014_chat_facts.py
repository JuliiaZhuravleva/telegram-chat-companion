"""Knowledge Base: chat_facts table + kb_organizer_ids/kb_enabled on chat_settings.

Revision ID: 014
Revises: 012
Create Date: 2026-07-24

Per-chat Knowledge Base (Phase 1 manual MVP, docs/decisions/ADR-0003):
curated, authority-ranked, vector-retrieved facts distinct from the existing
episodic RAG memory in chat_memory. MemStrata bi-temporal lifecycle
(supersession = close old row + insert new, never DELETE).

Migration number note (ADR-0003): the source plan referenced "migration 013",
but ADR-0002 (accepted) already reserves 013 for 013_spend_limit_per_chat.py,
which has not yet landed on disk. This migration is numbered 014 and chains
onto 012 (the newest migration present) rather than 013, per ADR-0003's
reservation record -- re-check that ADR if 013 lands with a different
down_revision expectation before this merges.

Adds:
- chat_facts table (schema + indexes + updated_at trigger)
- chat_settings.kb_organizer_ids (JSONB, default '[]')
- chat_settings.kb_enabled (BOOLEAN, default false -- opt-in per chat)
"""

from collections.abc import Sequence

from alembic import op

revision: str = "014"
down_revision: str = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. chat_facts table
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_facts (
            id                 BIGSERIAL PRIMARY KEY,
            chat_id            BIGINT NOT NULL,
            topic              TEXT,                 -- grouping: 'event:summer-meetup' | 'general'
            subject            TEXT NOT NULL,         -- normalized key, e.g. 'мероприятие'
            predicate          TEXT NOT NULL,         -- 'дата', 'место', 'программа', ...
            value              TEXT NOT NULL,         -- short value, absolute dates only (no "tomorrow")
            fact_text          TEXT NOT NULL,         -- full NL statement, used for embedding + prompt render
            embedding          vector(768),
            -- lifecycle (MemStrata/Graphiti bi-temporal pattern)
            status             TEXT NOT NULL DEFAULT 'pending',  -- pending|active|rejected|superseded
            valid_from         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            valid_to           TIMESTAMPTZ,           -- NULL = currently valid
            superseded_by      BIGINT REFERENCES chat_facts(id),
            -- provenance + trust (RAG-poisoning mitigation, §2.5)
            source             TEXT NOT NULL,         -- 'manual' | 'extracted'
            source_message_id  BIGINT,
            source_user_id     BIGINT,
            authority_level    SMALLINT NOT NULL DEFAULT 0,  -- author's rank snapshot at write time
            confidence         FLOAT,                 -- extractor confidence (NULL for manual)
            salience           FLOAT DEFAULT 0.5,     -- context-priority weight
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_facts_status
        ON chat_facts(chat_id, status, valid_to)
    """)

    # UNIQUE: DB-level backstop for the "exactly one active row per key"
    # invariant (ADR-0003). Application-level serialization alone (FOR UPDATE)
    # cannot cover the create-create race — with no existing row there is
    # nothing to lock. The DROP first upgrades any dev DB that already has the
    # pre-review non-unique version of this index.
    op.execute("""
        DROP INDEX IF EXISTS idx_chat_facts_active_key
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_facts_active_key
        ON chat_facts(chat_id, subject, predicate) WHERE valid_to IS NULL
    """)

    # ivfflat: lists=10 for small initial dataset (same rationale as migration
    # 005's sticker_knowledge_embedding index). Raise to 100 once a chat's
    # active-fact count approaches ~4000 rows -- track via a follow-up ops
    # note, not blocking Phase 1.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_facts_embedding
        ON chat_facts USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS chat_facts_updated_at ON chat_facts
    """)
    op.execute("""
        CREATE TRIGGER chat_facts_updated_at
            BEFORE UPDATE ON chat_facts
            FOR EACH ROW EXECUTE FUNCTION update_updated_at()
    """)

    # 2. chat_settings: organizer list + KB opt-in toggle
    op.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS kb_organizer_ids JSONB NOT NULL DEFAULT '[]'
    """)
    # No NOT NULL and no DEFAULT: a chat_settings row leaves kb_enabled NULL
    # until the chat explicitly opts in/out, so the three-layer merge's
    # global layer (bot_config default_kb_enabled) actually applies to
    # already-onboarded chats. (Siblings with DEFAULT materialize a value on
    # ensure_exists and silently shadow their global default — pre-existing
    # gap, tracked separately.) The two ALTERs upgrade dev DBs that ran the
    # pre-review NOT NULL DEFAULT false version; both are idempotent.
    op.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS kb_enabled BOOLEAN
    """)
    op.execute("""
        ALTER TABLE chat_settings
        ALTER COLUMN kb_enabled DROP NOT NULL
    """)
    op.execute("""
        ALTER TABLE chat_settings
        ALTER COLUMN kb_enabled DROP DEFAULT
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS kb_enabled;")
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS kb_organizer_ids;")
    op.execute("DROP TRIGGER IF EXISTS chat_facts_updated_at ON chat_facts;")
    op.execute("DROP INDEX IF EXISTS idx_chat_facts_embedding;")
    op.execute("DROP INDEX IF EXISTS idx_chat_facts_active_key;")
    op.execute("DROP INDEX IF EXISTS idx_chat_facts_status;")
    op.execute("DROP TABLE IF EXISTS chat_facts CASCADE;")
