---
schema_version: 3
plan_id: rag-s3-eval-harness
source_artifact:
  path: docs/plans/rag-s3-eval-harness.md
  sha256: 60e3ca4fb61fa90844113e3ed02f96e04ab9048b10959e82caea401123357a2d
  type: session-analysis
created_at: '2026-08-09T20:47:30Z'
approved_at: null
approved_by: null
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: draft
  started_at: null
  completed_at: null
  current_batch: null
  task_list_id: rag-s3-eval-harness
items:
- id: S3-1
  title: Схема кейса (chat_id, вопрос, asked_at [обязателен], ожидаемые диапазоны message_id, страта, свободная заметка) + трекаемый синтетический шаблон в tests/fixtures/eval/ с выдуманными чатами; шаблон валидируется ТОЙ ЖЕ схемой, что и настоящий internal/eval/cases.json (один общий валидатор-модуль, чтобы не разошлись)
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
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
- id: S3-3
  title: 'Необязательный time-bound before: datetime|None=None по НАСТОЯЩЕМУ пути поиска (MemoryRepository.search → RAGMemoryService.search): условие в WHERE ДО LIMIT, а не постфильтрация (она молча уменьшает k). None-check, не ''x or default'' (паттерн S2-2). Без миграции, по умолчанию None — поведение прода не меняется. Единственный прод-вызов: pipeline.py:491. Без ADR (аддитивно, обратно совместимо)'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 1-1.5h
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
- id: S3-2
  title: 'scripts/eval_rag.py дёргает настоящий RAGMemoryService.search() (тот же вход, что у пайплайна), а не переписанный SQL как в q5_replay.py; эмбеддинг запроса через настоящий провайдерский путь AIRouter(settings) (Dishka не нужен), не сырой HTTP; DSN — обязательный аргумент CLI без дефолта (нельзя случайно навести на живую базу). Побочно: флаг rag_backend на S5 переключается тут ровно как в проде'
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
    ts: null
    executor: null
    note: null
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
  title: 'Авто-страта: эвристический harvest (регексп ''помнишь/напомни/что решили/…'') из q5_replay.py в харнесс как ГЕНЕРИРУЕМАЯ страта — измеримая цифра уже сегодня, до S3b. Границы честно: 11 кейсов, 4 с попаданием выше 0.7 → это ПОЛ, а не золотой набор; не отменяет S3b. NB: читает n8n-корпус (порт 55435), отдельный DSN от seed'
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
  title: 'Записанная базовая линия как публично-безопасный артефакт: агрегаты (кол-во кейсов по стратам, recall@k, MRR, доля пустых, перцентили best-sim, версия конфига, дата прогона) — в трекаемый документ; покейсовые строки с цитатами/id — в internal/. Документ обязан проходить scripts/check_plan_artifacts.py. Рекомендуемое место — docs/ (долгоживущий справочник для S4–S6), см. вопрос [S3-7]'
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
  consumed_usd: 0.0
review_gate:
  why: []
  approve_action: /execute-plan <projects>/telegram-chat-companion.rag-s3-eval-harness-wt/docs/plans/rag-s3-eval-harness.execution.md
  reject_action: /plan-fixes docs/plans/rag-s3-eval-harness.md --revise <projects>/telegram-chat-companion.rag-s3-eval-harness-wt/docs/plans/rag-s3-eval-harness.execution.md
safe_to_replay_from: null
clarifying_questions:
- '[Q1] Субстрат и DSN для базовой линии. Специалисты уточнили: нужны ДВЕ throwaway-БД — rag-analysis-seed (порт 55434, ~2918 строк chat_memory; на момент разбора контейнер был Up, а не Exited как записано в доке) для S3-2/S3-4/S3-5, и rag-analysis-n8n (порт 55435, bot_response_log/chat_messages) для авто-страты S3-6. Рекомендация: обе БД — обязательные аргументы CLI без дефолта (чтобы харнесс нельзя было случайно навести на живую базу), поднять и проверить данные обеих перед прогоном. Подтверждаем правило DSN-без-дефолта и снятие базовой линии с этих двух seed-контейнеров?'
- '[Q2] Как харнесс эмбеддит запрос. Архитектор и backend-dev сходятся: AIRouter(settings) конструируется напрямую, без полного DI (Dishka не нужен) — эмбеддинг идёт настоящим провайдерским путём. Запасной вариант из доки (сырой HTTP плюс тест на форму вызова) — это вторая реимплементация настоящего пути, ровно та болезнь, ради которой затеян S3-2. Рекомендация: закрываем как через настоящий AIRouter, без HTTP-фолбэка. Согласна?'
- '[Q3] Доступ этого воркта к гитигноренному internal/. Три специалиста независимо подтвердили: воркти ...rag-s3-eval-harness-wt не содержит папку internal/ (и CLAUDE.md) — они лежат только в основном чекауте. S3-6 обязан прочитать internal/analysis/q5_replay.py, чтобы перенести регексп-харвест; S3-2/S3-3 ссылаются на его строки. Без доступа backend-dev либо не видит исходник, либо переизобретает логику и расходится с корпусом, на котором посчитаны числа q5-replay. Решение: симлинкнуть/скопировать internal/ в этот воркти перед выполнением, или подтвердить, что песочница execute-режима читает абсолютные пути в основной чекаут?'
- '[S3-7] Где живёт трекаемый документ базовой линии (открытый вопрос №3 доки). Прецедент репозитория (docs/architecture.md, docs/NORTH-STAR.md, docs/deployment.md — долгоживущие справочники прямо под docs/; docs/plans/*.md — точечные планы) и рамка самой доки (базовая линия — оправдательный артефакт для S4–S6, мультислайсовый) указывают на docs/. Рекомендация: docs/rag-eval-baseline.md, не docs/plans/. Ок?'
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
и роутинг задач проставлены автоматически по разбору и консультациям со специалистами; спорные
решения вынесены в блок «нужно решить» ниже.

## Оценка
~13–15 часов работ суммарно; потолок бюджета — $30 на план ($6 на пункт), 8 пунктов.
<!-- BRIEF:END -->

# Plan — rag-s3-eval-harness

## Source

[`docs/plans/rag-s3-eval-harness.md`](docs/plans/rag-s3-eval-harness.md) (sha256 `60e3ca4fb61f...`).

## Items

(none yet — populated by /plan-fixes)
