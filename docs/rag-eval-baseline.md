# RAG retrieval — recorded eval baselines

> Long-lived reference, not a plan artifact — decided [S3-7]: this document
> lives under `docs/` (survives any one plan) rather than `docs/plans/`.
> S4–S6 of [rag-revision-2026-08.md](plans/rag-revision-2026-08.md) reference
> the numbers here as the "before" side of the retrieval rework.
>
> **Public-safe by construction.** This file holds only aggregates — case
> counts by stratum, recall@k, MRR, blind rate, best-similarity percentiles,
> the config the run used, and the run date. **No question text, no answer
> text, no chat/message ids, no Telegram account names.** That detail is
> real production chat content, so it stays in `internal/eval/` (gitignored,
> `CLAUDE.md` §"This repo is public"). This document is scanned by
> `scripts/check_plan_artifacts.py` (`EXTRA_TRACKED_PATHS`) precisely so a
> future edit that pastes case-level detail in here gets caught, not
> shipped. Verify by hand at any time:
> `python3 scripts/check_plan_artifacts.py docs/rag-eval-baseline.md`.

## How a baseline is produced

Every run below replays a fixed eval-case file through the **real** search
path — `RAGMemoryService.search()`
([memory.py](../src/services/rag/memory.py#L61)), the same entry point
`TextProcessingPipeline` uses — via `scripts/eval_rag.py` (S3-2), against a
throwaway seed database, never a live one. `scripts/eval_metrics.py` (S3-4)
computes the aggregates from the harness's per-case results; nothing here is
hand-computed.

```
python -m scripts.eval_rag <seed-dsn> --cases <case-file>
```

## Baseline 1 — auto-strata floor (S3-6), 2026-08-10

**What this is, honestly (S3-6's own boundary):** `scripts/harvest_auto_strata.py`
pulls every question in the n8n-era corpus that *looks* memory-seeking (a
heuristic regex — "помнишь / напомни / что решили / …") and turns each into
a `found`-stratum `EvalCase` (S3-1 schema). Nobody has verified by hand which
earlier message actually answers any of them, so `expected_message_id_ranges`
is deliberately the widest honest bound ("anywhere at or before the
question"), not a pinpoint. That collapses `recall@k`/`MRR` here to "did the
real search path return *anything at all*" — i.e. the same number as
`1 - blind_rate`. **Read `blind_rate` as the meaningful number from this
baseline, not `recall@k`/`MRR`.** This is a floor, not a substitute for
S3b's manually-curated golden set (roadmap §7: "<50 cases: trust only large
deltas").

- **Run date:** 2026-08-10
- **Config version:** commit `92e6285` (`config/default.yml` at that
  commit) — `rag.min_similarity=0.7`, `rag.max_results=5`; query embeddings
  via `gemini-embedding-001` (768-dim, no `task_type`)
- **Source corpus:** n8n-era production corpus (throwaway seed snapshot),
  `rag-analysis-seed` container — 2 918 `chat_memory` rows
- **Case file:** `internal/eval/cases_auto_harvest.json` (gitignored — real
  chat content; harvested via `scripts/harvest_auto_strata.py`)

| Stratum | n |
|---|---|
| `found` (auto-harvested) | 11 |
| `knowledge-update` | 0 |
| `answer-absent` | 0 |
| **Total** | **11** |

| Metric | Value | n | Read as |
|---|---|---|---|
| `recall@5` | 0.364 | 11 | **Not meaningful here** — see boundary note above |
| MRR | 0.364 | 11 | **Not meaningful here** — see boundary note above |
| Blind rate (empty result on a `found`/`knowledge-update` case) | 0.636 | 11 | **Primary number for this baseline** — matches the prior one-off analysis (`internal/analysis/results/q5-replay.md`: 7/11 empty), now reproduced through the real search path instead of a reimplemented SQL query |
| Negative-control rate (`answer-absent`, correctly empty) | n/a | 0 | No `answer-absent` cases exist yet — auto-harvest only produces `found` (S3-5's negative control needs S3b) |
| Best-sim percentiles (over cases with ≥1 hit) | p10=0.713, p25=0.727, p50=0.739, p75=0.758, p90=0.786 | 4 | Free from the same run; S6 calibrates the similarity floor against this distribution |

> **Superseded as a ruler by R0 (2026-08-18, TD-092).** This run embedded
> each question exactly as it was typed, and auto-harvested questions come
> from `chat_messages.content` verbatim — so most of them still carried the
> leading `бот`/`bot` that addressed the bot. That token was measured to
> depress a real hit from 0.821 to 0.675 and to *raise* a miss from 0.524 to
> 0.640, so the numbers below describe retrieval through a query the bot no
> longer sends. `run_eval()` now strips the address the same way the pipeline
> does, which means a re-run of this very case file will not reproduce these
> figures — that is the fix working, not drift. The blind rate of 0.636
> remains a true statement about the system as of 2026-08-10; it is not a
> baseline S4/S5 deltas may be measured against. R2 re-records it.

**Caveats carried forward to S4–S6:**
- Only 11 cases, all one stratum — far below the roadmap's "<50 cases, trust
  only large deltas" threshold. Treat any future delta against this baseline
  as directional, not conclusive, until S3b's golden set exists.
- `answer-absent` has zero coverage here — nothing in this baseline can
  currently tell a genuine recall improvement apart from a lowered
  similarity floor (S3-5's whole reason for that stratum). The
  negative-control row above is a placeholder until S3b fills it.
- Best-sim percentiles are computed over only the 4 cases that returned a
  hit — not a reliable distribution shape on its own; useful mainly once S6
  has more cases to compare against.

## Reproducing

```
python -m scripts.harvest_auto_strata <n8n-dsn> --out internal/eval/cases_auto_harvest.json
python -m scripts.eval_rag <seed-dsn> --cases internal/eval/cases_auto_harvest.json
```

Both DSNs are required positional arguments with no default (decided [Q1] in
`docs/plans/rag-s3-eval-harness.md`) — the harness must never be able to
default onto a live database.
