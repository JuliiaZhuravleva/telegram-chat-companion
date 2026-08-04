---
schema_version: 3
plan_id: summary-mentions-quotes-2026-08-04
source_artifact:
  path: docs/plans/summary-mentions-quotes-2026-08-04.md
  sha256: 726eb8b0ce4b0c715e6ba693f195a72a72c318e6fb37d066774e3ddedc88113b
  type: session-analysis
created_at: '2026-08-04T00:26:12Z'
approved_at: null
approved_by: null
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: draft
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
    note: null
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
  title: 'B(emoji): фиксированный emoji-скелет разметки в системном промпте /summary (ru+en)'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - M-1
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 45d74f2f-e882-41e8-bdc2-882cae6dc190
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: E-2
  title: 'B(emoji)/qa: unit-тест emoji×markdown_to_html + живой прогон формата на дешёвых моделях'
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
    note: null
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
    note: null
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
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 0.0
review_gate:
  why: []
  approve_action: /execute-plan /Users/julia/my-projects/telegram-chat-companion.summary-mentions-quotes-2026-08-04-wt/docs/plans/summary-mentions-quotes-2026-08-04.execution.md --resume
  reject_action: /plan-fixes docs/plans/summary-mentions-quotes-2026-08-04.md --revise /Users/julia/my-projects/telegram-chat-companion.summary-mentions-quotes-2026-08-04-wt/docs/plans/summary-mentions-quotes-2026-08-04.execution.md
safe_to_replay_from: null
clarifying_questions:
- '[M-1] Кого упоминать в саммари? Варианты: (1) всех участников сообщения; (2) только
  3-5 ключевых; (3) сделать кликабельность упоминаний отключаемой отдельной настройкой
  чата. Вариант 3 потребует изменения структуры базы данных и в текущий объём не заложен.
  Рекомендация по умолчанию: упоминать всех участников сообщения, без переключателя.
  Утверждаем этот вариант?'
- '[E-1] Как расставлять emoji в саммари? Вариант A — фиксированный набор блоков (🗣 темы,
  👥 участники, ✅ решения, ❓ вопросы, 🔥 конфликты): предсказуемо и проверяемо тестом.
  Вариант B — свободно, на усмотрение модели: живее, но результат каждый раз разный и его
  нельзя закрепить автотестом. Рекомендация: фиксированный скелет со свободой формулировок
  внутри блоков. Устраивает такой набор?'
- '[Q-1] Что показывать боту при ответе на выделенный фрагмент? Только сам фрагмент, или
  фрагмент плюс всё сообщение с пометкой «пользователь выделил вот это»? Фрагмент без
  окружающего текста может исказить смысл на противоположный. Рекомендация: отдавать и
  фрагмент, и полное сообщение с пометкой. Подтверждаем?'
- '[Q-PERSIST] Сохранять ли выделенную цитату в историю сообщений? Сейчас история ответов
  не помнит, на какой именно фрагмент отвечали. Это отдельная работа с изменением структуры
  базы данных, в текущий план она не входит. Рекомендация: отложить (не входит в этот план).
  Согласны отложить?'
---















<!-- BRIEF:START -->
# Саммари: кликабельные имена, emoji-разметка и учёт выделенной цитаты

## Что произошло

Разобран список из трёх пожеланий владельца к боту: два про команду саммари и одно — про
то, как бот отвечает на реплай с выделенным фрагментом. Пожелания между собой не связаны и
могут выполняться независимо. Ни одно не требует новой команды или перестройки бота — это
точечные улучшения.

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
  «ловушки» в именах обезврежены, порядок безопасной вставки зафиксирован в проектной заметке,
  чтобы позже случайно не сломать (M-1, M-2, M-3).
- Саммари получит аккуратную emoji-разметку по блокам (темы, участники, решения, вопросы),
  одинаково на русском и английском; формат проверен и автотестом, и живым прогоном (E-1, E-2).
- Бот научится учитывать именно выделенный фрагмент, не теряя общий смысл сообщения; попытки
  спрятать команду в цитате обезврежены (Q-1, Q-2).

## Не входит в этот план

- Сохранение выделенной цитаты в историю переписки (потребует изменения базы данных) — вынесено
  на отдельное решение.
- Вариант с отдельным переключателем упоминаний в настройках чата — тоже отдельная работа с
  изменением базы данных; по умолчанию сейчас не закладывается.

## Оценка

Суммарно около 14 часов работы; потолок бюджета — $30 на план (до $6 на отдельный пункт).
<!-- BRIEF:END -->

# Plan — summary-mentions-quotes-2026-08-04

## Source

[`docs/plans/summary-mentions-quotes-2026-08-04.md`](docs/plans/summary-mentions-quotes-2026-08-04.md) (sha256 `726eb8b0ce4b...`).

## Items

(none yet — populated by /plan-fixes)
