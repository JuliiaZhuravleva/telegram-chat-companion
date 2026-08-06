---
schema_version: 3
plan_id: sticker-management-2026-08-06
source_artifact:
  path: docs/plans/sticker-management-2026-08-06.md
  sha256: 8ce06bed2fb5bf2024dc72d370782014353e4b1563281ad2c79a64babff9dc98
  type: feature-prd
created_at: '2026-08-06T13:14:44Z'
approved_at: null
approved_by: null
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: draft
  started_at: null
  completed_at: null
  current_batch: null
  task_list_id: sticker-management-2026-08-06
items:
- id: A-1
  title: 'ADR: модель данных дедупликации стикеров — что считать похожестью (perceptual-hash до Vision vs эмбеддинг описания после), схема копии, порог'
  specialist: architect
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 1h
  confidence: null
  consult_session_id: 37b9213a-89ec-44b3-ae9c-aeab84156215
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A-2
  title: 'Миграция + ingest-проверка near-duplicate в learning.py: при высокой похожести переиспользовать описание/эмоции/теги/эмбеддинг канонического стикера (и будущие vision-поля обобщённо)'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A-1
  estimated_effort: 5-6h
  confidence: null
  consult_session_id: 21acdb73-c42a-409a-adca-4ffdd330a8b3
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A-3
  title: 'Тесты дедупликации: детект + граница ложных срабатываний (реальные эмбеддинги, не моки) + live-чеклист по 3 примерам'
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - A-2
  estimated_effort: 2.5h
  confidence: null
  consult_session_id: 6f61aa82-e7ba-4085-a914-8e5f6a741d59
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: B-1
  title: 'Хендлер стикера в DM админа (перехват до media.py): найден → описание; не найден → кнопка «Проанализировать» (синхронно, ADR-0003); проверка admin+private'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 3h
  confidence: null
  consult_session_id: 21acdb73-c42a-409a-adca-4ffdd330a8b3
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: B-2
  title: 'Тесты DM-хендлера: found/not-found/analyze, admin+private scoping, регрессия что media.py больше не учит молча, соответствие ADR-0003 + live-чеклист'
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - B-1
  estimated_effort: 2h
  confidence: null
  consult_session_id: 6f61aa82-e7ba-4085-a914-8e5f6a741d59
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: C-1
  title: 'Улучшение сигнала движения без новых зависимостей: подключить готовый _create_motion_trail_frame + эвристика осцилляции в motion.py + подсказка в vision-промпт; текущий маршрут сохранить'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on: []
  estimated_effort: 4h
  confidence: null
  consult_session_id: 21acdb73-c42a-409a-adca-4ffdd330a8b3
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: C-2
  title: 'Тесты движения: unit на подсказку осцилляции (test_motion.py), регрессия сохранности текущего маршрута, live-чеклист повторной отправки AgAD7DoAAppnmEg'
  specialist: qa
  priority: P2
  status: pending
  depends_on:
  - C-1
  estimated_effort: 2h
  confidence: null
  consult_session_id: 6f61aa82-e7ba-4085-a914-8e5f6a741d59
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: D-1
  title: 'ADR: модель толерантности — поля explicitness_score/tolerance_level, политика NULL (fail-closed), семантика сравнения, источник оценки, one-off backfill, НЕ через abuse-модуль'
  specialist: architect
  priority: P2
  status: pending
  depends_on: []
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 37b9213a-89ec-44b3-ae9c-aeab84156215
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: D-2
  title: 'Пайплайн оценки приемлемости: миграция explicitness_score + расширение vision-промпта/парсера + one-off скрипт backfill существующего каталога'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - D-1
  estimated_effort: 3.5h
  confidence: null
  consult_session_id: 21acdb73-c42a-409a-adca-4ffdd330a8b3
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: D-3
  title: 'Per-chat tolerance_level: миграция + FieldSpec(FLOAT) + сид default 0.5 + фильтр выбора кандидатов (tolerance vs explicitness) + FSM админ-установки'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - D-1
  - D-2
  estimated_effort: 3.5h
  confidence: null
  consult_session_id: 21acdb73-c42a-409a-adca-4ffdd330a8b3
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: D-4
  title: 'Тесты толерантности: fail-closed NULL (integration+миграция), end-to-end гейтинг (0.5 vs anarchy 1.0), направление неравенства, дефолт-сид, three-layer merge; синтетические фикстуры'
  specialist: qa
  priority: P2
  status: pending
  depends_on:
  - D-2
  - D-3
  estimated_effort: 3h
  confidence: null
  consult_session_id: 6f61aa82-e7ba-4085-a914-8e5f6a741d59
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
  approve_action: /execute-plan /Users/julia/my-projects/telegram-chat-companion.sticker-management-2026-08-06-wt/docs/plans/sticker-management-2026-08-06.execution.md --resume
  reject_action: /plan-fixes docs/plans/sticker-management-2026-08-06.md --revise /Users/julia/my-projects/telegram-chat-companion.sticker-management-2026-08-06-wt/docs/plans/sticker-management-2026-08-06.execution.md
safe_to_replay_from: null
clarifying_questions:
- "[A-1] Дубликаты стикеров: три примера (AgAD6xIAAv3NMUs, AgAD_xEAAsJVUEo, AgADzioAAuSTIEs) — это одна и та же картинка, перезалитая под новым id (тогда дешёвая проверка по хешу картинки ДО анализа ИИ, экономит вызовы), или разные картинки с одинаковым смыслом (тогда сравнение по смыслу описания уже ПОСЛЕ анализа, без экономии)? Нужно глянуть эти три в Telegram/базе — по тексту заметок это не определить. Рекомендация: проверка по хешу картинки (совпадает со словом «дубликаты», без новых библиотек). Подтвердить перед стартом A-1."
- "[D-1] Фильтр «приемлемости»: у уже разобранных стикеров в базе оценки приемлемости нет — новая оценка появится только у будущих, поэтому на существующий каталог (ради которого всё и затевается) фильтр без доработки почти не повлияет. Варианты: (а) только вперёд, старые не трогаем — в новых чатах пошлятина всё равно проскакивает; (б) «оценки нет» = «скрыть в приличных чатах» — безопасно, но временно прячет много безобидных; (в) разовый служебный скрипт переоценки старого каталога (НЕ кнопка массовой переоценки в интерфейсе — она запрещена ADR-0003). Рекомендация: (в) + до прогона считать «нет оценки» скрытым (fail-closed). Что выбираем?"
- "[Q1] Объём: все четыре области — это 11 задач (≈30+ часов работы), больше типового однодневного цикла и на потолке бюджета одного плана. Предлагаю два захода: сначала P1 (дедупликация A + проверка стикера в DM B — быстрее, без внешних зависимостей), затем P2 (анимации C + толерантность D — тяжелее, D зависит от разового backfill и от реестра настроек из плана chat-settings-panel). Делим на два запуска или гоним всё одним планом?"
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
- Бот научится узнавать повторные/похожие стикеры и переиспользовать одно описание вместо
  повторного анализа (A).
- В личке админа: прислал стикер — бот сразу покажет описание, если знает его, либо предложит
  кнопку «Проанализировать», если нет (B).
- Анимации: бот начнёт учитывать степень и характер движения и «видеть» тряску и резкие жесты;
  прежний разбор останется как запасной вариант (C).
- Появится настройка «уровня приличия» стикеров: по умолчанию 0.5 для новых чатов (пошлое не
  шлём), а в «своих» чатах планку можно поднять и разрешить хоть самые всратые (D).

## Не входит в этот план
- Тяжёлые библиотеки для анализа движения (оптический поток и т.п.) — на первом этапе обойдёмся
  тем, что уже есть в боте; отдельной опцией можно вернуться позже.
- Массовая переоценка старого каталога стикеров через интерфейс — она запрещена прежним решением;
  при необходимости старые стикеры переоценим разовым служебным скриптом.
- Планка приличия не связана с антиспамом/антиабьюзом — это разные механизмы, смешивать не будем.

## Оценка
≈30+ часов, 11 задач, потолок бюджета $30. Рекомендуем два захода: сначала блоки A и B (быстрее,
без внешних зависимостей), затем C и D.
<!-- BRIEF:END -->

# Plan — sticker-management-2026-08-06

## Source

[`docs/plans/sticker-management-2026-08-06.md`](docs/plans/sticker-management-2026-08-06.md) (sha256 `8ce06bed2fb5...`).

## Items

(none yet — populated by /plan-fixes)
