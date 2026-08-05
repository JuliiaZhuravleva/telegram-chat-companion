"""Durable decision/retrieval logging — decision_log + retrieval_log.

Revision ID: 022
Revises: 021
Create Date: 2026-08-05

Before this migration the bot's two most consequential runtime decisions left
no durable trace:

- **Silence.** RelevancyGate decisions and the pipeline's suppression paths
  (blacklist, cooldown, provider failure) were logged to stdout only. The
  production deploy recreates the container, so those lines die with it —
  "where was the bot annoyingly silent" was unanswerable after the fact.
- **Retrieval.** RAG/KB similarity scores lived for microseconds between the
  search and the prompt render, then were discarded. "Is retrieval working?"
  had no data behind it, which makes any retrieval improvement unfalsifiable
  on production.

Two append-only log tables fix that (RAG revision Phase A / slice S1; the
retrieval rework in later slices is calibrated against these distributions).

`decision_log` — one row per explicit respond/silence decision:
- `stage` — 'relevancy_gate' (all its verdicts) | 'pipeline' (suppressions).
- `decision` — 'respond' | 'silent'.
- `tier` — which mechanism decided: fast_rules | engagement | llm_judge for
  the gate; blacklist | cooldown | provider_error for pipeline suppressions.
- `reason` — free-text detail (gate reasons incl. the LLM judge's reasoning).

`retrieval_log` — one row per retrieval source per pipeline turn:
- `source` — 'rag_memory' | 'kb' (a future chunk store adds its own value).
- `params` JSONB — thresholds/limits the search ran with.
- `results` JSONB — array of {id, sim, injected, head} where `head` is capped
  at ~120 chars to bound row size; `injected` marks items that actually
  reached the prompt after budget trimming.
- `duration_ms` — embed+search wall clock combined (they are not separable
  at the RAG service boundary today).
- `error` — non-NULL when the retrieval pass itself failed (embedding or
  search); without it a broken source is byte-identical to a healthy source
  that matched nothing, which is the one question this table must answer.
- `message_id` — the incoming message that triggered the turn; same name and
  meaning as decision_log.message_id.

`user_id` convention (both tables): NULL means "no sender" (channel or
anonymous-admin posts). Writers normalize the pipeline's historical 0
sentinel to NULL so GROUP BY user_id never invents a phantom user 0.

Both tables are pruned by RetentionCleaner (90 days, same window as
response_log — these are operational analytics, not history). The database
is private; storing query/result text here is deliberate and safe — only the
repository is public.

No FK to chat_messages: log rows must survive message retention/deletion,
and chat_messages has a composite identity (chat_id, message_id) anyway.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "022"
down_revision: str = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS decision_log (
            id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            message_id BIGINT,
            user_id BIGINT,
            stage TEXT NOT NULL,
            decision TEXT NOT NULL,
            tier TEXT,
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_decision_log_chat_created
        ON decision_log (chat_id, created_at DESC)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS retrieval_log (
            id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            message_id BIGINT,
            source TEXT NOT NULL,
            query_text TEXT,
            params JSONB,
            results JSONB,
            n_results SMALLINT NOT NULL DEFAULT 0,
            n_injected SMALLINT NOT NULL DEFAULT 0,
            duration_ms INTEGER,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_retrieval_log_chat_created
        ON retrieval_log (chat_id, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS retrieval_log")
    op.execute("DROP TABLE IF EXISTS decision_log")
