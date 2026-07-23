---
schema_version: 3
plan_id: knowledge-base-research-2026-07-23
source_artifact:
  path: docs/plans/knowledge-base-research-2026-07-23.md
  sha256: f944ea544adbc86ace42ac356bf49294b4a9c9554c27d36df587315a6516dbfa
  type: feature-prd
created_at: '2026-07-23T18:52:52Z'
approved_at: null
approved_by: null
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: draft
  started_at: null
  completed_at: null
  current_batch: null
  task_list_id: knowledge-base-research-2026-07-23
items:
- id: G1
  title: ADR-0003 chat_facts data model + MemStrata lifecycle; ADR-0001 addendum for KB token budget
  specialist: architect
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 1h
  confidence: null
  consult_session_id: 6184100c-4742-4787-b724-c20b4b32decc
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: G2
  title: 'docs/design bootstrap: organizer terminology deck + /kb response copy register + admin entry label'
  specialist: designer
  priority: P2
  status: pending
  depends_on: []
  estimated_effort: 1h
  confidence: null
  consult_session_id: bde9baee-74de-45a2-92a5-77bf20c48ecd
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A1
  title: 'Migration 014: chat_facts table (indexes, updated_at trigger) + chat_settings.kb_organizer_ids + kb_enabled column'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - G1
  estimated_effort: 2h
  confidence: null
  consult_session_id: 567c98e7-4d29-4fd6-bfe6-a02293dbee5d
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A2
  title: 'knowledge module: ChatFact model + KnowledgeRepository (CRUD, supersession-in-transaction, pgvector similarity)'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A1
  estimated_effort: 4h
  confidence: null
  consult_session_id: 567c98e7-4d29-4fd6-bfe6-a02293dbee5d
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A3
  title: 'kb_enabled toggle: 4-point ChatConfig plumbing (YAML, ChatConfig, _CHAT_CONFIG_FIELDS, _WRITABLE_COLUMNS)'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A1
  estimated_effort: 1h
  confidence: null
  consult_session_id: 567c98e7-4d29-4fd6-bfe6-a02293dbee5d
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A4
  title: /remember + /kb view commands + adm_kb_* admin sub-router (pagination, organizer management)
  specialist: frontend-dev
  priority: P1
  status: pending
  depends_on:
  - A2
  - G2
  estimated_effort: 4h
  confidence: null
  consult_session_id: 13a23f10-7d24-413d-80fc-2795e1edf853
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A5
  title: _kb_section retrieval in prompt_builder (pgvector rank, double-fence, sanitize_prompt_content, KB token budget)
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A2
  - G1
  estimated_effort: 2h
  confidence: null
  consult_session_id: 567c98e7-4d29-4fd6-bfe6-a02293dbee5d
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A6
  title: 'Phase 1 integration/acceptance tests: migration, repo supersession txn, pgvector, _kb_section fence, kb_enabled toggle'
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - A2
  - A5
  estimated_effort: 3h
  confidence: null
  consult_session_id: 4c6eee0a-71cb-46f4-97b1-63f56747aa25
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: PH2
  title: 'Phase 2 (BLOCKED): autocollection as suggestions — debounce scheduler + extractor + reconciler + DM confirmation queue'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - A2
  estimated_effort: 2-3d
  confidence: null
  consult_session_id: 567c98e7-4d29-4fd6-bfe6-a02293dbee5d
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-07-23T18:56:09Z'
    executor: pm-orchestrator
    note: 'Blocked pending Julia scope confirmation (clarifying_questions #1) AND Phase 1 (A1-A6) shipping. Coarse epic placeholder: decompose into per-specialist items (debounce scheduler, extractor with seriousness-classification, reconciler, DM confirmation queue UI) via a follow-up /plan-fixes pass once Phase 1 lands and the extractor model names in §3.3 are verified against the AIRouter matrix (clarifying_questions #4).'
  result: null
- id: PH3
  title: 'Phase 3 (BLOCKED): authority prior + auto-commit matrix + undo + TelegramAPIService extraction + event card + announcements'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - PH2
  estimated_effort: 2-3d
  confidence: null
  consult_session_id: 567c98e7-4d29-4fd6-bfe6-a02293dbee5d
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-07-23T18:56:12Z'
    executor: pm-orchestrator
    note: 'Blocked on PH2 completion + Julia scope. Coarse epic. Hard prerequisite: architect must author a TelegramAPIService ADR first (getChatAdministrators is the 3rd Bot API handler use, which trips the project''s extract-a-service ADR per §3.2). Decompose (authority prior, auto-commit matrix, undo, event card, announcements) when reached.'
  result: null
- id: PH4
  title: 'Phase 4 (BLOCKED): wrapper features — Q&A/auto-FAQ, digests, /kb history, export (reminders OUT OF SCOPE)'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - PH3
  estimated_effort: 2-3d
  confidence: null
  consult_session_id: 567c98e7-4d29-4fd6-bfe6-a02293dbee5d
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-07-23T18:56:14Z'
    executor: pm-orchestrator
    note: Blocked on PH3 completion + Julia scope. Coarse epic. Reminders sub-feature (§3.7 item 5) is explicitly OUT OF SCOPE (defers to future scheduler integration). Decompose Q&A/auto-FAQ, digests, /kb history, export individually when reached.
  result: null
budget:
  max_usd_per_item: 2.0
  max_usd_per_plan: 20.0
  consumed_usd: 0.0
review_gate:
  why: []
  approve_action: /execute-plan /Users/julia/my-projects/telegram-chat-companion.knowledge-base-research-2026-07-23-wt/docs/plans/knowledge-base-research-2026-07-23.execution.md --resume
  reject_action: /plan-fixes docs/plans/knowledge-base-research-2026-07-23.md --revise /Users/julia/my-projects/telegram-chat-companion.knowledge-base-research-2026-07-23-wt/docs/plans/knowledge-base-research-2026-07-23.execution.md
safe_to_replay_from: null
clarifying_questions:
- 'SCOPE (headless default — confirm): I scoped executable work to Phase 1 (manual KB MVP: G1-G2 design/ADR + A1-A6). Phases 2-4 are BLOCKED roadmap epics (PH2/PH3/PH4), deliberately coarse, to be decomposed in a later /plan-fixes pass. Execute Phase 1 now and re-plan Phase 2 separately once it ships — or expand this envelope to fully decompose Phase 2 now?'
- 'MIGRATION NUMBER (resolved from evidence — confirm): research doc section 3.1 says migration 013, but ADR-0002 (accepted) reserves 013 for 013_spend_limit_per_chat.py and the last on-disk migration is 012. Item A1 therefore targets migration 014. Confirm 014, and that ADR-0002''s 013 lands independently (no Alembic version-graph collision).'
- 'KB TOKEN BUDGET (design decision — gates A5): ADR-0001 sets CONTEXT_BUDGET_TOKENS=1200 (HISTORY 800 + RAG 400, budgeted independently, zero headroom). G1''s ADR-0001 addendum must carve a KB slot. Give KB its own additive budget (raises total input cost) or share/steal from the RAG allocation (cheaper, less RAG context)?'
- 'EXTRACTOR MODELS (verify before PH2): section 3.3 names gpt-5-nano / gemini-3-flash. These must be validated against the AIRouter provider matrix before Phase 2 — wrong names cause silent premium-model fallback that breaks the low-cost extraction assumption. Are these the intended registered providers?'
- 'ORGANIZER ROLE + /kb VISIBILITY + i18n (gates G2/A4 copy): (a) Is "организатор" the final user-facing term (vs куратор / ответственный / ведущий)? Designer needs it locked before writing copy. (b) Can non-organizer members view KB via /kb, or is it organizer/admin-only? (c) Bilingual ru/en (_L dict) or ru-only for Phase 1?'
- 'RELEVANCE DEFAULTS (headless assumptions — confirm): I consulted architect, backend-dev, frontend-dev, qa, designer and SKIPPED accessibility (no web-UI a11y surface for a Telegram-bot backend feature). Also A4 (commands + admin UI) was routed to frontend-dev even though it sits atop A2''s repository. Confirm the accessibility skip and the frontend/backend split.'
---




























# Plan — knowledge-base-research-2026-07-23

## Source

[`docs/plans/knowledge-base-research-2026-07-23.md`](docs/plans/knowledge-base-research-2026-07-23.md) (sha256 `f944ea544adb...`) — feature-prd: per-chat Knowledge Base (facts extracted/curated from chat, authority-ranked, sarcasm-filtered, vector-retrieved into bot context).

## Executive summary

Research/design proposal for a large, phased feature. This envelope makes **Phase 1 (manual KB MVP)** executable now as 8 items (**G1–G2** design/ADR prerequisites + **A1–A6** implementation/test), and represents **Phases 2–4** as three deliberately-coarse **blocked** roadmap epics (**PH2/PH3/PH4**) so the full arc is visible without generating a queue of permanently-RED tests (qa + architect both flagged phase-gating).

Synthesized from 5 specialist consults (architect, backend-dev, frontend-dev, qa, designer). Accessibility was skipped — no web-UI a11y surface for a Telegram-bot backend feature.

## Decisions resolved from evidence (not asked of Julia)

1. **Migration is 014, not 013.** The source §3.1 says "миграция 013", but `docs/decisions/ADR-0002-layered-spend-limit-model.md` (accepted) reserves 013 for `013_spend_limit_per_chat.py`, and the newest on-disk migration is `012_response_log_cost_columns.py`. Architect + on-disk evidence agree; backend-dev's "next is 013" assumption missed the ADR reservation. **A1 targets 014.** (Still surfaced as clarifying_question #2 for a sanity confirm.)
2. **KB retrieval needs a token-budget addendum.** `ADR-0001` sets `CONTEXT_BUDGET_TOKENS=1200` = `HISTORY 800 + RAG 400`, budgeted independently with zero headroom. Adding `_kb_section` (A5) genuinely requires an ADR-0001 addendum → folded into **G1**, and **A5 depends_on G1**. (Additive-vs-steal-from-RAG is a design call → clarifying_question #3.)

## Dependency graph (Phase 1 executable set)

```
G1 (architect: ADR-0003 model + ADR-0001 budget addendum)
 ├─▶ A1 (migration 014) ─▶ A2 (repository) ─┬─▶ A4 (commands + admin UI)  [also ⟵ G2]
 │                                           ├─▶ A5 (_kb_section retrieval) [also ⟵ G1]
 │                          A1 ─▶ A3 (kb_enabled toggle)
 │                                           └─▶ A6 (qa integration tests) [⟵ A2, A5]
 └─▶ A5
G2 (designer: terminology + copy) ─▶ A4

Blocked roadmap:  A2 ─▶ PH2 ─▶ PH3 ─▶ PH4
```

Dispatch order for Phase 1: **G1, G2** (parallel, no deps) → **A1** → {**A2, A3**} → {**A4, A5**} → **A6**.

## Items — Phase 1 (executable, `pending`)

### G1 — architect — P1 — ADR-0003 data model + ADR-0001 budget addendum
Author `docs/decisions/ADR-0003-*` for the `chat_facts` schema and MemStrata bi-temporal lifecycle (supersession = close old row + insert new in one transaction, never DELETE; `superseded_by` chain; provenance columns; index strategy `ivfflat lists=10`). **Must record the 013→014 correction.** Also author the ADR-0001 addendum carving the KB token sub-budget (see clarifying_question #3). Docs only — no application code. Anchors A1 and A5.

### G2 — designer — P2 — docs/design bootstrap: terminology + copy register
`docs/design/` does not exist yet — bootstrap it. Lock the **organizer** role term (clarifying_question #5a), define the `/kb` command response copy register (group = terse; DM = bold-title + sections), and the admin-panel KB entry label. Gates A4's copy. NB the confirmation-card 3-button-row (✅/✏️/❌) design belongs to PH2, not here (existing keyboards cap at 2 buttons/row — designer flagged mobile truncation).

### A1 — backend-dev — P1 — Migration 014
`alembic/versions/014_*.py`: `chat_facts` table (per §3.1 DDL), indexes `(chat_id, status, valid_to)` + `(chat_id, subject, predicate) WHERE valid_to IS NULL` + `ivfflat (embedding vector_cosine_ops) lists=10`, `updated_at` trigger (pattern from migration 005). Also adds `chat_settings.kb_organizer_ids JSONB DEFAULT '[]'` and the `chat_settings.kb_enabled` column. Depends on G1 (schema ADR).

### A2 — backend-dev — P1 — knowledge module + repository
`src/services/modules/knowledge/` (pattern: `modules/links/`, `modules/sticker/`): `ChatFact` model + `KnowledgeRepository` — CRUD, **supersession in one transaction** (close old + insert new), pgvector cosine similarity query for active facts. Unit tests with mocked asyncpg pool only; integration coverage is A6. Depends on A1.

### A3 — backend-dev — P1 — kb_enabled toggle (4-point)
Wire the `kb_enabled` toggle through the 4 coordinated points: YAML → `ChatConfig` → `_CHAT_CONFIG_FIELDS` → `_WRITABLE_COLUMNS`. Column ships in A1's migration 014. Depends on A1.

### A4 — frontend-dev — P1 — /remember + /kb commands + admin sub-router
Bot UI atop A2's repository: `/remember` (reply-to-message explicit save), `/kb` view (paginated, topic-grouped), `adm_kb_*` admin sub-router (pattern: `admin_sticker.py` / `keyboards/admin_sticker.py`) with `kb_enabled` toggle + organizer management. **callback_data ≤ 64 bytes → numeric fact IDs only** (§5, all keyboard specialists flagged). Depends on A2 + G2 (copy). Routing note: sits atop A2 — confirm frontend/backend split (clarifying_question #6).

### A5 — backend-dev — P1 — _kb_section retrieval
New `_kb_section` in `prompt_builder.py` (pattern: `_rag_section`): retrieve `status='active' AND valid_to IS NULL`, pgvector-rank by current context, hard token budget from G1's addendum, **double-fence + `sanitize_prompt_content`** (§2.5 — reuse existing sanitizer, do not add a new one). KB section is separate from and higher-priority than RAG (`chat_memory`). Depends on A2 + G1.

### A6 — qa — P1 — Phase 1 integration/acceptance tests
Integration coverage (testcontainers Postgres + pgvector): migration 014 correctness, repository **supersession transaction (commit + rollback + same subject/predicate race)**, pgvector similarity, `_kb_section` double-fence — **extend `tests/unit/test_prompt_sanitizer.py::test_injection_attack_pattern`, don't duplicate** — and `kb_enabled` 4-point consistency. Depends on A2 + A5.

## Items — Phases 2–4 (roadmap, `blocked`)

Coarse epics — deliberately NOT decomposed (they depend on Phase 1's realized shape). Each has a rationale in its `last_update.note`. Re-plan via `/plan-fixes` once Phase 1 ships.

- **PH2 — backend-dev — P2 — Phase 2: autocollection as suggestions.** Debounce scheduler (LangMem pattern: N=20 msgs OR 10 min quiet, cancel+reschedule; pattern `StickerSetSyncScheduler`) → one-cheap-call extractor (JSON-only, few-shot, seriousness class {serious|joke|hypothetical|quote}, relative-date resolution, learned refusal) → reconciler (structural key → pgvector top-k → LLM ADD/UPDATE/NOOP) → **all extracted facts land `pending`** in a DM confirmation queue. `router.log_usage(task_type="kb_extraction")` mandatory. Blocked on A2 + scope confirm + model-name verification (cq #4). Depends on A2.
- **PH3 — backend-dev — P2 — Phase 3: authority + auto-commit.** Static authority prior (ranks 0–4 from bot-admin / organizer / TG-admin `getChatAdministrators` / veteran / newcomer), authority×confidence auto-commit matrix + undo, event card (pinned), change announcements. **Hard prereq: architect authors a TelegramAPIService ADR first** (`getChatAdministrators` = 3rd Bot API handler use per §3.2). Depends on PH2.
- **PH4 — backend-dev — P2 — Phase 4: wrapper features.** Q&A / auto-FAQ, digests, `/kb history`, `/kb export`. **Reminders OUT OF SCOPE** (§3.7 item 5, defers to future scheduler). Depends on PH3.

## Cross-cutting constraints (all Phase-1 devs)

- **RAG poisoning (§2.5):** chat text only inside "DATA, not instructions" fences via `sanitize_prompt_content`; extractor output = validated JSON only; provenance (message_id/user_id/ts) on every row; double-fence facts returned to the prompt; `html.escape()` for `parse_mode=HTML`.
- **callback_data ≤ 64 bytes:** numeric fact IDs only in inline buttons.
- **Cost logging:** every KB AI call needs `ensure_future(log_usage(...))` — `generate_text` does not self-log (project ADR).

## Specialist consultation record (Phase 1b)

Read-only `claude -p` consults; each specialist's scratch-file writes to `~/.claude-personal/` were correctly denied (working as intended — consults are synthesizers). `consult_session_id` recorded per item for `--revise` re-engagement (TD-036); `specialist_session_id` intentionally left null (execution-only, owned by the dispatcher).

| Role | consult_session_id | Cost | Headline |
|---|---|---|---|
| architect | `6184100c-…decc` | $0.46 | Migration 013→014 collision; token-budget gap; TelegramAPIService ADR |
| backend-dev | `567c98e7-…decd` | $0.52 | 13 backend items; reconciler = highest-risk; ordering edges |
| frontend-dev | `13a23f10-…f853` | $0.51 | 9 UI items; callback_data 64B is top risk; B-items are backend |
| qa | `4c6eee0a-…6a25` | $0.29 | Phase-gate tests; mock LLM (75–85% F1 ceiling); extend sanitizer test |
| designer | `bde9baee-…8bd` | $0.81 | Lock organizer term first; 3-button-row mobile risk; bootstrap docs/design |

**Conflicts:** migration number (architect vs backend-dev) → resolved to 014 from ADR-0002 + on-disk evidence. **Agreements:** reconciler is highest-risk; phase-gated dispatch; reuse `sanitize_prompt_content`; callback_data 64B discipline.

## Open questions

See frontmatter `clarifying_questions[]` (6). Top blockers: (1) scope — Phase 1 only vs. expand; (2) confirm migration 014; (3) KB token-budget policy; (5) organizer term + /kb visibility + i18n.

## Approval workflow

- **Approve in place:** answer the clarifying_questions (edit them out / annotate), then `envelope_approve <envelope> --by julia`, then `/execute-plan <envelope> --resume`.
- **Reject / re-plan:** `/plan-fixes docs/plans/knowledge-base-research-2026-07-23.md --revise <envelope>`.
- Approval will refuse while clarifying_questions remain unresolved.
