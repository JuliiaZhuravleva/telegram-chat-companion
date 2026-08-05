---
schema_version: 3
plan_id: sticker-status-ux-2026-06-09
source_artifact:
  path: docs/plans/sticker-status-ux-2026-06-09.md
  sha256: 9f5e2100be1dbba14e883df14ea4cc35d42183bdf4d8db5583e91a0ae048f1e0
  type: bug-report
created_at: '2026-06-09T16:27:13Z'
approved_at: '2026-06-09T16:39:23Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: done
  started_at: '2026-06-09T16:41:21Z'
  completed_at: '2026-06-09T17:25:00Z'
  current_batch: null
  task_list_id: sticker-status-ux-2026-06-09
items:
- id: A-1
  title: Unified _status_badge(sticker, lang, short) helper — short in keyboard, long in detail (one ✅/⏳/⚠️ vocabulary)
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1.5h
  confidence: 0.98
  consult_session_id: f0f824ab-3393-4201-976e-fa9fefbb5e36
  specialist_session_id: f5a412f5-6512-4f3f-8687-2cc960b48b8e
  retry_count: 0
  last_update:
    ts: '2026-06-09T16:48:26Z'
    executor: backend-dev
    note: 'Extracted _status_badge(sticker, lang, *, short: bool) -> str into keyboards/admin_sticker.py. Updated sticker_set_detail_keyboard (short=True replaces [FAILED]/ожидает анализа) and handle_sticker_detail (short=False replaces separate ⏳/⚠️ lines). Removed redundant analysis_failed check from detail view — _status_badge prioritizes failed state in the else branch. 874 unit tests pass, ruff+mypy clean. Regression tests added for [FAILED] and ожидает анализа labels no longer appearing. qa integration coverage for status derivation via get_stickers_in_set() is B-1 (already in plan).'
  result:
    kind: commit
    ref: f804fe8
    verification: pytest tests/unit/test_admin_sticker_handler.py -v — 58 passed; pytest tests/unit/ -q — 874 passed, 0 failures; ruff check src/ — all checks passed; mypy src/ — no issues found in 103 source files
- id: A-2
  title: 'Edit-in-place re-analyze lifecycle: ⏳→✅/⚠️ + ReanalyzeResult reason + double-tap guard + HTML-safety + unit tests'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A-1
  estimated_effort: 4h
  confidence: 0.97
  consult_session_id: f0f824ab-3393-4201-976e-fa9fefbb5e36
  specialist_session_id: a59f8498-5ff7-4c91-bbf9-207b92c24bde
  retry_count: 0
  last_update:
    ts: '2026-06-09T17:07:08Z'
    executor: backend-dev
    note: 'Implemented edit-in-place re-analyze lifecycle. Changes: (1) ReanalyzeResult(ok, reason, visual_description) in models.py replaces bare bool from reanalyze(); (2) StickerLearningResult.failure_reason field added with vision/content_filter/empty discrimination from AIProviderError; (3) handle_run_analysis() refactored: ⏳ before call, ✅/⚠️ after, html.escape() on all model output, TelegramBadRequest suppressed on both edits, ⏳ failure logs-and-continues; (4) sticker_reanalyze_retry_keyboard() added; (5) unit tests cover all 4 failure reasons, double-tap guard, HTML-safety, in-progress ordering. Integration coverage for repo status transitions via get_stickers_in_set() is B-1 (qa item, already in plan). Live browser smoke test (⏳→✅, force failure ⚠️+Retry, clear→⏳) is B-2 (qa item, depends on Docker rebuild).'
  result:
    kind: commit
    ref: 13c89b2
    verification: pytest tests/unit/test_admin_sticker_handler.py tests/unit/test_sticker_learning.py — 112 passed; pytest tests/unit/ -q — 902 passed, 0 failures; ruff check src/ — all checks passed; mypy src/ — no issues found in 103 source files
- id: B-1
  title: Integration tests for status derivation across repo transitions via get_stickers_in_set() (clear→not-analyzed, analyze→completed, failed→failed)
  specialist: qa
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1.5h
  confidence: 0.99
  consult_session_id: cf12a749-bc7b-4f78-975e-150c4afc8182
  specialist_session_id: ee2ef82d-11de-4212-b6a6-8585054e47c0
  retry_count: 0
  last_update:
    ts: '2026-06-09T17:10:57Z'
    executor: qa
    note: 'Added TestGetStickersInSet (8 tests) and TestClearAnalysisSetListView (2 tests) to tests/integration/test_sticker_repository.py. Covers all three status transitions via get_stickers_in_set(): not-analyzed (visual_description NULL + analysis_failed=False), analyzed (description present), failed (analysis_failed=True). Also covers: set isolation, pagination (limit/offset), clear→not-analyzed round-trip, and analyze→completed transition. TestClearAnalysisSetListView adds 2 tests extending the existing clear_analysis coverage to use get_stickers_in_set() as the verification query rather than get_by_file_unique_id().'
  result:
    kind: commit
    ref: 30ca804
    verification: pytest tests/integration/test_sticker_repository.py -v — 27 passed (10 new + 17 existing); pytest tests/unit/ -q — 902 passed; ruff check src/ — all checks passed
- id: B-2
  title: Live browser smoke QA via Playwright MCP (⏳→✅, force failure ⚠️+Retry, clear→⏳); session-dead precondition → NEEDS_CLARIFICATION not FAIL
  specialist: qa
  priority: P1
  status: done
  depends_on:
  - A-1
  - A-2
  estimated_effort: 2h
  confidence: 0.97
  consult_session_id: cf12a749-bc7b-4f78-975e-150c4afc8182
  specialist_session_id: a191be0b-c456-4880-b88d-94dd7d1474f1
  retry_count: 0
  last_update:
    ts: '2026-06-09T17:25:00Z'
    executor: qa
    note: 'Completed manually in interactive Claude Code session via Playwright MCP (headless qa specialist lacked browser_* tools — expected). Bot redeployed with A-1+A-2; web session live as <qa-test-account> (in admin_ids). VERIFIED in bot DM, set Кря/stickers_duck. Flow 1 (re-analyze): ⏳ Анализирую… with buttons hidden → ✅ Анализ обновлён + refreshed description + buttons restored = PASS. Flow 3 (clear): toast Анализ очищен; ⏳ not-analyzed badges render (long form Визуальный анализ не выполнен in detail, short form ожидает анализа in list) = PASS — note handle_clear shows the toast without auto-refreshing the detail message, a pre-existing behaviour not introduced by this plan. Flow 2 (failure ⚠️+Retry): not injected live, covered by A-2 unit tests; live injection available on request.'
  result:
    kind: none
    ref: null
    verification: null
- id: C-1
  title: 'ADR-0003: sticker analysis state is transient UI, not a persisted enum column (records the locked decision + rejected alternatives)'
  specialist: architect
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 30m
  confidence: 0.97
  consult_session_id: 0d6fe300-01dc-41dd-80b4-e291384bfd11
  specialist_session_id: 009da31d-717b-4fe1-b6de-4cc10eb321ae
  retry_count: 0
  last_update:
    ts: '2026-06-09T17:16:33Z'
    executor: architect
    note: 'Created ADR-0003 recording the locked decision: sticker analysis state is transient UI derived from visual_description/analysis_failed/analyzed_at — no DB migration, no enum column, no background task. Documents the in-place ⏳→✅/⚠️ edit pattern rationale, crash-safety, and single-operator scope constraint. Three rejected alternatives documented: persisted enum column (synchronization risk), background job queue (out-of-scope complexity), dual-write (no query benefit). Scope boundary section added to gate any future backlog items that would require superseding this decision.'
  result:
    kind: file
    ref: docs/decisions/ADR-0003-sticker-analysis-transient-ui-state.md
    verification: ADR-0003 created at docs/decisions/ADR-0003-sticker-analysis-transient-ui-state.md; records locked decision, 3 rejected alternatives, and explicit scope boundary for future backlog items
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 5.1458
review_gate:
  why: []
  approve_action: /execute-plan <projects>/telegram-chat-companion/docs/plans/sticker-status-ux-2026-06-09.execution.md --resume
  reject_action: /plan-fixes docs/plans/sticker-status-ux-2026-06-09.md --revise <projects>/telegram-chat-companion/docs/plans/sticker-status-ux-2026-06-09.execution.md
safe_to_replay_from: null
clarifying_questions: []
---






























# Plan — sticker-status-ux-2026-06-09

## Source

[`docs/plans/sticker-status-ux-2026-06-09.md`](docs/plans/sticker-status-ux-2026-06-09.md) (sha256 `9f5e2100be1d...`).

## Synthesis summary

Source is a tightly-specified bug-report (UX defect + test coverage) with locked design
decisions and a code map. Consulted 3 roster specialists (backend-dev, qa, architect) plus an
off-roster designer pass for copy strings. All converged on consolidating the source's 8 raw
work items (I-1..I-8) into **5 dispatch units**:

| Envelope item | Specialist  | Folds in source items | User stories |
|---------------|-------------|-----------------------|--------------|
| **A-1** | backend-dev | I-2                   | US-1, US-6, US-7 |
| **A-2** | backend-dev | I-1 + I-3 + I-4 + I-5 + I-6 | US-2, US-3, US-4, US-5, US-8 (unit) |
| **B-1** | qa          | I-7                   | US-8 (integration) |
| **B-2** | qa          | I-8                   | US-8 (live) |
| **C-1** | architect   | (new — architect-proposed) | — (decision record) |

**Why the merges** (specialist consensus):
- I-3 (`reanalyze()` `bool` → `ReanalyzeResult`) is tightly coupled to the I-1 handler refactor
  that consumes the failure reason — one natural commit unit. (backend-dev + architect)
- I-4 (double-tap) and I-5 (HTML-safety) are 1–2 line constraints inside the same
  `handle_run_analysis()` function, not standalone deliverables. Folded into A-2 acceptance
  criteria. (architect: dispatching them separately risks no-op specialist turns.)
- I-6 (unit tests) travels with backend-dev's implementation per `sessions.md`
  ("backend-dev writes unit tests for its own code"). It is A-2's PASS gate, not a separate item.

## Items

### A-1 — Unified status-badge helper (short/long forms)  `[backend-dev, P1]`
Introduce one `_status_badge(sticker, lang, *, short: bool) -> str` (✅ analyzed / ⏳ not analyzed
/ ⚠️ failed), derived from existing columns (`visual_description` NULL ⇒ not analyzed,
`analysis_failed`, `analyzed_at`). Use it in BOTH the set-detail keyboard
(`keyboards/admin_sticker.py:92-101`) and the sticker-detail view (`admin_sticker.py:358,371`).
**Correction (Julia, 2026-06-09 — supersedes the earlier note):** `sticker_set_detail_keyboard()`
ALREADY takes `lang` (`keyboards/admin_sticker.py:80`, plumbed from the handler) — so this is a
helper extraction + vocabulary unification, NOT an interface change. No `lang` plumbing needed.
**Form (Q3, Julia):** `short=True` for the char-limited keyboard button (`⏳ Не выполнен` /
`⏳ Not analyzed`); `short=False` keeps the fuller detail-view copy
(`⏳ Визуальный анализ не выполнен`). Order-independent; most isolated rollback boundary, hence
kept separate from A-2. → US-1, US-6, US-7.

### A-2 — Edit-in-place re-analyze lifecycle  `[backend-dev, P1, depends_on: A-1]`
Refactor `handle_run_analysis()` (`admin_sticker.py:498-541`) to edit the message in place:
`⏳ Анализирую…` + hide action buttons **before** the blocking vision call; then
`✅ Анализ обновлён` (+ description, buttons restored) or `⚠️ Ошибка анализа: <reason>` (+ Retry)
**after** it returns. Stop the separate-message + bare-toast pattern. Bundles:
- **(was I-3)** Change `reanalyze()` (`learning.py:292-337`) from `bool` to a structured
  `ReanalyzeResult(ok: bool, reason: Literal['download','vision','content_filter','empty'] | None)`.
  **Resolved decision (backend-dev + architect):** prescribe the `Literal` type — not bare
  `str | None` — to keep localization clean and unit tests precise.
- **(was I-4)** Double-tap safety: the in-progress edit removes the buttons; catch
  `TelegramBadRequest` "message is not modified" specifically on status edits. → US-5.
- **(was I-5)** HTML-safety: `html.escape()` every model-generated description before it enters an
  edited message (default `parse_mode=HTML`). → all result-message stories.
- **(was I-6)** Unit tests for the new handler paths + badge helper following
  `tests/unit/test_admin_sticker_handler.py` mock patterns (success, each failure mode, double-tap,
  clear-then-status); update `test_sticker_learning.py` for the new `reanalyze()` return type.
- **Resolved edge case (architect):** if the initial `⏳` edit fails (network), **log and continue**
  the analysis anyway — still emit the `✅`/`⚠️` result edit. Do not abort silently (admin would
  get no feedback). Add to acceptance criteria.
→ US-2, US-3, US-4, US-5, US-8 (unit).

### B-1 — Integration tests for status derivation  `[qa, P1]`
New `TestGetStickersInSet` (and extension of `TestClearAnalysis`) in
`tests/integration/test_sticker_repository.py`, on real Postgres/pgvector via testcontainers.
Cover status transitions as observed through `get_stickers_in_set()` (the query powering the
set-detail view): clear → not-analyzed; analyze → completed; failed-flag → failed.
**qa note:** `get_stickers_in_set()` is not yet integration-tested. Independent of A-1/A-2 — can
run in parallel. → US-8 (integration).

### B-2 — Live browser smoke QA  `[qa, P1, depends_on: A-1, A-2]`
Playwright MCP from the technical test account against the whitelisted test chat: open a set →
tap Запустить заново → observe ⏳→✅; force a failure → observe ⚠️ + Retry; clear → observe ⏳.
**Resolved precondition (qa + architect + brief):** confirm the test account's Telegram Web
session is live FIRST (flagged possibly stale after the 2026-04 privacy sweep, TD-006). If the
session is dead, emit **NEEDS_CLARIFICATION** (do not FAIL, do not silently skip). Hard-last:
requires a Docker rebuild after the code lands; verify the redeploy log before starting.
→ US-8 (live).

### C-1 — ADR-0003: analysis state is transient UI  `[architect, P2]`  *(see clarifying Q2)*
Record the locked decision (no DB migration / no `analysis_status` enum column / no background
task; status stays derived from `visual_description` + `analysis_failed` + `analyzed_at`) and the
rejected alternative as `docs/decisions/ADR-0003-sticker-analysis-transient-ui-state.md`.
Independent. Architect-proposed; not in the source acceptance criteria — drop-or-keep is Q2.

## Resolved decisions (no Julia action needed)

1. **`ReanalyzeResult.reason` type** = `Literal['download','vision','content_filter','empty']`
   (not stringly-typed). — backend-dev + architect.
2. **Canonical failure-reason copy (designer D-2)**, applied inline by backend-dev in A-2:
   - download error → `Ошибка загрузки` / `Download error`
   - vision API error → `Ошибка API` / `API error`
   - empty/truncated (`finish_reason=length`) → `Пустой ответ` / `Empty response`
   - `content_filter` (`PROHIBITED_CONTENT`) → `Контент заблокирован` / `Content blocked`
3. **B-2 session-dead path** = NEEDS_CLARIFICATION, not FAIL (brief + qa + architect).
4. **`⏳`-edit-failure** = log-and-continue, still emit result edit (architect hidden-failure catch).
5. **Decomposition (Q1, Julia 2026-06-09)** = ACCEPT the 8→5 plan; A-2 stays one commit (not split).
6. **C-1 ADR-0003 (Q2, Julia)** = KEEP (build the decision record).
7. **Status-badge form (Q3, Julia)** = SHORT in keyboard, LONG in detail view (helper `short: bool`).

## Execution DAG

```
[parallel]
  A-1 (backend-dev)  ──▶  A-2 (backend-dev)  ──┐
  B-1 (qa)                                     ├──▶  B-2 (qa, after deploy)
  C-1 (architect)                              ┘
```

## Open questions

**All 3 resolved by Julia (2026-06-09)** — see "Resolved decisions" #5–#7 above:
(1) accept the 8→5 decomposition; (2) keep C-1 (ADR-0003); (3) status-badge form =
short-in-keyboard / long-in-detail. No open questions remain.

## Specialist consult sessions (for `--revise` re-engagement)

- backend-dev (A-1, A-2): `f0f824ab-3393-4201-976e-fa9fefbb5e36`
- qa (B-1, B-2): `cf12a749-bc7b-4f78-975e-150c4afc8182`
- architect (C-1): `0d6fe300-01dc-41dd-80b4-e291384bfd11`
- designer (off-roster, copy only): `1e65f718-c582-4ab3-86b1-f85c4e408086`
