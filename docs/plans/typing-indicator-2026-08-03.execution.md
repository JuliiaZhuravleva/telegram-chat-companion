---
schema_version: 3
plan_id: typing-indicator-2026-08-03
source_artifact:
  path: docs/plans/typing-indicator-2026-08-03.md
  sha256: 3af44bcd830163e84332448f7581b02a320ec762d56266b222a4a9ab74f4471b
  type: session-analysis
created_at: '2026-08-03T12:19:55Z'
approved_at: '2026-08-03T12:35:46Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: approved
  started_at: null
  completed_at: null
  current_batch: null
  task_list_id: typing-indicator-2026-08-03
items:
- id: I-6
  title: Общий помощник «бот печатает» (keep-alive + гарантированная остановка, обязательный message_thread_id, тип действия параметром) + перевод голос/видео-обработчика на него — чинит I-1, поглощает I-8/I-9
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 2.5h
  confidence: null
  consult_session_id: 80a3ccf0-d4d9-413c-b0f7-b7249114ae8c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: I-2
  title: Индикатор на основном текстовом пути (ответ на упоминание/триггер/реплай, pipeline.process) — самый частый пробел
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - I-6
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 80a3ccf0-d4d9-413c-b0f7-b7249114ae8c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: I-3
  title: Индикатор на анализе фото (+ follow-on генерация для фото с подписью); upload_photo где честно
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - I-6
  estimated_effort: 1h
  confidence: null
  consult_session_id: 80a3ccf0-d4d9-413c-b0f7-b7249114ae8c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: I-5
  title: 'Индикатор на AI-командах: /remember (эмбеддинг) и админ-мастер стикеров; добавить message_thread_id в handle_remember (сейчас отсутствует)'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - I-6
  estimated_effort: 1h
  confidence: null
  consult_session_id: 80a3ccf0-d4d9-413c-b0f7-b7249114ae8c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: I-4
  title: Индикатор на обучении на стикерах (vision) — ответ не гарантирован, актуальность зависит от решения владельца (Q2)
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - I-6
  estimated_effort: 0.5h
  confidence: null
  consult_session_id: 80a3ccf0-d4d9-413c-b0f7-b7249114ae8c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: 'СНЯТО решением владельца 2026-08-03 (Q2: «Не показывать»). Операции без гарантированного ответа индикатор не получают. Остаётся blocked, чтобы не диспетчеризоваться.'
  result: null
- id: I-7
  title: Свести /summary к общему индикатору либо оставить текстовую заглушку — решение владельца (Q3)
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - I-6
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 80a3ccf0-d4d9-413c-b0f7-b7249114ae8c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: 'СНЯТО решением владельца 2026-08-03 (Q3: «Да, оставим»). /summary сохраняет текстовую заглушку. Остаётся blocked, чтобы не диспетчеризоваться.'
  result: null
- id: I-10
  title: Per-chat выключатель индикатора (nullable-колонка chat_settings + ChatConfig + трёхслойный мердж) — решение владельца (Q4), добавляет миграцию БД
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - I-6
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 80a3ccf0-d4d9-413c-b0f7-b7249114ae8c
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: 'СНЯТО как отдельная работа решением владельца 2026-08-03 (Q4: по дефолту включён, но с заделом на будущую настройку в БД). Миграции в этом плане нет; задел перенесён в приёмку I-6 — признак включённости проходит через одну точку (параметр помощника со значением по умолчанию True, читаемый из ChatConfig), чтобы добавление колонки chat_settings позже было правкой в одном месте. Остаётся blocked.'
  result: null
- id: C-1
  title: 'ADR: паттерн общего TypingIndicator (as-built, после I-6) + зафиксировать отклонённую альтернативу с middleware'
  specialist: architect
  priority: P2
  status: pending
  depends_on:
  - I-6
  estimated_effort: 0.5h
  confidence: null
  consult_session_id: 43958ece-c2aa-4c81-b664-319614913876
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
  approve_action: /execute-plan /Users/julia/my-projects/telegram-chat-companion.typing-indicator-2026-08-03-wt/docs/plans/typing-indicator-2026-08-03.execution.md --resume
  reject_action: /plan-fixes docs/plans/typing-indicator-2026-08-03.md --revise /Users/julia/my-projects/telegram-chat-companion.typing-indicator-2026-08-03-wt/docs/plans/typing-indicator-2026-08-03.execution.md
safe_to_replay_from: null
clarifying_questions:
- '[I-2] Показывать ли «печатает» перед случайными (незапрошенными) репликами бота — когда он сам вступает в разговор? Такой индикатор выглядит навязчиво и вдобавок анонсирует ответ, который бот может передумать отправлять (внутренняя проверка релевантности иногда отменяет реплику — тогда «печатает» окажется ложью). Рекомендация: НЕ показывать перед случайными репликами, только перед ответами на упоминание/триггер/реплай. Оставляем так?'
- '[I-4] Показывать ли индикатор на операциях, где бот может ничего не ответить — обучение на присланном стикере и анализ фото без подписи? Тут «печатает» иногда не завершится ответом. Рекомендация: НЕ показывать на таких операциях (пункт I-4 тогда снимается, для фото без подписи индикатор не включаем). Согласны или всё-таки показывать?'
- '[I-7] Приводить ли /summary к общему индикатору «печатает» или оставить текущую текстовую заглушку «Генерирую саммари...»? Заглушка живёт дольше 5 секунд и её можно превратить в сообщение об ошибке, а индикатор таких гарантий не даёт — на длинных операциях заглушка честнее. Рекомендация: оставить заглушку для /summary (пункт I-7 тогда снимается). Оставляем?'
- '[I-10] Сделать индикатор настраиваемым для каждого чата (отдельный выключатель в настройках) или он всегда включён? Выключатель требует изменения схемы базы данных (новая колонка), которого в остальном плане нет. Рекомендация: всегда включён в первой версии (пункт I-10 тогда снимается). Если выключатель нужен — лучше решить сейчас, до старта остальных пунктов, чтобы не переделывать 5 мест. Как поступаем?'
human_feedback:
- ts: '2026-08-03T12:25:29Z'
  by: julia
  text: 'ANSWER [I-2]: Давай не показывать'
  applies_to: I-2
  status: addressed
  addressed_at: '2026-08-03T12:31:36Z'
  addressed_by: julia
- ts: '2026-08-03T12:25:41Z'
  by: julia
  text: 'ANSWER [I-4]: Не показывать'
  applies_to: I-4
  status: addressed
  addressed_at: '2026-08-03T12:31:40Z'
  addressed_by: julia
- ts: '2026-08-03T12:26:00Z'
  by: julia
  text: 'ANSWER [I-7]: Да, оставим'
  applies_to: I-7
  status: addressed
  addressed_at: '2026-08-03T12:31:43Z'
  addressed_by: julia
- ts: '2026-08-03T12:26:33Z'
  by: julia
  text: 'ANSWER [I-10]: Давай сделаем пока по дефолту включенным, но оставим задел на будущую настройку в БД если она потребуется'
  applies_to: I-10
  status: addressed
  addressed_at: '2026-08-03T12:31:47Z'
  addressed_by: julia
---



































<!-- BRIEF:START lang=ru -->
# Индикатор «бот печатает» — включаем везде, где бот думает

## Что произошло
Вы заметили, что статус «бот печатает» показывается не всегда. Разбор кода подтвердил: сейчас индикатор включается лишь в одном сценарии примерно из десяти — при обработке голосовых сообщений и видеокружков, — да и там гаснет раньше времени. В большинстве случаев, пока бот думает, в чате не происходит ничего.

## Найденные проблемы
- На долгих операциях индикатор гаснет через ~5 секунд и не возобновляется: видно «печатает» пару секунд, потом тишина, потом внезапный ответ. Это и есть то самое «не всегда работает».
- Главный сценарий — ответ на упоминание, реплай или триггер — не показывает вообще ничего, хотя это самый частый и самый заметный случай.
- Анализ фото и обучение на присланных стикерах идут молча, иногда две долгие операции подряд.
- AI-команды (`/remember`, мастер стикеров у админа) — тоже без индикатора.
- Причина общая: единого механизма нет. Каждый обработчик должен помнить про индикатор сам — и десять из одиннадцати забыли. Пока общего механизма нет, следующая новая операция снова про него забудет.

## Что будет сделано
- Появится единый механизм, который держит «печатает» всё время работы бота и гарантированно гасит его в конце (даже при ошибке), корректно работает в темах форум-групп и умеет показывать честный тип действия (например, «выбирает стикер»). Разовый баг с угасанием на голосовых уходит вместе с ним. (I-6)
- Индикатор появится в главном текстовом сценарии (I-2), при анализе фото (I-3) и в AI-командах (I-5).
- По желанию — короткая заметка-решение о том, как пользоваться общим механизмом, чтобы будущие сценарии про него не забывали (C-1).

## Не входит в этот план (пока)
- Три пункта отложены до вашего решения (см. вопросы ниже): индикатор на обучении стикерам (I-4), судьба `/summary` (I-7) и персональный выключатель индикатора для чата (I-10). По умолчанию рекомендуем самый простой вариант — не усложнять.
- Визуальная проверка «пульсирует ли „печатает“» делается только вручную в Telegram Web; автотесты покрывают лишь механику (ритм повторов, гарантированную остановку, работу в форум-темах).

## Оценка
Базовый объём (I-6, I-2, I-3, I-5 и заметка C-1) — около 6.5 часов работы; с учётом отложенных по решению пунктов — до ~10 часов. Потолок бюджета — $30.
<!-- BRIEF:END -->

# Plan — typing-indicator-2026-08-03

## Source

[`docs/plans/typing-indicator-2026-08-03.md`](docs/plans/typing-indicator-2026-08-03.md) (sha256 `3af44bcd8301...`).

## Items

Решения владельца от 2026-08-03 (см. `human_feedback[]`) уже учтены ниже.

### I-6 — общий помощник «бот печатает» (P1, backend-dev, 2.5h)

Базовый пункт, от него зависят все остальные.

- **Строить вокруг `aiogram.utils.chat_action.ChatActionSender`** — он уже есть в зависимостях
  и в проекте не используется ни разу. Он же решает keep-alive: сам переотправляет действие
  каждые ~4с и гасит его на выходе из контекста, в том числе при исключении. Не писать свой
  цикл, если штатный покрывает задачу.
- `message_thread_id` — **обязательный** параметр, не опциональный. `bot.send_chat_action`
  не наследует тему, в отличие от `message.answer()`; без него в форум-супергруппах индикатор
  уходит в General. Нужна отдельная регрессионная проверка на это (требование qa).
- Тип действия — параметр, а не константа: `typing` по умолчанию, `choose_sticker` /
  `upload_photo` там, где бот заведомо отвечает стикером или картинкой.
- **Задел под будущий per-chat выключатель** (решение владельца по Q4): индикатор в v1 всегда
  включён, миграции БД в этом плане нет. Но признак включённости должен проходить через
  **одну точку** — параметр помощника со значением по умолчанию `True`, читаемый из `ChatConfig`,
  а не хардкод в каждом вызове. Тогда добавление колонки в `chat_settings` позже будет правкой
  в одном месте, а не в пяти. Колонку сейчас НЕ создавать.
- Перевести обработчик голоса/видеокружка ([media.py:80-99](../../src/bot/handlers/media.py#L80-L99))
  на помощник — это чинит I-1 (индикатор гаснет через 5с на длинной транскрипции).

### I-2 — основной текстовый путь (P1, backend-dev, 1.5h)

Обернуть `pipeline.process` в [message.py:119](../../src/bot/handlers/message.py#L119).

- **Решение владельца (Q1): для `TriggerType.RANDOM` индикатор НЕ показывать.** Только
  `TRIGGER`, `REPLY` и упоминания. Причина: перед незапрошенной репликой это навязчиво, и
  гейт релевантности может отменить ответ уже после показа индикатора — тогда «печатает»
  окажется ложью. Проверка на тип триггера должна быть в приёмке.

### I-3 — анализ фото (P1, backend-dev)

`image_service.analyze` в [media.py:145](../../src/bot/handlers/media.py#L145) и следующая за
ним генерация для фото с подписью ([media.py:170](../../src/bot/handlers/media.py#L170)) — две
длинные операции подряд, обе должны быть под индикатором.

- Для фото **с подписью** — показываем (ответ гарантирован).
- Для фото **без подписи** — не показываем: по решению владельца (Q2) операции без
  гарантированного ответа индикатор не получают.

### I-5 — AI-команды (P2, backend-dev)

- `/remember` — эмбеддинг в [commands.py:321](../../src/bot/handlers/commands.py#L321).
  Попутно: у `handle_remember` **сейчас нет параметра `message_thread_id`** — добавить,
  иначе в форумах индикатор (да и сам ответ) уйдёт не в ту тему.
- Админ-мастер стикеров — `merge_admin_description` в
  [admin_sticker.py:138](../../src/bot/handlers/admin_sticker.py#L138), LLM-вызов в личке.

### C-1 — ADR (P2, architect)

Пишется **после** I-6, как as-built: зафиксировать паттерн общего помощника, требование
обязательного `message_thread_id`, задел под per-chat выключатель и отклонённую альтернативу
через middleware.

### Снято решением владельца (не диспетчеризуется)

Пункты остаются в статусе `blocked` — так они не уйдут в работу, но причина сохраняется:

- **I-4** (индикатор на обучении стикерам) — снят по Q2: операции без гарантированного ответа
  индикатор не получают.
- **I-7** (`/summary` на общий индикатор) — снят по Q3: текстовая заглушка
  «⏳ Генерирую саммари...» остаётся. Она живёт дольше 5 секунд и может превратиться в
  сообщение об ошибке — на длинных операциях это честнее индикатора.
- **I-10** (per-chat выключатель) — снят по Q4 как отдельная работа; вместо него в I-6
  заложен задел (см. выше). Миграции БД в этом плане нет.
