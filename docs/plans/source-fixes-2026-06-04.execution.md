---
schema_version: 3
plan_id: source-fixes-2026-06-04
source_artifact:
  path: internal/plan-source-fixes.md
  sha256: 692ef95008c213c66db3c34a4b94884ac0e8e7ef9302291287a2e2d12191b9cd
  type: session-analysis
created_at: '2026-06-04T18:20:49Z'
approved_at: '2026-06-04T18:56:50Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: done
  started_at: '2026-06-04T18:57:07Z'
  completed_at: '2026-06-04T21:41:36Z'
  current_batch: null
  task_list_id: source-fixes-2026-06-04
items:
- id: A-1
  title: 'TD-002: Stand up testcontainers harness + first integration tests for core repositories'
  specialist: qa
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1d
  confidence: null
  specialist_session_id: 8b50ffd7-d206-4948-83e0-e10392e2d19a
  last_update:
    ts: null
    executor: human-verified
    note: Human-verified done; migration-006 idempotent ALTER kept per Julia.
  result:
    kind: commit
    ref: f04b8ce (+74d9be6)
    verification: 55 integration tests pass via real testcontainers (pgvector/pgvector:pg16, session-scoped); verified manually by running `pytest tests/integration/`. Evaluator skipped (false FAILED — specialist did not emit verdict JSON).
- id: A-2
  title: 'TD-003: DM /summary gets a visible reply via a filter-routed private-chat handler'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 2h
  confidence: null
  specialist_session_id: null
  last_update:
    ts: null
    executor: human-verified
    note: Specialist work completed just before pause-kill; verified + committed.
  result:
    kind: commit
    ref: cd2d1f4
    verification: 751 unit tests pass incl. 4 new DM-/summary tests; ruff + mypy clean. Human-verified.
- id: A-3
  title: 'TD-004: Confirm-before-delete for admin rule deletion (mirror whitelist remove-confirm)'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 3h
  confidence: null
  specialist_session_id: 4ea13121-e110-47e0-8f8e-699a2cba4b33
  last_update:
    ts: null
    executor: human-verified
    note: False FAILED (no verdict JSON) — work verified real & committed.
  result:
    kind: commit
    ref: 4fd36e4
    verification: Confirm-before-delete for rule deletion; 810 unit pass (255-line rules test); ruff+mypy clean. Human-verified.
- id: B-1
  title: 'TASK-6: Persist AI usage cost per provider call (exactly one row per call, no double-count)'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1d
  confidence: null
  specialist_session_id: 7ee05eb5-61e7-4172-8ef9-abc94e3fa900
  last_update:
    ts: null
    executor: human-verified
    note: False FAILED (no verdict JSON) — work verified real & committed.
  result:
    kind: commit
    ref: d81250e
    verification: Cost persisted per provider call; migration 012 + router/sticker/summary logging; 810 unit + 55 integration pass. Human-verified.
- id: B-2
  title: 'TASK-7: /costs command - todays USD total + per-model breakdown'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - B-1
  estimated_effort: 2h
  confidence: null
  specialist_session_id: 74088219-2a51-44f1-a014-366e5808cb49
  last_update:
    ts: null
    executor: human-verified
    note: False FAILED — work verified real & committed.
  result:
    kind: commit
    ref: 79238b1
    verification: /costs command (today's USD total + per-model breakdown); 839 unit pass incl. 123-line costs-handler test; ruff+mypy clean. Human-verified.
- id: B-3
  title: 'TASK-8: Configurable daily spend limit with overrun warning'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - B-1
  estimated_effort: 4h
  confidence: null
  specialist_session_id: 1915d7d4-bf71-4db2-ad75-18a8b1649418
  last_update:
    ts: null
    executor: human-verified
    note: False FAILED — work verified real & committed.
  result:
    kind: commit
    ref: 0d8fc17
    verification: Configurable daily spend limit + overrun warning (new src/services/costs/spend_limit.py); 839 unit pass incl. 236-line test. Human-verified.
- id: C-1
  title: Cost-optimization analysis + recommendations doc (relates to Section B)
  specialist: architect
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 4h
  confidence: null
  specialist_session_id: 60593792-77eb-4c4b-b251-8d5f3125cd52
  last_update:
    ts: null
    executor: human-verified
    note: False FAILED (no verdict JSON) — work verified real & committed.
  result:
    kind: file
    ref: a7c7405
    verification: Cost-optimization analysis doc (grounded in real call sites). Design/analysis deliverable, not code. Human-reviewed.
- id: C-2
  title: Design layered spend-limit model (per-chat / bot-global / per-operation) - superset of TASK-8
  specialist: architect
  priority: P2
  status: done
  depends_on:
  - B-3
  estimated_effort: 4h
  confidence: null
  specialist_session_id: 840d1b3b-a414-4a6d-b706-ae387f129e72
  last_update:
    ts: null
    executor: human-verified
    note: False FAILED — ADR verified real & committed.
  result:
    kind: file
    ref: c3d9b84
    verification: ADR-0002 layered spend-limit design (grounded in B-3's SpendLimitService + C-1 analysis). Design deliverable; implementation is a follow-up backend-dev task. Human-reviewed.
- id: C-3
  title: Audit RAG retrieved-context token budget on long history; add bounds/trimming if over
  specialist: architect
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 3h
  confidence: null
  specialist_session_id: a5fbc587-c797-4a27-9a07-72b4a14a82fb
  last_update:
    ts: null
    executor: human-verified
    note: False FAILED (no verdict JSON) — work verified real & committed.
  result:
    kind: file
    ref: a7c7405
    verification: 'RAG context-budget audit + ADR-0001 (design spec). NOTE: trimming implementation is a follow-up backend-dev task, NOT done. Human-reviewed.'
- id: C-4
  title: 'Improve animated/video sticker analysis: (a) 6-frame sampling vs (b) motion detection'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 1d
  confidence: null
  specialist_session_id: 6d817d69-3574-481a-a9f5-e38628881cd5
  last_update:
    ts: null
    executor: human-verified
    note: False FAILED (no verdict JSON) — work verified real & committed.
  result:
    kind: commit
    ref: 92f8bce
    verification: Frame-diff vectorised via PIL ImageChops in motion.py + tests; suite green. Human-verified.
- id: C-5
  title: Verify + enhance the existing relevancy pre-check gate (src/services/relevancy/) before generation
  specialist: backend-dev
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 4h
  confidence: null
  specialist_session_id: 23040c00-7b9a-4b3d-bfe3-bc0e843b574e
  last_update:
    ts: null
    executor: human-verified
    note: False FAILED (no verdict JSON) — work verified real & committed.
  result:
    kind: commit
    ref: 06f7bcd
    verification: Relevancy gate enhanced (history order, provider passthrough, question shortcut) + tests; suite green. Human-verified.
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 0.0
review_gate:
  why:
  - 'finalize: 1 of 11 items failed; status partial (not done)'
  approve_action: /execute-plan docs/plans/source-fixes-2026-06-04.execution.md --resume
  reject_action: /plan-fixes internal/plan-source-fixes.md --revise docs/plans/source-fixes-2026-06-04.execution.md
safe_to_replay_from: null
clarifying_questions:
- 'Q1 (ROSTER GAP): Items A-2, A-3, B-1, B-2, B-3, C-4, C-5 require application-source edits, but the effective specialist roster only ships ''architect'' (docs/ADRs only) and ''qa'' (tests only). These items are provisionally assigned specialist ''backend-dev'', which has NO agent file. Create a project overlay .claude/agents/specialist-backend-dev.md before /execute-plan, or reassign these items?'
- 'Q2 (C-4 approach): Animated-sticker analysis has two mutually-exclusive options - (a) cheap 6-frame sampling vs (b) deeper motion detection. Estimate currently assumes (a). Which approach should be scoped?'
- 'Q3 (C-2 vs B-3): C-2 (layered spend-limit model) is a superset of B-3 (TASK-8 simple daily limit). I sequenced C-2 after B-3 via depends_on. Confirm: ship B-3 first then generalize in C-2, or collapse B-3 into C-2 to avoid rework?'
- 'Q4 (priority of A-3): All Section-A ''Critical'' items are set P1 and no item is P0 (no production-down blocker). A-3 (rule delete, destructive/no-undo) is the strongest P0 candidate. Bump A-3 to P0?'
- 'Q5 (CONSULT SKIPPED): Live specialist consultation (architect + qa) was skipped - the harness Bash allowlist denied the ''claude -p'' invocation. All effort estimates, dependency edges, and scope decisions below are PM-synthesized defaults, NOT specialist-validated. Defaults assumed for architect and qa.'
- 'Q6 (C-1/C-3/C-5 are inbox ideas): C-1 (analysis doc), C-3 (RAG audit), C-5 (relevancy enhancement) are raw inbox ideas scoped as investigation-first work. If any is out of milestone scope, drop before approval.'
---















































# Plan — source-fixes-2026-06-04

## Source

[`internal/plan-source-fixes.md`](internal/plan-source-fixes.md) (sha256 `692ef95008c2...`).

> **Draft status:** awaiting Julia's review. 11 items synthesized from the source's
> Section A (critical tech debt), Section B (Costs / US-1), and Section C (inbox ideas).
> Live specialist consultation was **skipped** (harness denied `claude -p`) — estimates and
> dependency edges are PM-synthesized, not specialist-validated. See `clarifying_questions[]`.

## Priority & dependency overview

| Item | Pri | Specialist | Depends on | Effort | Source |
|------|-----|-----------|-----------|--------|--------|
| A-1  | P1  | qa         | —     | 1d | TD-002 |
| A-2  | P1  | backend-dev| —     | 2h | TD-003 |
| A-3  | P1  | backend-dev| —     | 3h | TD-004 |
| B-1  | P1  | backend-dev| —     | 1d | TASK-6 |
| B-2  | P2  | backend-dev| B-1   | 2h | TASK-7 |
| B-3  | P2  | backend-dev| B-1   | 4h | TASK-8 |
| C-1  | P2  | architect  | —     | 4h | inbox  |
| C-2  | P2  | architect  | B-3   | 4h | inbox  |
| C-3  | P2  | architect  | —     | 3h | inbox  |
| C-4  | P2  | backend-dev| —     | 1d | inbox  |
| C-5  | P2  | backend-dev| —     | 4h | inbox  |

Dispatch-eligible immediately (no deps): A-1, A-2, A-3, B-1, C-1, C-3, C-4, C-5.
Gated: B-2, B-3 (← B-1); C-2 (← B-3).

## Items

### Section A — Critical Tech Debt

#### A-1 (TD-002) — Integration test suite is empty → qa
`tests/integration/` holds only a placeholder `conftest.py`. Stand up the testcontainers
harness against real Postgres+pgvector and add round-trip repository coverage (at minimum
`sticker_knowledge` upsert/increment_usage/clear_analysis, `chat_settings` merge, admin repo).
**Acceptance:** testcontainers spins up pgvector; `pytest tests/integration/` green in CI; core
repos have real-SQL round-trip coverage (not mocks).

#### A-2 (TD-003) — `/summary` in DM silently ignored → backend-dev
`commands.py:118` filters `/summary` on group/supergroup, so DM `/summary` is consumed by no
handler and the user sees nothing. Add a filter-routed private-chat handler that emits a visible
reply. **Must** keep chat-type guards in filter decorators, not handler bodies (matched handler
consumes the update). Mind router include order in `handlers/__init__.py`.
**Acceptance:** DM `/summary` produces a visible bot reply; group behavior unchanged; unit test
covers the DM path.

#### A-3 (TD-004) — Rule delete has no confirmation → backend-dev
The rule `🗑` delete executes in one click, destructive, no undo. Mirror the whitelist
remove-confirm added in 02d63e4: distinct stateless callback prefix (`adm_*_ask:` style, trailing
`:`), Yes/Cancel screen. **Acceptance:** rule-delete shows confirm; Cancel returns unchanged;
Confirm deletes; unit tests cover both branches. (Strongest P0 candidate — see Q4.)

### Section B — Costs (User Story US-1)

> Reconcile with the existing admin costs *view* (TASK-2, done) and the `router.log_usage()` path
> (ADR: `generate_text()` does not auto-log costs). Avoid double-counting.

#### B-1 (TASK-6) — Persist AI usage cost per request → backend-dev
Save prompt/completion tokens + computed price to DB after each provider call. Respect
`log_usage()` semantics: pipeline logs with full context; `generate_text()` callers (summary,
sticker merge) log explicitly. **Acceptance:** every provider call → exactly one usage row with
cost. Foundational dependency for B-2/B-3.

#### B-2 (TASK-7) — `/costs` command → backend-dev (← B-1)
Aggregate today's cost from DB → message with total + per-model breakdown.
**Acceptance:** `/costs` shows today's USD total and per-model breakdown.

#### B-3 (TASK-8) — Configurable daily spend limit + warning → backend-dev (← B-1)
Configurable daily limit (chat config / settings); on each AI request, warn when today's total >
limit. **Acceptance:** bot emits a warning when the configurable limit is exceeded. See Q3 re C-2.

### Section C — Inbox Ideas (refined during planning)

#### C-1 — Cost-optimization analysis + plan → architect
Analysis/architecture task → recommendations doc (not necessarily code). Relates to Section B.
Architect executes in docs/ only. Drop if out of milestone scope (Q6).

#### C-2 — Layered spend-limit model → architect (← B-3)
Design per-chat / bot-global / per-operation caps. Superset of B-3 — sequenced after it to avoid
conflict. See Q3: ship B-3 first then generalize, or collapse B-3 into C-2.

#### C-3 — RAG context-size check on long history → architect
Investigate whether too much retrieved context is passed to the model on long chat history; add
bounds/trimming if over budget. Areas: `rag/memory.py`, `text/prompt_builder.py`, `text/pipeline.py`.
Investigation-first; drop if out of scope (Q6).

#### C-4 — Improve animated-sticker analysis → backend-dev
Two mutually-exclusive options: (a) sample 6 frames instead of 4; (b) detect motion (libraries /
overlay frames). **Approach undecided — Q2.** Estimate assumes (a). Areas:
`sticker/motion.py`, `renderer.py`, `learning.py`.

#### C-5 — Relevancy pre-check gate before generation → backend-dev
A relevancy subsystem already exists (`src/services/relevancy/`: gate.py, llm_judge.py,
engagement.py, fast_rules.py). Scope this as **enhancement/verification** of the existing gate
(cheap relevancy score → threshold before full generation), NOT greenfield. Drop if out of scope (Q6).

## Open questions for Julia

See `clarifying_questions[]` in the frontmatter (Q1–Q6). The two most consequential:
- **Q1 (roster gap):** 7 items need a code-writing `backend-dev` specialist that has no agent
  file — only `architect` (docs) and `qa` (tests) exist. Create the overlay or reassign before execute.
- **Q5 (consult skipped):** estimates/edges are PM defaults, not specialist-validated.

## Approval workflow

Approve in place (edit envelope to answer Q1–Q6, then re-invoke `/execute-plan <path>`), or:
```
envelope_approve docs/plans/source-fixes-2026-06-04.execution.md --by julia
```
Reject and re-plan: `/plan-fixes internal/plan-source-fixes.md --revise docs/plans/source-fixes-2026-06-04.execution.md`.
(Note: `envelope.py approve` refuses while clarifying_questions / pending feedback remain.)
