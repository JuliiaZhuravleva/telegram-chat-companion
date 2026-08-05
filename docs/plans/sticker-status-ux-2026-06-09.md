# Sticker Admin — Re-analysis Status Visibility & Test Coverage

> **Source type:** bug-report (UX defect + enhancement)
> **Date:** 2026-06-09
> **Branch:** `feature/sticker-admin-rework`
> **Baseline commit:** `acbaecc` — *feat(admin): sticker admin panel rework — clear/re-analyze + direct sets list*
> **Reporter:** Julia (bot admin / maintainer)

## Summary

The sticker admin panel's **re-analyze** action (`🔄 Запустить заново`) works functionally
but is **unintuitive**: the admin gets no feedback about what is happening *right now*. There
is no visible "working…" state, the success/failure result is delivered as a separate toast +
new message instead of updating the view in place, and status labels are inconsistent between
the set-detail list and the single-sticker detail view. The admin wants to always know the
re-analysis lifecycle state: **pending/working → finished successfully → failed**.

This brief asks to (1) build **user stories** for the sticker admin surface, and (2) **develop
and test all sticker admin work** against those stories.

## Locked design decisions (from planning Q&A, 2026-06-09)

1. **Status model = Transient inline status (synchronous).**
   Keep re-analysis synchronous (as today). Make status visible by **editing the message in
   place**: show `⏳ Анализирую…` and hide action buttons *before* the blocking vision call,
   then edit to `✅ Анализ обновлён` (+ new description, buttons restored) or
   `⚠️ Ошибка анализа: <reason>` (+ Retry button) *after* it returns.
   - **NO database migration.** **NO new `analysis_status` enum column.** **NO background task.**
   - The "pending" state the admin wants = the transient `⏳ Анализирую…` in-progress edit. It
     is a UI state, not a persisted one.
   - Status is still **derived from existing columns**: `visual_description` (NULL ⇒ not
     analyzed), `analysis_failed` (bool), `analyzed_at` (timestamp).

2. **Test depth = Automated + live browser QA.**
   Hard gate: unit (`tests/unit/`) + integration (`tests/integration/`, testcontainers +
   Postgres/pgvector). On top: live browser smoke test via Playwright MCP from the technical
   test account against the whitelisted test chat (per CLAUDE.md "Local deploy + QA loop").
   - **Precondition to verify before relying on browser QA:** the test account's Telegram Web
     session must be live (memory flags it as possibly stale after the 2026-04 privacy sweep).
     If the session is dead, flag it — do not silently skip live QA.

## Current behaviour (grounded in code)

- **Re-analyze handler** — `handle_run_analysis()` at `src/bot/handlers/admin_sticker.py:498-541`.
  Callback `adm_stk_reanalyze:{lang}:{file_unique_id}`. Calls `sticker_service.reanalyze()`
  **synchronously (blocks the callback)**; on return it `callback.answer()`s a toast and sends
  the result as a **new message**, not an in-place edit. No "working…" state exists.
- **Re-analyze service** — `reanalyze()` at `src/services/modules/sticker/learning.py:292-337`:
  download bytes → `sticker_repo.clear_analysis()` → `learn(..., force_reanalyze=True)` →
  returns `not result.analysis_failed`. Vision call + embedding are the slow part
  (`learn()` lines 161-219, 261-269).
- **Status labels are inconsistent:**
  - set-detail keyboard `src/bot/keyboards/admin_sticker.py:92-101` → `[FAILED]` /
    `⏳ ожидает анализа` (ru) / `⏳ awaits analysis` (en) / else truncated description.
  - sticker-detail view `src/bot/handlers/admin_sticker.py:358,371` →
    `⏳ Визуальный анализ не выполнен` / `⚠️ Анализ провалился`.
  - Two different vocabularies for the same three states; no shared helper.
- **Clear flow** — `handle_clear()` `admin_sticker.py:465-492` → `clear_analysis()`
  `src/database/repositories/stickers.py:303-320` (NULLs description/embedding/emotion/etc.,
  resets `analysis_failed=false`, `analyzed_at=NULL`).
- **i18n** — lang is embedded in callback_data: `{action}:{lang}:{params}`; `_get_lang()`
  `admin_sticker.py:41-42`; label dicts keyed `"ru"/"en"`.

### Known failure modes re-analysis must surface
- Sticker download error (Telegram fetch fails).
- Vision API error / empty result (`finish_reason=length`, truncated JSON).
- Gemini `PROHIBITED_CONTENT` → `merge_admin_description()` raises `ValueError("content_filter")`
  (per CLAUDE.md). Re-analysis must degrade gracefully, not 500 the handler.

### Relevant CLAUDE.md gotchas the implementation must honour
- **Default parse_mode=HTML** ⇒ `html.escape()` every model-generated description before it
  goes into an edited message, or set `parse_mode=None`.
- **edit_text "message is not modified"** ⇒ catch `TelegramBadRequest` specifically on the
  status edits (esp. double-tap / no-op refreshes).
- **aiogram handler matching** ⇒ keep chat-type/private guards in filters, not handler bodies.
- **callback_data prefixes** ⇒ keep trailing `:` so `adm_stk_reanalyze:` doesn't collide.

## User stories

Persona: **Bot Admin** operating the DM admin panel.

- **US-1 — Status at a glance in the set list.**
  As an admin, when I open a sticker set, I want each sticker's analysis state
  (✅ analyzed / ⏳ not analyzed / ⚠️ failed) shown with one consistent icon + label, so I can
  scan which stickers need attention.

- **US-2 — Live feedback while re-analysis runs.**
  As an admin, when I tap `🔄 Запустить заново`, I want the message to immediately show
  `⏳ Анализирую…` and hide the action buttons, so I know the bot is working and don't tap again.

- **US-3 — Clear success result.**
  As an admin, when re-analysis finishes successfully, I want the same message to update to
  `✅ Анализ обновлён` with the new description and the buttons restored, so I see it worked and
  can read the result without hunting for a separate message.

- **US-4 — Clear, actionable failure result.**
  As an admin, when re-analysis fails, I want the message to update to
  `⚠️ Ошибка анализа: <reason>` with a **Retry** button, so I understand what went wrong and
  can retry in one tap. Distinct reasons for download / vision / content-filter / empty result.

- **US-5 — Double-tap safe.**
  As an admin, if I tap `Запустить заново` twice, I don't want overlapping analyses or a broken
  view; the in-progress edit removes the button and a stale second tap is handled cleanly
  (no unhandled `TelegramBadRequest`).

- **US-6 — Clear-analysis reflects status everywhere.**
  As an admin, after I clear a sticker's analysis, the status shows `⏳ not analyzed`
  consistently in both the detail view and the set-list row immediately.

- **US-7 — i18n status parity.**
  As an admin using ru or en, all status labels and result messages appear in my selected
  language, matching the existing callback-embedded lang pattern.

- **US-8 — Regression-proof sticker admin (the "test all sticker work" story).**
  As the maintainer, I want unit + integration tests covering set-list, set-detail,
  sticker-detail, re-analyze (success / each failure mode / double-tap), and clear — plus a
  passing live browser smoke test — so the sticker admin surface is regression-proof.

## Concrete issues / work items (for decomposition)

- **I-1 (backend):** Refactor `handle_run_analysis()` to edit-in-place: `⏳` before the blocking
  call, `✅ + description` / `⚠️ + reason + Retry` after. Stop sending a separate result message
  + bare toast. (`admin_sticker.py:498-541`) → satisfies US-2, US-3, US-4.
- **I-2 (backend):** Introduce a single `_status_badge(sticker, lang) -> str` (or equivalent)
  helper and use it in BOTH the set-detail keyboard (`keyboards/admin_sticker.py:92-101`) and the
  sticker-detail view (`admin_sticker.py:358,371`). One vocabulary for ✅/⏳/⚠️. → US-1, US-6, US-7.
- **I-3 (backend):** Surface re-analysis failure reasons. Map service outcomes (download error,
  vision error/empty, `content_filter`) to short localized reasons; add a Retry button
  (`adm_stk_reanalyze:` reused). The service may need to return a reason, not just a bool —
  `reanalyze()` `learning.py:292-337` currently returns `bool`. → US-4.
- **I-4 (backend):** Double-tap / idempotency guard: the in-progress edit removes the action
  buttons; catch `TelegramBadRequest` "message is not modified" on status edits. → US-5.
- **I-5 (backend):** HTML-safety: `html.escape()` model-generated descriptions in every edited
  message (default parse_mode=HTML). → all result-message stories.
- **I-6 (backend, unit tests):** Unit tests for the new handler paths + status-badge helper,
  following `tests/unit/test_admin_sticker_handler.py` mock patterns (success, each failure
  mode, double-tap, clear-then-status). → US-8.
- **I-7 (qa, integration tests):** Integration tests for status derivation across repository
  transitions (clear → not-analyzed; analyze → completed; failed-flag → failed) on real
  Postgres/pgvector, extending `tests/integration/test_sticker_repository.py`. → US-8.
- **I-8 (qa, live browser QA):** Live smoke test via Playwright MCP from the test account:
  open a set → tap Запустить заново → observe ⏳→✅; force a failure → observe ⚠️ + Retry;
  clear → observe ⏳. Gate on confirming the test-account session is live first. → US-8.

## Acceptance criteria (global)

- Re-analyze shows an in-place `⏳ Анализирую…` state, then `✅`/`⚠️` in the same message.
- Failure path shows a localized reason + working Retry button; no unhandled exception on any
  of the known failure modes (download / vision / content_filter / empty).
- One status vocabulary (✅/⏳/⚠️) shared by set-list and detail views, in ru and en.
- Clear updates status consistently in both views.
- `pytest tests/ -v` green; `ruff check src/ && mypy src/` clean.
- Live browser QA performed (or explicitly flagged blocked if the test session is dead).
- **No DB migration, no new status column, no background task** (per locked decision 1).

## Scope boundaries

- **IN:** per-sticker re-analysis status visibility, unified status labels, actionable failure
  surface + retry, double-tap safety, HTML-safety, automated tests, live browser QA.
- **OUT (future backlog, do NOT build now):**
  - Background/async re-analysis with a persisted `analysis_status` enum column + migration.
  - Set-level **bulk** re-analyze with X/N progress.
  - Push notification to the admin on completion (vs. in-place edit).

## References (code map, file:line)

- Handlers: `src/bot/handlers/admin_sticker.py` — `handle_run_analysis` 498-541,
  `handle_clear_ask` 427-462, `handle_clear` 465-492, `handle_sticker_detail` 325-421,
  `handle_sticker_sets` 166-202, `handle_sticker_set_view` 234-263, `_get_lang` 41-42.
- Keyboards: `src/bot/keyboards/admin_sticker.py` — set-detail status labels 92-101,
  sticker-detail buttons 148-182, clear-confirm 185-204.
- Service: `src/services/modules/sticker/learning.py` — `learn` 57-288, `reanalyze` 292-337.
- Repository: `src/database/repositories/stickers.py` — `clear_analysis` 303-320,
  `get_stickers_in_set` 354-375, `get_all_sets_with_stats` 324-347.
- Status columns: `alembic/versions/005_sticker_knowledge.py:34-59`
  (`visual_description`, `analyzed_at`, `analysis_failed`).
- Tests today: `tests/unit/test_admin_sticker_handler.py`, `tests/unit/test_admin_keyboards.py`,
  `tests/unit/test_sticker_learning.py`, `tests/unit/test_sticker_repository.py`,
  `tests/integration/test_sticker_repository.py`.
