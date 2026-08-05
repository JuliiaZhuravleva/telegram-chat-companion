---
schema_version: 3
plan_id: reactions-2026-08-03
source_artifact:
  path: docs/plans/reactions-2026-08-03.md
  sha256: 812c3a9fe801d05a31536242a02c1d2c8876f9983cc61a6d864d1457e08745cf
  type: session-analysis
created_at: '2026-08-03T15:34:05Z'
approved_at: '2026-08-03T17:04:51Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: partial
  started_at: '2026-08-03T17:12:03Z'
  completed_at: null
  current_batch: null
  task_list_id: reactions-2026-08-03
items:
- id: ADR-0004
  title: 'ADR-0004: модель данных реакций (денормализованная строка на эмодзи+действие; хранение типа-дискриминатора и сырого id кастом-эмодзи), отдельный модуль modules.reactions, приватность (короткий отдельный retention + privacy-тумблер, не отдавать внешнему AI), выбор реакции через LLM только на пути tier-3 (пиггибэк к llm_judge, 0 доп. токенов; на прочих путях в фазе 1 бот реакцию не ставит), диагностика прав бота. Фиксирует принятые владельцем решения.'
  specialist: architect
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 3h
  confidence: 0.85
  consult_session_id: b18e179d-d88f-4a35-98e8-1685602be325
  specialist_session_id: 449f332b-9d4e-4114-93c6-5e4cc6249aaa
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:17:33Z'
    executor: architect
    note: Locked message_reactions schema (denormalized row-per-emoji+action, Bot-API-native type discriminator, no FK to chat_messages), module split (modules/reactions/ pure logic + selector/responder vs database/repositories/reactions.py, mirroring ADR-0003 repository-location correction), two independent privacy toggles (reactions_enabled vs reactions_history_enabled) + separate 30d retention + hard no-AI-prompt rule for raw rows, R-5 tier-3 llm_judge piggyback with fail-closed emoji validation, R-D1 live (non-cached) admin-rights check rationale.
  result:
    kind: file
    ref: docs/decisions/ADR-0004-reactions-data-model.md
    verification: 'ADR grounded in verified code: aiogram ReactionType fields, set_message_reaction docstring, RETENTION_TABLES/MaintenanceSettings pattern, no existing FK to chat_messages, no existing get_chat_member call site; includes implementation notes for R-1/R-5/R-D1/QA-1 and a forward scope-note for Phase 2 R-4'
- id: R-1
  title: 'Хранение истории реакций: миграция 018, новая таблица, хендлер message_reaction (дифф old→new, аноним user=None/actor_chat, связь с chat_messages по chat_id+message_id, retention)'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - ADR-0004
  estimated_effort: 6h
  confidence: 0.85
  consult_session_id: 7b42f8a1-9094-496f-9e95-b5c085a470a2
  specialist_session_id: b2782e67-7ed5-41fe-9dbb-e147e238cc76
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:32:04Z'
    executor: backend-dev
    note: 'Migration 018 (message_reactions table + reactions_enabled/reactions_history_enabled toggles), modules/reactions/models.py (ReactionEvent + pure diff()), database/repositories/reactions.py (ReactionRepository), bot/handlers/reactions.py (message_reaction handler, gated on both toggles per ADR-0004 Decision 3), ChatConfigMiddleware extended for MessageReactionUpdated, dp.message_reaction wired in main.py, retention wired into MaintenanceSettings/RETENTION_TABLES/RetentionCleaner (30d default), Dishka provider registered. Did NOT touch R-5 (ReactionSelector/responder.py/LLM piggyback) or R-D1 (admin diagnostics) -- separate items. No admin-panel UI toggle built (not in R-1 title scope; DB/ChatConfig plumbing is in place). QA-1 routing hint: needs integration coverage (testcontainers) for the real INSERT, message_reaction auto-registration into allowed_updates, and a live admin-rights probe -- this item only has unit coverage. Did not touch test_alembic_online_upgrade.py''s hardcoded revision list -- that is QA-1''s fix.'
  result:
    kind: commit
    ref: ed17006
    verification: 'pytest tests/unit -q: 1043 passed; ruff check src/ tests/unit/: clean; mypy src/: no issues in 117 files; alembic upgrade head --sql chain-integrity subprocess check passes (017 -> 018)'
- id: R-2
  title: 'Иногда комментировать чужие реакции: шанс, взвешенный по «необычности» (редкий/негативный эмодзи, первый реакт, лавина, реакт на старое сообщение), антиспам-кулдаун, дешёвый AI-тир'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - R-1
  estimated_effort: 6h
  confidence: null
  consult_session_id: 7b42f8a1-9094-496f-9e95-b5c085a470a2
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:01:29Z'
    executor: pm-orchestrator
    note: 'Фаза 2 (после фазы 1 и накопления истории R-1) [Q1(б)]. Решено [R-2]: комментарий на чужой реакт — реплаем на исходное сообщение; для форумов топик брать из сохранённого сообщения. Только дешёвый AI-тир; антиспам через update_response_cooldown; шанс взвешен по «необычности» реакта. Разблокируется при планировании фазы 2.'
  result: null
- id: R-3
  title: 'Бот сам ставит реакции с малым шансом на любое сообщение (вариант (б), фаза 2): дополнительный триггер поверх примитива ReactionSelector из R-5; одна реакция на сообщение, per-chat тумблер и шанс, медиагруппа — первое неудалённое сообщение'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - R-5
  estimated_effort: 3h
  confidence: null
  consult_session_id: 7b42f8a1-9094-496f-9e95-b5c085a470a2
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:01:33Z'
    executor: pm-orchestrator
    note: 'Фаза 2 [Q1(б)]. [Q3]: в фазу 1 идёт вариант (а)=R-5; здесь остаётся вариант (б) — реакция с малым шансом на любое сообщение, самый спам-рисковый триггер, отложен. Использует примитив ReactionSelector из R-5 (зависимость перевешена ADR-0004 → R-5). Медиагруппа: реакция на первое неудалённое сообщение.'
  result: null
- id: R-4
  title: '«Учиться» у чата ставить реакции: профиль чата из накопленной истории R-1 (какие эмодзи в ходу, на какие сообщения), выбор реакции в стиле чата'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - R-1
  estimated_effort: 8h
  confidence: null
  consult_session_id: 7b42f8a1-9094-496f-9e95-b5c085a470a2
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:01:36Z'
    executor: pm-orchestrator
    note: 'Фаза 2 [Q1(б)]. Жёсткая зависимость от накопленной истории R-1. [Q4]: выбор реакции делает LLM (решение владельца). Режим (пиггибэк к идущему AI-вызову vs отдельный вызов) фиксируется в ADR-0004; детально прорабатывается при планировании фазы 2. Разблокируется, когда R-1 накопит данные.'
  result: null
- id: R-5
  title: 'Реакция вместо текстового ответа ТОЛЬКО на пути tier-3 (llm_judge relevancy gate): когда LLM решает не отвечать словами, тот же вызов возвращает и предлагаемый эмодзи — 0 доп. токенов. Включает примитив постановки реакции (setMessageReaction, обработка available_reactions/ошибок, одна реакция на сообщение, per-chat тумблер, единый ReactionSelector для R-3/R-8). На остальных путях в фазе 1 бот реакцию не ставит.'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - ADR-0004
  estimated_effort: 6h
  confidence: 0.8
  consult_session_id: 7b42f8a1-9094-496f-9e95-b5c085a470a2
  specialist_session_id: 9d8f6d01-bde6-4d16-8ec7-d6350fe07b2b
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:41:37Z'
    executor: backend-dev
    note: 'Implemented per ADR-0004 Decision 4: llm_judge returns a NO-only suggested_emoji at zero extra token cost (fail-closed parsing: prose/NONE/YES all -> None); GateDecision carries it through only on tier=llm_judge; new modules/reactions/selector.py (ReactionSelector, fail-closed against hardcoded ALLOWED_REACTION_EMOJI, exact-match only, never substitutes a default) and responder.py (wraps bot.set_message_reaction, swallows TelegramBadRequest); message.py reacts instead of replying on tier-3 silence, gated on reactions_enabled only (not reactions_history_enabled per Decision 3), with a broad try/except safety net around set_reaction. CAVEAT: ALLOWED_REACTION_EMOJI (73 entries) is hardcoded platform knowledge -- Bot API doesn''t expose this list and I could not verify it live in this sandbox; ASSUMED/best-effort (source plan section 3 partial list + recalled Telegram docs), recommend QA-1 spot-check a few against a real setMessageReaction call. Did not touch R-D1/QA-1. Only unit coverage (mocked Bot/AI router) per backend-dev scope; PM may want a qa follow-up for a live smoke-test of the reaction-on-silence path specifically, since QA-1 does not call it out by name.'
  result:
    kind: commit
    ref: d2b78af
    verification: 'pytest tests/unit -q: 1072 passed; ruff check src/ tests/unit/: clean; mypy src/: no issues in 119 files'
- id: R-6
  title: 'Закрыть бэклог F-9 (только сигнал/хранение): сохранять реакции на ответы бота (is_bot_message) как сигнал качества 👍❤🔥 vs 👎💩🤮; пересчёт importance_score вынесен в отдельный TD-пункт вне этого плана'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - R-1
  estimated_effort: 3.5h
  confidence: null
  consult_session_id: 7b42f8a1-9094-496f-9e95-b5c085a470a2
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:01:39Z'
    executor: pm-orchestrator
    note: 'Фаза 2 [Q1(б)]. Решено [R-6]: в этот план только сигнал/хранение реакций на bot-сообщения (через R-1). Потребитель в памяти (обновление importance_score, нужен новый метод update — дельта vs абсолют) вынесен в ОТДЕЛЬНЫЙ TD-пункт вне этого плана. Общий агрегатор валентности реакций с backlog-R-7.'
  result: null
- id: R-7
  title: 'Реакции как сигнал важности: сообщение, собравшее много реактов, — дешёвый приоритет для RAG-памяти и автосбора в knowledge base'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - R-1
  - R-6
  estimated_effort: 3h
  confidence: null
  consult_session_id: 7b42f8a1-9094-496f-9e95-b5c085a470a2
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:01:42Z'
    executor: pm-orchestrator
    note: Бэклог, вне этого плана [Q5]. Реакции как сигнал важности для RAG-памяти/knowledge base. Пересекается с TD-потребителем importance_score из R-6 — свести в один пункт при отдельном планировании.
  result: null
- id: R-8
  title: 'Реакция как действие rules_engine: мягкая, не-конфликтная модерация (в дополнение к warn_user)'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - R-3
  - R-5
  estimated_effort: 2.5h
  confidence: null
  consult_session_id: 7b42f8a1-9094-496f-9e95-b5c085a470a2
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:01:45Z'
    executor: pm-orchestrator
    note: Бэклог, вне этого плана [Q5]. Реакция как действие rules_engine (мягкая, не-конфликтная модерация). Зависит от примитива R-5/R-3 и от rules_engine (сам по умолчанию выключен, P2/бэклог как F-2).
  result: null
- id: R-9
  title: 'Аналитика реакций в админке / /summary: самое залайканное сообщение за период, топ реакторов, настроение чата по времени'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - R-1
  estimated_effort: 4h
  confidence: null
  consult_session_id: 7b42f8a1-9094-496f-9e95-b5c085a470a2
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:01:47Z'
    executor: pm-orchestrator
    note: Бэклог, вне этого плана [Q5]. Аналитика реакций в админке/summary. Читающий слой поверх R-1; полноценный дашборд требует сначала дизайна у architect, а не прямой backend-задачи.
  result: null
- id: R-D1
  title: 'Диагностика прав администратора бота (риск §5.1): без админ-прав апдейты о реакциях не приходят молча, без ошибки — показать это владельцу'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 2h
  confidence: 0.85
  consult_session_id: 7b42f8a1-9094-496f-9e95-b5c085a470a2
  specialist_session_id: 23115a89-bcce-4de5-9d60-473bcaa2f56a
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:52:48Z'
    executor: backend-dev
    note: 'New adm_react_* admin-panel submenu (chat picker + per-chat menu, mirrors admin_kb.py''s shape) since neither R-1 nor R-5 had built any admin-panel entry point for reactions_enabled/reactions_history_enabled yet. Per ADR-0004 Decision 5: live bot.get_chat_member() check (new src/bot/utils.py::is_bot_chat_admin, fail-closed on API error), never cached -- rendered as a status line on every menu render, and re-checked immediately with a popup warning when reactions_enabled is toggled ON (owner''s answer: active check at toggle-time + line in admin panel). reactions_history_enabled exposed as its own independent toggle per Decision 3. _check_admin_direct is now duplicated a 3rd time (admin_kb.py, admin_sticker.py, this file) -- flagged in-code as TD, not extracted here to keep blast radius to the new sub-router. QA-1 should still do the live admin-rights probe against a real chat where the bot is NOT an admin (already called out in its own item note) -- my coverage is unit-only (mocked Bot).'
  result:
    kind: commit
    ref: 7a5826e
    verification: 'pytest tests/unit -q: 1101 passed (29 new: test_bot_utils.py, test_admin_reactions_keyboards.py, test_admin_reactions_handler.py); ruff check src/ tests/unit/: clean; mypy src/: no issues in 121 files'
- id: QA-1
  title: 'Тесты фундамента реакций: интеграционные (testcontainers), миграция 018 alembic upgrade head на пустой базе, авто-регистрация message_reaction в allowed_updates (проверить тестом), проба прав админа, гигиена фикстур (без реальных Telegram id), фикс устаревшего списка ревизий в test_alembic_online_upgrade, ручной чек-лист живой проверки'
  specialist: qa
  priority: P1
  status: done
  depends_on:
  - R-1
  - R-D1
  estimated_effort: 4h
  confidence: 0.85
  consult_session_id: 34bb82c9-1856-4bae-843f-e03d00f1bede
  specialist_session_id: 3a880ad7-d826-4583-ab50-3117a0181e48
  retry_count: 0
  last_update:
    ts: '2026-08-03T17:59:32Z'
    executor: qa
    note: 'Wrote real-Postgres integration coverage (11 new tests, tests/integration/test_migration_018_message_reactions.py): message_reactions table shape/indexes, no-FK-to-chat_messages behavior, chat_settings reactions_enabled/reactions_history_enabled nullable-no-default plus independent toggles, and ReactionRepository against a live db_conn (unit coverage only mocked the pool -- this proves the real asyncpg INSERT works). Fixed the stale hardcoded revision list in test_alembic_online_upgrade.py (016/017/018 were missing -- migration 018 was never walked one-revision-at-a-time online; the full-head test already covered it end-to-end but not per-revision). Added tests/unit/test_reactions_allowed_updates.py proving message_reaction auto-registers into dp.resolve_used_update_types() via a real Dispatcher plus main_router (previously only asserted in a code comment), with a negative control. Live admin-rights probe and ALLOWED_REACTION_EMOJI spot-check cannot run in CI (need a real Telegram chat/bot token) -- wrote docs/plans/reactions-2026-08-03.qa-live-checklist.md covering R-D1 admin-rights diagnostics, R-1 history round-trip (add/remove/replace/anonymous/toggle-off), R-5 reaction-instead-of-reply incl. restricted available_reactions, and a fixture-hygiene grep step. Fixture hygiene audit: reaction test files reuse the codebase-wide placeholder chat id already used in 10 other unit test files -- no real Telegram ids found. Full verification: pytest tests/unit -q 1103 passed; pytest tests/integration/test_migration_018_message_reactions.py tests/integration/test_alembic_online_upgrade.py -q 15 passed; ruff check src/ tests/ clean; mypy src/ no issues in 121 files.'
  result:
    kind: commit
    ref: 1e3bda6
    verification: pytest tests/unit -q (1103 passed); pytest tests/integration/test_migration_018_message_reactions.py tests/integration/test_alembic_online_upgrade.py -q (15 passed, real pgvector testcontainer); ruff check src/ tests/ clean; mypy src/ clean (121 files)
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 19.4371
review_gate:
  why: []
  approve_action: /execute-plan <projects>/telegram-chat-companion.reactions-2026-08-03-wt/docs/plans/reactions-2026-08-03.execution.md --resume
  reject_action: /plan-fixes docs/plans/reactions-2026-08-03.md --revise <projects>/telegram-chat-companion.reactions-2026-08-03-wt/docs/plans/reactions-2026-08-03.execution.md
safe_to_replay_from: null
clarifying_questions: []
human_feedback:
- ts: '2026-08-03T16:50:45Z'
  by: julia
  text: 'ANSWER [Q1]: (б)'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-03T17:01:53Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T16:51:12Z'
  by: julia
  text: 'ANSWER [Q2]: давай отдельный'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-03T17:01:54Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T16:51:38Z'
  by: julia
  text: 'ANSWER [Q3]: (а) вместо заглушённого ответа (= R-5)'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-03T17:01:56Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T16:52:27Z'
  by: julia
  text: 'ANSWER [Q4]: Я бы делала чтоб LLM решал. Можно просто вместо ответа делать'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-03T17:01:57Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T16:52:44Z'
  by: julia
  text: 'ANSWER [Q5]: Давай по рекомендации'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-03T17:01:59Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T16:53:06Z'
  by: julia
  text: 'ANSWER [R-1a]: денормализованная строка на (эмодзи, действие)'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-03T17:02:00Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T16:53:23Z'
  by: julia
  text: 'ANSWER [R-1b]: хранить тип и сырой id'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-03T17:02:01Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T16:53:57Z'
  by: julia
  text: 'ANSWER [R-1c]: короткий отдельный срок + отдельный privacy-тумблер на запись'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-03T17:02:03Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T16:54:12Z'
  by: julia
  text: 'ANSWER [R-2]: реплай на исходное сообщение'
  applies_to: R-2
  status: addressed
  addressed_at: '2026-08-03T17:02:04Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T16:54:32Z'
  by: julia
  text: 'ANSWER [R-6]: в этот план только сигнал/хранение; потребитель в памяти — отдельным пунктом TD'
  applies_to: R-6
  status: addressed
  addressed_at: '2026-08-03T17:02:06Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T16:54:51Z'
  by: julia
  text: 'ANSWER [R-D1]: активная проверка при включении модуля + строка в admin-панели'
  applies_to: R-D1
  status: addressed
  addressed_at: '2026-08-03T17:02:07Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T16:55:20Z'
  by: julia
  text: 'ANSWER [Q6]: во всех чатах тестовых бот админ'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-03T17:02:08Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-03T17:08:16Z'
  by: julia
  text: 'ANSWER [R-5 / уточнение Q4]: формулировка «LLM выбирает эмодзи пиггибэком к уже идущему AI-вызову» верна ТОЛЬКО для tier-3 релевантность-гейта (llm_judge), где вызов действительно уже оплачен — проверено по коду: src/services/relevancy/gate.py:86. На остальных путях подавления AI-вызова НЕТ вообще: tier-1 fast_rules (gate.py:68, «zero cost»), tier-2 engagement (gate.py:75, 1 SQL), кулдаун/блэклист (src/services/text/pipeline.py:131-135, выход на Stage 1 задолго до генерации на строке 217), и невыпавший random_response_chance (src/bot/handlers/message.py:59). Решение владельца: LLM выбирает эмодзи ТОЛЬКО на пути tier-3 — llm_judge в одном вызове возвращает и решение «отвечать?», и предлагаемый эмодзи, то есть 0 дополнительных токенов. Отдельный AI-вызов ради выбора реакции не заводить ни на одном другом пути. В фазе 1 на остальных путях бот реакцию не ставит вовсе (накопленной истории для статистического выбора ещё нет); статистический селектор появляется вместе с R-4 во второй фазе. Перепиши R-5 так, чтобы это было явно в title/note и в критериях приёмки, и убери из формулировки обобщение «когда гейт/кулдаун/fatigue молчат» — оно шире реальной механики.'
  applies_to: R-5
  status: addressed
  addressed_at: '2026-08-03T17:11:22Z'
  addressed_by: pm-orchestrator
revision_number: 3
last_revised_at: '2026-08-03T17:11:42Z'
last_revised_by: pm-orchestrator
---




















































































































<!-- BRIEF:START lang=ru -->
# Реакции: чтобы бот жил в чате вместе со всеми

## Что произошло
Ты попросила научить бота полноценно работать с реакциями — сейчас он их вообще не видит и сам
не ставит. Мы разобрали материал, оценили работу и риски у трёх специалистов и получили от тебя
ответы на все открытые вопросы. Работа разбита на две фазы: сначала фундамент, потом — «умные»
поведения на накопленных данных.

## Что важно знать заранее
- **Без прав администратора Telegram молча не присылает боту реакции** — без единой ошибки.
  Поэтому первым делом бот проверит свои права при включении и покажет их статус в админ-панели.
- **Реакции — более личные данные, чем текст.** Их хранение будет с отдельным коротким сроком и
  отдельным выключателем по каждому чату; наружу (внешнему ИИ) эти данные не уходят.
- **Комментарии бота на чужие реакты — самая рискованная по «раздражению» функция.** Поэтому
  она уходит во вторую фазу, будет срабатывать редко и её можно выключить.

## Что будет сделано в первой фазе
- Бот начнёт запоминать, кто, когда и какой реакт поставил или снял — фундамент для всего
  остального (R-1, ADR-0004).
- Честная диагностика прав бота в чате (R-D1).
- Когда умная проверка релевантности решает, что отвечать словами не стоит, бот сможет тихо
  «согласиться» реакцией вместо текста. Какую реакцию поставить — решает ИИ в тот же момент,
  без каких-либо лишних расходов (это часть уже идущего решения «отвечать ли»). На остальных
  сценариях в первой фазе бот реакции сам не ставит — сначала копит данные (R-5).
- Тесты фундамента, включая живую проверку в тестовом чате, где бот — админ (QA-1).

## Что во второй фазе (после накопления данных)
- Иногда по-человечески подмечать неожиданный чужой реакт — ответом-реплаем на само сообщение (R-2).
- Со временем перенимать стиль реакций чата; выбор реакции делает ИИ (R-4).
- Дополнительный триггер: изредка реагировать на любое сообщение (R-3).
- Учитывать реакции на свои ответы как оценку качества — пока только сбор сигнала (R-6).

## Не входит в этот план
В бэклог отложены: приоритет важных сообщений по реакциям (R-7), реакция как действие правил
модерации (R-8) и аналитика реакций в админке (R-9). Пересчёт «важности» в памяти по реакциям на
ответы бота вынесен в отдельный технический пункт.

## Оценка
Первая фаза — примерно 21 час работы, в пределах потолка бюджета $30. Полный объём со второй
фазой и бэклогом — около 49 часов, поэтому и разбили на этапы.
<!-- BRIEF:END -->

# Plan — reactions-2026-08-03

## Source

[`docs/plans/reactions-2026-08-03.md`](docs/plans/reactions-2026-08-03.md) (sha256 `812c3a9fe801...`).

## Items

(none yet — populated by /plan-fixes)
