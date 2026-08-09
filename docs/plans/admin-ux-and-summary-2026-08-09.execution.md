---
schema_version: 3
plan_id: admin-ux-and-summary-2026-08-09
source_artifact:
  path: docs/plans/admin-ux-and-summary-2026-08-09.md
  sha256: 5faac3c79c424ad7a4b576311bc24ab714dda7694bd114196ff991a248181e52
  type: feature-prd
created_at: '2026-08-09T11:11:01Z'
approved_at: null
approved_by: null
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: draft
  started_at: null
  completed_at: null
  current_batch: null
  task_list_id: admin-ux-and-summary-2026-08-09
items:
- id: A-1
  title: 'Показывать оценку откровенности стикера во всех карточках DM админу (строка: оценка · порог чата · пройдёт/не пройдёт)'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: ~2ч, средний риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T11:38:56Z'
    executor: pm-orchestrator
    note: 'Формат подтверждён владельцем (ответ A-1): одна короткая строка «оценка · порог чата · вердикт пройдёт/не пройдёт». Термин стикера — «оценка откровенности» (Q1).'
  result: null
- id: A-2
  title: 'ADR: приоритет ручной оценки стикера над повторным анализом + закрепление терминов «оценка откровенности» (стикер) и «уровень приличия» (чат) — дополнение к ADR-0008'
  specialist: architect
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: ~1.5ч, средний риск
  confidence: null
  consult_session_id: da7e0b66-7a36-4adb-af45-63ddac2d67d0
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T11:39:05Z'
    executor: pm-orchestrator
    note: 'Владелец утвердил (Q1) терминологию: стикер — «оценка откровенности», чат — «уровень приличия». ADR должен зафиксировать это как глоссарий; дальше следовать во всём UI/копирайте (A-1, A-4, B-3).'
  result: null
- id: A-3
  title: 'Данные ручной оценки: миграция-маркер «выставлено вручную» + приоритет в апсерте + метод записи'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A-2
  estimated_effort: ~2ч, средне-высокий риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A-4
  title: 'Ручной ввод/сброс оценки откровенности стикера: кнопки-пресеты + ручной ввод числа 0.0–1.0, сброс к авто, бейдж «вручную»'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A-3
  - A-1
  estimated_effort: ~2.5ч, средний риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T11:39:18Z'
    executor: pm-orchestrator
    note: 'Владелец (A-4): делаем и кнопки-пресеты, и ручной ввод числа; плюс сброс к автоматическому значению и бейдж «вручную». Переиспользовать FSM-ввод из tolerance_level.'
  result: null
- id: B-1
  title: Схема навигации сгруппированной панели настроек (экран-на-группу, возврат, «где я») — дополнение к ADR-0006
  specialist: architect
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: ~2ч, средний риск
  confidence: null
  consult_session_id: da7e0b66-7a36-4adb-af45-63ddac2d67d0
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T11:39:22Z'
    executor: pm-orchestrator
    note: 'Схема подтверждена владельцем (B-1): отдельный экран на каждую группу + корневой экран со списком разделов и кратким статусом. Реализация — B-2.'
  result: null
- id: B-2
  title: Реализовать сгруппированную навигацию панели настроек (корневой список разделов + под-экраны, предсказуемый возврат, лимиты Telegram)
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - B-1
  estimated_effort: ~3–5ч, высокий риск (самый крупный пункт)
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: B-3
  title: 'Копирайт: подписи полей/групп и краткие статусы на корневом экране новой панели'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - B-2
  estimated_effort: ~1ч, низкий риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: C-1
  title: Сортировать пикер чатов по числу сообщений за 24ч + счётчик в подписи (один агрегатный запрос, без N+1)
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: ~1–1.5ч, низкий риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: D-1
  title: Шорткат к панели чата по ссылке/названию (список кандидатов при неоднозначности, доступ только админам/whitelist, безопасный роутинг). Inline-режим вне скоупа.
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - B-2
  estimated_effort: ~2.5–4.5ч, средне-высокий риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: E-1
  title: 'Параметр количества для /summary: /summary <n> (дефолт 100, минимум 20, максимум 1000); при n<20 — вежливый отказ «столько можно прочитать и самому»; валидация, фильтр темы форума, контроль токенов/стоимости'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: ~1–1.5ч, низкий риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T11:39:33Z'
    executor: pm-orchestrator
    note: 'Владелец (E): голая /summary остаётся дефолт 100; минимум 20 — при меньшем значении отвечаем, что столько можно прочитать и самому; максимум 1000. Быстрая команда вынесена отдельным пунктом E-2 (разблокирован).'
  result: null
- id: E-2
  title: 'Быстрая команда /summary500 (сводка по 500 сообщениям): регистрация в command_registry.py + меню команд (3 языковых варианта)'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - E-1
  estimated_effort: ~1ч, низкий риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T11:39:42Z'
    executor: pm-orchestrator
    note: 'Разблокировано владельцем (E): нужна и быстрая команда вдобавок к параметру. /summary500 = сводка по 500 сообщениям. Обязательна спека в command_registry.py, иначе CI падает; три языковых варианта скоупа.'
  result: null
- id: Q-1
  title: 'Интеграционный тест: ручная оценка стикера переживает повторный анализ (расширить TestExplicitnessScoreUpsert, реальная схема Postgres)'
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - A-3
  estimated_effort: ~1ч, низкий риск
  confidence: null
  consult_session_id: 61c8b3a2-fffe-451e-bd7a-d240383c5c84
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: Q-2
  title: Чек-лист ручного смоук-теста в личке понятным языком + подготовленные тестовые данные/фикстуры для минимального ручного прогона
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - A-4
  - B-2
  - C-1
  - D-1
  estimated_effort: ~1.5ч, низкий риск
  confidence: null
  consult_session_id: 61c8b3a2-fffe-451e-bd7a-d240383c5c84
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T11:39:57Z'
    executor: pm-orchestrator
    note: 'Новый пункт по ответу владельца (Q2): к ручному смоук-тесту приложить чек-лист понятным языком и заранее подготовленные тестовые данные, чтобы прогон требовал минимум раздумий. Покрывает карточку стикера (A-1/A-4), панель настроек (B-2), пикер чатов (C-1), шорткат (D-1).'
  result: null
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 0.0
review_gate:
  why: []
  approve_action: /execute-plan docs/plans/admin-ux-and-summary-2026-08-09.execution.md
  reject_action: /plan-fixes docs/plans/admin-ux-and-summary-2026-08-09.md --revise docs/plans/admin-ux-and-summary-2026-08-09.execution.md
safe_to_replay_from: null
clarifying_questions: []
human_feedback:
- ts: '2026-08-09T11:31:39Z'
  by: julia
  text: 'ANSWER [Q1]: Да, давай твою формулировку. Главное её зафиксировать в документации и в будущем следовать везде ему.'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T11:40:37Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:31:57Z'
  by: julia
  text: 'ANSWER [A-1]: Да, подходит'
  applies_to: A-1
  status: addressed
  addressed_at: '2026-08-09T11:40:41Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:32:21Z'
  by: julia
  text: 'ANSWER [A-4]: Сделай кнопки плюс возможность вручную ввести число'
  applies_to: A-4
  status: addressed
  addressed_at: '2026-08-09T11:40:45Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:32:42Z'
  by: julia
  text: 'ANSWER [B-1]: Да, всё так делаем'
  applies_to: B-1
  status: addressed
  addressed_at: '2026-08-09T11:40:48Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:33:08Z'
  by: julia
  text: "ANSWER [E]: и параметр и быструю команду делаем \n\nи можно вызывать просто /summary по дефолту остается 100\n\nкстати забыли еще минимум указать, я бы наверное взяла 20. Если ввели меньше то отвечать что можешь сам прочитать"
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T11:40:51Z'
  edited_at: '2026-08-09T11:33:56Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-09T11:35:15Z'
  by: julia
  text: 'ANSWER [Q2]: Да, чеклист обязательно надо сделать, ещё и понятным языком, плюс по максимуму подготовить данные для теста, чтобы для ручного теста можно было по минмуму задумываться'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-09T11:40:55Z'
  addressed_by: pm-orchestrator
revision_number: 2
last_revised_at: '2026-08-09T11:42:07Z'
last_revised_by: pm-orchestrator
---
























































<!-- BRIEF:START lang=ru -->
# Админка бота: удобные настройки, оценка откровенности стикеров и сводка по числу сообщений

## Что произошло
Разобрали ваш запрос на доработку админки в личке бота — шесть пунктов про управление
стикерами, настройки чатов и команду сводки. По каждому пункту сверились с тем, как бот
устроен сейчас, и учли ваши ответы на уточняющие вопросы.

## Найденные проблемы
- Когда бот присылает вам карточку стикера, в ней **не видно его оценки откровенности** —
  непонятно, пропустит ли бот такой стикер в чат.
- Поставить стикеру оценку вручную сейчас нельзя; а если просто добавить такую кнопку,
  следующий автоматический анализ **молча затрёт** вашу оценку — фича будет бесполезной без
  правила, которое её сохранит.
- Настройки чата идут **одним длинным списком** — листать неудобно, как вы и отметили.
- В меню выбора чата чаты идут по алфавиту, и **самые активные приходится искать** вручную.
- Чтобы открыть настройки конкретной группы, каждый раз нужно проходить через общий список.
- Команды для сводки по числу сообщений (той самой `/summary500`) в боте **нет** — её не
  перенесли со старой системы.

## Что будет сделано
- В карточку стикера добавим его оценку откровенности одной строкой с признаком «пройдёт /
  не пройдёт» для текущего чата (A-1). Оценку можно будет выставить кнопками-пресетами или
  ввести числом вручную, а также сбросить обратно к автоматической; ручная оценка переживёт
  повторный анализ (A-2, A-3, A-4, Q-1). Термины закрепим: у стикера — «оценка откровенности»,
  у чата — «уровень приличия».
- Настройки чата разложим по разделам с отдельными экранами и понятным возвратом «назад»
  (B-1, B-2, B-3).
- В меню выбора чата активные чаты поднимутся наверх, рядом — счётчик сообщений за сутки (C-1).
- Появится быстрый переход к настройкам группы по ссылке или названию, со списком вариантов
  при совпадениях (D-1).
- У сводки: обычная `/summary` берёт 100 сообщений по умолчанию, можно указать число
  (`/summary 500`, до 1000); если попросить меньше 20 — бот вежливо ответит, что столько
  проще прочитать самому. Плюс вернём привычную быструю команду `/summary500` (E-1, E-2).
- Приложим чек-лист ручной проверки понятным языком и заранее подготовленные тестовые данные,
  чтобы ваш ручной прогон в Telegram занял минимум сил (Q-2).

## Не входит в этот план
- Режим «автопоиска через @бот» (inline) — требует внешней настройки бота, вынесен за рамки
  этой итерации.
- Сам ручной прогон в Telegram после сборки остаётся за вами (автотесты, чек-лист и готовые
  тестовые данные мы подготовим).

## Оценка
Суммарно ≈ 20–27 часов работы. Потолок расходов — до $30 на весь план (до $6 на пункт).
<!-- BRIEF:END -->

# Plan — admin-ux-and-summary-2026-08-09

## Source

[`docs/plans/admin-ux-and-summary-2026-08-09.md`](docs/plans/admin-ux-and-summary-2026-08-09.md) (sha256 `5faac3c79c42...`).

## Items

(none yet — populated by /plan-fixes)
