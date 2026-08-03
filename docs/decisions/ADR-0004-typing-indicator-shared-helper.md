# ADR-0004: Shared `typing_indicator()` helper for the "bot is typing" pattern

**Status:** accepted (as-built)
**Date:** 2026-08-03
**Plan item:** C-1 (typing-indicator-2026-08-03), documenting I-6 (+ its consumers I-2/I-3/I-5)
**Author:** specialist-architect
**Relates to:** `docs/plans/typing-indicator-2026-08-03.md`, `src/utils/telegram.py::typing_indicator`

---

## Context

Before this plan, "bot is typing" was shown in exactly one handler (voice/video-note
transcription), via a one-shot `asyncio.create_task(bot.send_chat_action(...))` with no
keep-alive. Telegram expires a chat action client-side after ~5s; any operation longer than
that shows "typing" for a few seconds then goes silent until the reply arrives (I-1). Every
other slow operation in the bot — main text-reply pipeline, photo analysis, `/remember`
embedding, admin sticker merge — showed nothing at all, because there was no shared mechanism
and each handler would have had to remember to wire one up itself. Ten call sites needed it;
one had it, and that one was still buggy.

This ADR records the shared mechanism I-6 built, as it actually shipped (`798b2da`), and the
design alternatives considered and rejected — chiefly, whether to implement this as
`aiogram` middleware instead of an explicit per-call-site helper.

## Decision

A single async context manager, `typing_indicator()` in `src/utils/telegram.py`, wraps
`aiogram.utils.chat_action.ChatActionSender` and is called explicitly at each of the four
call sites that need it (I-2 main text path, I-3 photo analysis, I-5 `/remember` + admin
sticker merge, plus the pre-existing voice/video-note handler migrated onto it).

```python
@asynccontextmanager
async def typing_indicator(
    bot: Bot,
    chat_id: int,
    message_thread_id: int | None,
    action: str = "typing",
    *,
    enabled: bool = True,
) -> AsyncIterator[None]:
    ...
```

Four properties, each a deliberate choice:

1. **Built on `ChatActionSender`, not a hand-rolled loop.** `ChatActionSender` was already an
   `aiogram` dependency, unused anywhere in the codebase. It re-sends the action every ~4s
   (under Telegram's ~5s expiry) and guarantees cancellation on context exit — including on
   exception — for free. Writing a custom `asyncio.create_task` keep-alive loop (which is what
   the pre-existing voice handler did, minus the keep-alive) would duplicate library behavior
   for no benefit. This is a "use the framework" call, not a novel pattern.

2. **`message_thread_id` is a required positional parameter, not optional-with-default.**
   `bot.send_chat_action` does not inherit the forum topic the way `message.answer()` does;
   omitting it silently routes the indicator to the forum's General topic while the actual
   reply lands in the correct topic. Making it required (rather than `| None = None`) forces
   every call site to make an explicit choice — `TopicMiddleware` already injects
   `message_thread_id` into handler `data`, so callers have the value on hand; there is no
   excuse for a silent default to reintroduce this bug at a new call site later.

3. **`action` is a parameter, not a per-call-site constant baked into the helper.** Default is
   `"typing"`; call sites where the bot is committed to a non-text reply (sticker, image) can
   pass `choose_sticker` / `upload_photo`. As-built, every current call site uses the default —
   grep confirms there is no image- or sticker-generation reply path in this codebase yet
   (I-3's title mentioned `upload_photo` but that turned out not to apply to any real call
   site) — but the parameter exists so a future sticker/image-reply feature doesn't need to
   touch the helper's signature.

4. **`enabled: bool = True` is the single future per-chat-toggle seam.** Per the owner's Q4
   decision, a per-chat on/off switch (I-10) is out of scope for this plan and needs a
   `chat_settings` migration this plan does not include. Rather than hardcode `enabled=True`
   at every call site (which would mean touching five files again when the toggle ships),
   every call site either omits `enabled` (relies on the default) or passes a *business-logic*
   predicate (see below) through this one kwarg. When the migration lands, the kwarg's source
   becomes `chat_config.typing_indicator_enabled and <business predicate>` — one place to
   change, not five.

The `enabled` kwarg does double duty today: it is also how I-2 and I-3 encode their
owner-decided suppression rules (Q1, Q2) — `enabled=False` for `TriggerType.RANDOM` replies
(the relevancy gate can still veto the reply after the indicator would have shown, making
"typing" a lie), and `enabled=bool(caption)` for photos without a caption (no guaranteed
reply). This overload is intentional, not incidental: both the future per-chat toggle and
today's "does this operation guarantee a reply" gate are the same kind of question —
"should the indicator show for this specific invocation" — and belong behind the same seam
rather than two separate mechanisms.

### As-built call sites

| Call site | File | `enabled` |
|---|---|---|
| Voice / video-note transcription (I-6, migrated) | `media.py::handle_voice_message` | always on |
| Main text pipeline (I-2) | `message.py::handle_text_message` | `trigger_type != TriggerType.RANDOM` |
| Photo analysis + follow-on reply (I-3) | `media.py::handle_photo_message` | `bool(caption)` |
| `/remember` embedding (I-5) | `commands.py::handle_remember` | always on |
| Admin sticker description merge (I-5) | `admin_sticker.py::handle_admin_sticker_reply` | always on |

Three operations were deliberately **not** wired up, per owner decisions recorded in the
envelope's `human_feedback[]` (Q2, Q3): sticker-learning vision calls and uncaptioned-photo
analysis (I-4, no guaranteed reply), and `/summary` (I-7, keeps its existing text placeholder
because it can outlive 5s and be turned into an error message, a guarantee the indicator
doesn't offer). These are out of this ADR's scope beyond noting the pattern doesn't force
universal adoption — call sites opt in.

## Alternatives considered

### A: `aiogram` middleware that auto-wraps every handler invocation

Register a `BaseMiddleware` that opens a `ChatActionSender` around every incoming
`Message`/`CallbackQuery` dispatch, so no handler needs to remember to call anything —
symmetric with how `TopicMiddleware` already injects `message_thread_id` for free.

**Rejected.** The suppression rules (Q1, Q2) are the reason this doesn't work here, not just
a style preference:

- **The enable/disable predicate is computed *inside* the handler, after routing.** Whether
  I-2's reply is a `RANDOM` trigger is only known once `should_respond()` runs, which happens
  inside `handle_text_message` — a middleware runs *before* handler dispatch and would need
  either its own copy of the trigger-classification logic (duplicated business logic, drifts
  from the handler's copy) or a two-pass design (compute trigger type in middleware, stash in
  `data`, handler reads it back) that adds indirection with no corresponding gain. Same
  problem for I-3: "does this photo have a caption" is trivially available, but "will
  `pipeline.process` actually produce a reply" is not decided until deep inside the handler
  (blacklist/cooldown/abuse checks can still veto after the caption check passes).
- **Scope granularity would be wrong.** The current call sites wrap only the slow operation
  (the LLM/embedding call), not the whole handler — cheap steps like file download or DB
  lookups execute outside the indicator's `async with` block. A dispatch-level middleware can
  only wrap the entire handler invocation, which is coarser than what three of the four call
  sites actually want.
- **`action` type varies per call site** (today all `"typing"`, but the parameter exists for
  future `choose_sticker`/`upload_photo` uses) and is a decision the handler makes about its
  own reply, not something dispatch-level middleware can know generically without, again,
  either duplicating handler logic or a stash-in-`data` round trip.

A middleware remains the right tool for `message_thread_id` (pure extraction from the
incoming event, no handler-internal state needed, one shared value used by many consumers) —
that is exactly what `TopicMiddleware` already does, and `typing_indicator()`'s required
`message_thread_id` parameter is designed to consume its output. The two mechanisms are
complementary, not competing: middleware for "read something off the incoming event once",
explicit context manager for "wrap this specific slow operation depending on a decision the
handler itself makes."

### B: Continue the pre-existing `asyncio.create_task(bot.send_chat_action(...))` pattern, copied to each new call site

**Rejected.** This is the status quo that caused I-1 (indicator silently expiring on long
transcriptions) — a one-shot send with no keep-alive and no guaranteed cancellation on
exception. Copying a known-buggy pattern to four more call sites would have multiplied the
bug instead of fixing it.

### C: Per-handler direct `ChatActionSender` usage, no shared wrapper

Call `ChatActionSender(...)` directly in each handler instead of a project-owned wrapper
function.

**Rejected.** Would lose the two project-specific invariants that must hold at every call
site — mandatory `message_thread_id` (library default is `None`, which is exactly the silent
General-topic bug this plan set out to prevent) and the single `enabled` seam for the future
per-chat toggle. A thin wrapper is the only place to enforce both without relying on every
future call site's author reading this ADR.

## Consequences

### Positive

- One helper, one keep-alive interval constant (`_TYPING_INDICATOR_INTERVAL = 4.0`), one
  place future call sites copy from — the plan's stated goal ("the next new operation won't
  forget this either") is structural, not aspirational: the required `message_thread_id`
  parameter makes the forum-topic bug a type error waiting to happen if a future call site
  tries to skip it, not just a documented convention.
- `enabled` gives I-10 (per-chat toggle, deferred) a single integration point instead of five
  call sites to revisit.
- No new attack surface or dependency — `ChatActionSender` was already vendored via `aiogram`.

### Negative / trade-offs

- The `enabled` kwarg is overloaded (future per-chat toggle *and* today's per-invocation
  business predicate). This is a deliberate reuse of one seam rather than two, but it means a
  future reader implementing I-10 needs to compose the two conditions (`chat_config.enabled
  and business_predicate`) rather than finding a single obvious toggle — flagged here so that
  composition isn't missed.
- Coverage is call-site-by-call-site (opt-in), not automatic. A new slow operation added later
  without reading this pattern will silently get no indicator, same failure mode as before
  this plan — the fix here is discoverability (this ADR + the helper's own docstring), not a
  structural guarantee. This is the direct cost of rejecting the middleware alternative (A):
  middleware would have made non-adoption impossible but couldn't honor the suppression rules.
  A "lint check that new slow-await handlers call `typing_indicator`" would close this gap but
  is out of this plan's scope.
- `enabled=False` still opens the context manager (thin `if not enabled: yield; return` guard
  inside `typing_indicator`, not a call-site-level `if`), so every call site's code reads
  uniformly regardless of whether the indicator actually shows — a minor readability win traded
  for the guard living in one place instead of at each `async with` call site.

## Open items tracked elsewhere (not this ADR's scope)

- **I-10** (per-chat toggle): needs a `chat_settings` migration + `ChatConfig` field, reads
  through the `enabled` seam described above. Blocked pending owner decision to actually build
  it; the seam exists now so it's a small change when it does.
- **qa forum regression sweep**: I-6's `last_update` note flags a broader "does every
  `typing_indicator` call site actually forward `message_thread_id` correctly in a forum
  supergroup" regression check, to run now that I-2/I-3/I-5 have all landed.

---

*Document generated as part of C-1 (typing-indicator-2026-08-03 plan).*
*Architect: specialist-architect (universal baseline).*
