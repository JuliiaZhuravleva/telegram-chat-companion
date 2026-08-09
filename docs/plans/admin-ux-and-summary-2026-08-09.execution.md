---
schema_version: 3
plan_id: admin-ux-and-summary-2026-08-09
source_artifact:
  path: docs/plans/admin-ux-and-summary-2026-08-09.md
  sha256: 5faac3c79c424ad7a4b576311bc24ab714dda7694bd114196ff991a248181e52
  type: feature-prd
created_at: '2026-08-09T11:11:01Z'
approved_at: '2026-08-09T13:41:33Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: partial
  started_at: null
  completed_at: null
  current_batch: null
  task_list_id: admin-ux-and-summary-2026-08-09
items:
- id: A-1
  title: 'Показывать оценку откровенности стикера во всех карточках DM админу (строка: оценка · порог чата · пройдёт/не пройдёт)'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: ~2ч, средний риск
  confidence: 0.9
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: 5f73bcf2-8095-41d7-83ce-c9dbbf7ca3d6
  retry_count: 0
  last_update:
    ts: '2026-08-09T14:30:20Z'
    executor: backend-dev
    note: 'A-1 was already fully implemented, unit-tested, committed (f5d376e) and independently evaluator-verified PASS in an earlier session on this envelope (see .verdicts/A-1-1.json + A-1-evaluator-1.json, ts ~13:57-13:59Z), but the envelope''s item status/result was never updated to reflect it -- likely because that run''s overall session was later interrupted by A-4 hitting the per-item budget cap before the envelope patch step ran. This dispatch re-verified rather than re-implemented: confirmed f5d376e is on HEAD and contains format_explicitness_line() (tolerance.py) wired into every DM sticker card call site (_build_detail_text/detail view/clear re-render/DM catalog check, handle_run_analysis, notify_admins with real per-chat tolerance_level), matching the item''s required line format ''оценка · порог чата · пройдёт/не пройдёт'' and 3-state handling (scored/unscored=''не оценён''/un-analyzed=line omitted). Re-ran the 4 relevant test files in the CURRENT working tree (which also carries uncommitted A-4 WIP layered on the same files) -- 242 passed; ruff clean on admin_sticker.py/media.py/tolerance.py/learning.py; mypy clean on tolerance.py/learning.py. No new commit needed -- nothing left to do for A-1''s own scope. A-2''s ADR-0009 already ratified the ''default vs real chat tolerance_level for catalog-wide views'' design question the original specialist flagged. No code changes made this session; did not touch the in-progress A-4 WIP in the working tree.'
  result:
    kind: commit
    ref: f5d376e
    verification: 'pytest tests/unit/test_sticker_tolerance.py tests/unit/test_admin_sticker_handler.py tests/unit/test_sticker_learning.py tests/unit/test_media_handler.py -q: 242 passed (current tree, incl. later A-3 tests); ruff check clean; mypy clean on tolerance.py/learning.py'
- id: A-2
  title: 'ADR: приоритет ручной оценки стикера над повторным анализом + закрепление терминов «оценка откровенности» (стикер) и «уровень приличия» (чат) — дополнение к ADR-0008'
  specialist: architect
  priority: P1
  status: done
  depends_on: []
  estimated_effort: ~1.5ч, средний риск
  confidence: 0.9
  consult_session_id: da7e0b66-7a36-4adb-af45-63ddac2d67d0
  specialist_session_id: 9a086dbc-4f91-49ec-bb41-cd9acd6edb18
  retry_count: 0
  last_update:
    ts: '2026-08-09T14:33:18Z'
    executor: architect
    note: 'A-2 (ADR-0009: manual sticker score priority + terminology addendum to ADR-0008) was already fully written, committed (c797c1c), and independently specialist+evaluator PASS-verified in an earlier session on this envelope (see .verdicts/A-2-1.json + A-2-evaluator-1.json, ts ~14:06-14:07Z) -- but the envelope''s item status/result was never patched to reflect it, same staleness pattern as A-1 (interrupted run before the envelope-patch step). This dispatch re-verified rather than re-authored: confirmed c797c1c is on HEAD (git cat-file -e), contains only docs/decisions/ADR-0009-manual-explicitness-override.md (382 lines, no application source touched -- architect scope restriction honored), and matches the working tree byte-for-byte (git diff HEAD -- <path> empty). Spot-checked that A-3''s since-landed implementation (commit 408b2b5) actually followed the ADR: migration 026_sticker_explicitness_manual_flag.py exists (Decision 3), stickers.py contains the prescribed CASE-on-existing-flag upsert clause and both set_manual_explicitness_score()/reset_explicitness_to_auto() methods (Decisions 4-5), explicitness_is_manual is threaded into _VISION_DERIVED_COLUMNS (Decision 6). No code changes made this session; did not touch the in-progress A-4 WIP present in the working tree (src/bot/handlers/admin_sticker.py, keyboards/admin_sticker.py, states/admin.py, learning.py, tolerance.py + tests, all uncommitted, out of architect scope regardless).'
  result:
    kind: commit
    ref: c797c1c
    verification: docs/decisions/ADR-0009-manual-explicitness-override.md (382 lines) committed at c797c1c, verified present on HEAD and identical to working tree; downstream A-3 implementation (408b2b5) cross-checked against Decisions 3-6 and matches.
- id: A-3
  title: 'Данные ручной оценки: миграция-маркер «выставлено вручную» + приоритет в апсерте + метод записи'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A-2
  estimated_effort: ~2ч, средне-высокий риск
  confidence: 0.95
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: 26ee579d-e49a-40c5-bf34-09252d3918f7
  retry_count: 0
  last_update:
    ts: '2026-08-09T14:36:18Z'
    executor: backend-dev
    note: 'A-3 was already fully implemented and committed (408b2b5) in an earlier session on this envelope -- the envelope''s item status/result was never patched to reflect it (same staleness pattern already documented on A-1/A-2 in this envelope''s history). This dispatch re-verified rather than re-implemented: read ADR-0009 Decisions 3-6 + its ''Implementation notes for A-3'' section, then cross-checked each against the committed code. Migration 026_sticker_explicitness_manual_flag.py adds explicitness_is_manual BOOLEAN NOT NULL DEFAULT false (Decision 3, matches exactly). stickers.py''s save_sticker() upsert uses the prescribed CASE-on-existing-row''s-own-flag for explicitness_score, with explicitness_is_manual itself correctly absent from the SET clause (Decision 4). Two new methods set_manual_explicitness_score()/reset_explicitness_to_auto() match Decision 5''s NULL-both-fields reset semantics exactly. explicitness_is_manual is in _VISION_DERIVED_COLUMNS and threaded through learning.py''s duplicate-copy path + StickerLearningResult (Decision 6). clear_analysis() confirmed untouched per implementation note 6. Ran tests/unit/test_sticker_repository.py + test_sticker_learning.py: 98 passed (covers the CASE priority rule, the two new methods, and duplicate-copy flag propagation via mocked-pool SQL assertions -- real-Postgres integration coverage is Q-1''s job per the ADR, not re-added here). ruff + mypy clean on stickers.py, models.py, and the migration file. No code changes made this session. Did NOT touch the in-progress A-4 WIP present uncommitted in the same working tree (admin_sticker.py handler/keyboards, states/admin.py, learning.py, tolerance.py + their tests) -- that belongs to a separate item (A-4, its own specialist_session_id, resuming after a budget-cap interruption) and is out of A-3''s scope regardless.'
  result:
    kind: commit
    ref: 408b2b5
    verification: 'pytest tests/unit/test_sticker_repository.py tests/unit/test_sticker_learning.py -q: 98 passed; ruff check clean on stickers.py/models.py/026_sticker_explicitness_manual_flag.py; mypy clean on stickers.py/models.py'
- id: A-4
  title: 'Ручной ввод/сброс оценки откровенности стикера: кнопки-пресеты + ручной ввод числа 0.0–1.0, сброс к авто, бейдж «вручную»'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A-3
  - A-1
  estimated_effort: ~2.5ч, средний риск
  confidence: 0.85
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: 54f2469b-c766-4185-becc-485ab86fccda
  retry_count: 0
  last_update:
    ts: '2026-08-09T14:38:03Z'
    executor: backend-dev
    note: 'Implemented ADR-0009''s Implementation notes for A-4 exactly, on top of A-3''s data layer (set_manual_explicitness_score/reset_explicitness_to_auto already existed). tolerance.py: format_explicitness_line() gained is_manual kwarg -> ''(вручную)''/''(manual)'' badge, only rendered alongside an actual score (never on the unscored branch). states/admin.py: new AdminStates.awaiting_sticker_score (fresh state per Decision 7''s cosmetic-alternative escape hatch, since awaiting_sticker_edit''s docstring already means ''new description'' and awaiting_setting_value is now owned by admin_chat_panel.py''s per-chat tolerance FSM). keyboards/admin_sticker.py: sticker_detail_keyboard() gained a 5-value preset row (0.00/0.25/0.50/0.75/1.00, index-encoded in callback_data to stay short), a free-text-entry button, and a reset-to-auto button gated on explicitness_is_manual (only shown when there is a manual override to revert); new sticker_explicitness_cancel_keyboard() mirrors admin_chat_panel.tolerance_cancel_keyboard''s shape. admin_sticker.py: new handlers adm_stk_expset:/adm_stk_expedit:/adm_stk_expcancel:/adm_stk_expreset:, mirroring admin_chat_panel.py''s tolerance FSM idiom (reject-not-clamp validation, cancel converts the prompt bubble into a fresh detail card via a new _render_and_show_detail() helper); is_manual threaded through every existing DM-card call site (_build_detail_text, handle_run_analysis''s post-reanalyze text/keyboard incl. the sticky-manual-survives-reanalyze case, handle_admin_sticker_check, handle_admin_sticker_dm_analyze incl. the duplicate-copy-inherits-manual edge case per ADR Decision 6) and learning.py''s notify_admins(). Unit tests added: tolerance.py badge table (6 cases incl. the unscored-never-shows-badge invariant), keyboard row presence/gating + cancel-keyboard shape, all 4 new handlers (preset set/invalid-index/not-authorized, edit-prompt sets-state/not-authorized, input valid/comma-decimal/out-of-range/non-numeric/missing-fsm-data/not-authorized, cancel clears-state+rerenders, reset clears-and-hides-button/not-authorized/missing-id), _build_detail_text manual-badge threading, notify_admins manual-badge threading (incl. duplicate-inherits-badge-on-first-notification scenario). Full suite: 1780 passed, 5 skipped, 1 pre-existing unrelated failure (test_admin_sticker_dm_router_order.py, a MagicMock/await issue in _resolve_default_tolerance_level pre-dating this item — re-verified via git stash to HEAD before my changes, same failure, matches A-3''s own handoff note). ruff check + ruff format + mypy clean on all touched src files. No integration test added — Q-1''s existing scope (real-Postgres upsert CASE etc.) doesn''t cover this UI layer; qa''s manual smoke checklist (Q-2) already lists this line item across DM surfaces and should exercise the new buttons/FSM live.'
  result:
    kind: commit
    ref: f821a4d
    verification: 'pytest tests/unit/test_admin_sticker_handler.py tests/unit/test_sticker_tolerance.py tests/unit/test_sticker_learning.py tests/unit/test_sticker_repository.py tests/unit/test_admin_chat_panel_handler.py -q: 277 passed; full tests/unit/: 1780 passed, 5 skipped, 1 pre-existing unrelated failure (verified pre-existing on HEAD via git stash); ruff check + ruff format + mypy clean on all touched src files'
  budget_checkpoint:
    count: 1
    spent_usd: 4.8437
    session_id: 54f2469b-c766-4185-becc-485ab86fccda
    source: budget_death
    state: resolved
    ts: '2026-08-09T14:27:00Z'
- id: B-1
  title: Схема навигации сгруппированной панели настроек (экран-на-группу, возврат, «где я») — дополнение к ADR-0006
  specialist: architect
  priority: P1
  status: done
  depends_on: []
  estimated_effort: ~2ч, средний риск
  confidence: 0.9
  consult_session_id: da7e0b66-7a36-4adb-af45-63ddac2d67d0
  specialist_session_id: 886a1482-3805-422f-8d1e-12e41f81256a
  retry_count: 0
  last_update:
    ts: '2026-08-09T14:45:44Z'
    executor: architect
    note: 'ADR-0010 (docs/decisions/ADR-0010-chat-panel-grouped-navigation.md): grouped chat-panel navigation schema, addendum to ADR-0006. Two-tier model — root screen (7 rows: 4 group buttons BEHAVIOR/MODULES/STICKERS/RULES + unchanged KB/Reactions link rows, Decision 3 keeps ADR-0006 Decision 2 verbatim + back) and one group screen per field-owning group (Decision 1). New callback prefix adm_pnl_grp:{lang}:{chat_id}:{group} (Decision 2, 38-byte budget checked). Group screen = breadcrumb ''{panel_title} › {group_label}'' (the literal ''где я'' ask, Decision 4) + moved (not rewritten) per-field rows + single back row to adm_pnl_menu:. Decision 5: mutation re-render target (toggle, tolerance save/cancel) is derived from the field''s own FieldGroup, zero new callback_data/FSM params -- gives ''predictable return'' (B-2''s title) for free. Decision 6: render_chat_panel keeps its existing name/signature for root (so D-1''s already-planned adm_pnl_menu: deep link is unaffected) + new render_chat_panel_group. Decision 7 re-checks Telegram limits (bytes/rows/safe_edit_text double-tap handling, already-existing helper). Decision 8 flags a pre-existing, explicitly out-of-scope non-goal: KB/Reactions'' own back button returns to their module''s picker, not to this panel -- documented for Q-2''s checklist so it isn''t filed as a new bug. Implementation notes sections for B-2 (structure) and B-3 (root-screen status copy, left undetermined by design per the plan''s own structure/copy split, mirroring ADR-0009 Decision 7''s precedent for A-4 badge copy) and Q-2 (manual checklist coverage). Verified against current code: chat_panel_keyboard()/render_chat_panel (admin_chat_panel.py handlers+keyboards) and FieldGroup/fields_by_group() (settings_fields.py) read in full; field/group counts (25 fields, 6 groups, largest group MODULES=8) confirmed by running fields_by_group() directly, not assumed from docstrings. No application source touched (architect scope restriction honored) -- committed as 8c471c4, docs/ only.'
  result:
    kind: commit
    ref: 8c471c4
    verification: docs/decisions/ADR-0010-chat-panel-grouped-navigation.md (354 lines) committed at 8c471c4; git status confirms only that file + the pre-existing envelope modification staged, no other in-flight worktree WIP swept in; git show --stat 8c471c4 confirms single-file diff
- id: B-2
  title: Реализовать сгруппированную навигацию панели настроек (корневой список разделов + под-экраны, предсказуемый возврат, лимиты Telegram)
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - B-1
  estimated_effort: ~3–5ч, высокий риск (самый крупный пункт)
  confidence: 0.88
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: 99fda307-a2c0-4475-9f72-5ca68ba65004
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:17:50Z'
    executor: backend-dev
    note: 'Implemented ADR-0010''s grouped navigation exactly per its ''Implementation notes for B-2'': split chat_panel_keyboard() into chat_panel_root_keyboard() (7 rows: 4 group buttons behavior/modules/stickers/rules + unchanged KB/Reactions link rows + back) and chat_panel_group_keyboard(lang,*,chat_id,group,config,row) (lifted per-field loop body, scoped to one group, + back row to adm_pnl_menu:). New adm_pnl_grp:{lang}:{chat_id}:{group} callback + handle_chat_panel_group handler, new render_chat_panel_group() with the ''{title} › {group_label}'' breadcrumb (the item''s ''where am I''). render_chat_panel kept its exact name/signature (Decision 6) so handle_chat_panel_menu/_render_and_show_panel/the tolerance-input handler needed zero changes; two parameters that became structurally unused under the new split (render_chat_panel''s chat_config_service, render_chat_panel_group''s bot_config_repo) are kept per Decision 6''s call-site-symmetry mandate and marked with ARG001 noqa + a comment explaining why (matches existing project precedent in config.py/base.py for interface-required-but-unused args) rather than silently dropped. Mutation re-render target (toggle, tolerance save/cancel) now derives the group from the field spec (field.group / hardcoded FieldGroup.STICKERS for tolerance) per Decision 5 -- zero new callback_data/FSM params, verified via new tests asserting the re-rendered keyboard''s back-row targets adm_pnl_menu: (group screen) not the picker (root). Rewrote both unit test files for the new shape (ADR explicitly flagged this as expected, not a regression) -- 69 tests in the two files (was 55), covering: root screen''s 4 group buttons + no leaked field rows + 7-row count; group screen scoping/back-row/row-count (9 for MODULES); inherited-marker split across root (KB/Reactions link rows) vs group (field rows); new adm_pnl_grp: handler (deny non-admin, invalid chat_id, invalid/unknown group, HTML render); toggle/tolerance-input/tolerance-cancel re-render-target regression tests. Full tests/unit/: 1796 passed, 5 skipped, 1 pre-existing unrelated failure (test_admin_sticker_dm_router_order.py -- confirmed via git stash to pre-B-2 HEAD, same failure, matches A-3/A-4''s own handoff notes, untouched by this item). ruff check + ruff format clean; mypy src/ clean (131 files). No integration/browser smoke test added -- source plan''s ''Общее по всему списку'' requires a local-deploy + browser smoke test before this is truly done; that''s Q-2''s job per the envelope (already scoped to cover B-2''s new screens/back-nav/breadcrumb) and out of backend-dev''s unit-test-only scope.'
  result:
    kind: commit
    ref: 412da254d06bd45b783610ae2aece1b4d3df4d3e
    verification: 'pytest tests/unit/test_admin_chat_panel_handler.py tests/unit/test_admin_chat_panel_keyboards.py -q: 69 passed; full tests/unit/: 1796 passed, 5 skipped, 1 pre-existing unrelated failure (verified pre-existing via git stash); ruff check src/ clean; ruff format clean; mypy src/ clean (131 files)'
- id: B-3
  title: 'Копирайт: подписи полей/групп и краткие статусы на корневом экране новой панели'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - B-2
  estimated_effort: ~1ч, низкий риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: C-1
  title: Сортировать пикер чатов по числу сообщений за 24ч + счётчик в подписи (один агрегатный запрос, без N+1)
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: ~1–1.5ч, низкий риск
  confidence: 0.9
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: f7058f7b-c0bf-44e5-962a-236277ef2f13
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:24:27Z'
    executor: backend-dev
    note: 'Picker (adm_pnl:) now sorted by rolling-24h chat_messages count desc, counter in caption. New dedicated AdminRepository.get_enabled_chats_page_by_activity() (one LEFT JOIN aggregate query, no N+1, reuses migration 002''s idx_chat_messages_chat_created index -- no new migration needed) rather than changing the shared get_enabled_chats_page(), which KB/Reactions/whitelist pickers also use and stay title-sorted (out of C-1''s scope). Rolling 24h window and title/chat_id tie-break for zero-count chats chosen as the lowest-risk reading of the PRD''s open question (not escalated -- low-stakes, easily revisited). Pagination (_PER_PAGE=10) unaffected: ORDER BY + LIMIT/OFFSET at DB level. Unit tests added: repository (counts+total, single fetch() call = no N+1, default/custom window + pagination params passed through) and keyboard (counter shown incl. zero, backward-compatible omission when key absent). No qa follow-up needed -- pure SQL aggregate + caption formatting, adequately covered by mocked-pool unit tests; no new schema/migration for qa''s integration suite to exercise.'
  result:
    kind: commit
    ref: c5cf372
    verification: 'pytest tests/unit/test_admin_repository.py tests/unit/test_admin_chat_panel_keyboards.py tests/unit/test_admin_chat_panel_handler.py -q: 115 passed; full tests/unit/: 1804 passed, 5 skipped, 1 pre-existing unrelated failure (test_admin_sticker_dm_router_order.py, documented pre-existing in A-4''s handoff, unaffected by this change); ruff check src/ clean; ruff format --check clean; mypy clean on all 3 touched src files'
- id: D-1
  title: Шорткат к панели чата по ссылке/названию (список кандидатов при неоднозначности, доступ только админам/whitelist, безопасный роутинг). Inline-режим вне скоупа.
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - B-2
  estimated_effort: ~2.5–4.5ч, средне-высокий риск
  confidence: 0.85
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: d6b993ef-a8ef-4d1b-b13f-8ebad4a2be5b
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:39:44Z'
    executor: backend-dev
    note: 'New admin-only /panel <query> command (Command+IsAdmin, registered in command_registry.py) opens a whitelisted chat''s panel directly via render_chat_panel(): t.me/c/<id> links resolve purely (parse_chat_reference in src/utils/telegram.py, next to build_chat_url per the source item''s own suggestion, round-trip tested against it), t.me/<username>/@username resolve via one bot.get_chat() call, everything else falls back to a chat_title ILIKE substring search (AdminRepository.find_enabled_chats_by_title, wildcard-escaped). Multiple title matches render chat_panel_candidates_keyboard (D-1''s ''список кандидатов при неоднозначности''); every path -- link, username, title -- is filtered through chat_settings.enabled=true before rendering, so the shortcut cannot reveal a non-whitelisted chat even when a link resolves to a real one. Deliberately a dedicated slash command, not a bare-text handler, per the source PRD''s own ''безопасный роутинг'' concern (this DM already runs FSM text inputs for tolerance/sticker-edit/sticker-score plus a reply-to-sticker handler) -- Command(''panel'') only ever matches exact /panel prefix, no StateFilter/exclusion-list needed. Inline mode (@bot query) confirmed out of scope by both the item title and the source plan''s ''Не входит в этот план''. Forwarded-message entry (mentioned as ''возможно'' in the source''s item 5 detail) was dropped from scope: the envelope''s actual item title only asks for link/name, and the human-approved plan brief''s D-1 line says only ''по ссылке или названию''. Unit tests: 12 new parser tests (round-trip against build_chat_url, reserved t.me paths, invite links, username forms) + 3 repo tests (match/escape/limit) + 9 handler tests (usage, link+whitelisted, link+not-whitelisted, username resolve success/failure, title single/multiple/zero match, non-private no-op) + 2 command-registry snapshot updates (deliberate per that test''s own convention). This was a retry dispatch (previous attempt exhausted its per-item budget mid-session after all code+tests were written but before verdict submission) -- all files were intact in the uncommitted working tree; this session verified, re-ran the full suite, committed, and is submitting the verdict now. No qa follow-up needed beyond what Q-2 (manual smoke checklist) already scopes for D-1 -- pure unit-mockable logic (regex parsing, one aggregate SQL query, handler branching), no new schema/migration for an integration suite to exercise.'
  result:
    kind: commit
    ref: 3716e61d839141dcf7cdd0ba10168226bbdedf56
    verification: 'pytest tests/unit/test_telegram_utils.py tests/unit/test_admin_repository.py tests/unit/test_command_registry.py tests/unit/test_admin_chat_panel_handler.py tests/unit/test_admin_chat_panel_keyboards.py -q: 212 passed; full tests/unit/: 1831 passed, 5 skipped, 1 pre-existing unrelated failure (test_admin_sticker_dm_router_order.py, documented pre-existing in A-3/A-4/B-2/C-1 handoffs); ruff check src/ clean; ruff format --check clean on all touched files; mypy src/ clean (131 files)'
  budget_checkpoint:
    count: 1
    spent_usd: 4.8045
    session_id: d6b993ef-a8ef-4d1b-b13f-8ebad4a2be5b
    source: budget_death
    state: resolved
    ts: '2026-08-09T15:35:42Z'
  max_usd_override: 8.0
- id: E-1
  title: 'Параметр количества для /summary: /summary <n> (дефолт 100, минимум 20, максимум 1000); при n<20 — вежливый отказ «столько можно прочитать и самому»; валидация, фильтр темы форума, контроль токенов/стоимости'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: ~1–1.5ч, низкий риск
  confidence: 0.9
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: c9dbb8e8-a6ff-4ef0-883a-fecf38ec1232
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:51:09Z'
    executor: backend-dev
    note: 'Added /summary <n> (default 100, min 20, max 1000): garbage/out-of-range -> clear validation reply; n<20 -> polite ''read it yourself'' refusal (owner''s exact framing). count wired through to SummaryService.generate()/get_for_summary limit; forum-topic filter (message_thread_id) untouched and covered by a passthrough test. Added a conservative, clearly-commented char-budget safety net in SummaryService (no per-provider context-window table exists in this codebase) that trims to the most recent messages and logs a structlog warning on trim; header now reports the actually-summarized count instead of the raw request. Cost logging itself needed no change — log_usage() already logs real provider-returned token counts regardless of n (ADR: generate_text() does not auto-log). Pre-existing unrelated failure noted, NOT caused by this change and untouched by my diff: tests/unit/test_admin_sticker_dm_router_order.py::test_admin_private_known_sticker_hits_admin_check_not_media_learn (TypeError: MagicMock awaited in admin_sticker.py) — worth a follow-up ticket. E-2 (/summary500) depends on this and can now call SummaryService.generate(count=500) directly.'
  result:
    kind: commit
    ref: 699069b969f17aa774fdab785bd9b2bf74515619
    verification: 'pytest tests/unit/test_summary.py tests/unit/test_commands_handler.py -q: 48 passed. Full tests/unit: 1848 passed, 5 skipped, 1 pre-existing unrelated failure (admin_sticker, untouched by this diff). ruff check src/ clean. mypy src/: Success, no issues in 131 source files.'
- id: E-2
  title: 'Быстрая команда /summary500 (сводка по 500 сообщениям): регистрация в command_registry.py + меню команд (3 языковых варианта)'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - E-1
  estimated_effort: ~1ч, низкий риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T11:39:42Z'
    executor: pm-orchestrator
    note: 'Разблокировано владельцем (E): нужна и быстрая команда вдобавок к параметру. /summary500 = сводка по 500 сообщениям. Обязательна спека в command_registry.py, иначе CI падает; три языковых варианта скоупа.'
  result: null
- id: Q-1
  title: 'Интеграционный тест: ручная оценка стикера переживает повторный анализ (расширить TestExplicitnessScoreUpsert, реальная схема Postgres)'
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - A-3
  estimated_effort: ~1ч, низкий риск
  confidence: null
  consult_session_id: 61c8b3a2-fffe-451e-bd7a-d240383c5c84
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: Q-2
  title: Чек-лист ручного смоук-теста в личке понятным языком + подготовленные тестовые данные/фикстуры для минимального ручного прогона
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - A-4
  - B-2
  - C-1
  - D-1
  estimated_effort: ~1.5ч, низкий риск
  confidence: null
  consult_session_id: 61c8b3a2-fffe-451e-bd7a-d240383c5c84
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T11:39:57Z'
    executor: pm-orchestrator
    note: 'Новый пункт по ответу владельца (Q2): к ручному смоук-тесту приложить чек-лист понятным языком и заранее подготовленные тестовые данные, чтобы прогон требовал минимум раздумий. Покрывает карточку стикера (A-1/A-4), панель настроек (B-2), пикер чатов (C-1), шорткат (D-1).'
  result: null
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 24.1727
  warn_emitted: true
review_gate:
  why:
  - 'item D-1: per-item cap bumped $6.00→$8.00 — budget-consult: No commit landed but git status shows substantial on-topic uncommitted work matching D-1''s scope: handler routing +135 lines, keyboard +23, admin repo +25 (with new repo tests), and a new telegram-utils helper +78 (with tests) — only the handler itself lacks matching tests, suggesting that''s the remaining gap. Budget (not turns, 76/150 used) was the binding constraint on an item PRD-estimated as medium-high risk/2.5-4.5h against only a $6 cap, so sizing was likely too tight; bumping to $8.00 total (~$3.20 more, in line with the ~$0.063/turn burn rate) should cover finishing handler tests + commit while staying under the 2x cap bound and leaving ~$8.9 of the plan''s $12.1 headroom for the other 6 items.'
  - 'budget cap reached: consumed $24.1727 of $30.0'
  approve_action: /execute-plan docs/plans/admin-ux-and-summary-2026-08-09.execution.md
  reject_action: /plan-fixes docs/plans/admin-ux-and-summary-2026-08-09.md --revise docs/plans/admin-ux-and-summary-2026-08-09.execution.md
safe_to_replay_from: null
clarifying_questions: []
human_feedback:
- ts: '2026-08-09T11:31:39Z'
  by: julia
  text: 'ANSWER [Q1]: Да, давай твою формулировку. Главное её зафиксировать в документации и в будущем следовать везде ему.'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T11:40:37Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:31:57Z'
  by: julia
  text: 'ANSWER [A-1]: Да, подходит'
  applies_to: A-1
  status: addressed
  addressed_at: '2026-08-09T11:40:41Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:32:21Z'
  by: julia
  text: 'ANSWER [A-4]: Сделай кнопки плюс возможность вручную ввести число'
  applies_to: A-4
  status: addressed
  addressed_at: '2026-08-09T11:40:45Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:32:42Z'
  by: julia
  text: 'ANSWER [B-1]: Да, всё так делаем'
  applies_to: B-1
  status: addressed
  addressed_at: '2026-08-09T11:40:48Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:33:08Z'
  by: julia
  text: "ANSWER [E]: и параметр и быструю команду делаем \n\nи можно вызывать просто /summary по дефолту остается 100\n\nкстати забыли еще минимум указать, я бы наверное взяла 20. Если ввели меньше то отвечать что можешь сам прочитать"
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T11:40:51Z'
  edited_at: '2026-08-09T11:33:56Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:35:15Z'
  by: julia
  text: 'ANSWER [Q2]: Да, чеклист обязательно надо сделать, ещё и понятным языком, плюс по максимуму подготовить данные для теста, чтобы для ручного теста можно было по минмуму задумываться'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T11:40:55Z'
  addressed_by: pm-orchestrator
revision_number: 2
last_revised_at: '2026-08-09T11:42:07Z'
last_revised_by: pm-orchestrator
---

































































































<!-- BRIEF:START lang=ru -->
# Админка бота: удобные настройки, оценка откровенности стикеров и сводка по числу сообщений

## Что произошло
Разобрали ваш запрос на доработку админки в личке бота — шесть пунктов про управление
стикерами, настройки чатов и команду сводки. По каждому пункту сверились с тем, как бот
устроен сейчас, и учли ваши ответы на уточняющие вопросы.

## Найденные проблемы
- Когда бот присылает вам карточку стикера, в ней **не видно его оценки откровенности** —
  непонятно, пропустит ли бот такой стикер в чат.
- Поставить стикеру оценку вручную сейчас нельзя; а если просто добавить такую кнопку,
  следующий автоматический анализ **молча затрёт** вашу оценку — фича будет бесполезной без
  правила, которое её сохранит.
- Настройки чата идут **одним длинным списком** — листать неудобно, как вы и отметили.
- В меню выбора чата чаты идут по алфавиту, и **самые активные приходится искать** вручную.
- Чтобы открыть настройки конкретной группы, каждый раз нужно проходить через общий список.
- Команды для сводки по числу сообщений (той самой `/summary500`) в боте **нет** — её не
  перенесли со старой системы.

## Что будет сделано
- В карточку стикера добавим его оценку откровенности одной строкой с признаком «пройдёт /
  не пройдёт» для текущего чата (A-1). Оценку можно будет выставить кнопками-пресетами или
  ввести числом вручную, а также сбросить обратно к автоматической; ручная оценка переживёт
  повторный анализ (A-2, A-3, A-4, Q-1). Термины закрепим: у стикера — «оценка откровенности»,
  у чата — «уровень приличия».
- Настройки чата разложим по разделам с отдельными экранами и понятным возвратом «назад»
  (B-1, B-2, B-3).
- В меню выбора чата активные чаты поднимутся наверх, рядом — счётчик сообщений за сутки (C-1).
- Появится быстрый переход к настройкам группы по ссылке или названию, со списком вариантов
  при совпадениях (D-1).
- У сводки: обычная `/summary` берёт 100 сообщений по умолчанию, можно указать число
  (`/summary 500`, до 1000); если попросить меньше 20 — бот вежливо ответит, что столько
  проще прочитать самому. Плюс вернём привычную быструю команду `/summary500` (E-1, E-2).
- Приложим чек-лист ручной проверки понятным языком и заранее подготовленные тестовые данные,
  чтобы ваш ручной прогон в Telegram занял минимум сил (Q-2).

## Не входит в этот план
- Режим «автопоиска через @бот» (inline) — требует внешней настройки бота, вынесен за рамки
  этой итерации.
- Сам ручной прогон в Telegram после сборки остаётся за вами (автотесты, чек-лист и готовые
  тестовые данные мы подготовим).

## Оценка
Суммарно ≈ 20–27 часов работы. Потолок расходов — до $30 на весь план (до $6 на пункт).
<!-- BRIEF:END -->

# Plan — admin-ux-and-summary-2026-08-09

## Source

[`docs/plans/admin-ux-and-summary-2026-08-09.md`](docs/plans/admin-ux-and-summary-2026-08-09.md) (sha256 `5faac3c79c42...`).

## Items

(none yet — populated by /plan-fixes)
