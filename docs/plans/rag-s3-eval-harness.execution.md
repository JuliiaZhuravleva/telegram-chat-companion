---
schema_version: 3
plan_id: rag-s3-eval-harness
source_artifact:
  path: docs/plans/rag-s3-eval-harness.md
  sha256: 60e3ca4fb61fa90844113e3ed02f96e04ab9048b10959e82caea401123357a2d
  type: session-analysis
created_at: '2026-08-09T20:47:30Z'
approved_at: '2026-08-09T21:37:25Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: done
  started_at: '2026-08-09T21:38:37Z'
  completed_at: '2026-08-09T23:17:48Z'
  current_batch: null
  task_list_id: rag-s3-eval-harness
items:
- id: S3-1
  title: Схема кейса (chat_id, вопрос, asked_at [обязателен], ожидаемые диапазоны message_id, страта, свободная заметка) + трекаемый синтетический шаблон в tests/fixtures/eval/ с выдуманными чатами; шаблон валидируется ТОЙ ЖЕ схемой, что и настоящий internal/eval/cases.json (один общий валидатор-модуль, чтобы не разошлись)
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 2h
  reasoning_effort: null
  confidence: 0.9
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: 3b62d442-a71f-4eea-bac7-1388b4ffdd98
  retry_count: 0
  last_update:
    ts: '2026-08-09T21:43:45Z'
    executor: backend-dev
    note: 'Added scripts/eval_schema.py: one pydantic EvalCase model (chat_id, question, asked_at [required, tz-aware], expected_message_id_ranges, stratum found|knowledge-update|answer-absent, note) used by both the tracked template tests/fixtures/eval/cases.json and the real (gitignored) internal/eval/cases.json — load_cases() is the single entry point both paths must use. Enforces: asked_at required+tz-aware (S3-3 needs it for the before-filter), expected_message_id_ranges empty iff stratum=answer-absent (S3-5 negative control), range end>=start. CLI (python3 scripts/eval_schema.py <file>...) for manual validation, mirrors scripts/check_plan_artifacts.py''s main() shape. Template has 4 fictional cases covering all 3 strata + a multi-range case. Downstream items should depend on this: S3-2''s eval_rag.py and S3-8''s tests (qa) should import EvalCase/load_cases from scripts/eval_schema.py rather than re-implementing parsing — flagging for PM/qa routing awareness. S3-8 (qa) still needs the recall@k/MRR arithmetic tests and the degradation control described in the plan; this item only covers the schema + template + schema''s own unit tests.'
  result:
    kind: commit
    ref: b9ae19e9895cd19882bab9bb15bd48e67005d2df
    verification: 'python3 -m pytest tests/unit/test_eval_schema.py -q: 19 passed; ruff check + ruff format --check clean on scripts/eval_schema.py, tests/unit/test_eval_schema.py, tests/fixtures/eval/; mypy scripts/eval_schema.py: Success (mypy strict, matches CI scope which only checks src/); CLI sanity: python3 scripts/eval_schema.py tests/fixtures/eval/cases.json -> OK, 4 cases, {found:2, knowledge-update:1, answer-absent:1}'
- id: S3-3
  title: 'Необязательный time-bound before: datetime|None=None по НАСТОЯЩЕМУ пути поиска (MemoryRepository.search → RAGMemoryService.search): условие в WHERE ДО LIMIT, а не постфильтрация (она молча уменьшает k). None-check, не ''x or default'' (паттерн S2-2). Без миграции, по умолчанию None — поведение прода не меняется. Единственный прод-вызов: pipeline.py:491. Без ADR (аддитивно, обратно совместимо)'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1-1.5h
  reasoning_effort: null
  confidence: 0.9
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: 94a0ff73-60cc-4d69-b81e-08d786acc4b1
  retry_count: 0
  last_update:
    ts: '2026-08-09T21:47:46Z'
    executor: backend-dev
    note: 'Added optional before: datetime|None=None to MemoryRepository.search() and RAGMemoryService.search(), applied in WHERE ahead of LIMIT (not postfiltered). Default None, additive, no migration. Confirmed the only prod caller (TextProcessingPipeline._timed_rag_search, pipeline.py:491) does not pass it, so prod behavior is unchanged. Followed the S2-2 None-check convention (not ''x or default'') for the passthrough. Unit tests cover: service-level passthrough of explicit before and of the None default (must reach repo as None, not be swallowed), and repository-level call-args assertions (before is the last bind param, defaults to None). Did NOT write an integration test for the actual WHERE-before-LIMIT SQL ordering (that the time bound doesn''t consume a LIMIT slot before being applied) -- that needs a real Postgres+pgvector fixture with future-dated rows, same pattern as tests/integration/test_memory_repository_chat_scoping.py. Routing hint: qa should add that integration test before S3-2''s harness leans on this parameter for correctness, mirroring the chat-scoping test''s positive+negative-control structure.'
  result:
    kind: commit
    ref: 8cbcdee
    verification: 'pytest tests/unit/test_rag_memory_service.py -q: 21 passed; pytest tests/unit/ -q: 1958 passed, 5 skipped; ruff check src/database/repositories/memory.py src/services/rag/memory.py tests/unit/test_rag_memory_service.py: All checks passed; mypy src/: Success, no issues in 131 source files'
- id: S3-2
  title: 'scripts/eval_rag.py дёргает настоящий RAGMemoryService.search() (тот же вход, что у пайплайна), а не переписанный SQL как в q5_replay.py; эмбеддинг запроса через настоящий провайдерский путь AIRouter(settings) (Dishka не нужен), БЕЗ сырого-HTTP-фолбэка (решено по [Q2]); seed-DSN = throwaway-контейнер rag-analysis-seed (порт 55434), обязательный аргумент CLI без дефолта — нельзя случайно навести на живую базу (решено по [Q1]). Побочно: флаг rag_backend на S5 переключается тут ровно как в проде'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - S3-1
  - S3-3
  estimated_effort: 3h
  reasoning_effort: null
  confidence: 0.85
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: 48f679c4-23cb-45f4-9b7e-66b8219e7c1a
  retry_count: 0
  last_update:
    ts: '2026-08-09T22:43:59Z'
    executor: backend-dev
    note: 'scripts/eval_rag.py replays EvalCase (S3-1) through RAGMemoryService.search() -- same entry point TextProcessingPipeline uses, no reimplemented SQL. Embeds via AIRouter(settings) real provider path (no Dishka, no raw HTTP -- Q2), passes before=case.asked_at (S3-3). Seed DSN is a required CLI positional, no default (Q1). Side-add: MemoryRepository.search()/RAGMemoryService.search() now also return source_message_id (additive SELECT, WHERE/ORDER/LIMIT unchanged) -- without it S3-4 cannot match a retrieved memory to expected_message_id_ranges for recall@k; flagging this dependency explicitly since S3-4''s item text doesn''t mention it. run_eval() -> list[CaseResult] is the stable contract for S3-4 to build recall@k/MRR on top of; this item deliberately stops at raw per-case hits, no metrics arithmetic. Did not write an integration test for the new source_message_id column against a real DB (existing tests/integration/test_memory_repository_chat_scoping.py covers search() with testcontainers but I didn''t extend it) -- qa (S3-8) already owns the pgvector-testcontainer integration coverage for this search path and should assert source_message_id round-trips for real.'
  result:
    kind: commit
    ref: 2a64b11
    verification: 'pytest tests/unit/test_eval_rag.py tests/unit/test_rag_memory_service.py -q: 33 passed; pytest tests/unit/ -q: 1970 passed, 5 skipped; ruff check + ruff format --check clean on scripts/eval_rag.py, tests/unit/test_eval_rag.py, tests/unit/test_rag_memory_service.py, src/database/repositories/memory.py, src/services/rag/memory.py; mypy src/: Success, 131 files; mypy scripts/eval_rag.py: Success'
- id: S3-4
  title: 'Метрики: recall@k — первичная, MRR — вторичная; доля кейсов с пустой выдачей выше порога (аналог ''bot answers blind'', сегодня 7 из 11); распределение best-sim (перцентили) — собирается бесплатно тем же прогоном, на нём S6 будет калибровать порог'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - S3-2
  estimated_effort: 2h
  reasoning_effort: null
  confidence: 0.85
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: 6b018fc2-2f86-4b39-a6bb-eb596e4d4148
  retry_count: 0
  last_update:
    ts: '2026-08-09T22:51:54Z'
    executor: backend-dev
    note: 'scripts/eval_metrics.py adds recall@k (primary), MRR (secondary), blind_rate (found/knowledge-update empty-result analog of q5_replay''s ''bot answers blind'', 7/11), negative_control_rate (answer-absent correctly-empty share, kept separate from blind_rate per S3-5), and best_sim_percentiles (linear-interpolation percentiles over every case with >=1 hit, for S6). Design call flagged for architect: EvalCase (S3-1) has no field marking which expected_message_id_ranges entry is authoritative for knowledge-update cases -- this module treats the LAST-listed range as freshest/correct and earlier ranges as superseded (a hit landing only on an earlier range is a miss), matching the fixture''s note but not yet schema-enforced. Worth formalizing once S3b''s real golden set has more than one knowledge-update case. eval_rag.py''s CLI now prints the metrics summary after per-case output. No integration coverage needed here -- pure arithmetic over CaseResult, no DB/provider calls; qa''s S3-8 testcontainer coverage is unaffected.'
  result:
    kind: commit
    ref: 3a66523
    verification: 'pytest tests/unit/test_eval_metrics.py tests/unit/test_eval_rag.py -q: 31 passed; pytest tests/unit/ -q: 1991 passed, 5 skipped; ruff check + ruff format --check clean on scripts/eval_metrics.py, scripts/eval_rag.py, tests/unit/test_eval_metrics.py; mypy src/: Success, 131 files (CI''s only mypy gate, ci.yml:48). mypy on scripts/ directly hits a pre-existing ''Source file found twice'' namespace-package quirk (scripts/ has no __init__.py) that reproduces even on S3-2''s original eval_rag.py+eval_schema.py pair alone, before this item''s changes -- not CI-gated, not introduced here.'
- id: S3-5
  title: 'Страты found / knowledge-update / answer-absent. answer-absent — негативный контроль всего эвала: правильное поведение = пустая выдача, метрика ОБЯЗАНА раздельно показывать ''нашли что просили'' и ''не выдумали, когда нечего было находить''. Без неё снижение порога всегда выглядит улучшением и калибровка на S6 сходится к нулю'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - S3-1
  estimated_effort: 1.5h
  reasoning_effort: null
  confidence: 0.9
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: 0556eeb1-fac4-4f68-b3de-db450675440c
  retry_count: 0
  last_update:
    ts: '2026-08-09T22:55:14Z'
    executor: backend-dev
    note: 'No code delta required: S3-5''s full scope is already implemented and PASS-verified by two prior items in this plan. (1) S3-1 (commit b9ae19e, scripts/eval_schema.py) defines Stratum = found|knowledge-update|answer-absent and a model_validator that REJECTS a non-empty expected_message_id_ranges on answer-absent and REJECTS an empty one on found/knowledge-update -- tests/fixtures/eval/cases.json (the tracked template) already carries one case per stratum, with the answer-absent case''s note citing S3-5 by name. (2) S3-4 (commit 3a66523, scripts/eval_metrics.py) computes recall_at_k/mrr over found+knowledge-update cases and a SEPARATE negative_control_rate over answer-absent cases (never conflated with blind_rate), which is exactly S3-5''s requirement: ''раздельно показывать нашли что просили и не выдумали когда нечего было находить''. S3-4''s own specialist notes say this was done ''per S3-5'', and its evaluator verdict confirms the same. I re-ran the full test suite (test_eval_schema.py + test_eval_metrics.py + test_eval_rag.py, 50 passed) plus ruff+mypy to confirm the existing implementation is still green -- did not find any gap in S3-5''s stated scope to add code for. Routing hint for PM: S3-5 appears to have been fully absorbed by S3-1+S3-4''s implementers reading the same source doc; worth checking S3-6/S3-7/S3-8 for similar overlap before dispatching, and marking S3-5 done-via-prior-item in the human plan doc (rag-s3-eval-harness.md) rather than needing a distinct commit.'
  result:
    kind: none
    ref: null
    verification: 'pytest tests/unit/test_eval_schema.py tests/unit/test_eval_metrics.py tests/unit/test_eval_rag.py -q: 50 passed; ruff check clean on scripts/eval_schema.py, scripts/eval_metrics.py, scripts/eval_rag.py + their tests; mypy src/: Success, 131 files. git status confirms no working-tree changes were needed.'
- id: S3-6
  title: 'Авто-страта: эвристический harvest (регексп помнишь/напомни/что решили/…) из internal/analysis/q5_replay.py в харнесс как ГЕНЕРИРУЕМАЯ страта — измеримая цифра уже сегодня, до S3b. Границы честно: 11 кейсов, 4 с попаданием выше 0.7 → это ПОЛ, а не золотой набор; не отменяет S3b. NB: читает n8n-корпус (порт 55435), отдельный DSN от seed. Доступ к internal/ в этом воркти обеспечен — internal/analysis/q5_replay.py присутствует (worktree_carry), решено по [Q3]'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - S3-1
  - S3-2
  estimated_effort: 1.5h
  reasoning_effort: null
  confidence: 0.85
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: c2725b1d-dfef-4d4c-9cd5-a9c5a79a58fd
  retry_count: 0
  last_update:
    ts: '2026-08-09T23:09:50Z'
    executor: backend-dev
    note: 'Added scripts/harvest_auto_strata.py: ports the memory-seeking regex harvest (internal/analysis/q5_replay.py:31-35, worktree_carry) into the tracked harness -- queries the n8n corpus (port 55435, plain asyncpg.connect since that schema has no pgvector, no create_pool()) with a required-no-default DSN positional (mirrors S3-2/[Q1]), and emits EvalCase (S3-1) objects instead of a hand-rolled SQL replay. Design call: expected_message_id_ranges for these cases is an honest WIDE placeholder ([1, trigger_message_id]), not a fabricated pinpoint, because the regex only identifies memory-seeking questions -- it cannot know the true answer location and nobody has hand-verified these 11 cases. That makes recall@k tautological for this file; only blind_rate (S3-4, whose own docstring already names ''auto-harvest corpus, S3-6'' at 7/11) is the honest number, documented prominently in the module docstring, each case''s note field, and the CLI''s own output banner (''FLOOR, NOT GOLDEN SET''). Output defaults to internal/eval/cases_auto_harvest.json (gitignored, same rule as the real S3b golden set) and replays unchanged through the existing scripts/eval_rag.py --cases flag -- no changes needed to S3-2/S3-4''s already-done code. Live-verified against the real rag-analysis-n8n container (port 55435, up): harvests exactly 11 cases, matching internal/analysis/results/q5-replay.md''s documented count, and the output validates via scripts/eval_schema.py''s CLI. Did NOT run a full live eval_rag.py replay (real embedding provider calls) in this session -- no provider credentials in this shell/no .env; S3-2/S3-4''s own specialist work already verified that plumbing with mocks and this item''s scope is the harvest side, not re-verifying search. qa (if picked up) could add one live-credentialed smoke run of eval_rag against this file to confirm the reproduced blind_rate stays close to 7/11, but that''s an optional confirmation, not a correctness gap in what''s committed.'
  result:
    kind: commit
    ref: 92e6285
    verification: 'pytest tests/unit/test_harvest_auto_strata.py -q: 11 passed; pytest tests/unit/ -q: 2002 passed, 5 skipped; ruff check + ruff format --check clean; mypy src/: Success, 131 files; mypy scripts/harvest_auto_strata.py: Success. Manual smoke: python3 -m scripts.harvest_auto_strata postgresql://r:r@127.0.0.1:55435/n8n --out /tmp/... -> 11 cases (matches q5-replay.md), validated OK via python3 scripts/eval_schema.py.'
- id: S3-7
  title: 'Записанная базовая линия как публично-безопасный артефакт: агрегаты (кол-во кейсов по стратам, recall@k, MRR, доля пустых, перцентили best-sim, версия конфига, дата прогона) — в трекаемый документ docs/rag-eval-baseline.md (решено по [S3-7]: долгоживущий справочник под docs/, НЕ docs/plans/); покейсовые строки с цитатами/id — в internal/. Документ обязан проходить scripts/check_plan_artifacts.py'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - S3-4
  - S3-6
  estimated_effort: 1h
  reasoning_effort: null
  confidence: 0.85
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: c4f31660-3d6a-4819-8609-c011076773a8
  retry_count: 0
  last_update:
    ts: '2026-08-09T23:17:16Z'
    executor: backend-dev
    note: 'Wrote docs/rag-eval-baseline.md: public-safe aggregates (strata counts, recall@k/MRR/blind-rate/negative-control-rate, best-sim percentiles, config version=commit 92e6285 (min_similarity=0.7, max_results=5, gemini-embedding-001), run date 2026-08-10) from a real S3-6 auto-harvest run (11 cases) replayed through the real search path (both rag-analysis-seed:55434 and rag-analysis-n8n:55435 containers were already up with data intact; harvested via scripts/harvest_auto_strata.py and replayed via scripts/eval_rag.py -- outputs written to internal/eval/cases_auto_harvest.json, gitignored, real chat content stays there). Reproduced the prior q5-replay.md finding (7/11 empty, blind_rate=0.636) through the real path. Per S3-6''s own honesty boundary, recall@k/MRR are explicitly flagged in the doc as not meaningful for this floor (expected_message_id_ranges is an unverified wide placeholder) -- blind_rate is the primary number. No answer-absent or knowledge-update cases exist yet (need S3b golden set from Julia); documented as an open caveat. Widened scripts/check_plan_artifacts.py''s tracked_plan_files() to also scan docs/rag-eval-baseline.md by default (EXTRA_TRACKED_PATHS) so the ''must pass the guard'' requirement is enforced durably by the existing test_tracked_plan_artifacts_are_clean CI test, not just checked once at write time; added a focused unit test (real tmp git repo) for that widening. Did NOT edit .pre-commit-config.yaml''s files: regex to match (blocked as a sensitive-file edit in this sandbox) -- local pre-commit hook still only fires on docs/plans/ for this doc; CI-level enforcement via the widened tracked_plan_files() is in place. A human should add ''  files: ^(docs/plans/|docs/rag-eval-baseline\.md$)'' to the check-plan-artifacts hook in .pre-commit-config.yaml to close that local-hook gap. Verified scripts/check_plan_artifacts.py docs/rag-eval-baseline.md and the full guard run both report clean.'
  result:
    kind: commit
    ref: eecc0cd
    verification: pytest tests/unit/ (2003 passed, 5 skipped); pytest tests/unit/test_plan_artifact_guard.py (34 passed, incl. new test_tracked_plan_files_also_covers_the_baseline_doc); ruff check src/ scripts/ tests/ clean; mypy src/ clean (131 files); python3 scripts/check_plan_artifacts.py -> 'docs/plans artifacts clean (228 file(s) scanned)'
- id: S3-8
  title: 'Тесты харнесса (инструмент, которым гейтят выкат, сам должен быть проверен): арифметика recall@k/MRR на синтетике с заранее посчитанным ответом; схема ОБЯЗАНА отвергать кейс без asked_at; контроль деградации — при заведомо испорченном поиске метрика ОБЯЗАНА упасть. Контроль не должен быть вакуумным: кейс, где будущая самоссылка ранжируется #1 ДО фильтра (иначе тест проходит и на баге постфильтрации); красный на здоровом коде до зелёного на испорченном. Переиспользовать tests/integration/conftest.py (pgvector testcontainer)'
  specialist: qa
  priority: P1
  status: done
  depends_on:
  - S3-1
  - S3-2
  - S3-3
  - S3-4
  estimated_effort: 1.5-2h
  reasoning_effort: null
  confidence: 0.9
  consult_session_id: ebb1f12f-20e3-41ba-8716-100b446997f9
  specialist_session_id: e6001157-d5b2-477a-825e-bb430899717f
  retry_count: 0
  last_update:
    ts: '2026-08-09T23:02:09Z'
    executor: qa
    note: 'S3-8 needed harness self-tests. Unit-level arithmetic (recall@k/MRR) and asked_at schema rejection were already covered by tests/unit/test_eval_{schema,metrics,rag}.py from S3-1..S3-4''s own work -- those mock the search path entirely, so they can''t exercise the item''s two DB-level requirements: (1) S3-3''s before-filter must be a real WHERE predicate, not a postfilter, verified non-vacuously via a future self-reference that ranks #1 by similarity before filtering (mirrors S2-7a''s positive/negative-control pattern in test_memory_repository_chat_scoping.py); (2) the harness''s recall@k must actually drop under a corrupted query vector and an empty index (real end-to-end run_eval()->compute_metrics() against pgvector, not mocked). Added tests/integration/test_eval_harness_integration.py (5 tests, reuses tests/integration/conftest.py''s pgvector testcontainer). Empirically verified the negative-control fixture is discriminating: temporarily reverted MemoryRepository.search() to a naive postfilter-after-LIMIT shape, confirmed the positive test goes red (0 hits instead of the real answer), then restored the file byte-for-byte (sha256 verified) before running the full suite green and committing. ruff+mypy clean on the new file.'
  result:
    kind: commit
    ref: a1dab48
    verification: 'pytest tests/integration/test_eval_harness_integration.py -q --no-cov: 5 passed. Re-ran tests/unit/test_eval_{schema,metrics,rag}.py (50 passed, unaffected). ruff check + mypy clean on the new file. Manually confirmed the postfilter negative control is non-vacuous by reverting src/database/repositories/memory.py''s before-clause to a Python postfilter, observing the positive test fail (0 results), then restoring the original file (sha256 match) -- CI''s mypy src/ / lint jobs are unaffected since only a tests/ file changed.'
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 18.9501
review_gate:
  why: []
  approve_action: /execute-plan <projects>/telegram-chat-companion.rag-s3-eval-harness-wt/docs/plans/rag-s3-eval-harness.execution.md
  reject_action: /plan-fixes docs/plans/rag-s3-eval-harness.md --revise <projects>/telegram-chat-companion.rag-s3-eval-harness-wt/docs/plans/rag-s3-eval-harness.execution.md
safe_to_replay_from: null
clarifying_questions: []
human_feedback:
- ts: '2026-08-09T20:54:33Z'
  by: julia
  text: 'ANSWER [Q1]: Да'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T21:35:46Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T20:54:57Z'
  by: julia
  text: 'ANSWER [Q2]: да'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T21:35:50Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T20:56:43Z'
  by: julia
  text: 'ANSWER [S3-7]: ок'
  applies_to: S3-7
  status: addressed
  addressed_at: '2026-08-09T21:35:53Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T21:29:48Z'
  by: julia
  text: 'ANSWER [Q3]: ок'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T21:35:57Z'
  addressed_by: pm-orchestrator
revision_number: 2
last_revised_at: '2026-08-09T21:36:42Z'
last_revised_by: pm-orchestrator
---





























































<!-- BRIEF:START -->
# Инструмент замера качества поиска по истории чата + первая базовая линия

## Что произошло
Разобран план слайса S3a из роадмапа переработки поиска по памяти чата (RAG). Сегодня
качество поиска меряется разовыми скриптами, которые переписывают логику поиска внутри
себя — то есть меряют не то, что реально работает в проде. Чтобы будущую переработку можно
было честно сравнивать «до/после» и чтобы разблокировать заполнение золотого набора, нужен
постоянный инструмент.

## Найденные проблемы
1. Замер идёт по копии поиска, а не по настоящему; на следующем слайсе копию пришлось бы
   переписывать, и замер начал бы врать — а этим инструментом собираются гейтить выкат.
2. У настоящего поиска нет границы «искать только то, что было до момента вопроса». Без неё
   в топ выдачи попадает сам вопрос, и замер по кругу меряет самоизвлечение — цифры
   занижаются не из-за качества поиска.
3. Нельзя отличить «нашли что просили» от «навыдумывали, когда ответа в истории не было».
   Без этого снижение порога всегда выглядит улучшением, а поздняя настройка порога сойдётся
   к нулю.
4. Заполнение золотого набора (за Julia) заблокировано отсутствием схемы кейса — её и
   определяет этот слайс.

## Что будет сделано
- Единая схема кейса и трекаемый учебный шаблон — заполнение золотого набора станет
  механическим (S3-1, S3-5).
- Инструмент гоняет НАСТОЯЩИЙ поиск и считает понятные метрики: долю найденного (recall@k),
  MRR, долю пустых ответов, распределение уверенности (S3-2, S3-4).
- В настоящий поиск добавится граница «до момента вопроса», аккуратно, без изменения
  поведения прода (S3-3).
- Первая измеримая базовая линия уже сегодня, честно помеченная как «пол, а не эталон» (S3-6).
- Базовая линия сохраняется безопасно: сводные числа — в открытый репозиторий, детали с
  цитатами и id — в приватную папку (S3-7).
- Инструмент покрыт тестами, включая контроль: на заведомо испорченном поиске метрика обязана
  падать (S3-8).

## Не входит в этот план
Золотой набор (S3b, за Julia), сбор реальных запросов из прод-логов, калибровка порогов (S6),
оценка качества генерации ответа, гейт в CI (инструмент остаётся ручным скриптом). Приоритеты
и роутинг задач проставлены автоматически по разбору и консультациям со специалистами.

## Решения (все подтверждены)
Четыре открытых вопроса прошлой версии плана закрыты: замер снимается с отдельных одноразовых
копий данных, а не с живой базы, причём путь к ним задаётся вручную — так нельзя случайно
замерить по проду; запрос обрабатывается тем же рабочим путём, что и в проде, без обходного
варианта; базовая линия сохраняется отдельным долгоживущим документом в открытой части
репозитория (детали с цитатами — в приватной части); исходные материалы для авто-базовой-линии
уже доступны в рабочей копии. Открытых вопросов к утверждению больше нет.

## Оценка
~13–15 часов работ суммарно; потолок бюджета — $30 на план ($6 на пункт), 8 пунктов.
<!-- BRIEF:END -->

# Plan — rag-s3-eval-harness

## Source

[`docs/plans/rag-s3-eval-harness.md`](docs/plans/rag-s3-eval-harness.md) (sha256 `60e3ca4fb61f...`).

## Items

(none yet — populated by /plan-fixes)
