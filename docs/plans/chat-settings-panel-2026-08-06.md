# Панель настроек чата в админке — feature PRD

> Source artifact для /plan-fixes. Дата: 2026-08-06. Автор запроса: Julia.
> Тип: feature-prd. Анализ кода выполнен на актуальном main (см. цитаты file:line).

## Запрос (формулировка Julia, дословно по смыслу)

Реализуем настройки групп/чатов в админском чате. Нужно собрать все отдельные
настройки, которыми можно управлять на уровне чата (база знаний, реакции и
прочее), в одну панель управления чатом. В будущем панель будет также доступна
админам чата вызовом изнутри чата, поэтому продумать её как самостоятельный
инструмент. Не забыть про настройки по умолчанию (автоматически для новых
чатов). Также после добавления нового чата в whitelist админу сразу должна
рисоваться кнопка, открывающая настройки этого чата: пришло уведомление о
добавлении бота в новый чат → админ жмёт «добавить в whitelist» → сразу
доступна кнопка «настройки чата».

## Цель

1. Единая per-chat панель настроек в админ-панели (DM), собирающая все
   существующие per-chat переключатели в одном месте, сгруппированно.
2. Панель — самостоятельный инструмент: рендер/логика параметризованы
   `chat_id` и не привязаны намертво к контексту «DM глобального админа»,
   чтобы позже её можно было открыть изнутри чата для админов этого чата
   (в этой итерации доступ — только глобальным админам бота из DM, но
   архитектура не должна этому мешать).
3. Экран «настройки по умолчанию» (для новых чатов) вместо текущей заглушки.
4. Кнопка «⚙️ настройки чата» сразу после одобрения чата в whitelist.

## Что уже есть (анализ кода, 2026-08-06)

### Per-chat настройки: БД и ChatConfig

Единый источник правды по полям: `src/models/chat_config.py` (ChatConfig),
`src/services/chat_config.py` `_CHAT_CONFIG_FIELDS` (строки 130–158),
`src/database/repositories/chat_settings.py` `_WRITABLE_COLUMNS` (строки 11–43).

Управляемые per-chat поля (24), по группам:

| Группа | Поля (тип) |
|---|---|
| Whitelist | `enabled` (bool) |
| Поведение | `trigger_words` (TEXT[]), `random_response_chance` (float), `random_response_min_interval` (int), `system_prompt` (text), `language` (str ru/en) |
| Модули | `rag_enabled`, `transcribe_voice`, `transcribe_video_notes`, `abuse_filter_enabled`, `save_messages`, `image_analysis_enabled`, `link_comments_enabled`, `relevancy_gate_enabled` (все bool) |
| Стикеры | `sticker_learning_enabled` (bool), `sticker_response_chance` (float), `sticker_reply_to_sticker_enabled` (bool), `sticker_reply_to_sticker_chance` (float), `image_comment_sticker_enabled` (bool), `image_comment_sticker_chance` (float) |
| Rules | `rules_enabled` (bool), `rules_mode` (str) |
| База знаний | `kb_enabled` (bool), `kb_organizer_ids` (JSONB, управляется в KB-панели) |
| Реакции | `reactions_enabled` (bool), `reactions_history_enabled` (bool) |

Плюс метаданные (не «настройки»): `chat_title`, `chat_type`, `last_activity_at`.

### Трёхслойный merge и настройки по умолчанию

YAML (`config/default.yml`) → `bot_config` (ключи `default_*`) →
`chat_settings` → frozen `ChatConfig`. `BotConfigRepository.get_defaults()`
берёт все ключи `LIKE 'default_%'` и срезает префикс
(`src/database/repositories/bot_config.py:48-57`) — т.е. глобальный дефолт
работает для ЛЮБОГО поля из `_CHAT_CONFIG_FIELDS`, стоит записать ключ
`default_<field>`. Засеяно 13 ключей в migration 001
(`alembic/versions/001_initial_schema.py:67-83`).

**Критичное легаси:** 13 колонок из migration 001 (`trigger_words`,
`random_response_chance`, `random_response_min_interval`, `system_prompt`,
`language`, `rag_enabled`, `transcribe_voice`, `transcribe_video_notes`,
`abuse_filter_enabled`, `sticker_learning_enabled`, `sticker_response_chance`,
`image_analysis_enabled`, `save_messages`) ДО СИХ ПОР несут SQL DEFAULT
(`001_initial_schema.py:85-113`). `ensure_exists()` создаёт строку при первом
контакте с чатом (`ChatConfigMiddleware`, `chat_events.py:84-92`), DEFAULT
материализует значения — и для этих 13 полей per-chat строка ЗАТЕНЯЕТ
глобальный слой `default_*` у всех существующих чатов. Т.е. сегодня смена
`default_rag_enabled` не влияет ни на один чат, который бот уже видел.
Новые колонки сделаны правильно: nullable без DEFAULT, NULL = «унаследовано»
(migrations 014 `kb_enabled`, 015 drift-колонки, 018 reactions, 020 rules;
правило зафиксировано в CLAUDE.md «Per-chat columns: nullable, no DEFAULT»).
Экран «настройки по умолчанию» без починки этого будет врать для старых полей.
Внимание: migrations forward-only, merge в main = автодеплой в прод.

### Админ-панель сегодня

- Главное меню (`src/bot/keyboards/admin.py:50-119`): Whitelist, Rules,
  Stickers, **Defaults — ЗАГЛУШКА** (`adm_defs:` →
  `handle_defaults_placeholder`, `src/bot/handlers/admin.py:1840-1843`,
  «Stage 3.1.4»), Statistics, Costs, Health, Notifications, KB, Reactions,
  Language, Close.
- Per-chat управление существует только помодульно, у каждого модуля СВОЙ
  chat-picker: KB (`adm_kb:` пикер → `adm_kb_menu:` → `adm_kb_toggle:` +
  организаторы, `src/bot/handlers/admin_kb.py:254-476`) и Reactions
  (`adm_react:` → `adm_react_menu:` → `adm_react_toggle:`,
  `src/bot/handlers/admin_reactions.py:192-250`). Единой панели «все
  настройки чата» нет; остальные 20 полей из таблицы выше вообще не
  управляются из UI (только руками в БД).
- Паттерн toggle (обязателен и для новой панели): флипается ЭФФЕКТИВНОЕ
  значение, потому что raw-колонка может быть NULL = унаследовано
  (`admin_kb.py:333-338` с комментарием почему).
- Callback-конвенция: stateless `adm_*:{lang}:{params}`, язык в callback_data;
  каждый callback проверяет админа (`check_admin_direct`) + private chat.
  Telegram-лимит callback_data 64 байта: `chat_id` занимает до 14 символов,
  длинные имена полей (`sticker_reply_to_sticker_enabled` — 32) впритык или
  не влезают → полям нужны короткие коды в callback_data.
- Хендлеры-переключатели НЕ инвалидируют кэш `ChatConfigService` (60s TTL) —
  ни KB-toggle, ни reactions, ни approve (`grep invalidate` по admin*.py пуст;
  инвалидация есть только в `chat_events.py:73`). Изменение настройки
  применяется с задержкой до 60 с. Для панели решить: инвалидировать после
  записи (сервис доступен через Dishka).

### Whitelist / approve flow (куда встаёт кнопка)

1. Бот добавлен в чат → `chat_events.handle_my_chat_member` записывает только
   метаданные, `enabled` не трогает (`src/bot/handlers/chat_events.py:50-97`).
2. Первое обращение к боту из не-whitelisted чата →
   `AccessControlMiddleware` шлёт админам DM-уведомление с
   `access_keyboard(attempt_id)`: кнопки `adm_approve:` / `adm_reject:`
   (`src/bot/middleware/access_control.py:171`).
3. `adm_approve:` → `_do_approve()`: approve всех attempts чата +
   `chat_settings.upsert(enabled=True)` (`admin.py:1509-1526`), после чего
   кнопки заменяются на `approved_notification_keyboard` — только индикатор
   «✅», никаких действий (`admin.py:1576-1581`, `keyboards/admin.py:600`).
   **Требование R3: здесь должна появиться кнопка «⚙️ Настройки чата».**
4. Approve возможен и из панели: pending-список `adm_wl_pending:` →
   `adm_wl_apr:` (`admin.py:1626`). Решить в плане: кнопка/переход к
   настройкам и в этом флоу (логично — да).
5. Список whitelisted-чатов уже есть: `adm_wl_chats:` (`admin.py:1017-1110`,
   пагинация, numbered buttons) — естественная точка входа в панель чата.

## Требования

- **R1 — Панель настроек чата.** Вход: из списка чатов whitelist (и, если
  уместно, отдельный пункт меню). Все per-chat поля из таблицы выше,
  сгруппированные по разделам; булевы — тогглы по паттерну эффективного
  значения; индикация «переопределено / унаследовано от дефолта» ценна —
  решить в плане, как показывать. Существующие KB- и Reactions-панели либо
  встраиваются/линкуются из панели чата, либо панель чата ссылается на них —
  решить в плане (без дублирования логики). Панель — самостоятельный
  инструмент (см. Цель 2): permission-модель «кто может открыть» должна быть
  отделена от рендера (глобальный админ бота сегодня; админ чата изнутри
  чата — потом; это РАЗНЫЕ проверки — `check_admin_direct` против
  Telegram-прав в чате).
- **R2 — Настройки по умолчанию.** Заменить заглушку `adm_defs:` реальным
  экраном управления `bot_config.default_*` для тех же полей = «настройки
  новых чатов». Разобраться с легаси-DEFAULT колонками (см. выше), иначе
  экран не работает для 13 старых полей у существующих чатов.
- **R3 — Кнопка после approve.** После `adm_approve:` из DM-уведомления —
  кнопка «⚙️ Настройки чата» вместо/рядом с индикатором «✅». Вероятно, то же
  после `adm_wl_apr:` из pending-списка.
- **R4 — Состав v1.** Не-булевы поля (`system_prompt`, `trigger_words`,
  chances/intervals, `language`, `rules_mode`) требуют ввода значений
  (FSM-стейты уже есть в проекте: `src/bot/states/admin.py`). Решить с Julia:
  v1 = только тогглы + просмотр остальных, или сразу редактирование каких-то
  не-булевых.

## Ограничения

- Репозиторий публичный: никаких реальных Telegram id в коде, тестах, доках.
- `main` = production (автодеплой при merge), migrations forward-only.
- aiogram-gotchas из CLAUDE.md обязательны: фильтры вместо guard'ов в теле
  хендлера, `:` в конце callback-префиксов, HTML parse mode по умолчанию
  (escape динамики), `edit_text` «message is not modified», i18n dict ru/en.
- Правило колонок: nullable, no DEFAULT (CLAUDE.md, migration 014/015/020).

## Открытые вопросы (кандидаты в clarifying questions)

1. Состав v1 панели: только булевы тогглы (+ read-only показ остальных) или
   сразу редактирование числовых/текстовых полей? Каких?
2. Легаси-DEFAULT: чинить миграцией (DROP DEFAULT + NULL-ификация значений,
   равных старому дефолту) в рамках этой работы, или R2 ограничить новыми
   полями и завести отдельный техдолг? (Миграция трогает прод-данные —
   forward-only.)
3. Кнопка настроек после approve в pending-списке (`adm_wl_apr:`) — нужна?
4. Инвалидация кэша ChatConfigService после изменения настройки — делаем
   частью этой работы (для всех admin-переключателей) или отдельно?
5. `kb_organizer_ids` остаётся только в KB-панели, или дублируется в панели
   чата?
