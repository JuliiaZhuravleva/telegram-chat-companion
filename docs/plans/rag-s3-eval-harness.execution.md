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
  status: partial
  started_at: '2026-08-09T21:38:37Z'
  completed_at: null
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
  status: pending
  depends_on:
  - S3-1
  - S3-3
  estimated_effort: 3h
  reasoning_effort: null
  confidence: null
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T22:34:51Z'
    executor: execute_plan.sh
    note: specialist dispatch failed (claude -p exit 1, transient API error — connection
      closed mid-response; api_error_status null, so neither auth nor quota) — see
      <projects>/telegram-chat-companion.rag-s3-eval-harness-wt/docs/plans/rag-s3-eval-harness.execution.md.log.jsonl
      dispatch_error; requeued to pending for resume
  result: null
- id: S3-4
  title: 'Метрики: recall@k — первичная, MRR — вторичная; доля кейсов с пустой выдачей выше порога (аналог ''bot answers blind'', сегодня 7 из 11); распределение best-sim (перцентили) — собирается бесплатно тем же прогоном, на нём S6 будет калибровать порог'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - S3-2
  estimated_effort: 2h
  reasoning_effort: null
  confidence: null
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S3-5
  title: 'Страты found / knowledge-update / answer-absent. answer-absent — негативный контроль всего эвала: правильное поведение = пустая выдача, метрика ОБЯЗАНА раздельно показывать ''нашли что просили'' и ''не выдумали, когда нечего было находить''. Без неё снижение порога всегда выглядит улучшением и калибровка на S6 сходится к нулю'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - S3-1
  estimated_effort: 1.5h
  reasoning_effort: null
  confidence: null
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S3-6
  title: 'Авто-страта: эвристический harvest (регексп помнишь/напомни/что решили/…) из internal/analysis/q5_replay.py в харнесс как ГЕНЕРИРУЕМАЯ страта — измеримая цифра уже сегодня, до S3b. Границы честно: 11 кейсов, 4 с попаданием выше 0.7 → это ПОЛ, а не золотой набор; не отменяет S3b. NB: читает n8n-корпус (порт 55435), отдельный DSN от seed. Доступ к internal/ в этом воркти обеспечен — internal/analysis/q5_replay.py присутствует (worktree_carry), решено по [Q3]'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - S3-1
  - S3-2
  estimated_effort: 1.5h
  reasoning_effort: null
  confidence: null
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S3-7
  title: 'Записанная базовая линия как публично-безопасный артефакт: агрегаты (кол-во кейсов по стратам, recall@k, MRR, доля пустых, перцентили best-sim, версия конфига, дата прогона) — в трекаемый документ docs/rag-eval-baseline.md (решено по [S3-7]: долгоживущий справочник под docs/, НЕ docs/plans/); покейсовые строки с цитатами/id — в internal/. Документ обязан проходить scripts/check_plan_artifacts.py'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - S3-4
  - S3-6
  estimated_effort: 1h
  reasoning_effort: null
  confidence: null
  consult_session_id: c666c356-1a08-4705-a6f2-bedcf1a51777
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S3-8
  title: 'Тесты харнесса (инструмент, которым гейтят выкат, сам должен быть проверен): арифметика recall@k/MRR на синтетике с заранее посчитанным ответом; схема ОБЯЗАНА отвергать кейс без asked_at; контроль деградации — при заведомо испорченном поиске метрика ОБЯЗАНА упасть. Контроль не должен быть вакуумным: кейс, где будущая самоссылка ранжируется #1 ДО фильтра (иначе тест проходит и на баге постфильтрации); красный на здоровом коде до зелёного на испорченном. Переиспользовать tests/integration/conftest.py (pgvector testcontainer)'
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - S3-1
  - S3-2
  - S3-3
  - S3-4
  estimated_effort: 1.5-2h
  reasoning_effort: null
  confidence: null
  consult_session_id: ebb1f12f-20e3-41ba-8716-100b446997f9
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 3.5522
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
