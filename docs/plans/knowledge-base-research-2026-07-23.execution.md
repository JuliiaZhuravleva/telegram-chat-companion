---
schema_version: 3
plan_id: knowledge-base-research-2026-07-23
source_artifact:
  path: docs/plans/knowledge-base-research-2026-07-23.md
  sha256: f944ea544adbc86ace42ac356bf49294b4a9c9554c27d36df587315a6516dbfa
  type: feature-prd
created_at: '2026-07-23T18:52:52Z'
approved_at: '2026-07-24T13:07:21Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: partial
  started_at: '2026-07-24T13:08:04Z'
  completed_at: null
  current_batch: null
  task_list_id: knowledge-base-research-2026-07-23
items:
- id: G1
  title: ADR-0003 chat_facts data model + MemStrata lifecycle; ADR-0001 addendum for KB token budget
  specialist: architect
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1h
  confidence: 0.85
  consult_session_id: 6184100c-4742-4787-b724-c20b4b32decc
  specialist_session_id: 66ee1665-7e14-4f3a-9b2e-94ef79873ddf
  retry_count: 0
  last_update:
    ts: '2026-07-24T15:03:20Z'
    executor: architect
    note: 'Wrote ADR-0003 (chat_facts schema + MemStrata bi-temporal lifecycle) and folded in the ADR-0001 KB token-budget addendum (KB_BUDGET_TOKENS=300, additive per Julia decision #3) into the same doc since both concern prompt_builder.py''s budget module. Two evidence-based corrections beyond the source plan: (1) recorded the 013(ADR-0002 spend-limit)->014(chat_facts) migration-number reservation as durable ADR text, not just envelope metadata; (2) corrected A2''s repository location from the source plan''s modules/knowledge/ table entry to database/repositories/knowledge.py, matching the verified existing split (StickerRepository/MemoryRepository live in database/repositories/, modules/*/models.py is dataclasses-only) -- flagged so A2 doesn''t introduce an unjustified new pattern. Also surfaced a pre-existing gap: ADR-0001''s own budget constants/trim functions were never actually shipped in prompt_builder.py (grep-verified empty) -- recommended A5 implement KB''s own trim only and open a separate tech-debt item rather than silently absorbing that gap into Phase 1 scope. Docs only, no application code touched.'
  result:
    kind: file
    ref: docs/decisions/ADR-0003-chat-facts-data-model.md
    verification: grep-verified repository split (stickers.py/memory.py in database/repositories vs modules/*/models.py dataclasses-only) and grep-verified ADR-0001 budget constants absent from prompt_builder.py before writing the addendum; on-disk alembic/versions/ listing confirms 012 is newest, 013 unclaimed on disk but reserved by ADR-0002 text
- id: G2
  title: 'docs/design bootstrap: organizer terminology deck + /kb response copy register + admin entry label'
  specialist: designer
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 1h
  confidence: 0.85
  consult_session_id: bde9baee-74de-45a2-92a5-77bf20c48ecd
  specialist_session_id: 6be09c3c-9187-45c6-97c6-e7cf908b4629
  retry_count: 0
  last_update:
    ts: '2026-07-24T13:24:41Z'
    executor: designer
    note: 'Bootstrapped docs/design/ (didn''t exist). Wrote kb-copy-register.md: organizer role term lock (ru «организатор» / en organizer, non-synonymous with bot-admin/TG-admin ranks); /kb copy register split group=terse-flat vs DM=bold-title+topic-sectioned (grouped by ADR-0003 topic column), page-size caps, pagination reusing existing sticker_sets_keyboard footer shape; /remember success+malformed+no-reply copy (flagged input-format as backend/frontend call, not mine); admin-panel KB entry (_L[''kb''], icon 📚 verified collision-free, deliberately distinct from 🧠 already used for RAG/chat_memory in /help); kb_enabled toggle reuses EXISTING notifications_keyboard boolean-toggle convention rather than inventing new verbiage; organizer management screen add/remove copy. Assigned each string to whichever of the 3 existing copy patterns (_L dict / inline ternary / module _XXX_TEXT dicts) matches its surface — no 4th pattern invented, no i18n module invented (none exists, flagged consolidation as architect''s call). Explicitly out of scope: PH2 3-button confirm row (flagged 2-button-per-row cap as real constraint for its future pass), PH3 announcement/event-card copy, PH4 digest copy. Callback namespaces verified collision-free against all existing adm_ prefixes via grep. docs/design/README.md created as index. No implementation code touched.'
  result:
    kind: file
    ref: docs/design/kb-copy-register.md
    verification: grep-verified no _L/callback collisions; toggle convention and voice register sourced from actual code (notifications_keyboard, _HELP_TEXT); date format flagged as NEW since no existing convention found in src/services/text or src/utils; docs/design/README.md cross-links and gates A4.
- id: A1
  title: 'Migration 014: chat_facts table (indexes, updated_at trigger) + chat_settings.kb_organizer_ids + kb_enabled column'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - G1
  estimated_effort: 2h
  confidence: 0.9
  consult_session_id: 567c98e7-4d29-4fd6-bfe6-a02293dbee5d
  specialist_session_id: 45e96738-2968-4376-9d31-70c881afa32a
  retry_count: 0
  last_update:
    ts: '2026-07-24T15:08:30Z'
    executor: backend-dev
    note: 'Migration 014: chat_facts table (per ADR-0003 DDL verbatim) + chat_settings.kb_organizer_ids/kb_enabled, bundled in one file per migration 008''s precedent. down_revision=012 per ADR-0003''s 013->014 reservation. Verified via ''alembic upgrade head --sql'' offline render: chains cleanly 012->014, no branch conflict, all DDL idempotent (IF NOT EXISTS/IF EXISTS). Wrote 15 unit tests (tests/unit/test_migration_014_chat_facts.py) covering revision chain, full alembic --sql subprocess render, upgrade() DDL content via op.execute monkeypatch, idempotency-guard sweep, downgrade() ordering+guards. No real-DB integration test here — that''s A6/qa scope (testcontainers Postgres+pgvector). Full unit suite (854 tests) green; ruff clean on touched files (pre-existing alembic/env.py F401 unrelated); mypy strict on src/ green (migration/tests outside mypy''s src/-only scope per CONTRIBUTING.md). Committed d5542c1, only the two A1 files staged.'
  result:
    kind: commit
    ref: d5542c1
    verification: 'pytest tests/unit/test_migration_014_chat_facts.py: 15 passed; full tests/unit: 854 passed; ruff check+format clean; alembic upgrade head --sql renders 012->014 with no errors; mypy src/ clean'
- id: A2
  title: 'knowledge module: ChatFact model + KnowledgeRepository (CRUD, supersession-in-transaction, pgvector similarity)'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A1
  estimated_effort: 4h
  confidence: 0.9
  consult_session_id: 567c98e7-4d29-4fd6-bfe6-a02293dbee5d
  specialist_session_id: 60c620a4-36df-4f27-b47a-22ddcd659b92
  retry_count: 0
  last_update:
    ts: '2026-07-24T15:14:24Z'
    executor: backend-dev
    note: 'Implemented ChatFact dataclass + FactStatus/FactSource enums (src/services/modules/knowledge/) and KnowledgeRepository (src/database/repositories/knowledge.py) per ADR-0003''s repository-location correction. upsert_fact() does supersession-in-one-transaction (FOR UPDATE on existing active row at (chat_id,subject,predicate), close-old+insert-new, never DELETE); search_by_similarity() orders salience DESC then pgvector similarity DESC exactly per ADR-0003 Part 2''s contract that A5''s trim_facts_to_budget() relies on unmodified. Registered KnowledgeRepository in di.py''s RepositoryProvider per CONTRIBUTING.md checklist. Unit tests (10, mocked asyncpg pool/connection incl. transaction/FOR UPDATE mocking) cover: plain insert, supersession path, single-transaction usage, get_by_id, get_active_facts (with/without topic filter), search_by_similarity ordering, reject_fact true/false. Full unit suite green (864 = prior 854 + 10 new), ruff+format clean, mypy src/ clean. Routing hint for qa (A6): integration coverage needed for the FOR UPDATE race (two concurrent writers on same subject/predicate key) and real-pgvector similarity ranking -- noted as out of scope here per envelope (A6 owns testcontainers Postgres+pgvector).'
  result:
    kind: commit
    ref: 7088f9f
    verification: 'pytest tests/unit/test_knowledge_repository.py: 10 passed; full tests/unit: 864 passed; ruff check+format clean on touched files; mypy src/ clean (106 source files)'
- id: A3
  title: 'kb_enabled toggle: 4-point ChatConfig plumbing (YAML, ChatConfig, _CHAT_CONFIG_FIELDS, _WRITABLE_COLUMNS)'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A1
  estimated_effort: 1h
  confidence: 0.92
  consult_session_id: 567c98e7-4d29-4fd6-bfe6-a02293dbee5d
  specialist_session_id: 892e9d02-b510-4f22-bb46-c8469eefcb6d
  retry_count: 0
  last_update:
    ts: '2026-07-24T15:23:01Z'
    executor: backend-dev
    note: 'Wired kb_enabled through the 4 coordinated points, matching the exact chain of every other per-chat boolean toggle (rag_enabled/abuse_filter_enabled/rules_enabled/link_comments_enabled precedent): (1) config/default.yml modules.knowledge_base block (enabled:false, requires:[embeddings]) mirrors abuse_filter/rules_engine/link_comments YAML module registration; (2) ChatConfig.kb_enabled: bool = False in src/models/chat_config.py (default False matches A1 migration 014 DB column default, opt-in per ADR-0003); (3) added to _CHAT_CONFIG_FIELDS in src/services/chat_config.py so global/per-chat DB overrides merge correctly; (4) added to _WRITABLE_COLUMNS in src/database/repositories/chat_settings.py so the admin panel (A4 adm_kb_* toggle) can write it. Deliberately did NOT wire kb_organizer_ids writability or any runtime consumption (middleware/prompt_builder gating) -- out of A3 scope per envelope (A4 owns admin UI, A5 owns _kb_section consuming the toggle). Added 8 unit tests across test_config.py (YAML leg), test_chat_config_service.py (ChatConfig+_CHAT_CONFIG_FIELDS merge, 3 tests), test_repositories.py (_WRITABLE_COLUMNS, 2 tests). Drive-by fixed one pre-existing UP038 lint violation in the same file (isinstance tuple-check) blocking the pre-commit hook pinned ruff v0.9.6 (local ruff 0.15.0 does not flag it) -- zero behavior change, needed to pass the project actual commit gate. Full unit suite 870 passed (862 prior + 8 new), ruff+format clean, mypy src/ clean (106 files). No qa routing hint needed -- pure config plumbing, A6 already covers kb_enabled 4-point consistency.'
  result:
    kind: commit
    ref: 345a37f
    verification: 'pytest tests/unit: 870 passed; ruff check+format clean; mypy src/ clean (106 source files); pre-commit hooks passed on commit'
- id: A4
  title: /remember + /kb view commands + adm_kb_* admin sub-router (pagination, organizer management)
  specialist: frontend-dev
  priority: P1
  status: done
  depends_on:
  - A2
  - G2
  estimated_effort: 4h
  confidence: 0.85
  consult_session_id: 13a23f10-7d24-413d-80fc-2795e1edf853
  specialist_session_id: 9f69a9c6-3264-4e19-a86a-16db4901c546
  retry_count: 2
  last_update:
    ts: '2026-07-24T15:47:51Z'
    executor: frontend-dev
    note: 'Fixes the evaluator''s NEEDS_WORK finding on commit 9fbd4d8: kb_view callback_data was rendered on every paginated slash-kb view but no handler existed for it, so the next/prev buttons were dead once a chat had more facts than one page. Added a real callback handler that re-fetches and re-slices the active facts at the requested page and edits the message in place, refactoring the group/DM rendering into shared page-aware helpers used by both the initial command and the new callback so there is exactly one rendering code path per mode, not a duplicate. Render mode (group terse vs DM sectioned) is picked from the callback message''s own chat type, so the callback_data shape is unchanged from the original contract -- no knock-on changes needed in the keyboard module. Added 3 regression tests exercising the previously-dead path directly (group page 2, DM page 2, empty-facts no-op), plus confirmed the existing 16 tests from the prior commit still pass unchanged. Full unit suite green (889, up from 886), ruff and mypy clean. Residual known gap carried over from the prior verdict, not touched by this fix and still worth flagging: slash-kb typed directly in a private DM operates on the DM''s own chat id, which structurally never has chat_facts rows (those belong to a real group chat) -- so a literal DM invocation of slash-kb will always render the empty state in practice. The DM-rendering code path is real and tested (verified via the callback test using chat_type=private), but is only reachable today if something in a future item forwards a specific group''s view into DM; there is no in-scope mechanism yet for a user to pick which group''s KB to view from within a DM. This mirrors the same per-chat-scoping ambiguity already flagged for the admin panel''s chat picker and was left unresolved by G2''s copy register; flagging again explicitly here since it directly affects slash-kb''s advertised DM behavior, in case a follow-up item or qa''s E2E pass needs to reconcile it.'
  result:
    kind: commit
    ref: '6297270'
    verification: 'pytest tests/unit/test_commands_kb.py tests/unit/test_admin_kb_handler.py: 19 passed (16 prior plus 3 new regression tests for the fixed pagination callback); full tests/unit: 889 passed; ruff check and format clean on src and tests; mypy src clean (108 source files)'
- id: A5
  title: _kb_section retrieval in prompt_builder (pgvector rank, double-fence, sanitize_prompt_content, KB token budget)
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A2
  - G1
  estimated_effort: 2h
  confidence: 0.88
  consult_session_id: 567c98e7-4d29-4fd6-bfe6-a02293dbee5d
  specialist_session_id: b180535e-fdeb-47e1-b461-2179fd901086
  retry_count: 0
  last_update:
    ts: '2026-07-24T15:56:44Z'
    executor: backend-dev
    note: 'Implemented _kb_section() in prompt_builder.py (pattern: _rag_section) per ADR-0003 Part 2: KB_BUDGET_TOKENS=300/MAX_FACT_CHARS=600 constants, trim_facts_to_budget() (KB''s own trim only, per ADR-0003''s explicit recommendation not to absorb the pre-existing ADR-0001 history/RAG trim gap -- that gap should be its own backend-dev tech-debt item, still open). KB section placed before RAG, sharing one double-fence security reminder when either is present. Also wired full end-to-end retrieval (beyond the ADR''s narrow 3-item prompt_builder.py-only note, but required for the feature to actually do anything -- otherwise chat_facts saved via A4 would never reach the bot''s context): pipeline.py gathers KB facts in parallel with RAG/link/sticker when config.kb_enabled, via AIRouter.generate_embedding() (self-logging) + KnowledgeRepository.search_by_similarity(), non-blocking on failure; di.py wires the existing knowledge_repo provider into TextProcessingPipeline. Unit tests: 20 new (12 trim_facts_to_budget + _kb_section in test_prompt_builder.py, 6 pipeline wiring in test_text_pipeline.py -- disabled/enabled/prompt-passthrough/embedding-failure/search-failure/no-repo-configured). Full unit suite 909 passed (889 prior + 20 new), ruff+format clean, mypy src/ clean (108 files). Routing hints for qa (A6): integration coverage needed for real-pgvector similarity ranking feeding _kb_section end-to-end, and for the double-fence sanitizer behavior against actual injection payloads (extend test_prompt_sanitizer.py per envelope''s A6 instruction, don''t duplicate). Also flagging for PM: the pre-existing ADR-0001 history/RAG trim implementation gap (constants/trim functions never shipped, found during G1) is still untracked as its own item -- recommend opening a backend-dev tech-debt item.'
  result:
    kind: commit
    ref: f208286
    verification: 'pytest tests/unit/test_prompt_builder.py tests/unit/test_text_pipeline.py: 71 passed; full tests/unit: 909 passed; ruff check+format clean; mypy src/ clean (108 source files); pre-commit hooks passed on commit'
- id: A6
  title: 'Phase 1 integration/acceptance tests: migration, repo supersession txn, pgvector, _kb_section fence, kb_enabled toggle'
  specialist: qa
  priority: P1
  status: done
  depends_on:
  - A2
  - A5
  estimated_effort: 3h
  confidence: 0.9
  consult_session_id: 4c6eee0a-71cb-46f4-97b1-63f56747aa25
  specialist_session_id: 30d99a76-6fd2-4e74-bf9c-1fe474345b24
  retry_count: 0
  last_update:
    ts: '2026-07-24T16:08:35Z'
    executor: qa
    note: 'Wrote 3 new real-Postgres+pgvector integration test files (34 tests: test_migration_014_chat_facts.py 12, test_knowledge_repository.py 17, test_kb_enabled_toggle.py 5) plus extended (not duplicated) tests/unit/test_prompt_sanitizer.py::test_injection_attack_pattern per the envelope instruction. Coverage: migration 014 DDL actually applied (table/columns/defaults/3 indexes incl ivfflat/self-FK/updated_at trigger, redesigned the trigger test after discovering Postgres now() is transaction-frozen inside db_conn); KnowledgeRepository supersession commit path, cross-predicate independence, atomicity-on-insert-failure, a real concurrent-writer race via asyncio.gather on the FOR UPDATE lock (exactly one active row survives regardless of winner), real pgvector search_by_similarity salience-DESC-then-similarity-DESC ordering with deterministic one-hot embeddings, exclusion/limit/reject_fact guards; kb_enabled real DB round-trip through ChatConfigService merge. FINDING surfaced via a passing documentary test (flagged for backend-dev, not fixed -- out of A6 mandate): kb_enabled is the only per-chat toggle column declared NOT NULL (migration 014) -- unlike every sibling boolean toggle (nullable) -- so once any chat_settings row exists, its always-present value silently shadows any future bot_config default_kb_enabled global override. KnowledgeRepository.upsert_fact needs pool.acquire() so tests use the real db_pool fixture with per-test chat_id namespacing instead of the standard rolled-back db_conn fixture (documented in file docstring). Full suite 998/998 green, ruff check+format clean, mypy src/ clean (108 files, no src/ changes). Committed f0b9425, only the 4 A6 files staged.'
  result:
    kind: commit
    ref: f0b9425
    verification: pytest (45 new/modified tests) + full tests/ 998 passed; ruff check+format clean; mypy src/ clean; pre-commit hooks passed on commit
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
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 21.5252
review_gate:
  why:
  - 'budget cap reached: consumed $17.9684 of $20.0'
  approve_action: /execute-plan <projects>/telegram-chat-companion.knowledge-base-research-2026-07-23-wt/docs/plans/knowledge-base-research-2026-07-23.execution.md --resume
  reject_action: /plan-fixes docs/plans/knowledge-base-research-2026-07-23.md --revise <projects>/telegram-chat-companion.knowledge-base-research-2026-07-23-wt/docs/plans/knowledge-base-research-2026-07-23.execution.md
safe_to_replay_from: null
clarifying_questions: []
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

## Decisions (Julia, 2026-07-24 — resolves all 6 clarifying questions)

1. **Scope: Phase 1 only.** G1–G2 + A1–A6 execute now; PH2/PH3/PH4 stay blocked roadmap epics, decomposed via a follow-up `/plan-fixes` after Phase 1 ships.
2. **Migration 014 confirmed.** ADR-0002's 013 lands independently.
3. **KB token budget: additive.** G1's ADR-0001 addendum carves a **separate KB slot of ~300 tokens** (total context budget 1200 → ~1500). RAG allocation (400) untouched.
4. **Extractor models (Phase 2): verified.** Registered cheap ids in `capabilities.py` are `gpt-5-nano` / `gemini-3-flash-preview` — PH2 task config must use these exact ids (NB: `-preview` suffix on the Gemini model).
5. **Copy: term = «организатор»; `/kb` visible to all chat members; bilingual ru+en** (`_L` dict pattern, consistent with existing admin i18n).
6. **Relevance defaults confirmed:** accessibility skip stands; A4 frontend-dev / A2 backend-dev split stands.

## Approval workflow

- **Approve in place:** answer the clarifying_questions (edit them out / annotate), then `envelope_approve <envelope> --by julia`, then `/execute-plan <envelope> --resume`.
- **Reject / re-plan:** `/plan-fixes docs/plans/knowledge-base-research-2026-07-23.md --revise <envelope>`.
- Approval will refuse while clarifying_questions remain unresolved.
