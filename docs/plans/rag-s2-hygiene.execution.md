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
  title: 'Единый источник порога сходства: YAML — единственный источник истины; убрать дефолты 0.65 из конструктора и метода репозитория (и починить x or default → x if x is not None else default при консолидации)'
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
    executor: pm-orchestrator
    note: 'Ревизия 1: Julia — «один источник, YAML». YAML (config/default.yml) остаётся единственным местом значения порога; дублирующие дефолты 0.65 в конструкторе сервиса и методе репозитория убрать (сделать аргумент обязательным либо тянуть из конфига). RAGSettings.min_similarity читает YAML — competing хардкод не оставлять. При консолидации починить x or default → x if x is not None else default, иначе явный min_similarity=0.0 снова молча перезатрётся.'
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
    executor: pm-orchestrator
    note: 'Ревизия 1: Julia выбрала retention по возрасту (вариант а) — как у остальных 8 таблиц, общий механизм _windows + белый список RETENTION_TABLES (оба слоя, тест обязан покрыть оба). Это определяет и судьбу delete_expired: он не подключается, удаляется как мёртвый код (S2-6 больше не блокирует S2-5a, зависимость снята). Отдельно: Julia также хочет перед удалением сохранять высокоуровневую «историческую память» удаляемого периода — это НЕ входит в текущую правку S2-6 и вынесено в открытый вопрос [S2-6b], т.к. конфликтует со stopgap-рамкой (таблица выводится в S5–S6).'
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
    executor: pm-orchestrator
    note: 'Ревизия 1: Julia согласна с разводкой двух ключей сортировки — ранжирование выдачи по похожести и ранжирование для обрезки под бюджет по важности (salience). Решение архитектора (S2-3a: переписать пункт ADR-0003 Part 2) до правки SQL (S2-3b). Наивный флип ORDER BY ломает и обрезку под бюджет, и зелёный тест test_salience_wins_over_similarity — поэтому именно два ключа.'
  result: null
- id: S2-1
  title: 'Фолбэк эмбеддингов (вариант а): честно снять резерв — убрать openai из цепочки embeddings, задокументировать отсутствие 768-мерного резерва; лёгкий guard длины вектора перед записью оставить как дешёвую защиту'
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
    executor: pm-orchestrator
    note: 'Ревизия 1: Julia выбрала вариант (а) — убрать openai из цепочки embeddings, оставить gemini-only, задокументировать отсутствие 768-мерного резерва. Валидация размерности (нужная в основном для варианта б) больше не критична, но лёгкий guard длины вектора перед записью оставляем как дешёвую защиту (негативный контроль из разбора). Дополнительное пожелание Julia — фоновый дозапис упавших эмбеддингов — вынесено в новый пункт S2-10 (depends_on S2-1), чтобы вариант (а) не приводил к безвозвратной потере памяти при недоступности Gemini.'
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
  title: 'delete_expired(): удалить как мёртвый код — S2-6 выбрал retention по возрасту (общий механизм), delete_expired не подключается'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on: []
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
  title: 'relevancy_check (вариант а): удалить мёртвую секцию конфига + переномеровать дублирующую запись TD-039 (роутер) на следующий свободный номер в _tech-debt.md; правку сигнатуры generate_text() вынести отдельным PR позже'
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
    executor: pm-orchestrator
    note: 'Ревизия 1: Julia подтвердила вариант (а) — удалить вводящую в заблуждение секцию relevancy_check из config/default.yml (дёшево, в общем PR). Правку роутера (параметр задачи в generate_text(), затрагивает все 5 вызовов в 4 файлах — вариант б) НЕ делаем в этом слайсе, выносим отдельным PR позже (возможно, с коротким ADR). Дополнительно (ответ на Q2): в _tech-debt.md номер TD-039 занят дважды (автоблэклист бытовых слов и роутинг generate_text) — при выполнении переномеровать запись про роутер на следующий свободный номер, чтобы ссылки на TD-039 не были двусмысленными. Правку _tech-debt.md делает исполнитель этого пункта, не PM.'
  result: null
- id: S2-10
  title: 'Фоновый бэкофилл эмбеддингов: воркер дозаписывает embedding для строк chat_memory, чей эмбеддинг упал в момент записи (новое пожелание Julia к варианту а S2-1)'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - S2-1
  estimated_effort: 4.5h
  confidence: null
  consult_session_id: 756ee269-b593-4d25-9743-0d375b523f5c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: pm-orchestrator
    note: 'Скоуп (консультация backend-dev, ревизия 1): (1) миграция НЕ нужна — chat_memory.embedding уже nullable vector(768), а search() фильтрует embedding IS NOT NULL, поэтому строка с NULL-эмбеддингом = естественный маркер pending и инертна к поиску; едет в общем PR. (2) Предусловие в этом же скоупе: сейчас RAGMemoryService.store() при падении эмбеддинга вообще не пишет строку (ловит исключение, warning, return None до _repo.store); нужно расширить error-path, чтобы строка сохранялась с embedding=None. Единственный вызывающий (_safe_rag_store) игнорирует возврат — контракт не меняется. (3) Инфру не изобретать: EmbeddingBackfillWorker зеркалит RetentionCleaner 1:1 (pool+config, start/stop/_run_loop, синглтон в main.py) + мелкий конфиг-блок по образцу MaintenanceSettings (enabled/interval_seconds/batch). (4) Политика ретраев (дефолт, ADR не нужен): повторять без ограничения по попыткам (gemini-embedding-001 бесплатен), warning после N подряд неудачных проходов; пересмотреть, когда приземлится retention S2-6. Кэйденс — часовой как у обслуживания; логирование — structlog, без отдельной таблицы. depends_on S2-1 (оба трогают memory.py, идти после).'
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
- '[S2-6b] Историческая память перед очисткой chat_memory: вы просили перед удалением сохранять высокоуровневую «историческую память» удаляемого периода, чтобы память не терялась полностью. Загвоздка: по роадмапу эта таблица выводится из эксплуатации в S5–S6 (миграция на дроп), а нынешняя очистка по возрасту — заведомо временный stopgap; строить сейчас пайплайн суммаризации-перед-удалением почти наверняка одноразовая работа. Рекомендую: в этом слайсе только чистим по возрасту (S2-6), а «историческую память» спроектировать в момент вывода таблицы из эксплуатации (S5–S6), где она не будет выброшена. Отложить до S5–S6 или всё же сделать лёгкую высокоуровневую сводку уже сейчас?'
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
revision_number: 2
last_revised_at: '2026-08-09T11:43:34Z'
last_revised_by: pm-orchestrator
---























































<!-- BRIEF:START lang=ru -->
# Память и база знаний бота: чиним корректность и наводим порядок (слайс S2)

## Что произошло
Разобрали технический слайс развития долгосрочной памяти бота и его базы знаний.
Это в основном приведение уже работающего механизма в честное и предсказуемое
состояние — чтобы следующий этап (замеры и калибровка качества ответов) вообще
что-то значил. Нашли 9 проблем; по вашим ответам все ключевые решения приняты, и
по вашей просьбе добавлена одна новая задача. Остался один открытый вопрос — он в
блоке «нужно решить» ниже.

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
- Таблица памяти начнёт регулярно чиститься по возрасту, как остальные. (S2-6, S2-5a)
- Ответ станет чуть быстрее, а расходы — видимыми по чатам. (S2-4)
- Уберём мёртвый код и наведём порядок в настройках. (S2-5b, S2-9)

## Не входит в этот план
- Смена архитектуры поиска (гибридный поиск, чанки, калибровка порога по
  эталонному набору) — это следующие слайсы S3–S6.
- Автосбор базы знаний остаётся на паузе; здесь только готовим для него почву.
- Ревизия порога автобана бытовых слов — ведётся отдельно.
- «Историческая память» удаляемого периода (высокоуровневая сводка перед
  очисткой) — вынесена в открытый вопрос: таблица всё равно выводится из
  эксплуатации на следующих этапах, поэтому такую сводку логичнее проектировать
  там, а не одноразово сейчас.
- Переработка адресации провайдера в relevancy_check — отдельным PR позже; в этом
  слайсе только убираем вводящую в заблуждение секцию настроек.

## Оценка
Около 20 часов работы по 13 задачам (добавлена фоновая дозапись эмбеддингов);
потолок бюджета — $20.
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
