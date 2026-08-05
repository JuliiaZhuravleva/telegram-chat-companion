---
schema_version: 3
plan_id: admin-ux-review-2026-08-02
source_artifact:
  path: internal/backlog/video-review/admin-ux-review-2026-08-02.md
  sha256: 29713042c8b409c7d35c99a4db4422ec4a76de5a5923d6ac9dbfb9af8dbc6b14
  type: session-analysis
created_at: '2026-08-02T22:40:20Z'
approved_at: '2026-08-03T09:11:12Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: done
  started_at: '2026-08-03T09:11:51Z'
  completed_at: '2026-08-03T09:48:19Z'
  current_batch: null
  task_list_id: admin-ux-review-2026-08-02
items:
- id: A-1
  title: 'Пронумеровать кнопки в списке ожидающих: номер на кнопке == номер пункта в тексте'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1.5h
  confidence: 0.95
  consult_session_id: 1b33c07f-48bd-4e55-9930-ec231d472afa
  specialist_session_id: c9a647e9-a0e9-4fe5-8db5-667d3b177802
  retry_count: 0
  last_update:
    ts: '2026-08-03T09:14:53Z'
    executor: backend-dev
    note: 'pending_list_keyboard() now takes start_index (0-based offset = page*_PER_PAGE) and renders ''{i} ✅''/''{i} ❌'', matching the body''s enumerate(attempts, start=offset+1) numbering exactly. Also fixed a latent bug surfaced while wiring the call site: offset was only assigned inside the total>0 branch of _render_wl_pending, which would have raised NameError on the empty-list path once the keyboard call started reading it -- hoisted the assignment above the if/else. Scope kept to the pending list only, per A-1; A-2 (rejected + chats lists, shared helper) is a separate dispatched item and intentionally untouched. A-2 can reuse the same start_index parameter pattern.'
  result:
    kind: commit
    ref: eb4aab5
    verification: 'pytest tests/unit/test_admin_keyboards.py tests/unit/test_admin_handler.py -q: 113 passed; full pytest tests/unit/ -q: 945 passed; ruff check clean; mypy clean'
- id: A-2
  title: Тот же номерной помощник для «Отклонённых» и списка чатов (один общий helper на три списка)
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A-1
  estimated_effort: 1h
  confidence: 0.93
  consult_session_id: 1b33c07f-48bd-4e55-9930-ec231d472afa
  specialist_session_id: 0b2817ba-2bc8-405b-b29d-a481342a23ba
  retry_count: 0
  last_update:
    ts: '2026-08-03T09:21:44Z'
    executor: backend-dev
    note: 'Extracted _numbered_button() as the single shared numbering helper (per A-2 spec: one common numbering helper for all three lists) and routed pending_list_keyboard (refactored, behavior-preserving), rejected_list_keyboard, and chats_list_keyboard through it. Threaded start_index=page*_PER_PAGE into rejected/chats keyboards mirroring A-1''s pending pattern; hoisted the offset computation above the total==0 branch in _render_wl_rejected/_render_wl_chats to avoid the same latent NameError A-1 fixed. Chats-list numbering applied to the Remove button, consistent with the action-button numbering pattern used by the other two lists. Own unit tests added: numbering-matches-body tests for chats and rejected keyboards (mirroring A-1''s pending tests), plus a TestNumberedButtonSharedAcrossLists class that patches admin_kb._numbered_button and asserts all three keyboard builders call it -- covers the plan''s cross-list QA note at the unit level; no separate qa item needed for that specific check. No integration-test gap: pure keyboard-construction logic, no DB/network involved.'
  result:
    kind: commit
    ref: cbaeb0c
    verification: 'pytest tests/unit/test_admin_keyboards.py tests/unit/test_admin_handler.py -q: 120 passed; full pytest tests/unit/ -q: 952 passed; ruff check src/ clean; mypy src/ clean (113 files)'
- id: B-1
  title: '«Добавить организатора»: снять ложное обещание @username (срочно) + реальный резолв по истории чата — в один тикет'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 4-5h
  confidence: 0.85
  consult_session_id: 1b33c07f-48bd-4e55-9930-ec231d472afa
  specialist_session_id: 15a2222e-7bfe-45ca-8422-4c794abf9cfb
  retry_count: 0
  last_update:
    ts: '2026-08-03T09:31:24Z'
    executor: backend-dev
    note: 'Implemented both stages of B-1 in one pass (single ticket per Julia''s decision): (1) switched forward resolution from legacy forward_from (Telegram no longer populates it) to forward_origin, with MessageOriginHiddenUser (forward-privacy-on) now getting dedicated copy instead of falling into generic not-found; (2) implemented real @username resolution via chat-scoped message history (MessageRepository.find_by_username, case-insensitive), distinguishing ''never seen this username'' from ''seen it, just not in this chat'' (username_seen_elsewhere). Did NOT add a DB migration/index for the username lookup -- chat_messages has no index on username, so find_by_username/username_seen_elsewhere are full scans; acceptable for a low-frequency admin action, but flag for B-2''s already-planned schema review (chat_messages(chat_id,user_id) migration decision) to also consider a username index if lookup latency matters at scale. Left _ADD_PROMPT copy unchanged (it already promised exactly what''s now implemented) rather than stripping-then-restoring the @username mention within the same commit; kb-copy-register.md (docs/design, architect-owned per sessions.md) was NOT updated with the two new strings (_ADD_FORWARD_HIDDEN, _ADD_NOT_IN_CHAT) -- recommend a small architect follow-up to lock them into the copy register per that doc''s own gating rule. No integration-test gap requiring qa: the new repo methods are straightforward parameterized SELECTs against the existing chat_messages table (no new schema), covered by mocked-pool unit tests; qa could optionally add a real-Postgres case-insensitivity check if desired but it''s not blocking.'
  result:
    kind: commit
    ref: 14d8a6e
    verification: 'pytest tests/unit/test_admin_kb_handler.py tests/unit/test_message_repository.py -q: 25 passed; full pytest tests/unit/ -q: 969 passed; ruff check src/ clean; mypy src/ clean (113 files)'
- id: B-2
  title: 'Пикер участников для выбора организатора: сортировка по числу сообщений, топ-N + пагинация'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 4h
  confidence: 0.9
  consult_session_id: 1b33c07f-48bd-4e55-9930-ec231d472afa
  specialist_session_id: f11c251f-9fd9-4fe4-a239-ac81e298c869
  retry_count: 0
  last_update:
    ts: '2026-08-03T09:46:29Z'
    executor: backend-dev
    note: 'Implemented the participant picker as spec''d: MessageRepository.get_top_active_users() (GROUP BY user_id over chat_messages, excludes bot messages, ORDER BY message_count DESC with user_id ASC tiebreak) backs a new adm_kb_org_list:/adm_kb_org_pick: callback pair; kb_organizer_picker_keyboard renders ''Name (@nick)'' rows, top-5-per-page with pagination, reached via a new ''Show participants'' button on the existing add-organizer prompt. Picking a candidate adds them directly (mirrors org_rm''s single-tap-no-confirm convention) and clears the awaiting_kb_organizer FSM state so a stray later text isn''t misread as a username reply. Schema-review gate (plan''s first step of execution): verified idx_chat_messages_user(chat_id, user_id, created_at DESC) (migration 002) already supports the GROUP BY without a sort -- no migration needed, estimate did not grow. Did not update docs/design/kb-copy-register.md with the 4 new strings -- same gap B-1 flagged for its own new strings; recommend one architect follow-up covering both items'' copy at once. No integration-test gap requiring qa: get_top_active_users is a parameterized aggregate SELECT against the existing chat_messages table (no new schema), covered by mocked-pool unit tests; qa could optionally add a real-Postgres GROUP BY/ranking check but it''s not blocking.'
  result:
    kind: commit
    ref: 9db1a61
    verification: 'pytest tests/unit/test_admin_kb_handler.py tests/unit/test_admin_kb_keyboards.py tests/unit/test_message_repository.py -q: 52 passed; full pytest tests/unit/ -q: 996 passed; ruff check src/ clean; ruff format --check clean; mypy src/ clean (113 files)'
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 10.7695
review_gate:
  why: []
  approve_action: /execute-plan <projects>/telegram-chat-companion.admin-ux-review-2026-08-02-wt/docs/plans/admin-ux-review-2026-08-02.execution.md --resume
  reject_action: /plan-fixes internal/backlog/video-review/admin-ux-review-2026-08-02.md --revise <projects>/telegram-chat-companion.admin-ux-review-2026-08-02-wt/docs/plans/admin-ux-review-2026-08-02.execution.md
safe_to_replay_from: null
clarifying_questions: []
human_feedback:
- ts: '2026-08-03T08:44:56Z'
  by: julia
  text: 'ANSWER [B-1]: Одним пунктом. Уточнение: имеется в виду «просто не дроби на два тикета» — объедини B-1a и B-1b в один пункт B-1. Это НЕ значит «поднять весь объём в срочный P1»: приоритет выстави по существу работы (снятие ложного обещания @username — срочная часть, реальный резолв по истории чата — нет).'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-03T09:05:18Z'
  edited_at: '2026-08-03T08:54:39Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T08:46:06Z'
  by: julia
  text: 'ANSWER [B-2]: Берем в текущий этап'
  applies_to: B-2
  status: addressed
  addressed_at: '2026-08-03T09:05:24Z'
  addressed_by: pm-orchestrator
revision_number: 2
last_revised_at: '2026-08-03T09:08:08Z'
last_revised_by: pm-orchestrator
---













































<!-- BRIEF:START lang=ru -->
# Админка бота: понятные кнопки в списках заявок и рабочее добавление организатора

## Что произошло
Разобрали видеозапись сессии с телефона, где вы проходили по админке прод-бота: списки заявок в вайтлист и добавление организаторов Базы знаний. Всплыли две независимые проблемы, обе подтверждены вживую.

## Найденные проблемы
1. **В списках заявок непонятно, какая кнопка к какому пункту относится.** Пункты пронумерованы в тексте сообщения, а ряды кнопок ✅/❌ идут без подписей — связь только по расположению. При десятках заявок и длинной прокрутке легко одобрить или отклонить не тот чат. То же самое на вкладке «Отклонённые» и в списке чатов.
2. **Добавление организатора по @username не работает вообще.** Бот просит прислать @username, но такой способ не реализован — на любой ввод отвечает «не нашёл такого участника», даже когда Telegram уже показал карточку пользователя. Это гарантированный тупик: текст обещает то, чего в поведении нет.

## Что будет сделано
- Рядом с каждой кнопкой в списках ожидающих, отклонённых и чатов появится номер, совпадающий с номером пункта в тексте — промахнуться станет нельзя. (A-1, A-2)
- Добавление организатора починим одним заходом (в один тикет). Сначала уберём ложное обещание про @username и покажем понятную подсказку — в том числе отдельный текст на случай, когда пересылка скрыта настройками приватности отправителя. Следом научим бота действительно находить организатора по @username среди участников чата и внятно различать «такого не знаю» и «знаю, но он не в этом чате». Срочная часть — снятие тупика — делается первой. (B-1)
- Добавим выбор организатора из списка участников с сортировкой по активности — чтобы не угадывать форвардом, а нажать на нужного. (B-2)

## Не входит в этот план
Полноценный интерактивный поиск участников (inline-режим Telegram) — отдельная, более крупная история. По выбору из списка (B-2): сначала проверим, как у бота устроено хранение сообщений для сортировки по активности; если понадобится доработка базы данных — сделаем её в рамках этого же пункта (тогда время может немного вырасти).

## Оценка
Суммарно ориентировочно 10–12 часов работы; потолок бюджета — 30 $.
<!-- BRIEF:END -->

# Plan — admin-ux-review-2026-08-02

## Source

[`internal/backlog/video-review/admin-ux-review-2026-08-02.md`](internal/backlog/video-review/admin-ux-review-2026-08-02.md) (sha256 `29713042c8b4...`).

> Синтезировано из разбора сессии 2026-08-02. Консультации: backend-dev, qa, frontend-dev
> (frontend-dev подтвердил: проект чисто-бэкендовый на Python/aiogram — роли frontend-dev/designer
> нет по `sessions.md`, все пункты уходят backend-dev). Оценки сведены по backend-dev + qa.

## Items

### A-1 — Пронумеровать кнопки в списке ожидающих · P1 · backend-dev · ~1.5h

Тело сообщения нумерует записи сквозной нумерацией по страницам, а инлайн-клавиатура кладёт голый
глиф `✅`/`❌` без подписи — связь только позиционная (риск одобрить не тот чат при 13 страницах).

- Root cause: `keyboards/admin.py` `pending_list_keyboard()` (≈L497) — кнопка без номера; нумерация
  тела — `handlers/admin.py` (≈L1033) `enumerate(attempts, start=offset+1)`, `offset = page*_PER_PAGE`.
- Fix: передавать в клавиатуру стартовый offset/индекс и подписывать `text=f"{i} ✅"` / `f"{i} ❌"`;
  индекс обязан совпадать со сквозной нумерацией тела.
- Тесты: unit на клавиатуру (backend-dev, свой код) — номер кнопки == номер пункта на 3-й странице.

### A-2 — Тот же помощник на «Отклонённых» и в списке чатов · P1 · backend-dev · ~1h · depends: A-1

Та же схема на вкладке «Отклонённые» (`🔄 Вернуть` / `🗑`) и в списке чатов — привязки к номерам нет.

- Root cause: `keyboards/admin.py` `rejected_list_keyboard()` (≈L413) и `chats_list_keyboard()`
  (≈L307); рендер тела — `handlers/admin.py` (≈L1136 и ≈L867).
- Fix: один общий помощник нумерации на все три списка (правило «номер в теле == номер на кнопке»
  живёт в одном месте). Делать после A-1, переиспользуя его helper.
- Тесты: qa добавляет кросс-списочную проверку, что все три списка используют ОДИН общий helper
  (иначе helper продублируется/разъедется).

### B-1 — «Добавить организатора»: копия ↔ поведение + реальный резолв `@username` · P2 · backend-dev · ~4-5h

Объединённый пункт (решение Julia 2026-08-03: один тикет, не два). Две стадии в одном тикете; приоритет
пункта P2 отражает основной объём — резолв, который НЕ срочный. Срочная копи-правка (стадия 1) — ведущая
подзадача, делается первой. (Прямое указание Julia: весь пункт НЕ поднимать в P1. Если предпочтёте P1 —
переставьте при утверждении.)

**Стадия 1 — срочная копи-правка (делать первой).** Бот предлагает отправить `@username`, но обработчик
этот путь не поддерживает и всегда отвечает «не нашёл» — гарантированный тупик.

- Root cause: `handlers/admin_kb.py` (≈L411) читает только legacy `message.forward_from`; любое
  сообщение без форварда (включая корректный `@username`) падает в `_ADD_NOT_FOUND`. Текст-приглашение
  (`handlers/admin_kb.py` ≈L68) при этом обещает `@username`.
- Fix: убрать обещание `@username` из приглашения, объяснить, что нужен именно форвард. Для разведения
  «нет форварда» и «форвард скрыт приватностью отправителя» перейти на `message.forward_origin`
  (aiogram 3.24 установлен; `MessageOriginHiddenUser` — сигнал приватного форварда; `forward_from`
  один эти два случая различить не может) → отдельный текст про приватность, а не общий «не нашёл».
- Тесты: unit на две ветки (нет форварда / скрытый форвард) — backend-dev.

**Стадия 2 — не срочная (тот же тикет).** Сделать так, чтобы `@username` действительно находился, и
различать причины отказа.

- Данные уже есть: история сообщений чата хранит `user_id`/`username`/`first_name` (verified
  backend-dev). Bot API не даёт lookup username→user, а `get_chat_member` требует числовой id —
  поэтому резолв через chat-scoped индекс по накопленным сообщениям чата — единственный рабочий путь.
- Fix: запрос по истории сообщений чата; различать «не знаю такого username» и «знаю, но не в этом
  чате». Новая таблица не нужна (переиспользуем существующие данные), возможно — вспомогательный
  запрос/индекс.
- Тесты: интеграционный тест (testcontainers Postgres) на резолв по реальному запросу — QA-owned по
  конвенции; backend-dev добавляет unit на новый метод репозитория.

### B-2 — Пикер участников (сортировка по активности) · P2 · backend-dev · ~4h

Новая фича, не баг: сейчас единственный способ назначить организатора — угадать и прислать форвард.
Взято в текущий этап по решению Julia (2026-08-03) — прежний блок снят.

- Хотелка (из транскрипта): кнопка «Показать участников» → инлайн-список кандидатов, подпись
  `Имя (@nick)`, сортировка по числу сообщений пользователя в этом чате по убыванию, топ-N (Julia
  предлагает 5) + пагинация. Вход сейчас единственный — `handlers/admin_kb.py` (≈L358).
- **Первый шаг исполнения (бывший блокер, теперь в объёме пункта):** верифицировать по коду источник
  счётчика сообщений. Сортировка требует агрегата `GROUP BY COUNT(*)` по истории сообщений и может
  потребовать нового индекса/миграции на `chat_messages(chat_id, user_id)` — решение уровня схемы БД,
  его принимает backend-dev (роль architect в проекте не выделена). Если миграция нужна — делается в
  рамках этого же пункта; оценка ~4h может вырасти.
- Технически B-2 **не** зависит от резолва `@username` (B-1 стадия 2) — независимый путь.
  «Интерактивный поиск» (inline-режим Telegram) — вне scope.
