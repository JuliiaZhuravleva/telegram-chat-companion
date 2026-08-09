-- Q-2 (admin-ux-and-summary-2026-08-09): подготовленные тестовые данные для
-- минимального ручного прогона личного DM-раздела администратора.
--
-- Покрывает B-2 (сгруппированная панель), C-1 (сортировка пикера по
-- активности) и D-1 (шорткат /panel) БЕЗ единого реального Telegram-чата:
-- рендер пикера/панели/групп-экранов и поиск по названию — чистый SQL,
-- никаких вызовов Telegram API (проверено по коду: AdminRepository.
-- get_enabled_chats_page_by_activity / find_enabled_chats_by_title и
-- render_chat_panel / render_chat_panel_group читают только chat_settings/
-- chat_messages). Единственная живая ветка D-1, которую эти данные НЕ
-- покрывают — резолв по @username (там нужен настоящий bot.get_chat());
-- она уже закрыта юнит-тестами D-1 (test_admin_chat_panel_handler.py) —
-- в чек-листе она помечена опциональной.
--
-- Все id — заведомо фиктивные (шаблон -100 9999 000NNN, супергрупповый
-- формат, но узнаваемо-ненастоящий), безопасно коммитить в публичный
-- репозиторий. Скрипт идемпотентен (ON CONFLICT DO NOTHING/UPDATE) — можно
-- запускать повторно. Раздел A (стикеры) сюда не входит: он использует ОДИН
-- реальный стикер, отправленный админом боту вживую в личку во время
-- прогона (см. чек-лист, Раздел A, шаг 0) — так реально работает кнопка
-- "Проанализировать" (настоящий Vision-вызов) и answer_sticker показывает
-- настоящую картинку, а не битую по фиктивному file_id.
--
-- Запуск (локальный dev-стек из docker-compose.yml):
--   docker compose exec -T postgres psql -U bot_user -d telegram_bot \
--     -f - < docs/plans/admin-ux-and-summary-2026-08-09.q2-fixtures.sql
-- (либо psql "$DATABASE_URL" -f ... если Postgres проброшен на localhost:5432)

BEGIN;

-- ── 3 фиктивных whitelist-чата ──────────────────────────────────────────
-- Alpha/Alphaville специально делят подстроку "Alpha" — это неоднозначный
-- запрос для D-1 (несколько совпадений → список кандидатов). Bravo —
-- однозначное название. Остальные поля chat_settings НЕ указаны нарочно:
-- «легаси»-колонки (trigger_words, random_response_chance, ... — миграция
-- 001) получат свои SQL DEFAULT и будут выглядеть настроенными; более новые
-- поля без SQL DEFAULT (tolerance_level, поля RULES/REACTIONS) останутся
-- NULL — то есть ровно то, на чём должен сработать маркер «унаследовано из
-- глобального дефолта» в B-2, без отдельной подготовки.
INSERT INTO chat_settings (chat_id, chat_title, chat_type, enabled)
VALUES
    (-1009999000001, 'QA Smoke — Alpha',      'supergroup', true),  -- check-plan-artifacts: allow telegram-id
    (-1009999000002, 'QA Smoke — Alphaville', 'supergroup', true),  -- check-plan-artifacts: allow telegram-id
    (-1009999000003, 'QA Smoke — Bravo',      'supergroup', true)  -- check-plan-artifacts: allow telegram-id
ON CONFLICT (chat_id) DO UPDATE SET
    chat_title = EXCLUDED.chat_title,
    chat_type  = EXCLUDED.chat_type,
    enabled    = true;

-- ── Сообщения за последние 24ч — задают порядок для C-1 ────────────────
-- Alpha: 50 сообщений (самый активный из трёх) · Alphaville: 5 · Bravo: 0.
-- Ожидаемый порядок этих трёх строк в пикере: Alpha, Alphaville, Bravo.
DELETE FROM chat_messages
WHERE chat_id IN (-1009999000001, -1009999000002)  -- check-plan-artifacts: allow telegram-id
  AND content = 'qa-smoke-fixture';

INSERT INTO chat_messages (chat_id, message_id, user_id, message_type, content, created_at)
SELECT -1009999000001, gs, 900000001, 'text', 'qa-smoke-fixture',  -- check-plan-artifacts: allow telegram-id
       NOW() - (gs || ' minutes')::interval
FROM generate_series(1, 50) AS gs;

INSERT INTO chat_messages (chat_id, message_id, user_id, message_type, content, created_at)
SELECT -1009999000002, gs, 900000002, 'text', 'qa-smoke-fixture',  -- check-plan-artifacts: allow telegram-id
       NOW() - (gs || ' minutes')::interval
FROM generate_series(1, 5) AS gs;
-- Bravo (-1009999000003) остаётся без сообщений — 0 за 24ч, проверяет  -- check-plan-artifacts: allow telegram-id
-- тайбрейк «по алфавиту», когда счётчик активности равен нулю/равен.

COMMIT;

-- ── D-1: готовые запросы для /panel (посчитаны по тому же chat_id) ─────
-- t.me/c/<internal_id> = |chat_id| без ведущих "100":
--   Alpha      → t.me/c/9999000001  -- check-plan-artifacts: allow telegram-id
--   Alphaville → t.me/c/9999000002  -- check-plan-artifacts: allow telegram-id
--   Bravo      → t.me/c/9999000003  -- check-plan-artifacts: allow telegram-id
-- Сырой id тоже подходит как есть, например -1009999000003.  -- check-plan-artifacts: allow telegram-id
-- Все команды — см. чек-лист, Раздел D.

-- ═════════════════════════════════════════════════════════════════════
-- ОЧИСТКА — выполнить в конце прогона (Раздел "Гигиена" чек-листа).
-- Отдельным блоком, закомментирован по умолчанию: раскомментируй перед
-- запуском, когда чек-лист пройден и результаты записаны.
-- ═════════════════════════════════════════════════════════════════════
-- BEGIN;
-- DELETE FROM chat_messages WHERE content = 'qa-smoke-fixture';
-- DELETE FROM chat_settings WHERE chat_id IN
--     (-1009999000001, -1009999000002, -1009999000003);  -- check-plan-artifacts: allow telegram-id
-- COMMIT;

-- ── Раздел A (стикеры): один живой стикер, переводимый через состояния ─
-- Не запускать сразу — сначала выполнить шаг 0 чек-листа (отправить боту
-- реальный стикер, дать ему проанализироваться) и подставить сюда его
-- file_unique_id. Дальше используется ТОЛЬКО для состояния "проанализирован,
-- но explicitness_score = NULL" («не оценён») — единственное состояние,
-- которое неудобно вызвать через живой UI напрямую (реальный Vision-ответ
-- почти всегда возвращает какое-то число). Все остальные переходы
-- (авто-оценка → пресет → ручной ввод → сброс) делаются кнопками в боте, не
-- SQL — см. чек-лист.
--
-- \set fuid 'ЗАМЕНИ_НА_РЕАЛЬНЫЙ_file_unique_id'
-- UPDATE sticker_knowledge
--    SET explicitness_score = NULL, explicitness_is_manual = false
--  WHERE file_unique_id = :'fuid';
