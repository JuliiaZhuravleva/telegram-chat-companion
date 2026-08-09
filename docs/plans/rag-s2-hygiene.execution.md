---
schema_version: 3
plan_id: rag-s2-hygiene
source_artifact:
  path: docs/plans/rag-s2-hygiene.md
  sha256: deaaeea76621225057b9263935aa24a2e1552a44421671d6c383129b973e7e55
  type: session-analysis
created_at: '2026-08-09T11:02:26Z'
approved_at: null
approved_by: null
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: draft
  started_at: null
  completed_at: null
  current_batch: null
  task_list_id: rag-s2-hygiene
items:
- id: S2-2
  title: 'Единый источник порога сходства (0.7): убрать дефолты 0.65 из конструктора и метода репозитория'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 1h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S2-6
  title: 'chat_memory под retention: выбрать одну политику очистки и закрыть два слоя (_windows + RETENTION_TABLES)'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on: []
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S2-3a
  title: 'ADR-0003: решить порядок сортировки KB (развести ранжирование выдачи и обрезку под бюджет)'
  specialist: architect
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 45m
  confidence: null
  consult_session_id: 2cc86a83-dace-4f68-b7a8-3b0f6762aff8
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S2-1
  title: 'Фолбэк эмбеддингов: починить оба дефекта одним изменением (проводка + размерность 768 vs 1536)'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - S2-2
  estimated_effort: 3h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S2-3b
  title: 'KB: применить решённый порядок сортировки в SQL + переписать ADR-заблокированный тест'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - S2-3a
  estimated_effort: 1h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S2-4
  title: Один эмбеддинг запроса на ход для RAG и KB + проброс chat_id в логи стоимости (TD-009)
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - S2-1
  estimated_effort: 2h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S2-5a
  title: 'delete_expired(): удалить или подключить — в зависимости от выбранной в S2-6 политики'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - S2-6
  estimated_effort: 20m
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S2-5b
  title: Удалить мёртвый код ChatFact (не используется вне своего модуля)
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on: []
  estimated_effort: 15m
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S2-7a
  title: Интеграционный тест chat-scoping (privacy-инвариант) с обязательным негативным контролем
  specialist: qa
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: ba8f0cfa-a04c-4bbf-afc1-f51cedc268a5
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S2-7b
  title: Юнит-покрытие RAGMemoryService (поведение при падении эмбеддинга, проброс порога/лимита)
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - S2-1
  - S2-2
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S2-8
  title: '/kb view: проверять kb_enabled (фильтр/ранний ответ с текстом, не тихий return)'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on: []
  estimated_effort: 1h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: S2-9
  title: 'relevancy_check: удалить мёртвую секцию конфига (или, по решению, отдельный PR по роутеру) — TD-039'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on: []
  estimated_effort: 1h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
budget:
  max_usd_per_item: 2.0
  max_usd_per_plan: 20.0
  consumed_usd: 0.0
review_gate:
  why: []
  approve_action: /execute-plan <projects>/telegram-chat-companion.rag-s2-hygiene-wt/docs/plans/rag-s2-hygiene.execution.md --resume
  reject_action: /plan-fixes docs/plans/rag-s2-hygiene.md --revise <projects>/telegram-chat-companion.rag-s2-hygiene-wt/docs/plans/rag-s2-hygiene.execution.md
safe_to_replay_from: null
clarifying_questions:
- "[S2-1] Фолбэк эмбеддингов: (а) честно убрать резерв — выкинуть openai из цепочки эмбеддингов и задокументировать, что резерва нет, пока нет второй 768-мерной модели; или (б) прокинуть fallback_models + dimensions и добавить валидацию длины вектора перед записью в БД. Вариант (б) дороже (~×3) и не даёт сравнимого 768-мерного резерва у OpenAI без усечения пространства. Рекомендую (а). Что выбираем?"
- "[S2-2] Единый источник порога: убрать дублирующие дефолты 0.65 из конструктора сервиса и метода репозитория. Отдельно решить: оставляем ли настройку min_similarity, которая сейчас дублирует значение из YAML, или конфиг (YAML) остаётся единственным местом. Рекомендую один источник — YAML. Подтверждаем?"
- "[S2-3] Порядок сортировки KB завязан на зафиксированное решение ADR-0003: переписать его пункт (новое решение) и поменять сортировку — или оставить ADR и задокументировать отступление кода. Тонкость от ревью: сборка фактов под бюджет сейчас опирается на тот же порядок, поэтому решение должно явно развести ранжирование выдачи (по похожести) и ранжирование для обрезки под бюджет (по важности), иначе починка выдачи сломает обрезку. Рекомендую развести два ключа сортировки и оформить решением архитектора (S2-3a) до правки кода (S2-3b). Согласны?"
- "[S2-6] Очистка таблицы памяти chat_memory: (а) по возрасту, как у остальных 8 таблиц (общий механизм), или (б) вызвать существующий, но сейчас не подключённый delete_expired по сроку годности. Политики конфликтуют — нужна одна, вторую удалить; этот выбор определяет и судьбу мёртвого delete_expired (S2-5a). Правка нужна в двух местах: список окон очистки и белый список таблиц (защита от SQL-инъекции). Таблица уходит в S5–S6, поэтому решение должно быть дешёвым и временным. Рекомендую (а). Что выбираем?"
- "[S2-9] Мёртвая секция конфига relevancy_check: (а) просто удалить вводящий в заблуждение блок (дёшево, влезает в общий PR), или (б) добавить параметр задачи в generate_text() — это затрагивает все 5 вызовов метода в 4 файлах и заслуживает отдельного PR (и, возможно, короткого ADR). Рекомендую (а) в этом слайсе, (б) вынести отдельным PR позже. Согласны?"
- "[Q1] Дробление на PR: S2-3 (решение ADR), S2-6 (очистка прод-таблицы) и S2-9 (если выбран вариант с роутером) — отдельными PR; остальное одним. Уточнение: ни один пункт не требует миграции схемы — «отдельный PR» здесь про ревью архитектурного решения, ops-риск на прод-таблице и радиус изменения API, а не про миграцию. Подтверждаем такое деление?"
- "[Q2] В трекере техдолга номер TD-039 занят дважды (автоблэклист бытовых слов и маршрутизация generate_text(), на которую ссылается S2-9). Переномеровать одну запись в этом слайсе, чтобы ссылки на TD-039 не были двусмысленными? Рекомендую переномеровать запись про роутер на следующий свободный номер."
---

























<!-- BRIEF:START lang=ru -->
# Память и база знаний бота: чиним корректность и наводим порядок (слайс S2)

## Что произошло
Разобрали технический слайс развития долгосрочной памяти бота и его базы знаний.
Это не новые функции, а приведение уже работающего механизма в честное и
предсказуемое состояние — чтобы следующий этап (замеры и калибровка качества
ответов) вообще что-то значил. Нашли 9 проблем; часть из них требует вашего
решения — они собраны в блоке «нужно решить» ниже.

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
- **Таблица памяти растёт без ограничений** и никогда не чистится.
- **Лишняя задержка в ответе** (запрос обрабатывается дважды за ход) и слепая
  статистика расходов по чатам.
- **Мёртвый код и вводящая в заблуждение секция настроек**, которые следующий
  читатель примет за рабочие.

## Что будет сделано
- Резерв памяти приведём в честное состояние — ошибок записи на живом трафике не
  будет ни при каком из вариантов. (S2-1)
- Появится тест, который ломается, если приватность чатов нарушат; плюс базовое
  покрытие поведения памяти. (S2-7a, S2-7b)
- Порог релевантности станет один, из одного места. (S2-2)
- База знаний останется корректной и после включения оценки важности фактов;
  решение зафиксируем документом. (S2-3a, S2-3b)
- Просмотр базы знаний начнёт уважать выключатель и отвечать понятным текстом, а
  не молчанием. (S2-8)
- Таблица памяти начнёт регулярно чиститься. (S2-6, S2-5a)
- Ответ станет чуть быстрее, а расходы — видимыми по чатам. (S2-4)
- Уберём мёртвый код и наведём порядок в настройках. (S2-5b, S2-9)

## Не входит в этот план
- Смена архитектуры поиска (гибридный поиск, чанки, калибровка порога по
  эталонному набору) — это следующие слайсы S3–S6.
- Автосбор базы знаний остаётся на паузе; здесь только готовим для него почву.
- Ревизия порога автобана бытовых слов — ведётся отдельно.
- Запуск был автоматический, без живых ответов, поэтому приоритеты и разбиение на
  задачи выставлены по умолчанию; всё, что требует вашего слова, вынесено в блок
  «нужно решить».

## Оценка
Около 15–16 часов работы по 12 задачам; потолок бюджета — $20.
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

## Items

Полный список из 12 пунктов — во frontmatter (`items[]`). Открытые решения — в
`clarifying_questions[]` (виджет «нужно решить» под брифом).
