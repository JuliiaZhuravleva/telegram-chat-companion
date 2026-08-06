---
schema_version: 3
plan_id: sticker-management-2026-08-06
source_artifact:
  path: docs/plans/sticker-management-2026-08-06.md
  sha256: 8ce06bed2fb5bf2024dc72d370782014353e4b1563281ad2c79a64babff9dc98
  type: feature-prd
created_at: '2026-08-06T13:14:44Z'
approved_at: '2026-08-06T14:00:54Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: done
  started_at: '2026-08-06T14:01:36Z'
  completed_at: '2026-08-06T19:36:15Z'
  current_batch: null
  task_list_id: sticker-management-2026-08-06
items:
- id: A-1
  title: 'ADR дедупликации: первичный путь — сверка по хешу картинки ДО Vision (экономит вызовы, решение [A-1]); зафиксировать тип хеша и допустимость библиотеки под цель «без внешних зависимостей», порог похожести, схему канонической копии (переиспользование описания/эмоций/тегов/эмбеддинга); семантическое сравнение ПОСЛЕ анализа — будущее расширение, не в этом плане'
  specialist: architect
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1h
  confidence: 0.85
  consult_session_id: 37b9213a-89ec-44b3-ae9c-aeab84156215
  specialist_session_id: c766b44b-c3c9-4332-9e83-6dce90103362
  retry_count: 0
  last_update:
    ts: '2026-08-06T14:10:43Z'
    executor: architect
    note: 'ADR-0007 (docs/decisions/): 64-bit dHash via Pillow only (no new dep) as primary pre-Vision path, empirically validated (script run, not just asserted) - same picture recompressed WEBP/PNG and differing transparent-pad RGB both hash to distance 0, random-noise control lands at distance 26, so the chosen threshold (Hamming <=4/64) has real headroom on both sides. Fixed: hash source frame per type (static=raw bytes; tgs reuses already-rendered sampled_frames[0], zero extra cost; webm needs one dedicated ffmpeg -ss 0 anchor frame, NOT the motion-selected keyframe[0], because re-encoding can shift which frame motion analysis picks first). Schema: image_hash CHAR(16) + duplicate_of_file_unique_id self-FK (migration 023), app-side O(N) Hamming scan (no new Postgres extension, catalog is small per migration 005''s own sizing note). Canonical-copy field list + two concrete A-2 pitfalls flagged (format auto-tag must be reapplied for the new sticker''s own type, not copied verbatim; analyzed_at=NOW() on copy is an accepted derived-state consequence, not a bug, per ADR-0003 philosophy). Explicit scope call: no backfill of the 3 named example stickers (not requested by the plan) - documented so A-3''s live checklist doesn''t misinterpret them as a merge target vs. a hash-correctness proof. Semantic/description-level comparison stays out of scope per the source plan and Julia''s [A-1] answer. No src/ changes (architect stays doc-only per project convention).'
  result:
    kind: file
    ref: docs/decisions/ADR-0007-sticker-duplicate-hash-dedup.md
    verification: 'Manual: grep -n confirms full section structure (Context/8 Decisions/Consequences/Rejected alternatives/Implementation notes for A-2+A-3/Out of scope). dHash snippet independently executed (not just eyeballed) via python3 against synthetic Pillow images: same-picture-recompressed distance=0, transparent-padding-color-difference distance=0, random-noise-control distance=26 - confirms both the algorithm and the chosen threshold=4 before A-2 builds on it.'
- id: A-2
  title: 'Миграция + ingest: сверка по хешу картинки ДО Vision-анализа в learning.py — при совпадении/высокой похожести переиспользовать описание/эмоции/теги/эмбеддинг канонического стикера (и будущие vision-поля обобщённо), не вызывая Vision повторно'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A-1
  estimated_effort: 5-6h
  confidence: 0.9
  consult_session_id: 21acdb73-c42a-409a-adca-4ffdd330a8b3
  specialist_session_id: 5c44cd63-a196-4b2f-969d-61cf20980021
  retry_count: 0
  last_update:
    ts: '2026-08-06T16:06:30Z'
    executor: backend-dev
    note: 'Prior attempt (a7a2fa94) had already implemented ADR-0007 fully in the working tree before hitting the per-item budget cap uncommitted; reviewed that code against ADR-0007 line-by-line (dedup.py hash/threshold/find_duplicate, renderer.py hash_frame per type incl. webm t=0 anchor vs motion-keyframe distinction, stickers.py get_dedup_candidates + column wiring, learning.py insertion point + _save_duplicate copy-with-retag path, models.py duplicate_of), found it correct and complete, then fixed 2 ruff findings (unused import, blind-except assert) and 4 mypy findings (stickers.py no-any-return, dedup.py PixelAccess indexing/union-operator) the interrupted run never reached. Added tests/unit/test_sticker_renderer.py (5 new tests, real ffmpeg+rlottie, no mocking) covering a coverage gap in renderer.py''s blast radius: TGS hash_frame bit-for-bit determinism and WebM anchor-frame t=0-not-motion-keyframe (ADR-0007 Decision 2) verified against the real pipeline, not just mocked. Full suite 1527 passed, ruff+mypy clean on src/. QA (A-3) should still do: integration-level assert that analyze_image/generate_embedding are not called on a real DB-backed duplicate path, and the live checklist against the 3 named example stickers per ADR-0007 Decision 8 (hash-correctness proof only, not an in-product merge).'
  result:
    kind: commit
    ref: 5598e2a2ef96cc58851322dc42c065f500360eab
    verification: 'pytest tests/unit/ -q: 1527 passed, no regressions; ruff check src/ clean; mypy src/ clean (129 files)'
- id: A-3
  title: 'Тесты дедупликации: срабатывание хеш-сверки ДО Vision (Vision не вызывается на дубле) + граница ложных срабатываний + live-чеклист по 3 примерам (AgAD6xIAAv3NMUs, AgAD_xEAAsJVUEo, AgADzioAAuSTIEs)'
  specialist: qa
  priority: P1
  status: done
  depends_on:
  - A-2
  estimated_effort: 2.5h
  confidence: 0.9
  consult_session_id: 6f61aa82-e7ba-4085-a914-8e5f6a741d59
  specialist_session_id: 6eb26b75-ad8d-4213-97c0-dafbca15d0b5
  retry_count: 0
  last_update:
    ts: '2026-08-06T16:20:34Z'
    executor: qa
    note: 'Scope per A-2''s own routing note + ADR-0007 Implementation notes for A-3: (1) tests/integration/test_sticker_dedup_integration.py -- real Postgres+pgvector testcontainer, real StickerRepository, only AI router mocked: proves analyze_image/generate_embedding stay uncalled on a REAL DB-backed duplicate path (not the mocked-repo unit tests A-2 already wrote), proves get_dedup_candidates()''s WHERE clause really filters analysis_failed/NULL-description/NULL-hash rows, proves chain-flatten-to-root end to end, proves pgvector embedding round-trips. (2) tests/unit/test_sticker_dedup.py: 2 added adversarial controls closing ADR-0007 Consequences'' own named gaps -- same-pack ''different expression'' pair (harder than the existing solid-vs-checkerboard control) stays above threshold; a same-silhouette/different-fill-color pair lands well INSIDE threshold (2/64 bits) -- a real, empirically measured, previously-undocumented false-positive risk (dHash has near-zero color sensitivity), documented in the test docstring. Not a regression to fix under this item (ADR-0007 Decision 1 chose dHash deliberately) -- flagging as a finding, not blocking. (3) docs/plans/sticker-management-2026-08-06.a3-live-checklist.md: manual hash-correctness checklist for the 3 named stickers per Decision 8 -- explicitly framed as ''hash algorithm correctness'', NOT ''these will retroactively merge'' (they predate migration 023, image_hash stays NULL until someone re-triggers analysis). Checklist sections 1-2 require a live bot + real Telegram file access QA doesn''t have in this session; deferred to Julia, same convention as reactions-2026-08-03''s QA-1 checklist. False-positive boundary already had solid unit coverage from A-2''s own test authorship -- did not duplicate, only added the harder adversarial gap ADR-0007 explicitly named as unaddressed.'
  result:
    kind: commit
    ref: c15a000
    verification: 'pytest tests/unit tests/integration -q: 1529+177 passed (24 skipped, pre-existing env-gated), no regressions; ruff check src/ tests/ clean; mypy src/ clean (129 files). Control-verified the 3 new integration tests actually bite: disabled the dedup branch in learning.py, confirmed all 3 fail, re-enabled, confirmed all pass, reverted via git checkout.'
- id: B-1
  title: 'Хендлер стикера в DM админа (перехват до media.py): найден → описание; не найден → кнопка «Проанализировать» (синхронно, ADR-0003); проверка admin+private'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 3h
  confidence: 0.9
  consult_session_id: 21acdb73-c42a-409a-adca-4ffdd330a8b3
  specialist_session_id: 8340839e-13ca-4f0c-aefe-6f2af05d59db
  retry_count: 0
  last_update:
    ts: '2026-08-06T14:42:43Z'
    executor: backend-dev
    note: 'Added handle_admin_sticker_check (@router.message F.sticker, F.chat.type==private, IsAdmin()) and handle_admin_sticker_dm_analyze callback in src/bot/handlers/admin_sticker.py, plus sticker_dm_check_keyboard in keyboards/admin_sticker.py. Registered on admin_sticker_router, which handlers/__init__.py already wires before media_router, so this intercepts before handlers/media.py''s silent auto-learn for the admin''s own DM sticker -- verified via router include order. Found sticker -> reuses existing _build_detail_text/sticker_detail_keyboard (same renderer as the sets browser). Unknown sticker -> reply carries a single Proanalizirovat button; the callback reads the Sticker object back off callback.message.reply_to_message.sticker (no new cache/DB row -- matches ADR-0003 transient-UI-state stance) and mirrors handle_run_analysis edit-in-place lifecycle, including best-effort sticker-set registration for admin-panel consistency with the automatic learn path. Scoped out: notify_admins and sticker-to-sticker reply (redundant/inapplicable for a synchronous admin DM check). Concurrent A-2 (same plan) is editing stickers.py/learning.py/models.py/renderer.py/dedup.py in this worktree; read but did not touch those files, commit f79f874 stages only my 4 files. QA follow-up: B-2 should add a live-checklist regression asserting media.py handle_sticker_message is NOT invoked for this path (router-order proof).'
  result:
    kind: commit
    ref: f79f874
    verification: 'pytest tests/unit/test_admin_sticker_handler.py -q: 93 passed (incl 23 new tests); pytest tests/unit/ -q: 1522 passed, no regressions; ruff check src/ clean; mypy src/bot/handlers/admin_sticker.py src/bot/keyboards/admin_sticker.py src/bot/handlers/__init__.py clean (pre-existing mypy errors in A-2 in-flight dedup.py/stickers.py confirmed pre-dating this item via git stash).'
- id: B-2
  title: 'Тесты DM-хендлера: found/not-found/analyze, admin+private scoping, регрессия что media.py больше не учит молча, соответствие ADR-0003 + live-чеклист'
  specialist: qa
  priority: P1
  status: done
  depends_on:
  - B-1
  estimated_effort: 2h
  confidence: 0.9
  consult_session_id: 6f61aa82-e7ba-4085-a914-8e5f6a741d59
  specialist_session_id: a18c59ce-62f4-4238-a724-83afd291bc6c
  retry_count: 0
  last_update:
    ts: '2026-08-06T16:42:55Z'
    executor: qa
    note: 'Scope: B-1 own last_update note named the one gap QA needed to close: assert media.py handle_sticker_message is NOT invoked for this path (router-order proof). found/not-found/analyze, admin+private scoping, and ADR-0003 edit-in-place lifecycle were already covered by B-1''s own 23 unit tests (decorator-inspection convention for scoping, matching handle_admin_sticker_reply''s existing pattern) -- did not duplicate those. Added tests/unit/test_admin_sticker_dm_router_order.py: drives the REAL main_router via Router.propagate_event(), not a direct handler call. 2 positive tests (found+not-found) assert sticker_service.learn (media.py''s silent-learn call) is never awaited when admin_sticker''s DM check fires; 2 negative controls (non-admin private, admin in a group chat) prove the same harness DOES reach media.py''s learn() when the DM-check guard does not apply, so the positive assertions are not vacuous. 1 structural test pins admin_sticker''s earlier router registration slot. Control-verified per CLAUDE.md: temporarily reordered handlers/__init__.py to the pre-B-1 order, confirmed the 3 order-sensitive tests fail while the 2 negative controls correctly stay green, reverted via cp from a same-repo backup, confirmed clean git diff and green suite. DI note: bypassed dishka by supplying mocks under the exact parameter names FromDishka params use (aiogram''s own kwarg-injection matches by name against propagate_event''s data dict), no new dishka test-container infra introduced. Added docs/plans/sticker-management-2026-08-06.b2-live-checklist.md (same convention as A-3''s live checklist) for what only a real Telegram client can exercise: double-tap timing under real network latency, cross-account scoping, and confirming no silent DB write happens before the Analyze button is tapped.'
  result:
    kind: commit
    ref: 385104c
    verification: 'pytest tests/unit -q: 1534 passed (1529 prior + 5 new), no regressions. ruff check src/ tests/ clean. mypy src/ clean (129 files). Regression-bite control: reordered router registration to simulate the pre-B-1 bug, confirmed 3/5 new tests fail with the exact expected assertion, reverted and confirmed clean git diff plus green suite.'
- id: C-1
  title: 'Улучшение сигнала движения без новых зависимостей: подключить готовый _create_motion_trail_frame + эвристика осцилляции в motion.py + подсказка в vision-промпт; текущий маршрут сохранить'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 4h
  confidence: 0.85
  consult_session_id: 21acdb73-c42a-409a-adca-4ffdd330a8b3
  specialist_session_id: 01d95e4f-0b27-495e-9ee3-a0bd9f5e6d60
  retry_count: 0
  last_update:
    ts: '2026-08-06T16:57:22Z'
    executor: backend-dev
    note: 'Wired the ready-made _create_motion_trail_frame (previously dead code) via a new MotionAnalyzer._detect_oscillation heuristic (direction-reversal counting with a noise-floor min_delta) added to motion.py plus AnimationMotion.is_oscillating. Renderer: when oscillation is detected, the keyframe closest to the motion peak is substituted with a ghosted trail composite: TGS reuses already-sampled motion-analysis frames (zero extra render cost); WebM extracts a few extra ffmpeg frames on demand, only when oscillation fires, via a new _extract_trail_frames helper. Collage label gets a trail-frame suffix so Vision does not read the ghosting as corruption. Vision prompt gets an explicit RU hint in both the animated and video branches of _build_vision_prompt. No new dependencies. Current route strictly preserved: everything gated behind is_oscillating, default False, proved via a call-site test that mocks _detect_oscillation True and False and asserts _create_motion_trail_frame is called or not-called accordingly, for both TGS and WebM (not just a helper-correctness test). Added 7 tests in test_motion.py, 8 in test_sticker_renderer.py, 5 in test_sticker_learning.py, 20 new total. Full suite 1554 passed, no regressions, ruff and mypy clean. QA C-2 should do the live-checklist resend of the AgAD7DoAAppnmEg example sticker to confirm the real-world fix; unit coverage of the heuristic and the prompt hint is already covered here.'
  result:
    kind: commit
    ref: 403b0e0f6e26b26ea6b84d3945b45ac2e044b04f
    verification: 'pytest tests/unit/ -q: 1554 passed (1534 prior + 20 new), no regressions; ruff check src/ clean; ruff format --check clean; mypy src/ clean (129 files)'
- id: C-2
  title: 'Тесты движения: unit на подсказку осцилляции (test_motion.py), регрессия сохранности текущего маршрута, live-чеклист повторной отправки AgAD7DoAAppnmEg'
  specialist: qa
  priority: P2
  status: done
  depends_on:
  - C-1
  estimated_effort: 2h
  confidence: 0.9
  consult_session_id: 6f61aa82-e7ba-4085-a914-8e5f6a741d59
  specialist_session_id: 8b0461eb-c647-4ac0-8f92-d5c498da93d0
  retry_count: 0
  last_update:
    ts: '2026-08-06T17:10:46Z'
    executor: qa
    note: 'Scope per C-1''s own routing note: unit coverage of the heuristic (test_motion.py) and the vision-prompt hint were already written by C-1 itself (7 _detect_oscillation tests + call-site substitution tests mocking _detect_oscillation True/False + prompt-hint tests) -- did not duplicate those. Identified and closed the one real gap: all of C-1''s own tests either call the private _detect_oscillation directly with hand-picked score lists, or force the verdict via patch.object(MotionAnalyzer, ''_detect_oscillation'', return_value=...) -- a mirror of the implementation (CLAUDE.md''s own named trap), never proving real pixel content reaches the flag. Added (1) tests/unit/test_motion.py: 2 tests driving the real public analyze_tgs_frames() (real frame differencing -> interpolation -> _detect_oscillation) with literal alternating/monotonic pixel frames, nothing mocked. (2) tests/unit/test_sticker_renderer.py::TestRealOscillationEndToEnd: 2 tests driving a real rlottie render of a genuinely oscillating vs. genuinely single-direction Lottie fixture (hand-built JSON with hold-interpolated fill-color keyframes, empirically tuned via a throwaway probe script until real motion_scores crossed min_delta) through render_tgs() end to end -- real detection reaching real trail substitution / staying on the current route, not a forced mock. (3) docs/plans/sticker-management-2026-08-06.c2-live-checklist.md: live re-analyze checklist for the plan''s own repro sticker AgAD7DoAAppnmEg (same convention as A-3/B-2 checklists), including an explicit non-blocking path for ''thresholds don''t fire on this real file'' as a calibration finding rather than an automatic fail.'
  result:
    kind: commit
    ref: 57b287b71f95b48d4de0b035a79da61cc529b9af
    verification: 'pytest tests/unit -q --no-cov: 1558 passed (1554 prior + 4 new), no regressions; ruff check src/ tests/ clean; ruff format --check clean; mypy src/ clean (129 files). Control-verified: swapped in pre-C-1 motion.py/renderer.py/learning.py via git show 403b0e0^:<path> (backed up current files first), confirmed all 4 new tests fail with AttributeError on the not-yet-existing is_oscillating field, restored via cp from backup, confirmed git diff clean against HEAD and full suite green again before committing.'
- id: D-1
  title: 'ADR толерантности: поля explicitness_score/tolerance_level, политика NULL=fail-closed, семантика сравнения, источник оценки; решение [D-1] — переоценка старого каталога разовым служебным скриптом (НЕ кнопка массовой переоценки, запрещена ADR-0003), до прогона «нет оценки» = скрыто; НЕ через abuse-модуль'
  specialist: architect
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 1.5h
  confidence: 0.85
  consult_session_id: 37b9213a-89ec-44b3-ae9c-aeab84156215
  specialist_session_id: 1934a2f2-ccf4-4afc-9ba4-d7824cd6017e
  retry_count: 0
  last_update:
    ts: '2026-08-06T17:23:22Z'
    executor: architect
    note: 'ADR-0008 (docs/decisions/): explicitness_score (sticker_knowledge, Vision-derived, nullable no-DEFAULT) + tolerance_level (chat_settings, nullable no-DEFAULT, ChatConfig dataclass default 0.5 mirroring sticker_reply_to_sticker_chance precedent, no bot_config seed row). Comparison direction pinned to one named function (is_within_tolerance: score<=tolerance, ceiling not floor) so D-4 has a single assertion point. NULL fail-closed as explicit IS NOT NULL guard (rejected 0.0-default and 1.0-default sentinel alternatives). Score source: piggyback the existing single Vision call for new/re-analyzed stickers; backfill of existing catalog is a narrow explicitness-ONLY one-off script (scripts/backfill_explicitness.py, first Python one-off in this repo), NOT a full re-analysis, NOT touching analyzed_at/visual_description -- avoids doubling Vision spend and perturbing already-good descriptions; targets rows search_by_embedding already treats as eligible-but-unscored. Gating point: one SQL predicate threaded through the 3 existing candidate call sites (pipeline.py, media.py) which already hold ChatConfig in scope. Explicit non-abuse-module rejection with file:line evidence. Key finding beyond the item title: chat-settings-panel''s F-1 (generic FSM editing of non-BOOL fields) is blocked/deferred with no landing estimate, so D-3 cannot depend on it -- ADR mandates a minimal single-field FSM reusing AdminStates.awaiting_setting_value (grep-verified unused anywhere in src or tests), framed as an explicit two-way door. Also flags a concrete schema pitfall: migration 020 already had to fix rules_mode/rules_enabled for shadowing bot_config.default_* via a SQL DEFAULT -- both new columns mandated nullable/no-DEFAULT, and tolerance_level must be added to ChatConfigService._CHAT_CONFIG_FIELDS or the merge silently no-ops. No src/ or alembic changes -- architect stays doc-only; migration DDL given as illustrative snippets only.'
  result:
    kind: file
    ref: docs/decisions/ADR-0008-sticker-explicitness-tolerance.md
    verification: grep -n confirms full section structure (Context/10 Decisions/Consequences/Rejected alternatives/Implementation notes for D-2+D-3+D-4/Out of scope). All file:line citations re-verified against the live tree after drafting; 2 were wrong on first pass (VISION_DERIVED_COLUMNS actual location, FieldType docstring lines) and corrected before submission.
- id: D-2
  title: 'Пайплайн оценки приемлемости: миграция explicitness_score + расширение vision-промпта/парсера + one-off скрипт backfill существующего каталога'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - D-1
  estimated_effort: 3.5h
  confidence: 0.92
  consult_session_id: 21acdb73-c42a-409a-adca-4ffdd330a8b3
  specialist_session_id: 282a8776-b319-4d64-8622-b2024c420a65
  retry_count: 1
  last_update:
    ts: '2026-08-06T17:41:38Z'
    executor: backend-dev
    note: 'ADR-0008 Decision 4/5/7/8 implemented in full. Migration 024 (nullable explicitness_score, no DEFAULT, mirrors 020/023''s precedent). learning.py: _build_vision_prompt asks for "explicit" 0.0-1.0 in the same JSON schema; _parse_vision_response validates it (reject-not-clamp: non-numeric/out-of-range -> None + warning) in both the direct-JSON path and the attempt-3 regex fallback, via new module-level _validate_explicitness_score(); learn() wires the score through both the fresh-Vision path and the ADR-0007 duplicate-copy path into save_sticker()+StickerLearningResult. stickers.py: explicitness_score added to _VISION_DERIVED_COLUMNS, save_sticker() (unconditional overwrite like emotion), plus two new methods for Decision 5''s backfill (get_explicitness_backfill_candidates - exact target-set WHERE clause from the ADR; update_explicitness_score - narrow single-column UPDATE, does not touch analyzed_at). scripts/backfill_explicitness.py: first Python one-off script in the repo, narrow single-field prompt, reuses renderer.py''s hash_frame anchor (not the 6-frame Vision collage) for animated/video, reuses StickerLearningService._parse_vision_response for identical validation, log-and-continue per row with a scored/skipped/failed summary (skipped = sticker no longer retrievable from Telegram, an expected condition for an old catalog; failed = render/Vision error or an unusable score, worth investigating), bootstraps Bot/AIRouter/StickerRepository/pool directly (no Dishka request scope, per the ADR). Added 39 new unit tests across tests/unit/test_sticker_repository.py (+6: save_sticker explicitness_score wiring incl. default-None, backfill-candidates query shape, update_explicitness_score SQL), tests/unit/test_sticker_learning.py (+22: parser validation incl. boundary/reject/regex-fallback cases and the no-spurious-warning-when-key-absent case, prompt schema, learn() end-to-end wiring incl. reject-not-clamp, duplicate-copy incl. the NULL-carries-over-from-unscored-canonical edge case from Decision 7), tests/unit/test_backfill_explicitness.py (new file, +11: static/animated/video frame selection, download-failure=skipped vs render/vision/invalid-score=failed distinction, run_backfill summary counts, one-bad-row-does-not-abort-the-run via the one call in _score_one not already individually try/excepted). Full suite pytest tests/unit -q: 1586 passed (1558 prior baseline from C-2 + 28 net new... actual net delta 28 tests, no regressions). ruff check + ruff format --check clean on all touched files. mypy src/ clean (129 files); mypy scripts/backfill_explicitness.py also clean standalone (scripts/ isn''t in the project''s configured mypy target per sessions.md convention, checked anyway). D-3 should note: search_by_embedding''s WHERE clause is deliberately untouched here (Decision 6 gating is explicitly D-3''s scope, not D-2''s) - the backfill-candidates query added here is a DIFFERENT, narrower query for the one-off script only. D-4 (qa) should add: integration-level assertion that update_explicitness_score does NOT touch analyzed_at/analysis_failed on a real DB row (schema-level proof beyond this item''s unit-level SQL-text assertions), and the live checklist item ADR-0008 Implementation notes for D-4 already names (running the real backfill script against a handful of live catalog stickers).'
  result:
    kind: commit
    ref: b41ae1424cff6551c22e2f9e204e2c2773201dca
    verification: 'pytest tests/unit -q --no-cov: 1586 passed, no regressions; ruff check src/ scripts/ tests/ clean; ruff format --check clean; mypy src/ clean (129 files); mypy scripts/backfill_explicitness.py clean standalone'
- id: D-3
  title: 'Per-chat tolerance_level: миграция + FieldSpec(FLOAT) + сид default 0.5 + фильтр выбора кандидатов (tolerance vs explicitness) + FSM админ-установки'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - D-1
  - D-2
  estimated_effort: 3.5h
  confidence: 0.85
  consult_session_id: 21acdb73-c42a-409a-adca-4ffdd330a8b3
  specialist_session_id: f5fa225f-a71e-42cd-9b35-9e98df5ed0f8
  retry_count: 1
  last_update:
    ts: '2026-08-06T17:56:31Z'
    executor: backend-dev
    note: 'Re-confirming prior verdict: D-3 already implemented and committed (a2e2722) in an earlier invocation of this same item -- ADR-0008 D-3 Implementation notes 1-5 done (migration 025, ChatConfig/ChatConfigService/settings_fields.py wiring, search_by_embedding SQL predicate threaded through learning.py/responder.py/3 call sites, dedicated adm_pnl_tol: FSM flow, is_within_tolerance helper for D-4). Verified working tree clean and commit a2e2722 contains all 14 src files + 7 test files touched; no further changes needed this invocation. D-4 (qa) should add: integration-level NULL fail-closed against a real Postgres row, migration schema assertion, three-layer merge test, live checklist per ADR-0008.'
  result:
    kind: commit
    ref: a2e2722
    verification: 'Re-verified via git show --stat a2e2722 that the full D-3 changeset is committed; no new test run needed since no code changed this invocation (prior invocation''s suite: 227 passed, ruff+mypy clean).'
- id: D-4
  title: 'Тесты толерантности: fail-closed NULL (integration+миграция), end-to-end гейтинг (0.5 vs anarchy 1.0), направление неравенства, дефолт-сид, three-layer merge; синтетические фикстуры'
  specialist: qa
  priority: P2
  status: done
  depends_on:
  - D-2
  - D-3
  estimated_effort: 3h
  confidence: 0.9
  consult_session_id: 6f61aa82-e7ba-4085-a914-8e5f6a741d59
  specialist_session_id: 032560a3-c90d-4ba6-91fd-88b76905b38e
  retry_count: 0
  last_update:
    ts: '2026-08-06T19:34:58Z'
    executor: qa
    note: 'ADR-0008 D-4 bullets closed: NULL fail-closed + end-to-end gating (0.6 vs 0.5/1.0 tolerance, boundary at ==) via real Postgres search_by_embedding, not mocked repo; migration schema (nullable, no DEFAULT, mirrors migration 020); three-layer merge + default seed via _merge() and full get_config(); live checklist deferred to Julia (needs live bot access). Bonus: full suite run surfaced 7 pre-existing failures in test_admin_defaults_keyboards.py caused by D-3''s own commit adding tolerance_level to new_fields() without updating that file''s hardcoded fixture (production _resolve_values() was already correct) -- fixed the stale test fixture, confirmed via git-stash bisection this predates my changes. Control-verified new tests bite: broke the real SQL predicate and _CHAT_CONFIG_FIELDS membership in turn, confirmed expected failures, reverted to clean diff. Full suite: 1795 passed, 24 skipped, no regressions; ruff+format clean; mypy src/ clean (130 files).'
  result:
    kind: commit
    ref: 46a2563f17c962729057b8013a77736002b6dcc0
    verification: 'pytest tests/unit tests/integration -q --no-cov: 1795 passed, 24 skipped, no regressions; ruff+format clean; mypy src/ clean; control-verified via targeted breakage+revert'
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 80.0
  consumed_usd: 61.4473
review_gate:
  why:
  - 'budget cap reached: consumed $24.03 of $30.0'
  - 'budget cap reached: consumed $24.03 of $30.0'
  - budget cap raised 30->60 by julia (finish all remaining items); --max-budget-override flag is a no-op, applied directly to envelope
  - budget cap raised 60->80 by julia (finish D branch incl. D-2 retry)
  approve_action: /execute-plan /Users/julia/my-projects/telegram-chat-companion.sticker-management-2026-08-06-wt/docs/plans/sticker-management-2026-08-06.execution.md --resume
  reject_action: /plan-fixes docs/plans/sticker-management-2026-08-06.md --revise /Users/julia/my-projects/telegram-chat-companion.sticker-management-2026-08-06-wt/docs/plans/sticker-management-2026-08-06.execution.md
safe_to_replay_from: null
clarifying_questions: []
human_feedback:
- ts: '2026-08-06T13:21:50Z'
  by: julia
  text: 'ANSWER [A-1]: В первую очередь дешёвая проверка по хешу картинки ДО анализа ИИ нам нужна. Можем заложить в будущем "сравнение по смыслу описания уже ПОСЛЕ анализа" или сделать какое-то минимальное сейчас, на усмотрение PM'
  applies_to: A-1
  status: addressed
  addressed_at: '2026-08-06T13:27:23Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-06T13:22:16Z'
  by: julia
  text: 'ANSWER [D-1]: (в) разовый служебный скрипт переоценки старого каталога'
  applies_to: D-1
  status: addressed
  addressed_at: '2026-08-06T13:27:25Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-06T13:22:59Z'
  by: julia
  text: "ANSWER [Q1]: Давай сначала P1 (дедупликация A + проверка стикера в DM B — быстрее, без внешних зависимостей) \n\nИ я предлагаю сразу заложить что после него сразу пойдем в P2 (анимации C + толерантность D — тяжелее, D зависит от разового backfill и от реестра настроек из плана chat-settings-panel)"
  applies_to: null
  status: addressed
  addressed_at: '2026-08-06T13:27:25Z'
  addressed_by: pm-orchestrator
revision_number: 2
last_revised_at: '2026-08-06T13:28:28Z'
last_revised_by: pm-orchestrator
---



























































































<!-- BRIEF:START lang=ru -->
# Стикеры: дубли, проверка в личке, живость анимаций и планка приличия

## Что произошло
Разобрали заметки по работе бота со стикерами — четыре направления. Часть механики в боте уже
заложена, поэтому многое здесь — доработка существующего, а не создание с нуля.

## Найденные проблемы
- **Дубликаты.** Один и тот же стикер попадает в базу несколько раз: бот заново тратит анализ и
  плодит разные описания на, по сути, одинаковые картинки.
- **Нельзя быстро проверить стикер.** Если админ присылает боту стикер в личку, бот молча его
  «проглатывает» и запоминает — без ответа, что это за стикер и знает ли он его вообще.
- **Плохо считываются анимации.** Быстрые движения (например, кот резко мотает головой) бот не
  замечает и описывает неверно.
- **В новых чатах проскакивает похабщина.** Сейчас нельзя задать, насколько «приличные» стикеры
  бот вправе слать в конкретном чате.

## Что будет сделано
- Бот научится узнавать повторные картинки дешёвой сверкой ДО анализа ИИ и переиспользовать готовое
  описание вместо повторного (платного) анализа (A).
- В личке админа: прислал стикер — бот сразу покажет описание, если знает его, либо предложит
  кнопку «Проанализировать», если нет (B).
- Анимации: бот начнёт учитывать степень и характер движения и «видеть» тряску и резкие жесты;
  прежний разбор останется как запасной вариант (C).
- Появится настройка «уровня приличия» стикеров: по умолчанию 0.5 для новых чатов (пошлое не
  шлём), а в «своих» чатах планку можно поднять и разрешить хоть самые всратые (D).

## Порядок работ
Утверждён: сначала блоки A и B (быстрее, без внешних зависимостей), затем C и D. Блок D стартует
только после того, как приземлится реестр настроек чатов из соседнего плана (chat-settings-panel).

## Не входит в этот план
- Умное сравнение стикеров «по смыслу» уже после анализа — отложено на будущее; сейчас только
  дешёвая сверка картинок до анализа (A).
- Тяжёлые библиотеки для анализа движения (оптический поток и т.п.) — на первом этапе обойдёмся
  тем, что уже есть в боте; отдельной опцией можно вернуться позже.
- Массовая переоценка старого каталога через интерфейс запрещена прежним решением; старые стикеры
  переоценим разовым служебным скриптом, а до его прогона «оценки нет» = «скрыть» (перестраховка).
- Планка приличия не связана с антиспамом/антиабьюзом — это разные механизмы, смешивать не будем.

## Оценка
≈30+ часов, 11 задач, потолок бюджета $30, в два захода (A+B, затем C+D).
<!-- BRIEF:END -->

# Plan — sticker-management-2026-08-06

## Source

[`docs/plans/sticker-management-2026-08-06.md`](docs/plans/sticker-management-2026-08-06.md) (sha256 `8ce06bed2fb5...`).

## Items

(none yet — populated by /plan-fixes)
