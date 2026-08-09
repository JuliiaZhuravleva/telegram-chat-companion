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
  title: Показывать уровень допустимости стикера во всех карточках DM админу
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
    ts: null
    executor: null
    note: null
  result: null
- id: A-2
  title: 'ADR: приоритет ручной оценки стикера над повторным анализом (дополнение к ADR-0008)'
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
    ts: null
    executor: null
    note: null
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
  title: Ручной ввод/сброс уровня допустимости в карточке стикера + бейдж «вручную»
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A-3
  - A-1
  estimated_effort: ~2ч, средний риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
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
    ts: null
    executor: null
    note: null
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
  title: 'Параметр количества для /summary (максимум 1000): парсинг аргумента, валидация с понятной ошибкой, дефолт, фильтр темы форума, контроль токенов/стоимости'
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
- id: E-2
  title: (Опционально) отдельная команда /summary500 — реестр команд + меню, только если владелец решит оставить её вдобавок к параметру
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - E-1
  estimated_effort: ~1ч, низкий риск
  confidence: null
  consult_session_id: 298c5fcf-e6dd-4dbe-b33e-8ffc98ad5319
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-09T11:13:47Z'
    executor: pm-orchestrator
    note: 'Ждёт решения владельца по вопросу [E]: нужна ли отдельная команда /summary500 вдобавок к параметру /summary <n>. По умолчанию (рекомендация backend/qa) — параметра достаточно, этот пункт выбрасывается. Разблокировать только если владелец явно хочет отдельную команду.'
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
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 0.0
review_gate:
  why: []
  approve_action: /execute-plan docs/plans/admin-ux-and-summary-2026-08-09.execution.md
  reject_action: /plan-fixes docs/plans/admin-ux-and-summary-2026-08-09.md --revise docs/plans/admin-ux-and-summary-2026-08-09.execution.md
safe_to_replay_from: null
clarifying_questions:
- '[Q1] «Уровень допустимости» самого стикера и «уровень приличия» чата — это две разные величины (оценка конкретного стикера и общий потолок для чата), а называются сейчас почти одинаково и потому путаются. Предлагаю закрепить разные слова: у стикера — «оценка откровенности», у чата оставить «уровень приличия». Согласны на такую пару, или хотите сохранить именно вашу формулировку «уровень допустимости» для стикера?'
- '[A-1] Как показывать уровень стикера в карточке админу: голым числом (например, 0.37), словами (мягкий / средний / явный) или сразу с понятным вердиктом «пройдёт / не пройдёт» для текущего чата? Рекомендую одну короткую строку: число + порог чата + вердикт (например «0.37 · порог чата 0.50 · не пройдёт»). Подходит?'
- '[A-4] Как админ будет ставить оценку вручную: вводить число от 0 до 1 текстом или выбирать из готовых кнопок-пресетов? Число точнее, кнопки — быстрее и в пару нажатий. Рекомендую ввод числа (как уже сделано для уровня чата, с кнопкой отмены). Что предпочитаете?'
- '[B-1] Группировка настроек чата — самый крупный пункт. Предлагаю целевую схему: по отдельному экрану на каждую группу (как уже работает у «Базы знаний» и «Реакций»), а корневой экран — список разделов с кратким статусом. Подтвердите эту схему, прежде чем вкладывать в неё основные усилия, или хотите сначала обсудить альтернативы?'
- '[E] Команды /summary500 сейчас в боте нет вообще. Достаточно ли добавить параметр количества к обычной команде (/summary 500, максимум 1000), или нужна ещё и отдельная команда /summary500 как привычный шорткат? Рекомендую ограничиться параметром — он закрывает запрос и дешевле в поддержке; отдельную команду сделаем, только если она вам нужна (сейчас этот пункт отложен до вашего ответа).'
- '[Q2] Пункты про личку админа (карточка стикера, панель настроек, пикер чатов, шорткат) нельзя полностью проверить автоматическими тестами — после сборки нужен ручной прогон в самом Telegram. Предлагаю оставить этот ручной смоук-тест за вами (мы прикладываем автотесты + чек-лист по шагам). Подходит так, или нужно оформить отдельный шаг проверки на кого-то ещё?'
---



























<!-- BRIEF:START lang=ru -->
# Админка бота: удобные настройки, уровень стикеров и сводка по числу сообщений

## Что произошло
Разобрали ваш запрос на доработку админки в личке бота — шесть пунктов про управление
стикерами, настройки чатов и команду сводки. По каждому пункту сверились с тем, как бот
устроен сейчас, и проверили, где нужны не только правки интерфейса, но и изменения в данных.

## Найденные проблемы
- Когда бот присылает вам карточку стикера, в ней **не видно его уровня допустимости** —
  непонятно, пропустит ли бот такой стикер в чат.
- Поставить стикеру уровень вручную сейчас нельзя; а если просто добавить такую кнопку,
  следующий автоматический анализ **молча затрёт** вашу оценку — фича будет бесполезной без
  правила, которое её сохранит.
- Настройки чата идут **одним длинным списком** — листать неудобно, как вы и отметили.
- В меню выбора чата чаты идут по алфавиту, и **самые активные приходится искать** вручную.
- Чтобы открыть настройки конкретной группы, каждый раз нужно проходить через общий список.
- Команды для сводки по числу сообщений (той самой `/summary500`) в боте **нет** — её не
  перенесли со старой системы.

## Что будет сделано
- В карточку стикера добавим его уровень с понятной пометкой и признаком «пройдёт / не
  пройдёт» для текущего чата (A-1). Появится возможность выставить и сбросить уровень вручную
  с пометкой «вручную», причём ручная оценка переживёт повторный анализ (A-2, A-3, A-4, Q-1).
- Настройки чата разложим по разделам с отдельными экранами и понятным возвратом «назад»
  (B-1, B-2, B-3).
- В меню выбора чата активные чаты поднимутся наверх, рядом — счётчик сообщений за сутки (C-1).
- Появится быстрый переход к настройкам группы по ссылке или названию, со списком вариантов
  при совпадениях (D-1).
- У сводки появится параметр количества сообщений (до 1000) с понятной ошибкой на неверный
  ввод (E-1).

## Не входит в этот план
- Отдельная команда `/summary500` — пока отложена: по умолчанию хватает параметра (см. вопрос
  про неё ниже).
- Режим «автопоиска через @бот» (inline) — требует внешней настройки бота, вынесен за рамки
  этой итерации.
- Ручной прогон в Telegram после сборки остаётся за вами; автотесты и чек-лист прилагаем.

## Оценка
Суммарно ≈ 18–24 часов работы. Потолок расходов — до $30 на весь план (до $6 на пункт).
<!-- BRIEF:END -->

# Plan — admin-ux-and-summary-2026-08-09

## Source

[`docs/plans/admin-ux-and-summary-2026-08-09.md`](docs/plans/admin-ux-and-summary-2026-08-09.md) (sha256 `5faac3c79c42...`).

## Items

(none yet — populated by /plan-fixes)
