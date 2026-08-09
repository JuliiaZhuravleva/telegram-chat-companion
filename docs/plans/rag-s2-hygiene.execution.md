---
schema_version: 3
plan_id: rag-s2-hygiene
source_artifact:
  path: docs/plans/rag-s2-hygiene.md
  sha256: deaaeea76621225057b9263935aa24a2e1552a44421671d6c383129b973e7e55
  type: session-analysis
created_at: '2026-08-09T11:02:26Z'
approved_at: '2026-08-09T13:54:04Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: done
  started_at: '2026-08-09T15:52:19Z'
  completed_at: '2026-08-09T16:06:03Z'
  current_batch: null
  task_list_id: rag-s2-hygiene
items:
- id: S2-2
  title: 'Единый источник порога сходства: YAML — единственный источник истины; убрать дефолты 0.65 из конструктора и метода репозитория (и починить x or default → x if x is not None else default при консолидации)'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:48:26Z'
    executor: specialist-backend-dev
    note: dispatched
  result: null
- id: S2-6
  title: 'chat_memory: очистку по возрасту в этом слайсе НЕ включаем — данные не удаляем без предварительной сводки (инвариант S2-11). Правка = комментарий в _windows(), помечающий намеренное исключение chat_memory и ссылающийся на ADR'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - S2-11
  estimated_effort: 15m
  confidence: 0.95
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: 9b10da10-5e7a-4bce-ac56-490c3b9577bd
  retry_count: 0
  last_update:
    ts: '2026-08-09T16:05:23Z'
    executor: backend-dev
    note: 'Per ADR-0011''s explicit S2-6 implementation notes (chat_memory was never in RETENTION_TABLES, so no functional two-layer edit needed -- corrects the plan''s stale Revision-1 framing): added a doc-comment in RetentionCleaner._windows() citing ADR-0011''s data-preservation invariant, and a regression test (test_chat_memory_is_excluded_from_retention) asserting chat_memory is absent from both RETENTION_TABLES and _windows() output. Verified the test is not vacuous: it passes on unmodified code (nothing to regress against, since Revision-1''s age-based resolution was superseded before any code landed) but fails when chat_memory is experimentally re-added to RETENTION_TABLES, confirming it would catch a future accidental re-addition. No runtime behavior change, as ADR-0011 mandates for this item.'
  result:
    kind: commit
    ref: 5ade826
    verification: 'pytest tests/unit/test_retention_cleaner.py: 16 passed (was 15). Full pytest tests/unit/: 1759 passed, 5 skipped (was 1758). ruff check + ruff format --check on both touched files: clean. mypy src/: no issues, 130 files.'
- id: S2-3a
  title: 'ADR-0003: решить порядок сортировки KB (развести ранжирование выдачи и обрезку под бюджет)'
  specialist: architect
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 45m
  confidence: null
  consult_session_id: 2cc86a83-dace-4f68-b7a8-3b0f6762aff8
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:48:50Z'
    executor: pm-orchestrator
    note: 'Ревизия 1: Julia согласна с разводкой двух ключей сортировки — ранжирование выдачи по похожести и ранжирование для обрезки под бюджет по важности (salience). Решение архитектора (S2-3a: переписать пункт ADR-0003 Part 2) до правки SQL (S2-3b). Наивный флип ORDER BY ломает и обрезку под бюджет, и зелёный тест test_salience_wins_over_similarity — поэтому именно два ключа.'
  result: null
- id: S2-1
  title: 'Фолбэк эмбеддингов (вариант а): честно снять резерв — убрать openai из цепочки embeddings, задокументировать отсутствие 768-мерного резерва; лёгкий guard длины вектора перед записью оставить как дешёвую защиту'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - S2-2
  estimated_effort: 3h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:48:54Z'
    executor: pm-orchestrator
    note: 'Ревизия 1: Julia выбрала вариант (а) — убрать openai из цепочки embeddings, оставить gemini-only, задокументировать отсутствие 768-мерного резерва. Валидация размерности (нужная в основном для варианта б) больше не критична, но лёгкий guard длины вектора перед записью оставляем как дешёвую защиту (негативный контроль из разбора). Дополнительное пожелание Julia — фоновый дозапис упавших эмбеддингов — вынесено в новый пункт S2-10 (depends_on S2-1), чтобы вариант (а) не приводил к безвозвратной потере памяти при недоступности Gemini.'
  result: null
- id: S2-3b
  title: 'KB: применить решённый порядок сортировки в SQL + переписать ADR-заблокированный тест'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - S2-3a
  estimated_effort: 1h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:48:56Z'
    executor: null
    note: null
  result: null
- id: S2-4
  title: Один эмбеддинг запроса на ход для RAG и KB + проброс chat_id в логи стоимости (TD-009)
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - S2-1
  estimated_effort: 2h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:49:03Z'
    executor: null
    note: null
  result: null
- id: S2-5a
  title: 'delete_expired(): удалить как мёртвый код — ноль вызовов; инвариант сохранности (S2-11) окончательно закрывает развилку в пользу удаления (destructive-механизмы chat_memory в этом слайсе не подключаем)'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 20m
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:49:09Z'
    executor: null
    note: 'Ревизия 2 (консультация architect): удаление подтверждено и усилено. (1) Инвариант сохранности S2-11 разрешает прежнюю развилку delete_expired vs retention-по-возрасту в пользу удаления — ни один destructive-механизм chat_memory в этом слайсе не подключается. (2) expires_at нигде в src/ не пишется (INSERT в MemoryRepository.store() не содержит колонки), все строки NULL — delete_expired удалил бы 0 строк даже сейчас, живого риска нет. (3) Текущая форма (слепой DELETE ... WHERE expires_at < NOW()) не годится для будущего summary-gated удаления (нужно предусловие «сводка сохранена», а не голый TTL) — хранить мёртвую функцию «на будущее» смысла нет, будущий механизм проектируется заново против ADR S2-11. depends_on остаётся [].'
  result: null
- id: S2-5b
  title: Удалить мёртвый код ChatFact (не используется вне своего модуля)
  specialist: backend-dev
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 15m
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:49:12Z'
    executor: null
    note: null
  result: null
- id: S2-7a
  title: Интеграционный тест chat-scoping (privacy-инвариант) с обязательным негативным контролем
  specialist: qa
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: ba8f0cfa-a04c-4bbf-afc1-f51cedc268a5
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:49:00Z'
    executor: null
    note: null
  result: null
- id: S2-7b
  title: Юнит-покрытие RAGMemoryService (поведение при падении эмбеддинга, проброс порога/лимита)
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - S2-1
  - S2-2
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:49:15Z'
    executor: null
    note: null
  result: null
- id: S2-8
  title: '/kb view: проверять kb_enabled (фильтр/ранний ответ с текстом, не тихий return)'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 1h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:49:18Z'
    executor: null
    note: null
  result: null
- id: S2-9
  title: 'relevancy_check (вариант а): удалить мёртвую секцию конфига + переномеровать дублирующую запись TD-039 (роутер) на следующий свободный номер в _tech-debt.md; правку сигнатуры generate_text() вынести отдельным PR позже'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 1h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:49:23Z'
    executor: pm-orchestrator
    note: 'Ревизия 1: Julia подтвердила вариант (а) — удалить вводящую в заблуждение секцию relevancy_check из config/default.yml (дёшево, в общем PR). Правку роутера (параметр задачи в generate_text(), затрагивает все 5 вызовов в 4 файлах — вариант б) НЕ делаем в этом слайсе, выносим отдельным PR позже (возможно, с коротким ADR). Дополнительно (ответ на Q2): в _tech-debt.md номер TD-039 занят дважды (автоблэклист бытовых слов и роутинг generate_text) — при выполнении переномеровать запись про роутер на следующий свободный номер, чтобы ссылки на TD-039 не были двусмысленными. Правку _tech-debt.md делает исполнитель этого пункта, не PM.'
  result: null
- id: S2-10
  title: 'Фоновый бэкофилл эмбеддингов: воркер дозаписывает embedding для строк chat_memory, чей эмбеддинг упал в момент записи (новое пожелание Julia к варианту а S2-1)'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - S2-1
  estimated_effort: 4.5h
  confidence: 0.85
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: 3f47597e-4eff-49d9-bd2e-6f26eeab43a8
  retry_count: 0
  last_update:
    ts: '2026-08-09T15:54:45Z'
    executor: backend-dev
    note: 'Implemented per Julia''s [S2-1] follow-up + the plan''s Revision-1 scope note: EmbeddingBackfillWorker (src/services/rag/backfill.py, mirrors RetentionCleaner''s start/stop/run_once lifecycle, constructed directly in main.py per the ADR ''process-lifetime singletons via dp[]/main(), not Dishka'') retries chat_memory rows whose embedding is NULL. RAGMemoryService.store() (src/services/rag/memory.py) now persists content with embedding=None instead of dropping it when generate_embedding() raises (S2-1 made Gemini the only embeddings provider, so an outage previously meant silent permanent data loss) -- the wrong-dimensionality guard path is untouched (still refuses to store, deliberate S2-1 behavior, out of this item''s scope per the plan note). MemoryRepository gained get_pending_embeddings()/update_embedding(); store()''s embedding param is now Optional. No migration: NULL is the natural pending marker, matching the plan''s scope note. New EmbeddingBackfillSettings (config.py + config/default.yml: enabled/interval_seconds/batch_limit, mirrors MaintenanceSettings) wired into main.py''s startup/shutdown alongside health_checker/sticker_sync/retention_cleaner. Retries are uncapped (embeddings are free), per scope. Rewrote S2-7b''s TestStoreEmbeddingFailure (behavior it asserted -- drop-on-failure -- is exactly what this item changes) and added tests/unit/test_embedding_backfill.py (worker run_once: fills on success, leaves pending on provider failure/wrong-dimension/repo-write-failure, one-bad-row-does-not-stop-batch, batch_limit forwarding, lifecycle start/stop; plus repository-level tests for get_pending_embeddings/update_embedding/store(embedding=None) against a mocked pool) and config regression tests for the new settings section. IMPORTANT PROCESS NOTE for PM: this item''s actual code (src/config.py, src/database/repositories/memory.py, src/services/rag/memory.py, src/services/rag/backfill.py, src/main.py, config/default.yml + most of the tests) landed already-committed in ed70195 (''restore envelope bookkeeping lost to a pre-commit stash cycle'') -- a prior recovery pass on this same item bundled my uncommitted S2-10 diff into its own commit rather than S2-10 having its own. I verified the bundled content matches this design exactly (git show --stat ed70195) and only had a trivial ruff-format touch-up left (commit 17be181, test files only). Not a re-do; flagging so the PM doesn''t expect a dedicated S2-10 commit in git log. Routing hint: no browser/live QA done -- this is a background worker with no direct handler/UI surface, and reliably triggering a real Gemini outage isn''t practical in manual QA; if qa wants integration coverage it would be a testcontainers test asserting a NULL-embedding row becomes searchable after EmbeddingBackfillWorker.run_once() against real Postgres (mirrors S2-7a''s chat-scoping integration test).'
  result:
    kind: commit
    ref: 17be181
    verification: 'pytest tests/unit/ -q --no-cov: 1758 passed, 5 skipped (up from 1743 pre-item, consistent with the 15 new/changed tests: 12 in test_embedding_backfill.py, 2 rewritten + 0 net-new in test_rag_memory_service.py''s TestStoreEmbeddingFailure, 3 new in test_config.py). ruff check src/ + the touched test files: clean. ruff format --check on all 8 touched/added files: already formatted. mypy src/: no issues, 130 files.'
  budget_checkpoint:
    count: 1
    spent_usd: 3.2411
    session_id: 3f47597e-4eff-49d9-bd2e-6f26eeab43a8
    source: budget_death
    state: resolved
    ts: '2026-08-09T15:43:20Z'
  max_usd_override: 7.0
- id: S2-11
  title: 'ADR-инвариант сохранности данных памяти: строки памяти (chat_memory и будущий chat_chunks) нельзя безвозвратно удалять, не сохранив предварительно высокоуровневую сводку; реализацию самой сводки сейчас не делаем (отдельный ADR, только фиксация решения)'
  specialist: architect
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 30m
  confidence: 0.85
  consult_session_id: 2cc86a83-dace-4f68-b7a8-3b0f6762aff8
  specialist_session_id: 3e5b5912-79aa-4e48-a6b6-df2746be8edf
  retry_count: 0
  last_update:
    ts: '2026-08-09T16:02:09Z'
    executor: architect
    note: 'ADR-0011 (docs/decisions/ADR-0011-memory-data-preservation-invariant.md, commit 2423ec6) records the invariant from Julia''s [S2-6b] answer: chat_memory (and future chat_chunks) rows must not be irrecoverably deleted by an automatic/bulk process without a prior high-level summary; explicit carve-outs for never-stored rows (S2-1 dimension guard), user-directed single-record erasure (RAGMemoryService.delete(), currently dead code), non-prod DBs, and chat_facts/KB (different table, out of this item''s title). Distinguished explicitly from rag-revision-2026-08.md §6''s ''Digest/summary tier'' non-goal (different purpose: retrieval feature vs. preservation artifact) so a future reader doesn''t think §6 already settled this. Gates the S6 roadmap line (''chat_memory drop migration scheduled'') and gives S2-6 (still pending, depends_on this item) exact guidance: no functional RETENTION_TABLES change needed (chat_memory was never in that dict — this corrects a stale two-layer-edit framing left over from the plan''s superseded Revision-1 resolution), only a comment in _windows() citing ADR-0011 (snippet provided). Also flagged as tech debt, not fixed here: vestigial chat_memory.expires_at column, and rag-revision-2026-08.md''s S6 row text not yet citing this ADR. PROCESS NOTE for PM: picked ADR-0011, not ADR-0010 — checked concurrent branch plan/admin-ux-and-summary-2026-08-09, which already used both ADR-0009 (manual-explicitness-override) and ADR-0010 (chat-panel-grouped-navigation) independently of this branch''s own ADR-0009 (kb-retrieval-vs-budget-trim-ordering). No shared ADR-number allocator across concurrent plan branches -- both branches will collide on 0009 at merge regardless of what I do here; 0011 only avoids adding a third collision. Worth a lightweight registry/lock if concurrent plan branches doing ADR work becomes routine.'
  result:
    kind: commit
    ref: 2423ec6
    verification: ADR-0011 committed as 2423ec6 (git show --stat confirms only the ADR file, 224 lines, in that commit); git status after commit shows no other files staged/modified by this session. Content cross-checked against src/services/maintenance/cleanup.py (_windows), src/database/repositories/maintenance.py (RETENTION_TABLES), src/database/repositories/memory.py (store/delete/expires_at), src/services/rag/memory.py (delete(), S2-1 dimension guard), docs/plans/rag-revision-2026-08.md §4.1/§5/§6, and the execution.md item history for S2-5a/S2-6/S2-10 to ground every factual claim (dead-code status of delete_expired()/RAGMemoryService.delete(), expires_at always NULL, chat_chunks not yet migrated).
budget:
  max_usd_per_item: 4.0
  max_usd_per_plan: 45.0
  consumed_usd: 32.9802
review_gate:
  why:
  - 'item S2-10: per-item cap bumped $2.00→$4.00 — budget-consult: No commit yet, but git status/diff on the worktree shows the full 4-point scope from the consult note already landed uncommitted: new src/services/rag/backfill.py (147 lines, mirrors RetentionCleaner as specified) + tests/unit/test_embedding_backfill.py (234 lines), plus matching edits to config.py/main.py/repositories/memory.py/rag/memory.py for the null-embedding store path; running the affected unit tests gives 52 passed with 89-95% coverage on the new/changed modules. limit_hit=budget with turns_used only 78/150 confirms a sizing miss (4.5h estimated item vs $2 cap), not thrashing, and plan headroom ($16.8 of $20) easily covers finishing (commit + lint/mypy + envelope note); remainder of the current cap is negative so resume_with_remainder doesn''t fit, hence bump to the 2x-original ceiling.'
  - 'plan budget cap changed $20.00→$45.00 (consumed $3.61) — restoring after the envelope reverted to its committed state during S2-10 run 1: 11 done items, both caps and consumed_usd were lost; work commits intact'
  - 'item S2-10: per-item cap bumped $4.00→$7.00 — override was set to 4.0 against the reverted base cap of 2.0, leaving only 0.76 to finish; with checkpoint count already 1 a second death is a hard halt, and the work (backfill.py + tests, 52 passing) sits uncommitted'
  approve_action: /execute-plan <projects>/telegram-chat-companion.rag-s2-hygiene-wt/docs/plans/rag-s2-hygiene.execution.md --resume
  reject_action: /plan-fixes docs/plans/rag-s2-hygiene.md --revise <projects>/telegram-chat-companion.rag-s2-hygiene-wt/docs/plans/rag-s2-hygiene.execution.md
safe_to_replay_from: null
clarifying_questions: []
human_feedback:
- ts: '2026-08-09T11:10:36Z'
  by: julia
  text: 'ANSWER [S2-1]: (а) честно убрать резерв — выкинуть openai из цепочки эмбеддингов и задокументировать, что резерва нет, пока нет второй 768-мерной модели


    И я бы ещё сделала систему, которая фоново прогоняет эмбединги которые не удалось сделать сразу.'
  applies_to: S2-1
  status: addressed
  addressed_at: '2026-08-09T11:41:39Z'
  edited_at: '2026-08-09T11:25:33Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:25:54Z'
  by: julia
  text: 'ANSWER [S2-2]: Давай один источник — YAML'
  applies_to: S2-2
  status: addressed
  addressed_at: '2026-08-09T11:41:43Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:26:43Z'
  by: julia
  text: 'ANSWER [S2-3]: согласна'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T11:41:46Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:29:23Z'
  by: julia
  text: 'ANSWER [S2-6]: По возрасту я согласна, правда я бы перед удалением делала какую-то историческую память по удаляемому периоду, чтобы хотя бы верхнеуровнево хранилась память'
  applies_to: S2-6
  status: addressed
  addressed_at: '2026-08-09T11:41:49Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:29:46Z'
  by: julia
  text: 'ANSWER [S2-9]: Да, давай'
  applies_to: S2-9
  status: addressed
  addressed_at: '2026-08-09T11:41:52Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:30:03Z'
  by: julia
  text: 'ANSWER [Q1]: да'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T11:41:55Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:30:23Z'
  by: julia
  text: 'ANSWER [Q2]: давай'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T11:41:59Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T13:40:33Z'
  by: julia
  text: 'ANSWER [S2-6b]: Я бы в принципе заложила историческую сводку в архитектуру. Не обязательно это прямо сейчас делать. Мне не важно какие таблицы что делают, мне важно чтобы мы не удаляли данные, которые потом никак не восстановить и ничего из них не запомнили.'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T13:51:08Z'
  addressed_by: pm-orchestrator
revision_number: 3
last_revised_at: '2026-08-09T13:53:06Z'
last_revised_by: pm-orchestrator
---





































































































<!-- BRIEF:START lang=ru -->
# Память и база знаний бота: чиним корректность и наводим порядок (слайс S2)

## Что произошло
Разобрали технический слайс развития долгосрочной памяти бота и его базы знаний.
Это в основном приведение уже работающего механизма в честное и предсказуемое
состояние — чтобы следующий этап (замеры и калибровка качества ответов) вообще
что-то значил. Нашли 9 проблем; по вашим ответам все решения приняты, по вашим
пожеланиям добавлены две задачи, открытых вопросов не осталось.

## Найденные проблемы
- **Резерв памяти не работает.** Если основной поставщик недоступен, запись
  памяти и поиск по ней молча отключаются — при этом настройки выглядят так,
  будто резерв настроен. «Очевидная» починка сделала бы хуже: пошли бы ошибки на
  живом трафике.
- **Правило приватности ничем не защищено.** «Память одного чата никогда не
  попадает в другой» держится на одной строке и не покрыто ни одной
  автоматической проверкой.
- **Порог релевантности задан в четырёх местах** с разными значениями. Работает
  один, но это мина для любого следующего изменения.
- **База знаний сломается на следующем этапе:** начнёт выдавать «самые важные»
  факты вместо тех, что относятся к вопросу.
- **Просмотр базы знаний игнорирует выключатель:** команда показывает факты чата,
  даже когда модуль знаний для него отключён в админке.
- **Старая память со временем накапливается, а удалять её сейчас нельзя** — не
  потеряв безвозвратно то, что уже ниоткуда не восстановить.
- **Лишняя задержка в ответе** (запрос обрабатывается дважды за ход) и слепая
  статистика расходов по чатам.
- **Мёртвый код и вводящая в заблуждение секция настроек**, которые следующий
  читатель примет за рабочие.

## Что будет сделано
- Резерв памяти уберём честно: при недоступности основного поставщика бот больше
  не делает вид, что резерв настроен, и не сыплет ошибками на живом трафике. (S2-1)
- Появится фоновый механизм, который позже дозаписывает память, не сохранённую
  из-за временного сбоя, — она не теряется безвозвратно. (S2-10)
- Появится тест, который ломается, если приватность чатов нарушат; плюс базовое
  покрытие поведения памяти. (S2-7a, S2-7b)
- Порог релевантности станет один, из одного места. (S2-2)
- База знаний останется корректной и после включения оценки важности фактов;
  решение зафиксируем документом. (S2-3a, S2-3b)
- Просмотр базы знаний начнёт уважать выключатель и отвечать понятным текстом, а
  не молчанием. (S2-8)
- Зафиксируем правило: старую память нельзя удалять безвозвратно, пока с неё не
  снята короткая высокоуровневая сводка. Поэтому автоочистку этой памяти сейчас
  сознательно НЕ включаем (данные не теряем), а неиспользуемый код удаления
  убираем. (S2-11, S2-6, S2-5a)
- Ответ станет чуть быстрее, а расходы — видимыми по чатам. (S2-4)
- Уберём мёртвый код и наведём порядок в настройках. (S2-5b, S2-9)

## Не входит в этот план
- Смена архитектуры поиска (гибридный поиск, чанки, калибровка порога по
  эталонному набору) — это следующие слайсы S3–S6.
- Автосбор базы знаний остаётся на паузе; здесь только готовим для него почву.
- Ревизия порога автобана бытовых слов — ведётся отдельно.
- Сам механизм «исторической сводки» перед удалением памяти сейчас не строим — по
  вашему решению это закладывается как принцип (архитектурное правило), а
  реализуется тогда, когда память будет выводиться из эксплуатации на следующих
  этапах, чтобы не делать одноразовую работу. До тех пор ничего невосстановимого
  не удаляем.
- Переработка адресации провайдера в relevancy_check — отдельным PR позже; в этом
  слайсе только убираем вводящую в заблуждение секцию настроек.

## Оценка
Около 18 часов работы по 14 задачам; потолок бюджета — $20.
<!-- BRIEF:END -->

# Plan — rag-s2-hygiene

## Source

[`docs/plans/rag-s2-hygiene.md`](docs/plans/rag-s2-hygiene.md) (sha256 `deaaeea76621...`).

## Заметки PM (синтез — техническая часть, не для заказчика)

Составлено из консультаций трёх специалистов (architect, backend-dev, qa) по HEAD
на 2026-08-09. Исходные 9 пунктов S2-1…S2-9 сохранены; три из них разбиты, потому
что внутри одного ID жили две задачи с разными исполнителями и/или зависимостями.

### Разбиения (мои, не из источника)
- **S2-3 → S2-3a (architect) + S2-3b (backend-dev).** Пункт трогает
  зафиксированное решение ADR-0003, а не только SQL. Сначала решение архитектора,
  потом правка кода (`S2-3b depends_on S2-3a`).
- **S2-5 → S2-5a (delete_expired) + S2-5b (ChatFact).** delete_expired
  завязан на выбор политики в S2-6 (`S2-5a depends_on S2-6`); ChatFact —
  независимая тривиальная чистка.
- **S2-7 → S2-7a (qa, интеграционный privacy-тест) + S2-7b (backend-dev, юниты
  RAGMemoryService).** По конвенции проекта qa владеет tests/integration/,
  backend-dev — tests/unit/. Юниты имеют смысл только после S2-1/S2-2
  (`S2-7b depends_on S2-1, S2-2`), а privacy-тест независим и не должен их ждать.

### Порядок на общих файлах (во избежание конфликтов слияния)
S2-2 → S2-1 → S2-4 и S2-7b — все трогают память/роутер; выстроены цепочкой
через depends_on. S2-8, S2-5b, S2-7a, S2-3a независимы и могут идти параллельно.

### Расширения области, которых нет в тексте источника (важно исполнителю)
- **S2-6 — два слоя, а не один:** помимо списка окон очистки правку нужно внести
  и в жёсткий белый список таблиц (защита от SQL-инъекции); тест обязан
  проверять оба. Есть готовый паттерн `_EXEMPT_BY_DESIGN` для зеркалирования.
- **S2-3 — сцепка с обрезкой под бюджет:** сборка фактов под бюджет структурно
  опирается на текущий порядок сортировки. Наивный флип `ORDER BY` ломает не
  только релевантность выдачи, но и обрезку, плюс уже зелёный ADR-тест
  `test_salience_wins_over_similarity`. Решение S2-3a должно развести два ключа.
- **S2-1 — есть образец рядом:** `generate_text()` уже реализует правильный
  паттерн (модель из конфига только для основного провайдера, `None` для
  резервов); `generate_embedding()` этой index-awareness лишён — это и есть форма
  бага. Мириться с 768 vs 1536 всё равно придётся (у OpenAI нет сравнимого
  768-мерного резерва без усечения пространства) — образец не снимает развилку
  S2-1 (а/б).
- **S2-2 — не переносить баг:** оба места используют `x or default` вместо
  `x if x is not None else default`; при консолидации это надо починить, иначе
  явный `min_similarity=0.0` от будущего вызывающего снова молча перезатрётся.
- **S2-8 — есть готовый паттерн в том же файле** (`test_kb_disabled` для
  /remember): зеркалировать локализованный ответ, не «тихий return».

### Допущения headless-прогона (подтвердить или оспорить)
- Приоритеты P1/P2 и разбиение на PR проставлены мной; нет P0 — ни один дефект
  сейчас не роняет прод (S2-1 деградирует молча, опасна именно наивная починка).
- Оценки эффорта — медиана по трём специалистам; у S2-1 и S2-9 разброс большой,
  т.к. цена зависит от выбранного варианта развилки.

### Стоимость консультаций
architect $1.13 + backend-dev $1.03 + qa $0.79 ≈ $2.95 (< 50% потолка плана).
Сессии консультаций записаны в `consult_session_id` каждого пункта (для --revise).

## Ревизия 1 — разрешённые решения (ответы Julia)

- **S2-1 → вариант (а).** Убрать openai из цепочки embeddings, gemini-only,
  задокументировать отсутствие 768-мерного резерва. Лёгкий guard длины вектора
  оставляем как дешёвую защиту.
- **S2-10 (новый пункт по просьбе Julia).** Фоновый воркер дозаписывает
  эмбеддинги, упавшие в момент записи, — чтобы вариант (а) не приводил к
  безвозвратной потере памяти. Скоуп подтверждён консультацией backend-dev:
  миграция не нужна (NULL-embedding = естественный маркер pending), зеркалит
  RetentionCleaner, error-path store() расширяется до записи строки с
  `embedding=None`, ретраи без ограничения (эмбеддинги бесплатны), ~4.5h,
  depends_on S2-1.
- **S2-2 → один источник, YAML.** YAML — единственное место значения порога;
  дубли 0.65 убрать; попутно `x or default` → `x if x is not None else default`.
- **S2-3 → согласовано.** Развести два ключа сортировки (похожесть для выдачи,
  важность для обрезки под бюджет); решение архитектора S2-3a до кода S2-3b.
- **S2-6 → retention по возрасту (вариант а).** Оба слоя (`_windows` +
  `RETENTION_TABLES`). Как следствие — `delete_expired` не подключается, а
  удаляется (S2-5a больше не зависит от S2-6). Пожелание «историческая память
  перед удалением» — вынесено в открытый вопрос **[S2-6b]** (конфликтует со
  stopgap-рамкой таблицы).
- **S2-9 → вариант (а).** Удалить мёртвую секцию `relevancy_check`; правку
  роутера (`generate_text()`) — отдельным PR позже. Плюс (ответ на Q2)
  переномеровать дублирующий TD-039 (роутер) при выполнении S2-9 —
  правку `_tech-debt.md` делает исполнитель, не PM.
- **Q1 → подтверждено.** Отдельные PR: S2-3 (решение ADR) и S2-6 (очистка
  прод-таблицы); остальное — одним PR. (Роутерный PR из S2-9 отложен, поэтому в
  дроблении не участвует.) Ни один пункт не требует миграции схемы.

## Items

Полный список из 13 пунктов — во frontmatter (`items[]`). Единственное открытое
решение — **[S2-6b]** в `clarifying_questions[]` (виджет «нужно решить» под брифом).
