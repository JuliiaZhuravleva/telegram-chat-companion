# RAG Revision — current state, target architecture, roadmap

> 2026-08-05. Product direction: [docs/NORTH-STAR.md](../NORTH-STAR.md).
> Slice S1 shipped as PR #20 (migration 022). Behavioral evidence below is
> aggregate-only; the raw analysis lives outside the repository.

## 1. Why

Found on live traffic the day of the production cutover (TD-016): asked what
someone had been up to "a month ago", the bot retrieved plausible memories
(0.79–0.81 cosine) and confidently made up an answer. Both causes are design,
not damage:

1. **The index is an echo of the bot, not the chat.** Vector memory stores only
   `Q/A` pairs of the bot's own replies (~2.9k rows against ~36k messages, ~8%).
   The chat itself was never indexed.
2. **Retrieval is time-blind.** Cosine + a fixed 0.7 floor; no recency term, no
   time bounds, and until S1 the memory's date wasn't even shown to the model.

A full review of the RAG surface (vector memory + KB Phase 1 + prompt assembly)
found 26 secondary defects — dead settings, a latent 1536-dim fallback bug,
three disagreeing thresholds, zero tests on the retrieval path, zero
observability. This document is the holistic plan those pieces never had.

## 2. Evidence from production data (aggregates)

Replaying ~6.5 months of the previous generation's traffic against the current
index, at current production settings:

- Of real memory-seeking questions, **only ~1 in 3 finds any memory above the
  0.7 floor** once you exclude self-matches (memories created by the very
  exchange being replayed). The rest hit an empty section — and the model
  answers blind instead of saying "не помню".
- Best-similarity scores **cluster exactly at the floor** (0.63–0.70): a ±0.05
  shift changes outcomes radically. The floor must be calibrated against a
  golden set, not hand-picked.
- The bot writes **2–3× longer messages than the humans** around it.
- An unprompted (random) reply landed in an active conversation and the
  conversation **died within 30 minutes in ~6% of cases**.
- Repetitive-shtick complaints ("опять ты про X") appear verbatim in replies to
  the bot — the persona reuses its hobby-horses.
- The anti-abuse auto-blacklist has banned **everyday short words** (incl.
  "спасибо") — the bot silently ignores messages matching them. Invisible
  before decision_log existed.

## 3. External research (2024–2026, key takeaways)

Full digest with per-topic verdicts was compiled from primary sources; what
changes or sharpens the design:

- **Contextual Retrieval** ([Anthropic](https://www.anthropic.com/news/contextual-retrieval)):
  chunk-specific context prepended before embedding *and* BM25 indexing cuts
  retrieval failures 35–49% (67% with rerank). Our synthetic header (chat +
  date + speakers) is the cheap end of this; an LLM-generated per-chunk context
  (~$1/M tokens at ingestion) is worth an A/B on the golden set.
- **Hybrid + RRF** ([Supabase](https://supabase.com/docs/guides/ai/hybrid-search),
  [ParadeDB](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)):
  k=60 — leave it alone; tune per-leg *weights* instead; leg depth ≥2× the fused
  top-k; Postgres `ts_rank` is not BM25 — treat the FTS leg as a recall booster.
  Recency belongs in fusion as its **own ranked leg** (or post-fusion
  exponential decay), never baked into similarity.
- **Group-chat memory** ([GroupMemBench](https://arxiv.org/abs/2605.14498)):
  the first multi-party benchmark; best system scores 46%, and **a plain BM25
  baseline beats most agent-memory pipelines** because ingestion erases speaker
  and lexical structure. Direct orders: keep verbatim `Speaker: text` lines in
  chunks, attribute KB facts to speakers, over-represent knowledge-update
  questions in eval. Event-boundary episodic segmentation (our pause-based
  sessions) measurably helps multi-hop recall
  ([2602.01313](https://arxiv.org/pdf/2602.01313)).
- **When-to-retrieve** ([Adaptive-RAG](https://arxiv.org/abs/2403.14403),
  [budget-aware eval](https://arxiv.org/html/2607.24010)): retrieval actively
  *harms* 4–9% of answers — the strongest argument for an intent gate; but
  trigger thresholds are empirically unstable, so the gate must be instrumented
  and re-calibrated from logs, and a trivial heuristic must be benchmarked
  before paying for a classifier.
- **Memory frameworks** ([Zep](https://arxiv.org/abs/2501.13956),
  [mem0](https://arxiv.org/abs/2504.19413)): confirm our episodic-vs-curated
  split and bi-temporal KB; adopt mem0-style write-time resolution
  (new fact vs top-k similar → ADD/UPDATE/supersede/NOOP) when KB
  autocollection (PH2) is re-planned; invalidate, never delete; keep fact →
  source-chunk provenance.
- **Embeddings** ([Gemini docs](https://ai.google.dev/gemini-api/docs/embeddings),
  [ruMTEB](https://aclanthology.org/2025.naacl-long.12/)): set asymmetric
  `task_type` (`RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY`) — **but only for the
  new chunk index built from scratch (S4)**: task_type changes the embedding
  space, so it must never be flipped on the live `chat_memory` index whose
  stored vectors were embedded without it. (L2 re-normalization of truncated
  vectors is moot for us: cosine is scale-invariant; it would matter only if a
  dot-product path appeared.) For Russian FTS: ё→е normalization at index and
  query time; Snowball stemming is the accepted limit, Hunspell only if logs
  show FTS-leg misses.
- **Eval** ([Langfuse](https://langfuse.com/resources/engineering/golden-dataset-evaluation),
  [RAGAS](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)):
  recall@k primary, MRR secondary; below ~50 cases trust only large deltas;
  grow the set from logged real queries; include answer-absent and
  answer-contradicted strata.

## 4. Target architecture

### 4.1 Index: `chat_chunks` (new table; Q&A memory retires after shadow period)

Conversation-session chunks over the **whole** chat (`chat_messages` already
holds both sides):

- Session boundary: 3h pause per `(chat_id, thread_id)`; TARGET 1200 chars,
  HARD_MAX 2600, ≤80 msgs; overlap 2 msgs capped at 400 chars, suppressed
  across a pause. (Parameters measured in the sibling archive projects — do
  not re-derive.)
- Synthetic header `Чат «X», {топик,} дата` + verbatim `Имя (ЧЧ:ММ): текст`
  lines — time, interlocutors and the group's actual vocabulary stay
  searchable in both legs (GroupMemBench's core lesson).
- Natural key `(chat_id, thread_id, msg_from, msg_to, part)` +
  `ON CONFLICT DO NOTHING`; `senders BIGINT[]`; `emb_model` per row (model
  migration path designed before indexing, not after); generated `tsvector`
  (russian, ё→е normalized).
- **No ANN index in v1**: at hundreds–2k chunks per chat, exact scan is both
  faster and complete (and deletes the ivfflat-lists retuning problem).
  Revisit near ~50k chunks/chat.
- Incremental indexer: background task (RetentionCleaner pattern, ~15 min),
  chunks only **closed** sessions (debounce by design), GC of stale windows,
  embeds `WHERE embedding IS NULL` with per-vector validation, transaction
  closed before every API call. Backfill = the same code walking history.
- Curation: `rag_enabled` per chat is the whole curation story for ~11 chats.

### 4.2 Retrieval: hybrid FTS + vector, RRF, in one SQL

- Two `MATERIALIZED` CTE legs (ranks over materialized subqueries — never over
  an index scan), depth ≥2× fused top-k; RRF k=60 with per-leg weights as the
  tuning knob; AND→OR `websearch_to_tsquery` relaxation when the strict form
  matches zero rows (never for negated queries).
- Filters: `chat_id` always (privacy invariant, tested); time bounds and
  `senders @>` in the SQL signature from day one.
- The 0.7/0.65 floor dies. The injection floor is **calibrated on the golden
  set** (retain ≥95% of true positives) and re-calibrated from
  `retrieval_log` distributions; injected chunks are framed as "возможно
  релевантные фрагменты — игнорируй, если не в тему".
- **Explicit-empty contract** (north star 3.2): when nothing clears the floor,
  the prompt says so, licensing the in-character "не помню".

### 4.3 KB relationship

Separate stores, separate prompt sections (mixing content types in one index
measurably degrades the primary case). KB fix that must not wait: cosine
becomes the primary sort, salience a tiebreak — otherwise the first salience
differentiation (PH2) turns KB retrieval into "always inject the most salient
facts". One shared query embedding per turn (kills the double-embed, threads
`chat_id` into embedding cost logs — TD-009).

### 4.4 Prompt budget: ADR-0006 supersedes ADR-0001

Old premises are false (budget sized for Q&A pairs; chars÷4 undercounts
Cyrillic — use ÷3). CHUNKS_BUDGET ≈ 900 tokens (~2 full chunks), per-chunk
render cap; `trim_history_to_budget` finally lands (TD-007); all trims return
kept-lists so `retrieval_log.injected` stays truthful.

## 5. Roadmap

| # | Slice | Status | Ships |
|---|-------|--------|-------|
| S1 | Observability | ✅ PR #20 | `decision_log` + `retrieval_log` (migration 022), writers, dated RAG memories, bounded container logs, retention |
| S2 | Correctness & hygiene | next | 1536-dim latent fallback bug; threshold consolidation; KB order-by fix; single query embedding + TD-009; dead code (delete_expired, ChatFact, unread module toggles); `chat_memory` into retention (stopgap); missing tests (MemoryRepository chat-scoping, RAGMemoryService); riders: `/kb` gating, relevancy_check provider routing |
| S3 | Eval harness + baseline | | `scripts/eval_rag.py` + committed synthetic example template; golden set (gitignored) grown from `retrieval_log` real queries; **recorded baseline of the current Q&A store** — the justification artifact; knowledge-update + answer-absent strata |
| S4 | Chunk store + indexer + backfill | | migration: `chat_chunks`; chunker (golden-file tests); background indexer; `task_type`-asymmetric embeddings (fresh index only); optional A/B: LLM per-chunk context vs header-only, gated on eval delta |
| S5 | Retrieval cutover | | hybrid RRF SQL + OR-relaxation; `rag_backend` env flag; S5a shadow (chunks retrieval logs `injected=false` on live traffic, zero prompt impact) → S5b flip: injection + explicit-empty contract + ADR-0006 trims; Q&A writes stop |
| S6 | Calibration | | floors re-tuned from prod `retrieval_log`; KB floor; recency as third RRF leg / decay multiplier (eval-gated); intent gate: heuristic first, instrumented fire-rate, classifier only if the heuristic measurably loses; `chat_memory` drop migration scheduled |

Every slice merges to main independently (merge = production release); S1–S5
add **zero** new LLM calls (embeddings are free) — all of v1 fits the
cheap-model policy. Migrations are forward-only: natural keys and schema land
right the first time or as new migrations, never edits.

## 5.1 Пересборка порядка после замеров 2026-08-18

> Роадмап выше остаётся верным по существу: §4.1 (чанки по всей истории вместо
> Q&A-пар) — это и есть ответ на «RAG ничего не находит». Но три вещи обязаны
> приехать **перед S4**, потому что они портят те самые измерения, по которым
> S4–S6 принимают решения. Иначе слайсы будут гейтиться на искажённое число.

### R0 — гигиена запроса: триггерное слово не должно попадать в эмбеддинг

**TD-092.** Эмбеддинг запроса считается из сырого `message_text`, а каждое
адресованное сообщение начинается с `бот`/`bot`. Слово семантически громкое и
тянет запрос к содержимому *про бота*.

Измерено на живой продовой базе (метод валидирован: `бот, что такое открытый
проектор?` дал 0.706 — ровно то, что записано в продовом `retrieval_log`):

| запрос | верхнее совпадение |
|---|---|
| `бот, а почему все называют друг друга фурри?` → RAG | **0.675**, вся выдача про самого бота |
| `почему все называют друг друга фурри?` → RAG | **0.821**, верхние две записи реально про фурри |
| `бот, что такое открытый проектор?` → KB | 0.706 |
| `что такое открытый проектор?` → KB | **0.719** |
| `бот, как правильно варить борщ?` → KB (промах) | 0.640 |
| `как правильно варить борщ?` → KB (промах) | **0.524** |

Эффект двусторонний и потому особенно вредный: попадания **занижаются**,
промахи **завышаются** (в базе есть факты про бота, и «бот» цепляется за них).
Зазор сигнал/шум: с триггером **0.066**, без него **0.195** — втрое.

Из-за этого RAG вернул ноль на вопросе, для которого у него было совпадение
0.821. Записей, упоминающих ту тему, в памяти 24 — контент есть, его не нашли.

Фикс маленький: считать эмбеддинг ретривала по тексту без совпавшего триггера и
без ведущего @упоминания, сохраняя оригинал для промпта. `should_respond` уже
знает, какой триггер сработал. Контроль: сообщение с триггером и без него дают
одинаковый топ ретривала, и тест падает на коде, где эмбеддинг берётся из
сырого текста.

### R1 — слепая сторона RAG: порог отсекает до лога

Порог RAG применяется **в SQL** (`memory.py`: `AND 1 - (embedding <=> $2) >= $3`),
поэтому подпороговые строки не доходят до `retrieval_log` вовсе. При нуле
результатов из данных нельзя узнать, промахнулись на волосок или на километр.

Это ломает §4.2 изнутри: там сказано, что порог «re-calibrated from
`retrieval_log` distributions» — а таких данных текущая реализация **не
производит**. Калибровать не на чем, и это выяснится в S6, когда всё уже
построено.

Лечится ровно так же, как C0 сделал для базы знаний (PR #45): доставать top-k
без фильтра, писать в лог **все** строки со схожестями и флагом `above_floor`,
инжектить только надпороговые. Разница в одну функцию, и после неё калибровка
S6 становится возможной.

### R2 — переснять линию, потому что нынешняя описывает загрязнённый мир

`docs/rag-eval-baseline.md` (blind rate 0.636) снят через
`scripts/harvest_auto_strata.py`, который берёт `tm.content` — **сырой текст
сообщения**, то есть вопросы golden set'а несут то же триггерное слово.

Линия честна как описание сегодняшнего поведения и **непригодна как неподвижная
линейка через R0**: после фикса распределение сдвинется в обе стороны, и дельта
S4/S5 будет мерить смесь «стало лучше от чанков» и «стало лучше от гигиены
запроса». Тот же урок уже записан для KB — 0.588 из эры двух фактов нельзя
переиспользовать как порог.

Плюс отдельная проблема, обнаруженная при проверке: **линия сейчас
невоспроизводима** — `internal/eval/cases_auto_harvest.json` на машине владельца
отсутствует (gitignored, остался в снесённом worktree). Регенерация скриптом
возможна, но это будет другой набор кейсов; зафиксировать процедуру пересборки
надо в любом случае.

### Порядок

```
R0  гигиена запроса (TD-092)      ← дёшево, улучшает и RAG, и KB уже сегодня
R1  порог RAG из SQL в сервис     ← без него калибровка S6 невозможна
R2  пересобрать golden set + переснять линию
    ↓
S4  chat_chunks + индексатор + бэкфилл   (как в роадмапе)
S5  гибридный ретривал, shadow → flip
S6  калибровка                            ← теперь есть на чём
```

R0 и R1 независимы друг от друга и от всего остального; каждый — отдельный PR
на несколько часов. R2 — прогон, а не разработка.

### Решение владельца 2026-08-18: сохраняем сообщения — значит индексируем всё

> «Если у чата включено сохранение сообщений, то в RAG должно попадать всё.»

Это подтверждает §4.1 и заодно снимает с него ореол крупной работы: **захват уже
работает, недостающее звено — индексация того, что и так лежит.** Новых данных
собирать не нужно, `chat_messages` полон.

Замер на проде 2026-08-18 (все чаты, `save_messages = true` и `rag_enabled = true`
у **всех**):

| чат | сообщений | в индексе RAG | покрытие |
|---|---|---|---|
| крупнейший живой чат | 32 758 | 2 328 | **7.1%** |
| второй по объёму | 2 506 | 162 | 6.5% |
| третий | 2 210 | 164 | 7.4% |
| остальные живые | 356–395 | 15–31 | 4.2–7.8% |
| тестовые | 110–546 | 41–211 | 37–39% |

Живые чаты индексируются на 4–8%, и это не случайная выборка: индексируется
ровно то, где **бот участвовал**. Тестовые чаты выше именно потому, что там
почти всё — разговоры с ботом. То есть 93% истории живого сообщества для RAG не
существует, а видимая часть смещена в сторону «люди спрашивают бота про бота».

**Масштаб замены, посчитанный, а не прикинутый:**

| | строк в индексе | покрытие | размер |
|---|---|---|---|
| сейчас (Q&A-пары) | 3 002 | ~7% | 26.2 МБ |
| после S4 (чанки) | **≈1 903** | **100%** | ~6 МБ |

35 459 сообщений людей, 2.28 млн символов, в среднем 65 символов на сообщение →
~1 900 чанков при целевых 1200. Индекс получается **меньше** нынешнего при
полном покрытии: Q&A-пара весит 233 символа, половина — собственный ответ бота,
а чанк упаковывает десятки коротких реплик. Эмбеддинги бесплатные, бэкфилл
разовый, точный скан по 2k строк мгновенный — возражения по стоимости и
масштабу отсутствуют.

**Следствия для дизайна, которые надо зафиксировать явно:**

- Гейт индексации — `save_messages` (есть ли что индексировать), гейт выдачи —
  `rag_enabled` (нужно ли искать). Сегодня оба включены везде, поэтому переход
  никому не ломает память; но чат, выключивший сохранение позже, **заморозит**
  свой индекс, и это должно быть заявленным поведением, а не сюрпризом.
- Отказ от Q&A-store (S5b «Q&A writes stop») перестаёт быть рискованным: терять
  нечего, потому что чанки покрывают надмножество. Проверить перед flip'ом
  ровно одно — что нет чата с `rag_enabled = true` и `save_messages = false`,
  для которого Q&A-пары были единственной памятью.

### Решение владельца 2026-08-18: Q&A-пары остаются для чатов без сохранения

> «С `rag_enabled = true` и `save_messages = false` можно оставить Q&A-пары.»

То есть Q&A-store не выводится из эксплуатации целиком (вопреки формулировке
S5b «Q&A writes stop»), а становится **резервным источником памяти для чатов,
которые не сохраняют сообщения**. Чанки покрывают тех, кто сохраняет; пары —
остальных. Сегодня таких чатов в проде ноль, но настройка существует.

**Следствие 1: писать по флагу, искать в обоих.** Условной должна быть
**запись**, а не поиск:

- `save_messages = true` → пишутся чанки, Q&A-пары не пишутся (иначе один и тот
  же обмен попадает в индекс дважды, и выдача начнёт возвращать почти-дубликаты);
- `save_messages = false` → пишутся Q&A-пары, чанков не появляется;
- **искать всегда в обоих**, потому что переключение флага в любую сторону
  оставляет два непересекающихся по времени куска памяти. Чат, включивший
  сохранение вчера, имеет чанки только со вчера, а всё, что было раньше,
  существует исключительно как Q&A-пары. Поиск только в «текущем» хранилище
  потерял бы ровно ту историю, ради которой память и заводили.

**Следствие 2: retention делает чанки долговременной памятью, и это надо решить
явно.** Проверено по коду:

- `chat_messages` — окно **365 дней**, чистится по-настоящему
  (`maintenance.chat_messages_days`);
- `chat_memory` — **намеренно освобождён** от чистки (ADR-0011: «memory rows
  must not be irrecoverably deleted without first persisting a high-level
  summary»).

Из этого следует три вещи, каждая — решение, а не деталь реализации:

1. **Чанковать надо непрерывно, а не только бэкфиллом.** Как только сообщение
   старше 365 дней удалено, восстановить его нечем. Индексатор должен
   опережать окно, иначе в памяти появится дыра, которую никто не заметит —
   у неё нет наблюдаемого следа.
2. **`chat_chunks` обязан быть вне retention**, как `chat_memory`. Иначе память
   молча стареет ровно на годовой границе — худший класс дефекта, потому что
   выглядит как «бот стал хуже помнить», а не как удаление.
3. **И тогда содержимое сообщений переживает собственное окно retention.**
   Формально это не новая политика: `chat_memory` делает ровно это уже сейчас, и
   ADR-0011 её благословил. Но сейчас так сохраняется ~7% переписки, а после S4
   будет 100% — та же политика на четырнадцатикратно большем объёме. Раз окно в
   365 дней кто-то выбирал осознанно, расширение стоит подтвердить осознанно, а
   не унаследовать от решения, принятого для гораздо меньшего среза.

Проверка перед flip'ом остаётся одна и теперь она дешёвая: убедиться, что ни у
одного чата с `rag_enabled = true` и `save_messages = false` Q&A-пары не
перестали писаться.

### Что это меняет в §4.3 (связь с KB)

База знаний с 2026-08-18 живёт по другому плану — [kb-companion-2026-08.md](kb-companion-2026-08.md),
где бот ведёт базу сам через разговор. Для RAG следствие одно и оно усиливает
§4.1: **`chat_memory` хранит не историю чата, а пары `"Q: <сообщение>\nA: <ответ
бота>"` и только за ходы, где бот отвечал** — то есть собственный диалоговый
журнал бота (2328 строк за семь месяцев в проде). Компаньон, который помнит
только собственные разговоры, — плохой компаньон, и это ровно та причина, по
которой замена на чанки по всей истории (S4) перестаёт быть оптимизацией
ретривала и становится продуктовым требованием.

---

## 6. Non-goals (v1)

- **Cross-encoder rerank in the reply path** — ~4s/query can't fit chat
  latency; policy: interactive never reranks, background jobs always may.
- **Digest/summary tier** — top-k structurally cannot answer «что было за
  месяц»; it's a separate feature whose prerequisite (chunks) v1 builds.
- **NL time parsing** — dates in headers + rendered dates cover most value;
  explicit temporal queries become SQL filters later.
- **KB PH2 autocollection** — stays blocked until S2 (ordering fix) and S6
  (calibrated floor) exist, so salience differentiation doesn't poison
  retrieval on arrival; adopt mem0-style write-time resolution when re-planned.
- **GraphRAG / temporal KGs / HyDE / query rewriting** — not before the eval
  harness could detect their value.
- **Cross-chat retrieval — never** (privacy invariant).
- **Anti-abuse blacklist threshold revision** — real (it bans everyday words)
  but orthogonal; tracked separately.

## 7. Risks

- **Backfill while live**: indexer paced in batches; embed failures resume via
  `WHERE embedding IS NULL`; the bot never reads chunks until S5b.
- **Prompt-quality regression at S5b**: shadow period first, framing +
  budget + instant rollback via env flag (restart, no redeploy).
- **Eval too small to gate** (<50 cases): trust only large deltas; grow from
  logged queries before fine-grained tuning.
- **Speaker names baked into chunk text** go stale until re-chunk — accepted;
  names are also carried in `senders[]` for filtering.
