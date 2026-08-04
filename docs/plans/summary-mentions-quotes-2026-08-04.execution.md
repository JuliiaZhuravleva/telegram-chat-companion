---
schema_version: 3
plan_id: summary-mentions-quotes-2026-08-04
source_artifact:
  path: docs/plans/summary-mentions-quotes-2026-08-04.md
  sha256: 726eb8b0ce4b0c715e6ba693f195a72a72c318e6fb37d066774e3ddedc88113b
  type: session-analysis
created_at: '2026-08-04T00:26:12Z'
approved_at: '2026-08-04T07:39:12Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: approved
  started_at: null
  completed_at: null
  current_batch: null
  task_list_id: summary-mentions-quotes-2026-08-04
items:
- id: M-1
  title: 'A(mentions): кликабельные упоминания в /summary — индексный плейсхолдер, резолв в safe anchor после markdown_to_html'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 4h
  confidence: null
  consult_session_id: 45d74f2f-e882-41e8-bdc2-882cae6dc190
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: 'Решение владельца [M-1]: упоминать всех участников сообщения, без per-chat переключателя (вариант 1). Объём M-1 без изменений, миграция не нужна.'
  result: null
- id: M-2
  title: 'A(mentions)/qa: security-тест на имя-атаку через first_name (HTML-инъекция в anchor)'
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - M-1
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: d71fa183-d5cc-4995-bc6c-3d28ea6bae02
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: M-3
  title: 'A(mentions)/architect: ADR-0005 — паттерн резолва упоминаний и почему порядок обратен STICKER-маркеру'
  specialist: architect
  priority: P2
  status: pending
  depends_on: []
  estimated_effort: 1h
  confidence: null
  consult_session_id: 806f8277-4c83-4070-959e-8614f5e63ae1
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: E-1
  title: 'B(emoji): инструкция в системном промпте /summary — просить уместные emoji на усмотрение модели (свободная расстановка, без фиксированного скелета, ru+en)'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - M-1
  estimated_effort: 1h
  confidence: null
  consult_session_id: 45d74f2f-e882-41e8-bdc2-882cae6dc190
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: 'Решение владельца [E-1]: вариант B — свободная расстановка emoji на усмотрение модели, БЕЗ фиксированного словаря блоков. Правим обе языковые ветки (ru+en). Инструкцию писать под дешёвые модели по умолчанию (хуже держат формат).'
  result: null
- id: E-2
  title: 'B(emoji)/qa: unit-тест взаимодействия emoji×markdown_to_html (двойной маркер •, конверсия заголовков) + живой прогон качества разметки на дешёвых моделях'
  specialist: qa
  priority: P2
  status: pending
  depends_on:
  - E-1
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: d71fa183-d5cc-4995-bc6c-3d28ea6bae02
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: Формат свободный (реш. [E-1]=B), поэтому автотест НЕ фиксирует структуру блоков. Проверяет только техническое взаимодействие emoji с markdown_to_html (напр. '- 🔥 тема' → '• 🔥 тема', заголовки) и живым прогоном — что дешёвые модели дают осмысленную emoji-разметку.
  result: null
- id: Q-1
  title: 'C(quote): проброс message.quote через handlers → pipeline → prompt_builder + извлечение общего reply-хелпера'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 4h
  confidence: null
  consult_session_id: 45d74f2f-e882-41e8-bdc2-882cae6dc190
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: 'Решение владельца [Q-1]: модели отдаём И выделенный фрагмент, И полное сообщение с явной пометкой, что именно выделил пользователь (не только фрагмент). У цитаты отдельный лимит длины; текст цитаты — пользовательские данные, идёт через sanitize_prompt_content(). Учитываем только is_manual (ручное выделение). Общий хелпер extract_manual_quote(message) из этого пункта переиспользует новый Q-3 (персист), чтобы фильтр is_manual не дублировался в трёх местах.'
  result: null
- id: Q-2
  title: 'C(quote)/qa: тест на quote-инъекцию (sanitize) + расширение test_reply_context под reply_quote'
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - Q-1
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: d71fa183-d5cc-4995-bc6c-3d28ea6bae02
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: Q-3
  title: 'C(quote-persist): миграция 021 (quote_text TEXT, quote_is_manual BOOLEAN, nullable, без DEFAULT) + сохранение цитаты в message_saver.py'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - Q-1
  estimated_effort: 3h
  confidence: null
  consult_session_id: 45d74f2f-e882-41e8-bdc2-882cae6dc190
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: 'Добавлен по решению [Q-PERSIST]=«сделаем сразу». Persist-only (см. новый вопрос [Q-CONSUME] про использование): сейчас НИКАКОЙ код не читает reply/quote из chat_messages обратно — живой контекст реплая всегда строится из in-memory Message. Поля: quote_text + quote_is_manual (только они, YAGNI; position/entities не персистим — дёшево добавить позже). Две отдельные op.execute() ADD COLUMN IF NOT EXISTS без DEFAULT (metadata-only, не переписывает таблицу; идиома миграций 015/018). Онлайн-апгрейд миграции 021 автоматически покрыт test_alembic_online_upgrade.py (glob), но backend-dev обязан прогнать локально: прод накатывает этим же online-путём, отката нет. Переиспользовать общий хелпер extract_manual_quote() из Q-1. save_message() best-effort (try/except с warning) — сбой извлечения цитаты деградирует до «сообщение без полей цитаты», не крэш.'
  result: null
- id: Q-4
  title: 'C(quote-persist)/qa: интеграционный тест round-trip (message_saver сохраняет quote_text/quote_is_manual) + подтвердить, что online-upgrade guard покрывает миграцию 021'
  specialist: qa
  priority: P2
  status: pending
  depends_on:
  - Q-3
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: d71fa183-d5cc-4995-bc6c-3d28ea6bae02
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: 'Добавлен вместе с Q-3 по решению [Q-PERSIST]. test_alembic_online_upgrade.py находит миграции glob-ом, поэтому 021 покрывается автоматически без правки тест-файла — но прогнать явно (прод накатывает online, отката нет). Основной новый тест: round-trip через message_saver — сохранили Message с ручной цитатой → в chat_messages легли quote_text и quote_is_manual; сообщение без цитаты → оба NULL. В фикстурах использовать заведомо фейковые telegram id (репо публичный, gitleaks их не ловит).'
  result: null
- id: Q-5
  title: 'C(quote-consume): учёт сохранённой ручной цитаты в историческом контексте реплаев — quote_text/quote_is_manual в get_recent_with_topic_context (3 SELECT-а, UNION ALL по позиции) + аннотация в prompt_builder._format_message через sanitize_prompt_content с отдельным лимитом ~150-200 симв.'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - Q-1
  - Q-3
  estimated_effort: 2.5h
  confidence: null
  consult_session_id: 45d74f2f-e882-41e8-bdc2-882cae6dc190
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: 'Добавлен по решению [Q-CONSUME]=(б): бот должен УЧИТЫВАТЬ сохранённые ручные цитаты в последующих репликах, а не только сохранять их (Q-3). Единственный путь чтения истории из chat_messages в контекст модели, совпадающий с формулировкой «в последующих репликах» — get_recent_with_topic_context() -> pipeline.process() -> prompt_builder._format_message(). Реализация: добавить quote_text/quote_is_manual во все ТРИ SELECT-списка get_recent_with_topic_context (не-forum ветка + ОБЕ стороны UNION ALL, колонки в одинаковой позиции — иначе Postgres падает в рантайме); в _format_message при quote_is_manual is True и непустом quote_text дописывать инлайн-аннотацию к строке сообщения, прогоняя quote_text через sanitize_prompt_content() (тот же двойной забор, что и content). Отдельный небольшой лимит длины цитаты ~150-200 симв (путь рендерит до ~30 историч. сообщений за ход). Старые строки без миграции 021: quote_text = NULL -> нет аннотации. RAG (rag_memories) НЕ трогаем — отдельная таблица. Гейт на quote_is_manual is True — defense in depth независимо от того, как Q-3 пишет поле. Приоритет P1/P2 — решение владельца; по умолчанию P2 в тон семье персиста (Q-3/Q-4). depends_on Q-3 (жёстко: нужны колонки и данные) и Q-1 (по последовательности: чтобы формулировка про выделенный фрагмент совпадала с живым путём Q-1).'
  result: null
- id: Q-6
  title: 'C(quote-consume)/qa: интеграционный тест на Postgres — get_recent_with_topic_context возвращает quote-колонки и UNION ALL проходит type-check + регресс на quote-инъекцию в историческом quote_text + гейтинг quote_is_manual + усечение + обратная совместимость NULL'
  specialist: qa
  priority: P2
  status: pending
  depends_on:
  - Q-5
  estimated_effort: 2h
  confidence: null
  consult_session_id: d71fa183-d5cc-4995-bc6c-3d28ea6bae02
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: 'Добавлен вместе с Q-5 по решению [Q-CONSUME]=(б). У MessageRepository сейчас НОЛЬ тестов, поэтому SQL-изменение (правильные ли колонки возвращаются, проходит ли UNION ALL type-check на реальной БД) — обязательный qa-owned интеграционный тест на Postgres+pgvector (testcontainers). Плюс регресс на quote-инъекцию: payload, ломающий разметку блока chat_history, в историческом quote_text не должен выходить за пределы блока (аналог теста Q-2 для живого пути). Плюс проверки: гейтинг quote_is_manual (server-injected цитата НЕ попадает в аннотацию), усечение длинной цитаты, обратная совместимость строк с quote_text = NULL (нет аннотации). Юнит-логику рендера/санитайза/усечения _format_message backend-dev покрывает своими юнит-тестами на простых dict; здесь — интеграция и безопасность. В фикстурах — заведомо фейковые telegram id (репо публичный, gitleaks их не ловит).'
  result: null
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 0.0
review_gate:
  why: []
  approve_action: /execute-plan /Users/julia/my-projects/telegram-chat-companion.summary-mentions-quotes-2026-08-04-wt/docs/plans/summary-mentions-quotes-2026-08-04.execution.md --resume
  reject_action: /plan-fixes docs/plans/summary-mentions-quotes-2026-08-04.md --revise /Users/julia/my-projects/telegram-chat-companion.summary-mentions-quotes-2026-08-04-wt/docs/plans/summary-mentions-quotes-2026-08-04.execution.md
safe_to_replay_from: null
clarifying_questions: []
human_feedback:
- ts: '2026-08-04T07:12:31Z'
  by: julia
  text: 'ANSWER [M-1]: (1) всех участников сообщения, без переключателя'
  applies_to: M-1
  status: addressed
  addressed_at: '2026-08-04T07:19:53Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-04T07:12:51Z'
  by: julia
  text: 'ANSWER [E-1]: B — свободно, на усмотрение модели'
  applies_to: E-1
  status: addressed
  addressed_at: '2026-08-04T07:20:23Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-04T07:13:09Z'
  by: julia
  text: 'ANSWER [Q-1]: отдавать и фрагмент, и полное сообщение с пометкой.'
  applies_to: Q-1
  status: addressed
  addressed_at: '2026-08-04T07:21:35Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-04T07:13:34Z'
  by: julia
  text: 'ANSWER [Q-PERSIST]: давай сделаем сразу'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-04T07:22:21Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-04T07:28:14Z'
  by: julia
  text: 'ANSWER [Q-CONSUME]: (б) добавить ещё и использование сохранённых цитат в истории'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-04T07:37:01Z'
  addressed_by: pm-orchestrator
revision_number: 3
last_revised_at: '2026-08-04T07:37:04Z'
last_revised_by: pm-orchestrator
---















































<!-- BRIEF:START -->
# Саммари: кликабельные имена, emoji-разметка и учёт выделенной цитаты

## Что произошло

Разобран список из трёх пожеланий владельца к боту: два про команду саммари и одно — про
то, как бот отвечает на реплай с выделенным фрагментом. Пожелания между собой не связаны и
могут выполняться независимо. По итогам разбора третий пункт расширили: выделенные цитаты
будут не только сохраняться в историю переписки, но и учитываться ботом в последующих ответах —
по вашему решению. Крупной перестройки бота работа не требует; изменения в базе данных
небольшие и безопасные.

## Найденные проблемы

1. **В саммари участники подписаны техническим ником, а не именем из чата.** Сейчас в списке
   стоит `@ник`, по которому к тому же нельзя перейти к человеку. Просили обратное — показывать
   видимое имя и делать его кликабельным. Здесь два подводных камня: (а) у кликабельного имени
   есть побочный эффект — упомянутый человек получает уведомление, и большое саммари может
   разослать пинги пол-чату; (б) имя может содержать «ловушку» из спецсимволов, ломающую
   оформление сообщения, — это ещё и брешь в безопасности, которую надо закрыть.
2. **Саммари почти без визуального оформления** — один значок в заголовке, дальше сплошной
   текст, который тяжело читать. Просили использовать emoji как разметку по смысловым блокам.
3. **Бот не различает ответ на выделенный кусок и ответ на всё сообщение.** Когда человек
   отвечает на конкретный фрагмент, бот всё равно берёт сообщение целиком и может ответить не
   на то, что имелось в виду. Здесь тоже есть риск безопасности: в выделенный текст можно
   спрятать постороннюю команду боту — её надо обезвредить.

## Что будет сделано

- В саммари участники станут отображаться видимым именем, по которому можно перейти к человеку;
  упоминаются все участники сообщения, без отдельного переключателя (по вашему решению).
  «Ловушки» в именах обезврежены, порядок безопасной вставки зафиксирован в проектной заметке,
  чтобы позже случайно не сломать (M-1, M-2, M-3).
- Саммари получит emoji-разметку: бот сам расставляет уместные emoji по смыслу (по вашему
  решению — свободно, без жёсткого шаблона блоков), одинаково на русском и английском.
  Качество проверяется живым прогоном на обычных (дешёвых) моделях (E-1, E-2).
- Бот научится учитывать именно выделенный фрагмент, не теряя общий смысл сообщения; попытки
  спрятать команду в цитате обезврежены (Q-1, Q-2).
- Выделенные цитаты начнут сохраняться в историю переписки, чтобы не терялось, на какой
  именно фрагмент отвечали (Q-3, Q-4).
- Бот будет учитывать сохранённые цитаты и в следующих ответах: видя, что человек когда-то
  выделил конкретный фрагмент, он точнее понимает контекст прошлых сообщений (по вашему
  решению). Попытки спрятать команду в такой сохранённой цитате тоже обезврежены (Q-5, Q-6).

## Не входит в этот план

- Вариант с отдельным переключателем упоминаний в настройках чата — отдельная работа с
  изменением базы данных; по умолчанию сейчас не закладывается.
- Точное повторное форматирование внутри цитаты — сохраняем только сам текст выделения и признак
  «выделено вручную»; при необходимости остальное легко добавить позже.
- Учёт выделенных цитат в самой команде саммари — оставлен за рамками: запрос был про обычные
  ответы бота, а не про саммари; при желании легко вынести отдельным пунктом.

## Оценка

Суммарно около 24 часов работы; потолок бюджета — $30 на план (до $6 на отдельный пункт).
<!-- BRIEF:END -->

# Plan — summary-mentions-quotes-2026-08-04

## Source

[`docs/plans/summary-mentions-quotes-2026-08-04.md`](docs/plans/summary-mentions-quotes-2026-08-04.md) (sha256 `726eb8b0ce4b...`).

## Items

(none yet — populated by /plan-fixes)
