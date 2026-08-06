---
schema_version: 3
plan_id: chat-settings-panel-2026-08-06
source_artifact:
  path: docs/plans/chat-settings-panel-2026-08-06.md
  sha256: 5bfae380e374e04447a96a442f5e85935c0bec40d82f5ba0fbe634ef5bf04fb1
  type: feature-prd
created_at: '2026-08-05T22:15:44Z'
approved_at: '2026-08-05T23:42:41Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: partial
  started_at: '2026-08-05T23:44:28Z'
  completed_at: null
  current_batch: null
  task_list_id: chat-settings-panel-2026-08-06
items:
- id: A-1
  title: 'Реестр полей настроек: единый модуль (группа, метка, короткий callback-код, тип) для 24 per-chat полей, общий для панели чата и экрана дефолтов'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1.5h
  confidence: 0.9
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: 01b1b982-8de6-4338-9e0c-2db5468b2c8a
  retry_count: 0
  last_update:
    ts: '2026-08-05T23:51:21Z'
    executor: backend-dev
    note: 'Added src/bot/settings_fields.py: single registry (FieldSpec: key, group, i18n label, short callback code, type, legacy flag) for the 24 per-chat settings fields, derived by cross-checking ChatConfigService._CHAT_CONFIG_FIELDS (25) minus ''enabled'' (whitelist gate, not a setting) and excluding kb_organizer_ids (stays in KB panel per A-2). Legacy split (13 migration-001 SQL-DEFAULT columns vs 11 new nullable/no-DEFAULT columns) matches the PRD legacy list exactly and gates B-2/C-1. Short 2-3 char callback codes solve the 64-byte callback_data limit noted in the PRD. Pure data module, no handlers/keyboards touched (B-1/C-1''s job), no DB/browser QA needed. 28 new unit tests cross-check registry against _CHAT_CONFIG_FIELDS and _WRITABLE_COLUMNS, code uniqueness/format, legacy/new split, grouping, label i18n; verified assertions catch drift by sabotaging a callback code (3 tests failed as expected) then reverting. Full unit suite (1411 tests), ruff, mypy --strict all green.'
  result:
    kind: commit
    ref: 4147315980450f51cbff6664895ee4336321573e
    verification: pytest tests/unit/test_settings_fields.py -q (28 passed); pytest tests/unit/ -q (1411 passed); ruff check src/ (clean); mypy src/ (clean, 124 files)
- id: A-2
  title: 'ADR: архитектура панели — рендер параметризован chat_id и отделён от проверки прав (Цель 2); KB/Reactions встраиваются ссылкой (без дублирования); kb_organizer_ids остаётся в KB-панели'
  specialist: architect
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1.5h
  confidence: 0.85
  consult_session_id: e1f29a7c-df13-4271-8f67-38379d80a274
  specialist_session_id: 39c8879f-1352-4e09-948a-460ce963d600
  retry_count: 0
  last_update:
    ts: '2026-08-05T23:59:37Z'
    executor: architect
    note: 'ADR-0006 (docs/decisions/ADR-0006-chat-settings-panel-architecture.md): (1) render_chat_panel returns pure (text, keyboard), permission check stays a call-site guard (check_admin_direct, matching KB/Reactions precedent) -- no premature permission-strategy interface, satisfies Cel 2''s future in-chat entry point without a rewrite; (2) KB/Reactions rows embed by link into existing adm_kb_menu:/adm_react_menu: (no duplicate toggle write-path for kb_enabled/reactions_enabled/reactions_history_enabled -- those already have a single-source-of-truth handler that E-1 is retrofitting), kb_organizer_ids stays KB-only per A-1''s own docstring which this ADR now formally backs; (3) one generic adm_pnl_tgl: toggle callback for the ~20 fields with no existing UI, reusing ChatConfigService.get_config() instead of per-field helpers, callback_data verified at 34/64 bytes worst-case; (4) panel gets its own adm_pnl:/adm_pnl_menu: chat-picker mirroring the adm_kb:/adm_react: precedent rather than bolting onto adm_wl_chats: (keeps whitelist-removal screen untouched). Explicit consequences recorded for B-2 (raw-row needed alongside effective value for the inherited marker), C-1 (must call invalidate_all(), not invalidate(chat_id) -- global cache, easy miss), D-1 (link straight to adm_pnl_menu:, no picker needed), E-1 (scope unaffected, exactly the two call sites this ADR keeps as sole write path).'
  result:
    kind: file
    ref: docs/decisions/ADR-0006-chat-settings-panel-architecture.md
    verification: ADR file created under docs/decisions/ (next sequential number after ADR-0005); all file:line citations verified against current src/ tree; no application source touched; ADR is self-consistent with A-1's already-shipped registry which pre-cited this ADR's kb_organizer_ids decision.
- id: B-1
  title: 'Панель настроек чата (R1): вход из adm_wl_chats:, сгруппированное меню, булевы тогглы по паттерну эффективного значения, ссылки на KB/Reactions, инвалидация кэша ChatConfigService на своих записях'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A-1
  - A-2
  estimated_effort: 5h
  confidence: 0.9
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: 04cdb591-bf88-4dcd-8b1f-e73c083c4f5c
  retry_count: 0
  last_update:
    ts: '2026-08-06T00:12:27Z'
    executor: backend-dev
    note: 'Implemented per ADR-0006 exactly: own picker (adm_pnl:/adm_pnl_menu:), generic bool toggle (adm_pnl_tgl:, Decision 3) for the ~20 fields with no existing UI, KB/Reactions rendered as link rows into their existing sub-panels (Decision 2, never reachable via the generic toggle -- explicit rejection test for codes kb/rx/rh even though A-1''s registry marks them BOOL). render_chat_panel() is a pure (text, keyboard) function per Decision 1 (no CallbackQuery/permission check inside), permission check (check_admin_direct + private-chat) lives in each callback handler, matching KB/Reactions precedent. New main-menu button per Decision 4. KB/Reactions status is read fresh (chat_settings_repo + bot_config_repo direct read, bypassing ChatConfigService''s 60s cache) rather than through the cached effective config, because their existing toggle handlers (admin_kb.py/admin_reactions.py) don''t self-invalidate yet (E-1, not landed) -- regression test test_kb_reactions_status_is_fresh_not_cached asserts this with a deliberately contradictory stale cache. The panel''s own generic toggle path does self-invalidate (chat_config_service.invalidate(chat_id)) per Decision 3. Non-BOOL fields render read-only (noop callback) per F-1 deferral. 33 new unit tests (keyboards: picker/format/grouping/link-rows/back-button; handler: render_chat_panel incl. the freshness regression, picker/menu/toggle admin-gating, invalid chat_id, link-only-code rejection, non-bool-code rejection, unknown-code rejection, cache invalidation, re-render after toggle) plus 2 added assertions in the pre-existing main-menu keyboard test for the new adm_pnl button. Full unit suite (1444 tests), ruff, ruff format, mypy --strict all green. Routing hint for G-1 (qa, depends_on B-1): integration coverage should assert the toggle write reaches chat_settings via a real repo/DB and that chat_config_service.get_config() reflects the flip post-invalidate -- this item''s tests mock both repos and the service.'
  result:
    kind: commit
    ref: 97cedc42f9aea1bf231423002ab20aeaff9d2f85
    verification: pytest tests/unit/test_admin_chat_panel_keyboards.py tests/unit/test_admin_chat_panel_handler.py tests/unit/test_admin_keyboards.py -q (92 passed); pytest tests/unit/ -q (1444 passed); ruff check src/ clean; ruff format --check clean (128 files); mypy src/ clean (126 files)
- id: B-2
  title: 'Индикатор «переопределено / унаследовано от дефолта» по строкам панели (R1): маркер-суффикс ТОЛЬКО на унаследованных строках. Ограничен 11 новыми полями, где наследование честно; для 13 легаси-полей «унаследовано» не показывать (миграция C-2 отложена)'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - B-1
  estimated_effort: 1h
  confidence: 0.93
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: 5cdde273-3741-465d-bacd-d6b85a97b1dd
  retry_count: 0
  last_update:
    ts: '2026-08-06T00:46:06Z'
    executor: backend-dev
    note: 'Added inherited-from-default marker to chat panel rows, restricted to new_fields() (11 non-legacy columns) per Julia''s B-2 answer; legacy fields never marked even if raw is NULL (baked into _is_inherited, not left to call sites). chat_panel_keyboard() now takes the raw chat_settings row alongside the effective ChatConfig (ADR-0006 impl note #2); render_chat_panel threads it through. Marker applied to generic bool/read-only rows and to the KB/Reactions link rows (Decision 2 consequence), with each half of the combined Reactions row marked independently since one field can be overridden while the other is inherited. Updated 8 pre-existing keyboard-test call sites for the new row= param (row=None preserves old behavior since none of those tests touch new_fields() marker assertions) and added 8 new marker-focused tests plus 1 handler-level test proving render_chat_panel actually threads the raw row through (not just the keyboard unit). Verified discriminating power: sabotaged _is_inherited to always return False, 6 of the new tests failed as expected, reverted (grep confirms no leftover sabotage marker) before committing. No DB/integration surface touched -- pure keyboard/handler composition over B-1''s already-shipped render path -- no qa routing hint needed beyond existing G-1 coverage.'
  result:
    kind: commit
    ref: 4ab7d81a4835ef4ce262f40d2a72a8916b81ad75
    verification: 'pytest tests/unit/test_admin_chat_panel_keyboards.py tests/unit/test_admin_chat_panel_handler.py tests/unit/test_admin_keyboards.py -q (105 passed); pytest tests/unit/ -q (1479 passed); ruff check src/ clean; ruff format --check clean (4 files); mypy src/ clean (128 files); control check: sabotaged _is_inherited, 6 new tests failed as expected, reverted and verified clean before commit'
- id: C-1
  title: 'Экран «настройки по умолчанию» (R2): заменить заглушку adm_defs: реальным управлением bot_config.default_* — ТОЛЬКО 11 новыми полями (без SQL-DEFAULT), для которых значение по умолчанию уже применяется к чатам. 13 легаси-полей вне экрана дефолтов до миграции C-2 (отложена)'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A-1
  estimated_effort: 4h
  confidence: 0.9
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: b63c5a64-1f58-4bd9-b4df-2e54830ab468
  retry_count: 0
  last_update:
    ts: '2026-08-06T00:21:52Z'
    executor: backend-dev
    note: 'Replaced adm_defs: placeholder with real settings-by-default screen for A-1''s 11 new_fields(), per ADR-0006 C-1 notes. New admin_defaults.py handler+keyboard pair (mirrors B-1''s admin_chat_panel.py split), registered in handlers/__init__.py; removed dead placeholder handler from admin.py. Per Decision 2''s C-1 consequence, kb/reactions toggle as ordinary bool fields here (no defaults-layer sub-panel to link to, unlike B-1). Fallback default is ChatConfig''s own dataclass default, verified to match ChatConfigService._merge() layer-1 semantics for all 11 fields. Writes use invalidate_all(), never invalidate(chat_id), regression-tested. Legacy and non-BOOL codes rejected even though the shared registry allows them. 21 new unit tests. Routing hint for G-1 (qa): integration coverage should assert the default write reaches bot_config via a real repo/DB and that get_config() reflects it post invalidate_all() for a chat with no per-chat override.'
  result:
    kind: commit
    ref: 629b400beed083c5d3f8a9f215ab1b8b1532af1e
    verification: pytest tests/unit/test_admin_defaults_handler.py tests/unit/test_admin_defaults_keyboards.py tests/unit/test_admin_keyboards.py -q (80 passed); pytest tests/unit/ -q (1465 passed); ruff check clean; ruff format --check clean; mypy src/ clean (128 files)
- id: C-2
  title: 'Миграция легаси-DEFAULT (R2): DROP DEFAULT + NULL-ификация 13 колонок migration-001, где значение == старому SQL-дефолту, чтобы default_* реально управлял существующими чатами. Forward-only, трогает прод-данные — высокий риск'
  specialist: backend-dev
  priority: P1
  status: blocked
  depends_on: []
  estimated_effort: 2.5h
  confidence: null
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-05T23:40:08Z'
    executor: pm-orchestrator
    note: 'Отложено в техдолг по решению Julia (revise 2026-08-06): forward-only миграция прод-данных вне v1. Экран дефолтов C-1 ограничен 11 новыми полями, для которых default_* уже работает.'
  result: null
- id: C-3
  title: 'Интеграционный тест миграции C-2: колонки nullable+no-DEFAULT, явные per-chat переопределения сохранены, только строки со старым дефолтом занулены — эмпирически на фикстуре (не только инспекция схемы)'
  specialist: qa
  priority: P1
  status: blocked
  depends_on:
  - C-2
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 0fb3e85d-3bb5-4b02-b68c-61ad2bc74eea
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-05T23:40:11Z'
    executor: pm-orchestrator
    note: Отложено вместе с C-2 (интеграционный тест той миграции). Вне v1.
  result: null
- id: D-1
  title: 'Кнопка «⚙️ Настройки чата» после approve (R3): в DM-уведомлении (adm_approve:) и в pending-списке (adm_wl_apr:), рядом/вместо индикатора «✅»'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - B-1
  estimated_effort: 1.5h
  confidence: 0.9
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: c6464b8b-1736-475e-9007-e9525e7d2de6
  retry_count: 0
  last_update:
    ts: '2026-08-06T00:30:27Z'
    executor: backend-dev
    note: 'Per ADR-0006 Decision 4: both approve entry points (adm_approve: DM notification, adm_wl_apr: pending list) now show a settings-panel button deep-linking to adm_pnl_menu:{lang}:{chat_id} -- no picker needed, chat_id already known. DM flow: approved_notification_keyboard(lang, chat_id) puts the settings button next to the existing Approved indicator (same row). Pending-list flow: handle_wl_approve no longer re-renders the pending list -- the approved attempt drops out of get_pending_attempts_page immediately (status flip), so there would be no row left to attach a button to; instead it shows a new interstitial (wl_approved_keyboard: settings link + back-to-pending-list), mirroring the existing confirm-screen pattern (adm_wl_rm_ask:/confirm_remove_chat_keyboard). Reused B-1''s chat_panel i18n label and the existing _build_chat_link_html helper, no new i18n dict. 8 new/updated unit tests. Pure routing + keyboard construction over already-shipped B-1 render_chat_panel, no DB/integration surface touched, no additional qa routing hint needed beyond G-1.'
  result:
    kind: commit
    ref: dd9a9668f27d7d50f591fd73dbf1cac946bda22c
    verification: pytest tests/unit/test_admin_handler.py tests/unit/test_admin_keyboards.py -q (126 passed); pytest tests/unit/ -q (1470 passed); ruff check src/ clean; ruff format --check clean; mypy src/ clean (128 files)
- id: E-1
  title: Ретрофит инвалидации кэша ChatConfigService для СУЩЕСТВУЮЩИХ тогглов KB/Reactions (admin_kb.py, admin_reactions.py) — предсуществующий пробел, вскрытый анализом PRD; сейчас invalidate() только в chat_events.py:73
  specialist: backend-dev
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 1h
  confidence: 0.95
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: 0cf0292a-e89e-4c9f-af90-74c164a5c3b1
  retry_count: 0
  last_update:
    ts: '2026-08-06T00:53:25Z'
    executor: backend-dev
    note: 'Added chat_config_service.invalidate(chat_id) right after the DB write in admin_kb.py''s handle_kb_toggle and admin_reactions.py''s handle_reactions_toggle (the two pre-existing KB/Reactions toggle handlers named in the item) -- mirrors B-1''s handle_chat_panel_toggle self-invalidation pattern. Reactions handler places the invalidate call before the not-admin-warning branch since the write commits either way. Also refreshed two now-stale ''E-1, not landed'' docstrings in admin_chat_panel.py/keyboards/admin_chat_panel.py (B-1''s fresh-read bypass of the cache for KB/Reactions link rows) to say the bypass is now defense-in-depth rather than a required workaround -- no behavior change there, B-1''s own regression test (test_kb_reactions_status_is_fresh_not_cached) still passes untouched since the bypass mechanism itself wasn''t removed. 5 new/updated unit tests (2 new regression tests asserting invalidate() is called exactly once with chat_id after a successful toggle, plus invalidate.assert_not_called() added to the existing denial/rejection tests, plus one existing not-admin-warning test extended to assert invalidate still fires on that branch). Verified discriminating power: commented out both invalidate() calls, re-ran the two test files -- exactly the 3 invalidate-dependent tests failed, reverted via Edit (grep confirms no leftover SABOTAGE marker) before committing. Full unit suite (1481 passed, up from 1479), ruff check clean, ruff format --check clean, mypy src/ clean (128 files). No DB/integration surface touched -- pure handler-level cache-invalidation call plus DI param -- no additional qa routing hint needed; existing G-1 integration coverage exercises the analogous B-1/C-1 invalidate paths against a real Postgres testcontainer, this item''s mechanism is identical (ChatConfigService.invalidate(chat_id), same as chat_events.py:73''s precedent).'
  result:
    kind: commit
    ref: 1b58b193ef3e16fc48fa29f49b6f2ce3c58419d7
    verification: 'pytest tests/unit/test_admin_kb_handler.py tests/unit/test_admin_reactions_handler.py tests/unit/test_admin_chat_panel_handler.py -q (65 passed); pytest tests/unit/ -q (1481 passed); ruff check src/ clean; ruff format --check clean (224 files); mypy src/ clean (128 files); control check: commented out both invalidate() calls, 3 tests failed as expected (test_invalidates_cache_for_this_chat_after_write x2, test_enabling_reactions_when_bot_not_admin_warns_immediately), reverted and verified clean before commit'
- id: F-1
  title: 'Редактирование не-булевых полей через FSM (R4): system_prompt, trigger_words, chances/intervals, language, rules_mode с валидацией диапазонов. Кандидат на отдельную итерацию — зависит от ответа по составу v1'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - B-1
  estimated_effort: 5h
  confidence: null
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-05T23:40:16Z'
    executor: pm-orchestrator
    note: 'Отложено в отдельную итерацию по решению Julia (revise 2026-08-06): v1 = булевы тогглы + просмотр остальных полей. Редактирование не-булевых (system_prompt, trigger_words, chances/интервалы, language, rules_mode) вне v1.'
  result: null
- id: F-2
  title: 'Тесты валидации ввода для FSM-редактирования (F-1): некорректные chances, отрицательные интервалы, неизвестные коды language, недопустимый rules_mode. Только если F-1 входит в v1'
  specialist: qa
  priority: P2
  status: blocked
  depends_on:
  - F-1
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 0fb3e85d-3bb5-4b02-b68c-61ad2bc74eea
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-05T23:40:19Z'
    executor: pm-orchestrator
    note: Отложено вместе с F-1 (тесты валидации FSM-ввода). Вне v1.
  result: null
- id: G-1
  title: 'Интеграционные тесты хендлеров панели чата (B-1) и экрана дефолтов (C-1): запись тоггла доходит до репозитория, эффективное значение флипается корректно, кэш инвалидируется после записи'
  specialist: qa
  priority: P1
  status: done
  depends_on:
  - B-1
  - C-1
  estimated_effort: 2h
  confidence: 0.92
  consult_session_id: 0fb3e85d-3bb5-4b02-b68c-61ad2bc74eea
  specialist_session_id: a45f53c6-f4c0-4cad-9218-ad2cd8ff851a
  retry_count: 0
  last_update:
    ts: '2026-08-06T00:37:26Z'
    executor: qa
    note: 'Added tests/integration/test_admin_chat_panel_toggle.py and tests/integration/test_admin_defaults_toggle.py, driving handle_chat_panel_toggle (B-1) and handle_defaults_toggle (C-1) against a real Postgres testcontainer with real ChatSettingsRepository/BotConfigRepository/ChatConfigService (no mocks), mirroring the existing test_kb_enabled_toggle.py pattern. Covers: (1) toggle write reaches chat_settings/bot_config.default_* columns for real; (2) get_config() reflects the flip immediately post-invalidate (the PRD''s documented delayed-toggle bug) instead of serving the 60s-TTL stale cache entry; (3) B-1''s invalidate(chat_id) stays scoped to one chat while C-1''s invalidate_all() clears every already-cached chat -- the ADR''s own flagged ''easy to get backwards by analogy'' regression; (4) an explicit per-chat override still outranks a global default flip after the toggle; (5) KB/Reactions link-only codes stay unreachable via the generic panel toggle, re-verified against a real (non-mocked) repo. Validated discriminating power empirically: commented out invalidate()/invalidate_all() in both handlers, re-ran the suite -- exactly the 4 cache-dependent tests failed while the write/rejection tests still passed -- then reverted via git checkout (confirmed clean via git diff/status) before committing. 14/14 integration tests pass together (new + pre-existing test_kb_enabled_toggle.py, no container/fixture conflicts); 28/28 pre-existing unit tests for both handlers still pass; ruff/ruff-format clean; mypy src/ clean (128 files, unchanged since no src/ edits survived the revert).'
  result:
    kind: commit
    ref: 711df8e21889532083cc026e9e71e6075e0515a4
    verification: pytest tests/integration/test_admin_chat_panel_toggle.py tests/integration/test_admin_defaults_toggle.py tests/integration/test_kb_enabled_toggle.py -v (14 passed, real testcontainers Postgres); pytest tests/unit/test_admin_chat_panel_handler.py tests/unit/test_admin_defaults_handler.py -q (28 passed, unaffected); control check disabled invalidate calls, re-ran suite, 4 cache-dependent tests failed as expected, reverted (git diff/status clean); ruff check clean; ruff format --check clean; mypy src/ clean
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 26.3549
review_gate:
  why: []
  approve_action: /execute-plan <projects>/telegram-chat-companion.chat-settings-panel-2026-08-06-wt/docs/plans/chat-settings-panel-2026-08-06.execution.md --resume
  reject_action: /plan-fixes docs/plans/chat-settings-panel-2026-08-06.md --revise <projects>/telegram-chat-companion.chat-settings-panel-2026-08-06-wt/docs/plans/chat-settings-panel-2026-08-06.execution.md
safe_to_replay_from: null
clarifying_questions: []
human_feedback:
- ts: '2026-08-05T23:35:27Z'
  by: julia
  text: 'ANSWER [C-2]: отложить в техдолг, ограничив экран дефолтов (C-1) только 11 новыми полями'
  applies_to: C-2
  status: addressed
  addressed_at: '2026-08-05T23:40:43Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-05T23:35:50Z'
  by: julia
  text: 'ANSWER [F-1]: v1 = тогглы + просмотр, F-1/F-2 отложить'
  applies_to: F-1
  status: addressed
  addressed_at: '2026-08-05T23:40:48Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-05T23:36:15Z'
  by: julia
  text: 'ANSWER [E-1]: включить E-1'
  applies_to: E-1
  status: addressed
  addressed_at: '2026-08-05T23:40:51Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-05T23:36:38Z'
  by: julia
  text: 'ANSWER [B-2]: маркер только на унаследованных строках, для легаси-полей не показывать «унаследовано» до C-2'
  applies_to: B-2
  status: addressed
  addressed_at: '2026-08-05T23:40:56Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-05T23:37:40Z'
  by: julia
  text: 'ANSWER [Q5]: оставить оба варианта по умолчанию'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-05T23:41:00Z'
  addressed_by: pm-orchestrator
revision_number: 2
last_revised_at: '2026-08-05T23:42:12Z'
last_revised_by: pm-orchestrator
---






































































<!-- BRIEF:START -->
# Панель настроек чата в админке

## Что произошло
Разобран PRD по сборке всех настроек чата в одну панель. Сейчас настройками каждого чата (база знаний, реакции, модули, стикеры и ещё ~20 параметров) можно управлять только вразнобой: у базы знаний и реакций свои отдельные экраны, а остальные параметры меняются вообще только руками в базе. Экран «настройки по умолчанию для новых чатов» — заглушка. После одобрения нового чата админу не предлагается сразу его настроить.

## Найденные проблемы
- Нет единого места, где видны и переключаются все настройки одного чата; большинство параметров недоступны из интерфейса вовсе.
- «Настройки по умолчанию» фактически не работают: для 13 старых параметров значение по умолчанию сегодня не влияет на чаты, которые бот уже видел — экран будет показывать неправду, если это не починить.
- Существующие переключатели применяются с задержкой до минуты (не сбрасывается кэш).
- После добавления чата в whitelist нет быстрого перехода к его настройкам.

## Что будет сделано
- Единая панель настроек чата: все параметры сгруппированы, булевы переключатели срабатывают сразу; база знаний и реакции открываются из неё без дублирования (A-1, A-2, B-1).
- Рабочий экран «настройки по умолчанию» для новых чатов — по 11 параметрам, у которых значение по умолчанию уже реально применяется (C-1).
- Кнопка «⚙️ Настройки чата» сразу после одобрения чата — как в личном уведомлении, так и в списке заявок (D-1).
- Пометка «унаследовано от дефолта» у строк панели — только там, где это правда (11 новых параметров); для старых параметров пометка не показывается, чтобы не вводить в заблуждение (B-2).
- Устранение задержки применения (до минуты) у существующих переключателей базы знаний и реакций (E-1).
- Автотесты на панель и экран дефолтов (G-1).

## Не входит в этот план
- **Починка старых параметров под «настройки по умолчанию»** (13 полей со старым SQL-значением): требует forward-only миграции прод-данных, при слиянии сразу уходит в прод — по решению вынесено в техдолг. Пока экран дефолтов управляет только 11 новыми параметрами (C-2, C-3 отложены).
- **Редактирование текстовых и числовых полей** (системный промпт, триггер-слова, вероятности, интервалы, язык) — отдельная итерация; v1 = только переключатели + просмотр этих полей (F-1, F-2 отложены).
- Доступ к панели админам изнутри чата сейчас не делается, но архитектура закладывается так, чтобы добавить его позже без переделки.

## Оценка
Объём v1 (рекомендованный состав) ~17–18 часов. Отложенные работы (~10 часов) остаются в плане помеченными как отложенные и в объём v1 не входят. Потолок бюджета — $30 на план, $6 на пункт.
<!-- BRIEF:END -->

# Plan — chat-settings-panel-2026-08-06

## Source

[`docs/plans/chat-settings-panel-2026-08-06.md`](docs/plans/chat-settings-panel-2026-08-06.md) (sha256 `5bfae380e374...`).

## Items

(none yet — populated by /plan-fixes)
