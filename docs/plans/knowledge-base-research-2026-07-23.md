# Chat Knowledge Base — research & design proposal

_Дата: 2026-07-23. Статус: research/design draft (source artifact для будущего /plan-fixes)._

## 1. Задача

Пер-чатовая база знаний (KB) для бота:

- **Ручное наполнение** — организатор/админ сохраняет факты (дата, время, место, программа ивента).
- **Автосбор** — бот извлекает факты из обсуждения и **актуализирует** базу (организатор объявил перенос → факт «место» обновился).
- **Градация авторитетности** участников: объявление организатора весит больше реплики случайного участника.
- **Фильтрация шуток** — саркастичные/шуточные утверждения не должны попадать в базу.
- **Векторный поиск** по базе, факты подмешиваются в контекст ответов бота.

Кейс-пример: организация ивента — базовая инфа заносится вручную, дальше бот ведёт её по ходу обсуждений.

## 2. Ключевые выводы ресёрча

### 2.1 Рынок (Slack/Discord/Telegram-боты)

- **Никто не делает «тихий» автосбор фактов из чата.** Все продукты либо (a) ограничивают, *кто* может учить бота (CommunityOne Spark — только trusted members), либо (b) ставят человека на подтверждение (Mava — suggestion queue; Question Base — expert-in-the-loop; AnswerHub — «save to KB one click»), либо (c) требуют явный жест (пины, реакции, команды — Pin Archiver сохраняет по 7 реакциям 📌). Это сильный сигнал: проблема шума/отравления базы реальна, автосбор надо делать как **suggestion pipeline с подтверждением**, а не как молчаливую запись.
- **Event-боты для Telegram** (TelegramEventBot, EventGram, EaseEvent): модель «ивент = одна структурированная запись, отрисованная одним закреплённым и постоянно редактируемым сообщением с inline-кнопками (RSVP)». Никто не извлекает факты из обсуждения — наша идея реально закрывает пробел. UX «единая карточка ивента» стоит позаимствовать.

### 2.2 Memory-системы (mem0, Zep/Graphiti, Letta, LangMem)

- **mem0**: двухфазный пайплайн — извлечение кандидатов-фактов дешёвой моделью → для каждого кандидата векторный поиск похожих существующих → LLM-решение **ADD / UPDATE / DELETE / NOOP** (tool-call с ID существующих записей). Отказ от извлечения зашит в сам промпт (`{"facts": []}` для болтовни) — один дешёвый вызов делает и гейтинг, и извлечение.
- **Zep/Graphiti**: би-темпоральная модель фактов — `valid_at/invalid_at` (когда факт был истинен в мире) + `created_at/expired_at` (когда система узнала/инвалидировала). Противоречие ⇒ старый факт **закрывается, а не удаляется** — история «место было X, стало Y» остаётся запрашиваемой. Относительные даты («в следующую субботу») резолвятся в абсолютные на этапе извлечения по timestamp сообщения — иначе яд для базы.
- **LangMem `ReflectionExecutor` — debounce-паттерн**: не извлекать по каждому сообщению; буферизовать, при новом сообщении отменять и перепланировать отложенную задачу, извлекать один раз, когда чат затих. Дёшево и контекст полнее.
- **Letta sleep-time agents**: тяжёлая консолидация памяти — фоновым процессом вне пути ответа пользователю.
- **Критичный замер** (arxiv 2606.26511): противоречащие факты в среднем *более* косинусно-похожи на оригинал, чем дубликаты; чистый similarity-порог для автозамены даёт precision ≈ 0.67. **Similarity находит кандидатов, но решение о замене принимает структурный ключ или LLM — никогда не порог похожести.**
- **MemStrata-паттерн** (ближайшая референс-схема нашей идеи): факты как строки `(subject, relation, value)` с интервалами валидности `valid_from/valid_to`, ссылкой `superseded_by` и provenance + embedding-колонка для поиска. Замена — структурная: одинаковый ключ `(subject, relation)`, другое значение ⇒ закрыть старую строку, открыть новую, в одной транзакции.
- **Граф не нужен**: на масштабе одного чата (сотни фактов) колонки (subject, predicate) дают главную пользу графа — скоуп проверки противоречий — обычным SQL-индексом. Graphiti требует Neo4j и не имеет Postgres-бэкенда; mem0 на pgvector — только vector-store без темпоральности. Схему делаем сами, всё в одном Postgres (join + транзакции вместо синка двух баз).

### 2.3 Авторитетность

- Итеративные truth-discovery алгоритмы (TruthFinder, CRH) — оверкилл для чата на десятки человек. Практический аналог — **статический приор ролей** (как trust levels в Discourse) + два правила: **свежее побеждает при равном ранге** и **подтверждение повышает** (админ отреагировал/переформулировал факт — факт продвигается).
- Бесплатные телеграм-сигналы авторитетности: статус админа/создателя чата (`getChatAdministrators`), **закреплённое сообщение** (сильнейший сигнал организатора), авторство исходного анонса, стаж/активность в чате (уже есть `chat_messages`/`user_activity`).

### 2.4 Фильтрация шуток

- SarcasmBench (arXiv:2408.11319): потолок LLM на сложном сарказме ~75–85% F1; few-shot лучше zero-shot, **CoT вредит**. Контекст диалога (±5 сообщений) существенно повышает точность. Русский — сопоставимо с английским (F1 ~0.76–0.84 на корпусах), но корпуса не чатовые.
- **Переформулировка задачи**: для KB не нужен вопрос «это сарказм?» — нужен «это серьёзное, пригодное для фиксации утверждение факта?» Классы: {серьёзное утверждение / шутка-сарказм / гипотеза-вопрос / цитата чужих слов}. Асимметрия цен ошибок в нашу пользу: пропущенный факт восстановим (повторят), отравленная запись — нет ⇒ **при сомнении воздерживаемся** и шлём в очередь подтверждения.
- Ожидаемые провалы: deadpan-ирония, «ага, конечно», абсурдное согласие, внутричатовые мемы старше окна контекста. Митигация — не автодетект, а маршрут в подтверждение.

### 2.5 Безопасность (RAG poisoning)

Контент участников, попадающий в retrieved-контекст — документированный вектор инъекций (OWASP LLM01/LLM04, Spotlighting arXiv:2403.14720):

1. Чатовый текст в промптах — только внутри ограждённых «DATA, not instructions» блоков (у нас уже есть `sanitize_prompt_content` — применять обязательно).
2. Выход экстрактора — **только валидируемый JSON-рекорд** (schema-валидация сама по себе фильтр инъекций), никогда свободный текст.
3. **Provenance на каждой записи** (message_id, user_id, timestamp) — позволяет отследить и вычистить отравленные записи.
4. Факты, возвращаемые из KB в промпт — ограждаются повторно (двойной fence: «ignore previous instructions, место — X» должен пережить два прохода LLM инертно).
5. Рендер в Telegram — `html.escape()` (наш parse_mode=HTML gotcha).

## 3. Предлагаемый дизайн

### 3.1 Модель данных — `chat_facts` (миграция 013)

```sql
CREATE TABLE IF NOT EXISTS chat_facts (
    id              BIGSERIAL PRIMARY KEY,
    chat_id         BIGINT NOT NULL,
    topic           TEXT,                    -- группировка: 'event:летний-митап' | 'general'
    subject         TEXT NOT NULL,           -- нормализованный ключ: 'мероприятие'
    predicate       TEXT NOT NULL,           -- 'дата', 'место', 'программа', ...
    value           TEXT NOT NULL,           -- 'Лофт №3, Артплей' (абсолютные даты!)
    fact_text       TEXT NOT NULL,           -- полная NL-формулировка для embedding/prompt
    embedding       vector(768),
    -- lifecycle (MemStrata/Graphiti)
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|active|rejected|superseded
    valid_from      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to        TIMESTAMPTZ,             -- NULL = действующий
    superseded_by   BIGINT REFERENCES chat_facts(id),
    -- provenance + trust
    source          TEXT NOT NULL,           -- 'manual' | 'extracted'
    source_message_id BIGINT,
    source_user_id  BIGINT,
    authority_level SMALLINT NOT NULL DEFAULT 0,  -- снимок ранга автора на момент записи
    confidence      FLOAT,                   -- уверенность экстрактора
    salience        FLOAT DEFAULT 0.5,       -- важность для приоритизации в контексте
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- индексы: (chat_id, status, valid_to), (chat_id, subject, predicate) WHERE valid_to IS NULL,
-- ivfflat (embedding vector_cosine_ops) WITH (lists=10)  -- поднять до 100 после ~4000 строк
```

Ручные и извлечённые факты в одной таблице (различаются `source`); ручной факт при равном ключе перевешивает извлечённый. **Замена = закрыть старую строку + вставить новую в одной транзакции; DELETE не бывает.** История эволюции факта — бесплатно (`superseded_by`-цепочка).

Плюс колонка `chat_settings.kb_organizer_ids JSONB DEFAULT '[]'` — пер-чатовые «организаторы» (новая концепция роли, в кодовой базе её нет).

### 3.2 Авторитетность — статический приор

| Ранг | Кто | Откуда |
|---|---|---|
| 4 | Бот-админ | `bot_config.admin_ids` |
| 3 | Организатор чата | `chat_settings.kb_organizer_ids` (назначается через админку/команду) |
| 2 | Админ Telegram-чата | `getChatAdministrators` (кэш ~1ч) |
| 1 | Старожил | стаж/активность из `chat_messages` (напр., ≥30 дней и ≥50 сообщений) |
| 0 | Новичок | остальные |

Правила: **свежее побеждает при равном или большем ранге**; факт от ранга <3, противоречащий факту от ранга ≥3, **не замещает**, а падает в очередь подтверждения; подтверждение организатором (кнопка/реакция) поднимает факт до active.

⚠️ `getChatAdministrators` — это 3-е использование Bot API из хендлеров ⇒ по нашему ADR пора выделять `TelegramAPIService`.

### 3.3 Пайплайн автосбора

```
каждое сообщение (MessageSaverMiddleware, kb_enabled)
  └─▶ буфер чата + debounce-таймер (LangMem-паттерн: N=20 сообщений ИЛИ 10 мин тишины;
      новое сообщение отменяет и перепланирует)
        └─▶ Экстрактор: ОДИН вызов дешёвой модели (gpt-5-nano / gemini-3-flash),
            JSON-only, few-shot; на батч сообщений сразу:
            – кандидаты-факты (subject, predicate, value, source_message_id, confidence)
            – класс серьёзности {serious|joke|hypothetical|quote} с контекстом окна
            – резолв относительных дат в абсолютные по timestamp (t_ref, Graphiti)
            – learned refusal: {"facts": []} для болтовни (mem0)
              └─▶ Reconciler (по каждому кандидату, последовательно):
                  1) точный ключ (chat_id, subject, predicate) → структурная замена/NOOP
                  2) нет ключа → top-k похожих active-фактов (pgvector) →
                     дешёвая LLM: ADD/UPDATE/NOOP (промпт по образцу mem0, с ID)
                  3) решение о commit по матрице authority × confidence:
                     ранг ≥3 & confidence высокая  → auto-commit (active) + undo
                     иначе                          → pending → очередь подтверждения
```

Стоимость: 1 дешёвый вызов на ~20–30 сообщений + по вызову на кандидата в reconciler; embeddings бесплатны (gemini-embedding-001). На живом чате — копейки; `router.log_usage(task_type="kb_extraction")` обязателен (наш ADR: generate_text не логирует сам).

### 3.4 Human-in-the-loop

- **Очередь подтверждения** в DM организатора: карточка «факт + цитата-источник + автор», кнопки **✅ принять / ✏️ править / ❌ отклонить** (verify-modify-reject триада). Никогда в группе.
- **Дайджест**: «за неделю я узнал: дата = 14.09 ✅, место = Лофт №3 (не подтверждено — подтвердить?)» с кнопками по пунктам.
- **Undo** для auto-commit: уведомление «записал: место = X [отменить]».
- Опционально: реакция-голосование в группе (📌 на «noted»-реплике бота) как лёгкое комьюнити-подтверждение.

### 3.5 Retrieval

- SQL-first: активные факты чата (`status='active' AND valid_to IS NULL`), ранжирование pgvector-косинусом по текущему контексту разговора, приоритизация по salience/recency, жёсткий токен-бюджет на секцию.
- Новая секция промпта `_kb_section` в `prompt_builder.py` (по образцу `_rag_section`), с двойным ограждением и `sanitize_prompt_content`.
- Отличие от существующего RAG (`chat_memory`): RAG — эпизодическая память «что обсуждали», KB — курируемые актуальные факты. Не смешивать; KB-секция идёт отдельно и выше по приоритету.

### 3.6 Интеграция в кодовую базу (всё по существующим паттернам)

| Что | Паттерн-образец |
|---|---|
| Миграция `013_knowledge_base.py` | 003 (chat_memory) + 005 (sticker_knowledge: updated_at-триггер, ivfflat lists=10) |
| `src/services/modules/knowledge/` (models, repository, extractor, reconciler, scheduler) | `modules/links/`, `modules/sticker/` |
| Debounce-scheduler (process-lifetime) | `StickerSetSyncScheduler` |
| JSON-extraction + repair | `generate_text(response_mime_type="application/json")` + `_parse_vision_response` 3-tier fallback |
| Toggle `kb_enabled` (4 согласованных правки) | YAML → `ChatConfig` → `_CHAT_CONFIG_FIELDS` → `_WRITABLE_COLUMNS` + миграция |
| Админ-подроутер `adm_kb_*` + пагинация | `admin_sticker.py` / `keyboards/admin_sticker.py` |
| Cost logging | `summary.py:97` (`ensure_future(log_usage(...))`) |
| Merge ручной правки с AI | sticker admin-correction merge (`learning.py:505-599`) |

### 3.7 Дополнительный функционал (вокруг ядра)

1. **📌 Карточка ивента** — одно закреплённое, постоянно редактируемое сообщение с актуальными фактами (проверенный UX event-ботов); опционально RSVP-кнопки.
2. **Анонс изменений** — при замене факта бот (opt-in) пишет в чат: «Обновление: место X → Y (по сообщению @организатора)».
3. **Q&A по базе** — «@бот когда мероприятие?» отвечает из KB с указанием источника; авто-FAQ: повторяющийся вопрос, на который есть факт → бот отвечает сам (паттерн Wallu/Question Base).
4. **История факта** — `/kb history место`: цепочка supersession «было → стало» с датами и авторами.
5. **Напоминания** — факты с датами → «через 3 дня мероприятие» (позже; интеграция с будущим scheduler).
6. **Явный жест сохранения** — reply на сообщение с `/remember` (или реакция 📌) кладёт его в экстрактор с повышенным приоритетом.
7. **Экспорт** — `/kb export` в markdown.

## 4. Фазирование

- **Фаза 1 — ручная KB (MVP)**: миграция 013, репозиторий, ручное добавление (/remember + организаторские команды/админка), retrieval в промпт, `/kb` просмотр, роль организатора. Без extraction вообще. Уже полезно и закрывает «вручную добавлять информацию».
- **Фаза 2 — автосбор как suggestions**: debounce-буфер, экстрактор с seriousness-классификацией, reconciler, очередь подтверждения в DM. **Все извлечённые факты — pending.**
- **Фаза 3 — авторитетность + auto-commit**: матрица authority × confidence, auto-commit с undo для рангов ≥3, анонсы изменений, карточка ивента.
- **Фаза 4 — обвязка**: Q&A/авто-FAQ, дайджесты, история, напоминания, экспорт.

Порядок отражает вывод рынка: сначала завоевать доверие с человеком в цикле, потом ослаблять контроль по типам фактов.

## 5. Риски

- **Потолок LLM-фильтра шуток ~75–85%** → дизайн «воздержаться при сомнении», ошибки уходят в очередь, а не в базу.
- **RAG poisoning** → структурный JSON, provenance, двойной fence, html.escape (§2.5).
- **callback_data 64 байта** → в кнопках только числовые id фактов.
- **Privacy**: KB хранит утверждения с атрибуцией (user_id) — данные и так есть в `chat_messages`; но в open-source репо не коммитить реальные примеры.
- **`getChatAdministrators`** → кэш + выделение `TelegramAPIService` (3-е использование по ADR).

## 6. Источники

mem0 pipeline/prompts: arxiv.org/html/2504.19413, github.com/mem0ai/mem0 (configs/prompts.py) · Zep/Graphiti bi-temporal: arxiv.org/html/2501.13956, blog.getzep.com/beyond-static-knowledge-graphs · LangMem debounce: langchain-ai.github.io/langmem/guides/delayed_processing · Letta sleep-time: letta.com/blog/sleep-time-compute · Similarity ≠ supersession + MemStrata: arxiv.org/pdf/2606.26511 · Truth discovery survey: arxiv.org/pdf/1505.02463 · Discourse trust levels: blog.discourse.org/2018/06/understanding-discourse-trust-levels · SarcasmBench: arxiv.org/abs/2408.11319 · Русский сарказм: arxiv.org/abs/2306.00445 · Spotlighting: arxiv.org/abs/2403.14720 · OWASP LLM Top-10 2025 · Продукты: Question Base, CommunityOne Spark, Mava, Wallu, AnswerHub, Pin Archiver, TelegramEventBot, EventGram, EaseEvent.
