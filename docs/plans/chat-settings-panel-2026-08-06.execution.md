---
schema_version: 3
plan_id: chat-settings-panel-2026-08-06
source_artifact:
  path: docs/plans/chat-settings-panel-2026-08-06.md
  sha256: 5bfae380e374e04447a96a442f5e85935c0bec40d82f5ba0fbe634ef5bf04fb1
  type: feature-prd
created_at: '2026-08-05T22:15:44Z'
approved_at: null
approved_by: null
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: draft
  started_at: null
  completed_at: null
  current_batch: null
  task_list_id: chat-settings-panel-2026-08-06
items:
- id: A-1
  title: 'Реестр полей настроек: единый модуль (группа, метка, короткий callback-код, тип) для 24 per-chat полей, общий для панели чата и экрана дефолтов'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A-2
  title: 'ADR: архитектура панели — рендер параметризован chat_id и отделён от проверки прав (Цель 2); KB/Reactions встраиваются ссылкой (без дублирования); kb_organizer_ids остаётся в KB-панели'
  specialist: architect
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: e1f29a7c-df13-4271-8f67-38379d80a274
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: B-1
  title: 'Панель настроек чата (R1): вход из adm_wl_chats:, сгруппированное меню, булевы тогглы по паттерну эффективного значения, ссылки на KB/Reactions, инвалидация кэша ChatConfigService на своих записях'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A-1
  - A-2
  estimated_effort: 5h
  confidence: null
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: B-2
  title: Индикатор «переопределено / унаследовано от дефолта» по строкам панели (R1) — открытый дизайн-вопрос; для 13 легаси-полей честный статус зависит от миграции C-2
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - B-1
  - C-2
  estimated_effort: 1h
  confidence: null
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: C-1
  title: 'Экран «настройки по умолчанию» (R2): заменить заглушку adm_defs: реальным управлением bot_config.default_* по тем же полям (=настройки новых чатов)'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A-1
  estimated_effort: 4h
  confidence: null
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: C-2
  title: 'Миграция легаси-DEFAULT (R2): DROP DEFAULT + NULL-ификация 13 колонок migration-001, где значение == старому SQL-дефолту, чтобы default_* реально управлял существующими чатами. Forward-only, трогает прод-данные — высокий риск'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 2.5h
  confidence: null
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: C-3
  title: 'Интеграционный тест миграции C-2: колонки nullable+no-DEFAULT, явные per-chat переопределения сохранены, только строки со старым дефолтом занулены — эмпирически на фикстуре (не только инспекция схемы)'
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - C-2
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 0fb3e85d-3bb5-4b02-b68c-61ad2bc74eea
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: D-1
  title: 'Кнопка «⚙️ Настройки чата» после approve (R3): в DM-уведомлении (adm_approve:) и в pending-списке (adm_wl_apr:), рядом/вместо индикатора «✅»'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - B-1
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: E-1
  title: Ретрофит инвалидации кэша ChatConfigService для СУЩЕСТВУЮЩИХ тогглов KB/Reactions (admin_kb.py, admin_reactions.py) — предсуществующий пробел, вскрытый анализом PRD; сейчас invalidate() только в chat_events.py:73
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on: []
  estimated_effort: 1h
  confidence: null
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: F-1
  title: 'Редактирование не-булевых полей через FSM (R4): system_prompt, trigger_words, chances/intervals, language, rules_mode с валидацией диапазонов. Кандидат на отдельную итерацию — зависит от ответа по составу v1'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - B-1
  estimated_effort: 5h
  confidence: null
  consult_session_id: d4b66650-f2e5-4fa0-925c-ca4fce81fd6f
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: F-2
  title: 'Тесты валидации ввода для FSM-редактирования (F-1): некорректные chances, отрицательные интервалы, неизвестные коды language, недопустимый rules_mode. Только если F-1 входит в v1'
  specialist: qa
  priority: P2
  status: pending
  depends_on:
  - F-1
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 0fb3e85d-3bb5-4b02-b68c-61ad2bc74eea
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: G-1
  title: 'Интеграционные тесты хендлеров панели чата (B-1) и экрана дефолтов (C-1): запись тоггла доходит до репозитория, эффективное значение флипается корректно, кэш инвалидируется после записи'
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - B-1
  - C-1
  estimated_effort: 2h
  confidence: null
  consult_session_id: 0fb3e85d-3bb5-4b02-b68c-61ad2bc74eea
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
  approve_action: /execute-plan /Users/julia/my-projects/telegram-chat-companion.chat-settings-panel-2026-08-06-wt/docs/plans/chat-settings-panel-2026-08-06.execution.md --resume
  reject_action: /plan-fixes docs/plans/chat-settings-panel-2026-08-06.md --revise /Users/julia/my-projects/telegram-chat-companion.chat-settings-panel-2026-08-06-wt/docs/plans/chat-settings-panel-2026-08-06.execution.md
safe_to_replay_from: null
clarifying_questions:
- '[C-2] Легаси-DEFAULT: 13 старых колонок несут SQL DEFAULT, из-за чего смена «настроек по умолчанию» сегодня НЕ влияет на чаты, которые бот уже видел. Чинить это в текущем плане миграцией (C-2 + тест C-3), которая обнулит только строки со старым дефолтом и сохранит явные переопределения, — или отложить в техдолг, ограничив экран дефолтов (C-1) только 11 новыми полями? Миграция forward-only и трогает прод-данные (merge = автодеплой), поэтому перед обнулением нужен dry-run подсчёт затронутых строк. Рекомендация: делать сейчас отдельным ревьюируемым пунктом с dry-run.'
- '[F-1] Состав v1 панели: только булевы тогглы плюс просмотр остальных полей (редактирование не-булевых — system_prompt, trigger_words, chances/интервалы, language, rules_mode — в отдельную итерацию, пункты F-1/F-2), или сразу включить редактирование части не-булевых? Полное редактирование примерно удваивает объём (нужны FSM-ввод и валидация по каждому типу). Рекомендация: v1 = тогглы + просмотр, F-1/F-2 отложить.'
- '[E-1] Существующие тогглы KB и Reactions сейчас НЕ сбрасывают кэш настроек — изменение применяется с задержкой до 60 секунд; это предсуществующий баг, вскрытый анализом. Чинить его для всех старых тогглов в рамках этого плана (пункт E-1, дёшево) или вынести отдельно? Рекомендация: включить E-1.'
- '[B-2] Индикатор «переопределено / унаследовано от дефолта» по строкам панели — включать в v1 и как показывать: маркер-суффикс только на унаследованных строках или бейдж на каждой? Важно: для 13 легаси-полей честно показать «унаследовано» нельзя, пока не выполнена миграция C-2, иначе индикатор соврёт. Рекомендация: маркер только на унаследованных строках, для легаси-полей не показывать «унаследовано» до C-2 (поэтому B-2 зависит от C-2).'
- '[Q5] Приняты два решения по умолчанию — подтвердите или измените: (1) kb_organizer_ids остаётся под управлением только в KB-панели, панель чата ссылается на неё без дублирования логики; (2) кнопка «Настройки чата» после одобрения появляется не только в DM-уведомлении, но и при approve из pending-списка (adm_wl_apr:) — тот же паттерн, почти без затрат. Рекомендация: оставить оба варианта по умолчанию.'
---

























<!-- BRIEF:START -->
# Панель настроек чата в админке

## Что произошло
Разобран PRD по сборке всех настроек чата в одну панель. Сейчас настройками каждого чата (база знаний, реакции, модули, стикеры и ещё ~20 параметров) можно управлять только вразнобой: у базы знаний и реакций свои отдельные экраны, а остальные параметры меняются вообще только руками в базе. Экран «настройки по умолчанию для новых чатов» — заглушка. После одобрения нового чата админу не предлагается сразу его настроить.

## Найденные проблемы
- Нет единого места, где видны и переключаются все настройки одного чата; большинство параметров недоступны из интерфейса вовсе.
- «Настройки по умолчанию» фактически не работают: для 13 старых параметров значение по умолчанию сегодня не влияет на чаты, которые бот уже видел — экран будет показывать неправду, если это не починить.
- Существующие переключатели применяются с задержкой до минуты (не сбрасывается кэш).
- После добавления чата в whitelist нет быстрого перехода к его настройкам.

## Что будет сделано
- Единая панель настроек чата: все параметры сгруппированы, переключатели срабатывают сразу; база знаний и реакции открываются из неё без дублирования (A-1, A-2, B-1).
- Рабочий экран «настройки по умолчанию» для новых чатов и аккуратная починка старых параметров, чтобы значения по умолчанию действительно применялись (C-1, C-2, C-3).
- Кнопка «⚙️ Настройки чата» сразу после одобрения чата (D-1).
- Пометка «переопределено / унаследовано» у каждого параметра (B-2) и устранение задержки применения у старых переключателей (E-1).
- Автотесты на всё перечисленное (C-3, G-1).

## Не входит в этот план
Редактирование текстовых и числовых полей (системный промпт, триггер-слова, вероятности, интервалы, язык) предлагается отложить в отдельную итерацию (F-1, F-2). Доступ к панели админам изнутри чата сейчас не делается, но архитектура закладывается так, чтобы добавить его позже без переделки.

## Оценка
Полный объём ~30 часов; при рекомендованном варианте (без редактирования не-булевых полей) ~23–24 часа. Потолок бюджета — $30 на план, $6 на пункт. Ряд решений вынесен в вопросы ниже.
<!-- BRIEF:END -->

# Plan — chat-settings-panel-2026-08-06

## Source

[`docs/plans/chat-settings-panel-2026-08-06.md`](docs/plans/chat-settings-panel-2026-08-06.md) (sha256 `5bfae380e374...`).

## Items

(none yet — populated by /plan-fixes)
