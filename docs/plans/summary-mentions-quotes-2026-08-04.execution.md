---
schema_version: 3
plan_id: summary-mentions-quotes-2026-08-04
source_artifact:
  path: docs/plans/summary-mentions-quotes-2026-08-04.md
  sha256: 726eb8b0ce4b0c715e6ba693f195a72a72c318e6fb37d066774e3ddedc88113b
  type: session-analysis
created_at: '2026-08-04T00:26:12Z'
approved_at: '2026-08-04T07:39:12Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: done
  started_at: '2026-08-04T07:40:15Z'
  completed_at: '2026-08-04T09:05:10Z'
  current_batch: null
  task_list_id: summary-mentions-quotes-2026-08-04
items:
- id: M-1
  title: 'A(mentions): кликабельные упоминания в /summary — индексный плейсхолдер, резолв в safe anchor после markdown_to_html'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 4h
  confidence: 0.9
  consult_session_id: 45d74f2f-e882-41e8-bdc2-882cae6dc190
  specialist_session_id: 43ee72f4-9367-498f-8b17-ae487a8e18c0
  retry_count: 0
  last_update:
    ts: '2026-08-04T07:47:33Z'
    executor: backend-dev
    note: 'Implemented opaque @@uN@@ placeholder-token mention resolution in SummaryService.generate(): model only ever sees tokens (never real names/ids); tokens resolved into escaped tg://user inline mentions AFTER markdown_to_html() (anchor would otherwise be HTML-escaped away per formatter ADR). first_name is html.escape()''d before insertion (attacker-controlled). Per owner decision: all participants mentioned, no toggle. Bot messages and user_id=NULL (anonymous admin/channel posts) never get a token. Hallucinated/unknown token indices degrade to a generic localized label (ru ''участник'' / en ''participant''), never leaking the placeholder or emitting partial markup. Added 8 new unit tests covering: token-not-real-name in prompt, valid resolution, HTML-injection-in-first_name escaping, bot exclusion, unknown-token fallback (ru+en), anonymous-message fallback, and token reuse for repeat authors. Fixed pre-existing test fixture (added user_id) so existing suite kept passing. Full tests/unit/ (1186 tests), ruff, and mypy all green. NOTE for qa (M-2, already planned): security test for name-attack via first_name is covered at unit level by test_first_name_html_injection_is_escaped_in_mention, but M-2 may want a broader adversarial pass (RTL override chars, tg:// scheme tricks, extremely long names).'
  result:
    kind: commit
    ref: c1f977a
    verification: 'pytest tests/unit/test_summary.py -v: 12 passed; pytest tests/unit/ -q: 1186 passed; ruff check src/ tests/unit/: All checks passed; mypy src/: Success no issues in 121 files'
- id: M-2
  title: 'A(mentions)/qa: security-тест на имя-атаку через first_name (HTML-инъекция в anchor)'
  specialist: qa
  priority: P1
  status: done
  depends_on:
  - M-1
  estimated_effort: 1.5h
  confidence: 0.9
  consult_session_id: d71fa183-d5cc-4995-bc6c-3d28ea6bae02
  specialist_session_id: d1299bc1-7076-4277-b15c-dd0c82c0fcea
  retry_count: 0
  last_update:
    ts: '2026-08-04T07:53:54Z'
    executor: qa
    note: 'Extended tests/unit/test_summary.py (TestSummaryMentionsAdversarial, 7 new tests) with the broader adversarial pass M-1 flagged as needed: (1) forged tg://user?id=<victim> href via crafted first_name -- the actual XSS/impersonation-relevant case, sanity-checked by hand-rolling an unescaped variant of _resolve_mentions() and confirming it DOES leak a second forged anchor with the victim id, so this test is a real regression guard, not vacuous; (2) quote-breakout attempt; (3) javascript: scheme lookalike (href always built from trusted user_id, unaffected); (4) RTL/bidi override chars -- structural (anchor count) guard only, noted as inherent Telegram-wide visual-spoofing class out of scope; (5) pathologically long (~40KB) hostile name -- no crash, stays escaped; (6) name equal to literal @@u1@@ token text -- confirms re.sub single-pass (non-recursive) substitution can''t be abused to forge a second anchor from an inserted name; (7) embedded NUL/control bytes -- no crash. All 7 pass against current M-1 code unmodified. Verification: pytest tests/unit/test_summary.py -v = 19 passed (12 pre-existing + 7 new); pytest tests/unit/ -q = 1193 passed (matches 1186 baseline + 7); ruff check = clean; mypy src/ = clean (test-only change, no src/ touched). Committed 507b068 (pre-commit hooks incl. gitleaks passed). No source-code changes needed -- M-1''s escape-before-insert design already closes every shape tried.'
  result:
    kind: commit
    ref: 507b068
    verification: pytest tests/unit/test_summary.py -v (19 passed) + pytest tests/unit/ -q (1193 passed) + ruff check + mypy src/, all green; forged-href test additionally validated against a deliberately unescaped stand-in implementation to confirm it is not a vacuous assertion
- id: M-3
  title: 'A(mentions)/architect: ADR-0005 — паттерн резолва упоминаний и почему порядок обратен STICKER-маркеру'
  specialist: architect
  priority: P2
  status: done
  depends_on: []
  estimated_effort: 1h
  confidence: 0.9
  consult_session_id: 806f8277-4c83-4070-959e-8614f5e63ae1
  specialist_session_id: d95d146f-d922-4d18-af16-9910b7b8da85
  retry_count: 0
  last_update:
    ts: '2026-08-04T08:19:01Z'
    executor: architect
    note: 'Wrote docs/decisions/ADR-0005-mention-resolution-marker-ordering.md. Verified both marker mechanisms by reading actual code (summary.py, formatter.py, responder.py, pipeline.py, message.py): STICKER extracts the model-echoed real file_id BEFORE markdown_to_html() because the resolved value is a non-text Bot API parameter (answer_sticker) that never enters HTML text -- order is pipeline hygiene only. Mentions resolve @@uN@@ tokens AFTER markdown_to_html() because the resolved value IS the HTML anchor markup itself, and markdown_to_html''s escape-first step is the pipeline''s only HTML-escaping pass -- resolving before would escape the anchor into inert visible text, and resolving after makes _resolve_mentions the last-chance escaper (html.escape() on DB-sourced first_name, never model output). Documented a generalized rule for future marker patterns plus a scope note distinguishing this output-side concern from Q-1/Q-2''s input-side sanitize_prompt_content. No application code touched (docs/ only).'
  result:
    kind: file
    ref: docs/decisions/ADR-0005-mention-resolution-marker-ordering.md
    verification: ADR cites verified file:line evidence for every claim (summary.py:21-52,111,162-163; formatter.py:27-28; responder.py:88-96,98-115; pipeline.py:236-241,246; message.py:271-278) confirmed by direct Read of each file before writing; git status confirms only docs/** and envelope-managed files changed, no src/ touched
- id: E-1
  title: 'B(emoji): инструкция в системном промпте /summary — просить уместные emoji на усмотрение модели (свободная расстановка, без фиксированного скелета, ru+en)'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - M-1
  estimated_effort: 1h
  confidence: 0.9
  consult_session_id: 45d74f2f-e882-41e8-bdc2-882cae6dc190
  specialist_session_id: b7609370-dc1b-4acf-a8c3-94a1f0f2cb13
  retry_count: 0
  last_update:
    ts: '2026-08-04T08:22:48Z'
    executor: backend-dev
    note: 'Added a system-prompt instruction (ru+en) in SummaryService.generate() telling the model to place relevant emoji freely for visual structure, per owner decision [E-1]=B (free placement, model''s own discretion, no fixed skeleton/set). Scope kept to the prompt-instruction change only, as titled -- did not touch formatter.py or add a fixed emoji vocabulary. 3 new unit tests (TestSummaryEmojiInstruction) pin: instruction present in both language branches, says ''free''/''no fixed'' rather than dictating a skeleton, and the instruction text doesn''t leak into the conversation payload. Full unit suite, ruff, mypy all green. Routing hint for qa (E-2, already planned next): my tests only assert the system_prompt text -- they cannot and do not assert anything about actual model output or the emoji x markdown_to_html() interaction (''- fire topic'' -> bullet topic, heading conversion) noted in the source plan; that plus a live cheap-model quality run is E-2''s full scope as titled.'
  result:
    kind: commit
    ref: 869b9c5
    verification: 'pytest tests/unit/test_summary.py -v: 22 passed; pytest tests/unit/ -q: 1224 passed; ruff check src/ tests/unit/: All checks passed; mypy src/: Success no issues in 121 files'
- id: E-2
  title: 'B(emoji)/qa: unit-тест взаимодействия emoji×markdown_to_html (двойной маркер •, конверсия заголовков) + живой прогон качества разметки на дешёвых моделях'
  specialist: qa
  priority: P2
  status: done
  depends_on:
  - E-1
  estimated_effort: 1.5h
  confidence: 0.85
  consult_session_id: d71fa183-d5cc-4995-bc6c-3d28ea6bae02
  specialist_session_id: 2fc896f4-f170-4ff4-bde9-a2819888d4de
  retry_count: 0
  last_update:
    ts: '2026-08-04T08:35:12Z'
    executor: qa
    note: 'Wrote tests/unit/test_formatter.py::TestEmojiMarkdownInteraction (13 tests) pinning the exact emoji x markdown_to_html interaction the source plan flagged: dash-list items with leading/trailing emoji get exactly one bullet prefix, headings with emoji convert cleanly, emoji survive HTML-escaping untouched (incl. multi-codepoint ZWJ sequences), italic/bold boundary regexes unaffected by adjacent emoji, and a realistic multi-block AI-shaped summary round-trips with zero raw markdown leaking. Also ran the mandated live quality check: called the REAL SummaryService.generate() pipeline against REAL gemini-3-flash-preview and gpt-5-nano (the two cheap-tier defaults in config/default.yml) with a synthetic multi-topic ru+en conversation, via a throwaway script (deleted after use, not committed) swapping a real GeminiProvider/OpenAIProvider behind a thin router stub while keeping SummaryService''s actual code path untouched. Both models produced clean, readable emoji-structured output; no double-bullet or broken-heading defects (the risk named in the source plan) were observed. The live run surfaced two real mention-token x formatter interactions invisible to a fully-mocked suite, captured as new regression tests in test_summary.py: (1) gpt-5-nano sometimes echoes a malformed single-@ ''@u0'' instead of ''@@u0@@'', leaking raw into the delivered summary, uncaught by the existing hallucinated-token fallback -- tracked via strict xfail (test_single_at_token_variant_from_live_gpt5_nano_does_not_leak); NOT fixed here per role boundary (QA does not write src/), backend-dev follow-up recommended. (2) gemini-3-flash-preview sometimes wraps its token echo in backticks, and markdown_to_html''s inline-code conversion runs before mention resolution, producing <a> nested inside <code> -- pinned as current behavior (test_backtick_wrapped_token_resolves_inside_code_tag, not xfail since Telegram HTML-parser acceptance of that nesting is unverified here). Verification: pytest tests/unit/ -q --no-cov = 1238 passed, 1 xfailed (1224 baseline + 14 new); ruff check src/ tests/unit/ = clean; mypy src/ = clean (test-only change). Committed 2dc67a7 (pre-commit hooks incl. gitleaks passed; only synthetic user_ids in fixtures).'
  result:
    kind: commit
    ref: 2dc67a7
    verification: 'pytest tests/unit/ -q --no-cov: 1238 passed, 1 xfailed (1224 baseline + 14 new); ruff check src/ tests/unit/: clean; mypy src/: clean; live run against real gemini-3-flash-preview + gpt-5-nano via a throwaway (deleted, uncommitted) script exercising the real SummaryService.generate() pipeline'
- id: Q-1
  title: 'C(quote): проброс message.quote через handlers → pipeline → prompt_builder + извлечение общего reply-хелпера'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 4h
  confidence: 0.9
  consult_session_id: 45d74f2f-e882-41e8-bdc2-882cae6dc190
  specialist_session_id: e048d21c-ef00-4ee6-adf9-a531e78d0c3b
  retry_count: 0
  last_update:
    ts: '2026-08-04T08:03:54Z'
    executor: backend-dev
    note: 'Wired aiogram Message.quote through handlers (message.py handle_text_message + media.py handle_photo_message) -> TextProcessingPipeline.process() -> PromptContext -> _reply_section(). Owner decision [Q-1] implemented: model gets both the highlighted fragment AND the full original message, clearly labeled. Gated on quote.is_manual (bool|None normalized to bool) so a server-attached quote falls back to the pre-existing plain-reply framing -- only a user''s own highlight triggers the fragment framing. Quote text has its own truncation budget (REPLY_QUOTE_MAX_CHARS=300 in prompt_builder.py), separate from the existing 500-char full-message cap, and is routed through sanitize_prompt_content() same as the rest of reply context (double-fence). Extracted the reply-context block (previously duplicated identically in message.py and media.py) into a shared extract_reply_context()/ReplyContext dataclass in message.py, imported by media.py -- this is the general reply-helper half of the item title. 20 new unit tests covering extract_reply_context, prompt_builder reply-quote rendering, pipeline wiring, and media.py caller wiring. Full suite: pytest tests/unit/ = 1213 passed (1193 baseline + 20). ruff and mypy clean. Routing hint for qa (Q-2, already planned next): unit coverage above is thorough for extraction/rendering logic on plain dicts/mocks; Q-2 already covers quote-injection + expanding test_reply_context, no gap beyond what is already scoped there.'
  result:
    kind: commit
    ref: f558f51
    verification: 'pytest tests/unit/ -q: 1213 passed; ruff check src/ tests/unit/: All checks passed; mypy src/: Success, no issues in 121 files'
- id: Q-2
  title: 'C(quote)/qa: тест на quote-инъекцию (sanitize) + расширение test_reply_context под reply_quote'
  specialist: qa
  priority: P1
  status: done
  depends_on:
  - Q-1
  estimated_effort: 1.5h
  confidence: 0.92
  consult_session_id: d71fa183-d5cc-4995-bc6c-3d28ea6bae02
  specialist_session_id: 19d2f440-1f3b-4e65-9b12-8545715d6254
  retry_count: 0
  last_update:
    ts: '2026-08-04T08:13:16Z'
    executor: qa
    note: 'Q-1 already shipped happy-path plus one basic sanitize smoke test for reply_quote. Gap for Q-2: broader adversarial pass mirroring M-2 precedent. Added TestReplyQuoteAdversarial (5 tests) in test_prompt_builder.py covering all 5 known prompt delimiter tags via the quote path (not just chat_history), case-insensitive tags, self-closing tags, a combined realistic breakout payload, and confirming a hostile payload in a non-manual quote is dropped entirely. Non-vacuousness: assertions check for presence of the full-width bracket substitute, not just absence of raw tag. Added one pipeline.process end-to-end injection test in test_text_pipeline.py closing the gap between unit-level guarantees and full production wiring. Extended TestExtractReplyContext in test_message_handler.py with raw-passthrough and orphan-quote defensive tests. No source changes needed -- Q-1 sanitize-before-insert design already closes every shape tried. Verification: pytest tests/unit/test_prompt_builder.py tests/unit/test_text_pipeline.py tests/unit/test_message_handler.py -q = 116 passed; pytest tests/unit/ -q = 1221 passed (1213 baseline + 8 new); ruff check src/ tests/unit/ = All checks passed; mypy src/ = Success no issues in 121 files; pre-commit hooks passed on commit 5c430fc.'
  result:
    kind: commit
    ref: 5c430fc
    verification: 'pytest tests/unit/ -q: 1221 passed; ruff check: clean; mypy: clean; pre-commit hooks (ruff, ruff-format, gitleaks) passed'
- id: Q-3
  title: 'C(quote-persist): миграция 021 (quote_text TEXT, quote_is_manual BOOLEAN, nullable, без DEFAULT) + сохранение цитаты в message_saver.py'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - Q-1
  estimated_effort: 3h
  confidence: 0.92
  consult_session_id: 45d74f2f-e882-41e8-bdc2-882cae6dc190
  specialist_session_id: 14c582dc-57b5-49af-97ef-b322be048b48
  retry_count: 0
  last_update:
    ts: '2026-08-04T08:42:43Z'
    executor: backend-dev
    note: 'Added migration 021 (chat_messages.quote_text TEXT, quote_is_manual BOOLEAN, both nullable/no DEFAULT -- NULL means no quote, per CLAUDE.md per-chat-columns rationale generalized to this fact table) and wired message_saver.py to persist message.quote (aiogram TextQuote) via MessageRepository.save(), same field Q-1 already reads on the live prompt path. is_manual normalized bool(None)->False so ''quote exists but not manual'' stays distinct from ''no quote'' (both matter for Q-5''s consumer gate). Quote text stored untruncated -- the 300-char cap is Q-1''s prompt-render concern, not a storage one. 7 new unit tests (3 repo-level on save()''s new params, 4 middleware-level on quote extraction incl. non-manual normalization and no-truncation-at-persist). Verified migration online (not just offline/simple-query): ran tests/integration/test_alembic_online_upgrade.py (the guard for the exact multi-statement-per-execute class of bug) against a real fresh Postgres -- 4/4 passed, including test_no_migration_bundles_multiple_commands_in_one_execute and idempotency. Full unit suite 1245 passed + 1 xfail (1238+1xfail baseline + 7 new); ruff and mypy clean. Routing hint for Q-4 (already planned, depends_on Q-3): round-trip integration test for message_saver persisting quote_text/quote_is_manual against real Postgres, plus explicit confirmation the online-upgrade guard covers 021 (it does, by glob, verified above) is qa''s to add -- no source gap found.'
  result:
    kind: commit
    ref: 5de14b1
    verification: 'pytest tests/unit/test_message_saver.py tests/unit/test_message_repository.py -v: 14 passed; pytest tests/unit/ -q --no-cov: 1245 passed, 1 xfailed; pytest tests/integration/test_alembic_online_upgrade.py -v: 4 passed; ruff check src/ tests/unit/test_message_saver.py tests/unit/test_message_repository.py alembic/versions/021_chat_messages_quote.py: All checks passed; mypy src/: Success no issues in 121 files'
- id: Q-4
  title: 'C(quote-persist)/qa: интеграционный тест round-trip (message_saver сохраняет quote_text/quote_is_manual) + подтвердить, что online-upgrade guard покрывает миграцию 021'
  specialist: qa
  priority: P2
  status: done
  depends_on:
  - Q-3
  estimated_effort: 1.5h
  confidence: 0.93
  consult_session_id: d71fa183-d5cc-4995-bc6c-3d28ea6bae02
  specialist_session_id: 85befc5d-bea6-4e79-8c9b-f7a2d1f7f1c2
  retry_count: 0
  last_update:
    ts: '2026-08-04T08:49:42Z'
    executor: qa
    note: 'Added tests/integration/test_migration_021_quote_persistence.py: (1) schema shape (quote_text TEXT / quote_is_manual BOOLEAN, both nullable, no DEFAULT); (2) round-trip through the real message_saver->MessageRepository->Postgres path for manual quote, server-attached (is_manual=None normalized to false), no-quote (both NULL), and untruncated long text -- closes the exact gap Q-3 flagged (unit tests mock the repo/pool; this hits a real DB); (3) new regression guard: MessageRepository.save()''s ON CONFLICT DO UPDATE clause omits quote_text/quote_is_manual from its SET list (same as user_id/username), so a later re-save on the same (chat_id, message_id) -- e.g. media.py::_update_message_content patching in a vision description -- must not silently wipe a previously-persisted quote; verified both directions; (4) confirmed the online-upgrade guard''s filesystem glob (alembic/versions/[0-9]*.py) actually discovers 021 and applies cleanly online against a fresh throwaway DB, independent of test_alembic_online_upgrade.py''s own internals. No source changes needed -- Q-3''s persistence design already correct; this only adds coverage.'
  result:
    kind: commit
    ref: a5bac99
    verification: 'pytest tests/integration/test_migration_021_quote_persistence.py tests/integration/test_alembic_online_upgrade.py -v --no-cov: 14 passed; pytest tests/unit/ -q --no-cov: 1245 passed, 1 xfailed (unchanged baseline); ruff check src/ tests/: All checks passed; mypy src/: Success no issues in 121 files; pre-commit hooks (ruff, ruff-format, gitleaks) passed on commit a5bac99'
- id: Q-5
  title: 'C(quote-consume): учёт сохранённой ручной цитаты в историческом контексте реплаев — quote_text/quote_is_manual в get_recent_with_topic_context (3 SELECT-а, UNION ALL по позиции) + аннотация в prompt_builder._format_message через sanitize_prompt_content с отдельным лимитом ~150-200 симв.'
  specialist: backend-dev
  priority: P2
  status: done
  depends_on:
  - Q-1
  - Q-3
  estimated_effort: 2.5h
  confidence: 0.92
  consult_session_id: 45d74f2f-e882-41e8-bdc2-882cae6dc190
  specialist_session_id: f7d74c5f-6f91-47a4-a5b9-351ab87167e3
  retry_count: 0
  last_update:
    ts: '2026-08-04T08:56:09Z'
    executor: backend-dev
    note: 'Implemented: get_recent_with_topic_context() now projects quote_text/quote_is_manual in all 3 SELECTs (non-forum + both forum-mode UNION ALL branches, migration 021 columns). prompt_builder._format_message() annotates a history row with its saved manually-highlighted quote, gated on quote_is_manual is True (mirrors _reply_section live-path gate), sanitized via sanitize_prompt_content() and truncated to a new HISTORY_QUOTE_MAX_CHARS=200 budget separate from REPLY_QUOTE_MAX_CHARS=300. 10 new unit tests covering repository projection and formatter annotation/gating/truncation/sanitization/back-compat. Routing hint for qa (Q-6, already planned): integration test against real Postgres (UNION ALL type-check, historical-path injection/gating/truncation/NULL back-compat) is qa-owned -- my tests mock the pool.'
  result:
    kind: commit
    ref: e3de0ca
    verification: 'pytest tests/unit/test_message_repository.py tests/unit/test_prompt_builder.py -q --no-cov: 77 passed; pytest tests/unit/ -q --no-cov: 1255 passed, 1 xfailed (1245+1xfail baseline + 10 new); ruff check src/ tests/unit/: All checks passed; mypy src/: Success, no issues in 121 files'
- id: Q-6
  title: 'C(quote-consume)/qa: интеграционный тест на Postgres — get_recent_with_topic_context возвращает quote-колонки и UNION ALL проходит type-check + регресс на quote-инъекцию в историческом quote_text + гейтинг quote_is_manual + усечение + обратная совместимость NULL'
  specialist: qa
  priority: P2
  status: done
  depends_on:
  - Q-5
  estimated_effort: 2h
  confidence: 0.93
  consult_session_id: d71fa183-d5cc-4995-bc6c-3d28ea6bae02
  specialist_session_id: 221c9238-13bb-4bbe-b0be-0eeeb2d1ae01
  retry_count: 0
  last_update:
    ts: '2026-08-04T09:04:04Z'
    executor: qa
    note: 'Added tests/integration/test_quote_history_context.py (10 tests, real testcontainers Postgres) closing all four gaps named in the item title: (1) get_recent_with_topic_context''s UNION ALL forum-mode query actually executes against a real schema and projects quote_text/quote_is_manual correctly in both branches -- unit tests only asserted query-string content, never sent it to Postgres; (2) a hostile quote_text saved via repo.save() and fetched back stays neutralized when rendered through the real pipeline.py conversion (dict(record) -> build_user_prompt) -- sanity-checked non-vacuous against a hand-rolled unsanitized _format_message variant (confirmed it would leak a second closing chat_history tag) before trusting it, same precedent as M-2/Q-2; (3) quote_is_manual gating verified against a real persisted row (manual annotated, server-attached/false not annotated); (4) HISTORY_QUOTE_MAX_CHARS truncation verified against a real persisted row (storage stays untruncated, render truncates); (5) NULL back-compat verified three ways: repo.save() without quote kwargs, a row inserted the way genuine pre-migration-021 data looks (INSERT bypassing repo.save() and the two columns entirely), and forum-mode UNION ALL with NULL quote fields on both branches. No source changes needed -- Q-5''s design was already correct; this only adds the qa-owned integration coverage it flagged. Committed 29244b1 (pre-commit hooks incl. gitleaks passed; only synthetic chat/user ids in fixtures).'
  result:
    kind: commit
    ref: 29244b1
    verification: 'pytest tests/integration/test_quote_history_context.py tests/integration/test_migration_021_quote_persistence.py tests/integration/test_alembic_online_upgrade.py -v --no-cov: 24 passed; pytest tests/unit/ -q --no-cov: 1255 passed, 1 xfailed (unchanged baseline); ruff check: clean; mypy src/: clean; non-vacuousness of the injection-regression test confirmed against a hand-rolled unsanitized variant before trusting it'
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 30.0
  consumed_usd: 25.4895
review_gate:
  why: []
  approve_action: /execute-plan /Users/julia/my-projects/telegram-chat-companion.summary-mentions-quotes-2026-08-04-wt/docs/plans/summary-mentions-quotes-2026-08-04.execution.md --resume
  reject_action: /plan-fixes docs/plans/summary-mentions-quotes-2026-08-04.md --revise /Users/julia/my-projects/telegram-chat-companion.summary-mentions-quotes-2026-08-04-wt/docs/plans/summary-mentions-quotes-2026-08-04.execution.md
safe_to_replay_from: null
clarifying_questions: []
human_feedback:
- ts: '2026-08-04T07:12:31Z'
  by: julia
  text: 'ANSWER [M-1]: (1) всех участников сообщения, без переключателя'
  applies_to: M-1
  status: addressed
  addressed_at: '2026-08-04T07:19:53Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-04T07:12:51Z'
  by: julia
  text: 'ANSWER [E-1]: B — свободно, на усмотрение модели'
  applies_to: E-1
  status: addressed
  addressed_at: '2026-08-04T07:20:23Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-04T07:13:09Z'
  by: julia
  text: 'ANSWER [Q-1]: отдавать и фрагмент, и полное сообщение с пометкой.'
  applies_to: Q-1
  status: addressed
  addressed_at: '2026-08-04T07:21:35Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-04T07:13:34Z'
  by: julia
  text: 'ANSWER [Q-PERSIST]: давай сделаем сразу'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-04T07:22:21Z'
  addressed_by: pm-orchestrator
- ts: '2026-08-04T07:28:14Z'
  by: julia
  text: 'ANSWER [Q-CONSUME]: (б) добавить ещё и использование сохранённых цитат в истории'
  applies_to: null
  status: addressed
  addressed_at: '2026-08-04T07:37:01Z'
  addressed_by: pm-orchestrator
revision_number: 3
last_revised_at: '2026-08-04T07:37:04Z'
last_revised_by: pm-orchestrator
---


















































































<!-- BRIEF:START -->
# Саммари: кликабельные имена, emoji-разметка и учёт выделенной цитаты

## Что произошло

Разобран список из трёх пожеланий владельца к боту: два про команду саммари и одно — про
то, как бот отвечает на реплай с выделенным фрагментом. Пожелания между собой не связаны и
могут выполняться независимо. По итогам разбора третий пункт расширили: выделенные цитаты
будут не только сохраняться в историю переписки, но и учитываться ботом в последующих ответах —
по вашему решению. Крупной перестройки бота работа не требует; изменения в базе данных
небольшие и безопасные.

## Найденные проблемы

1. **В саммари участники подписаны техническим ником, а не именем из чата.** Сейчас в списке
   стоит `@ник`, по которому к тому же нельзя перейти к человеку. Просили обратное — показывать
   видимое имя и делать его кликабельным. Здесь два подводных камня: (а) у кликабельного имени
   есть побочный эффект — упомянутый человек получает уведомление, и большое саммари может
   разослать пинги пол-чату; (б) имя может содержать «ловушку» из спецсимволов, ломающую
   оформление сообщения, — это ещё и брешь в безопасности, которую надо закрыть.
2. **Саммари почти без визуального оформления** — один значок в заголовке, дальше сплошной
   текст, который тяжело читать. Просили использовать emoji как разметку по смысловым блокам.
3. **Бот не различает ответ на выделенный кусок и ответ на всё сообщение.** Когда человек
   отвечает на конкретный фрагмент, бот всё равно берёт сообщение целиком и может ответить не
   на то, что имелось в виду. Здесь тоже есть риск безопасности: в выделенный текст можно
   спрятать постороннюю команду боту — её надо обезвредить.

## Что будет сделано

- В саммари участники станут отображаться видимым именем, по которому можно перейти к человеку;
  упоминаются все участники сообщения, без отдельного переключателя (по вашему решению).
  «Ловушки» в именах обезврежены, порядок безопасной вставки зафиксирован в проектной заметке,
  чтобы позже случайно не сломать (M-1, M-2, M-3).
- Саммари получит emoji-разметку: бот сам расставляет уместные emoji по смыслу (по вашему
  решению — свободно, без жёсткого шаблона блоков), одинаково на русском и английском.
  Качество проверяется живым прогоном на обычных (дешёвых) моделях (E-1, E-2).
- Бот научится учитывать именно выделенный фрагмент, не теряя общий смысл сообщения; попытки
  спрятать команду в цитате обезврежены (Q-1, Q-2).
- Выделенные цитаты начнут сохраняться в историю переписки, чтобы не терялось, на какой
  именно фрагмент отвечали (Q-3, Q-4).
- Бот будет учитывать сохранённые цитаты и в следующих ответах: видя, что человек когда-то
  выделил конкретный фрагмент, он точнее понимает контекст прошлых сообщений (по вашему
  решению). Попытки спрятать команду в такой сохранённой цитате тоже обезврежены (Q-5, Q-6).

## Не входит в этот план

- Вариант с отдельным переключателем упоминаний в настройках чата — отдельная работа с
  изменением базы данных; по умолчанию сейчас не закладывается.
- Точное повторное форматирование внутри цитаты — сохраняем только сам текст выделения и признак
  «выделено вручную»; при необходимости остальное легко добавить позже.
- Учёт выделенных цитат в самой команде саммари — оставлен за рамками: запрос был про обычные
  ответы бота, а не про саммари; при желании легко вынести отдельным пунктом.

## Оценка

Суммарно около 24 часов работы; потолок бюджета — $30 на план (до $6 на отдельный пункт).
<!-- BRIEF:END -->

# Plan — summary-mentions-quotes-2026-08-04

## Source

[`docs/plans/summary-mentions-quotes-2026-08-04.md`](docs/plans/summary-mentions-quotes-2026-08-04.md) (sha256 `726eb8b0ce4b...`).

## Items

(none yet — populated by /plan-fixes)
