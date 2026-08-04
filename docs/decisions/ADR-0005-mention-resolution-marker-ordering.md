# ADR-0005: Mention-resolution marker pattern, and why its ordering is the reverse of the STICKER marker

**Status:** accepted
**Date:** 2026-08-04
**Plan item:** ADR-0005 (summary-mentions-quotes-2026-08-04) — records M-1/M-2, prevents regression
**Author:** specialist-architect
**Relates to:** `docs/plans/summary-mentions-quotes-2026-08-04.md` §A; CLAUDE.md "ADR: Formatter
security — escape HTML first"; `src/services/modules/summary.py::_resolve_mentions`;
`src/services/modules/sticker/responder.py::extract_sticker_from_response`;
`src/services/text/pipeline.py` Stage 5

---

## Context

M-1 introduced a second "opaque marker embedded in raw AI output, resolved by code afterward"
pattern in this codebase. The first one — `STICKER:<file_id>` (`responder.py`) — already existed
and ships with the opposite pipeline order relative to `markdown_to_html()`:

| | Marker | Extracted/resolved | relative to `markdown_to_html()` |
|---|---|---|---|
| **STICKER** (existing) | `STICKER:<file_id>` | **before** formatting (`pipeline.py:236-241`) | text run through `markdown_to_html()` afterward is the *cleaned* text, marker already gone |
| **mentions** (M-1, new) | `@@uN@@` | **after** formatting (`summary.py:162-163`) | `markdown_to_html()` runs on text that *still contains* the raw tokens |

Two structurally similar mechanisms with opposite order is exactly the kind of thing a future
maintainer "fixes" into consistency and breaks — this ADR is the durable record of why the
order is *not* arbitrary and must not be unified.

---

## Decision

**The ordering is determined by one question: does the resolved value become part of the
HTML text sent with `parse_mode="HTML"`, or is it consumed as a structured, non-text API
parameter that Telegram interprets on its own?**

### STICKER: resolved value is a non-text API parameter → order before formatting is fine

The model is given the **real** `file_id` directly in the prompt (`format_candidates_for_prompt()`,
`responder.py:88-96`, `f"STICKER:{c.file_id}"`) — there is no name/id translation step, no DB
lookup at resolve time. `extract_sticker_from_response()` (`responder.py:98-115`) pulls the raw
string out with a regex and hands it to `pipeline.py` as `PipelineResult.sticker_file_id`
(`pipeline.py:246`), which the handler passes straight to `message.answer_sticker(...)`
(`message.py:271-278`) — **a separate Bot API call, never string-concatenated into `html_text`
and never passed through `markdown_to_html()`.** Telegram itself validates the id; a hallucinated
or stale one raises, caught by the broad `except Exception` at the call site and logged, never
rendered to the user (`message.py:274-278`).

Because the resolved value never becomes HTML text, `markdown_to_html()`'s escape-first step is
irrelevant to it either way. Extracting *before* formatting is done purely for pipeline hygiene —
it keeps `ai_text` free of the control token before the user-facing text is built, and keeps
`sticker_file_id` a distinct structured field rather than something baked into a string — not
because escaping order matters here.

### Mentions: resolved value IS HTML markup → order after formatting is required, not stylistic

`_resolve_mentions()` (`summary.py:25-52`) inserts `<a href="tg://user?id={user_id}">{name}</a>`
directly into the text that gets sent with `parse_mode="HTML"`. `markdown_to_html()`'s **first**
step escapes raw `<`, `>`, `&` (`formatter.py:27-28`, CLAUDE.md "Formatter security — escape
HTML first"). If mention resolution ran *before* `markdown_to_html()`, the anchor tag itself
would be escaped into inert visible text (`&lt;a href=...&gt;Name&lt;/a&gt;`) — mentions would
silently stop being clickable. Running it *after* is the only order under which the inserted
anchor survives to reach Telegram as markup.

This is not merely "avoid a formatting bug" — it is the security-critical half of the design.
`markdown_to_html()`'s escape-first step is also the **only** HTML-escaping pass anywhere in this
pipeline. Because mention resolution runs strictly after it, `_resolve_mentions()` is the last
code that will ever touch this text before it reaches Telegram — **there is no downstream escape
pass to catch a mistake here.** That is exactly why the model is never allowed to see or emit a
real name: the token table (`participants: dict[int, tuple[int, str]]`) is built by code from DB
rows, and `display_name` is passed through `html.escape()` at construction time
(`summary.py:111`), *before* it is placed inside `<a>…</a>`. The model only ever echoes back an
opaque `@@uN@@` index it cannot forge into anything meaningful (`_MENTION_TOKEN_RE`, `summary.py:21`);
an unresolvable index degrades to a generic localized label (`_UNKNOWN_MENTION_FALLBACK`,
`summary.py:22,43-48`) rather than leaking the placeholder syntax or emitting partial markup.

### Generalized rule for any future marker pattern

Before adding a third "marker in raw AI output, resolved by code" mechanism, classify the
resolved value first:

1. **Non-text API parameter** (file id, callback data, a numeric id passed to an SDK call) →
   extract *before* `markdown_to_html()`, or at any point — order is a hygiene choice, not a
   security one, because the value is never rendered as text.
2. **HTML markup that must appear in the final message** → resolve *after* `markdown_to_html()`,
   and the resolver itself becomes the last escaping opportunity — any attacker-influenced
   string (username, first_name, free-text field) it inserts MUST be `html.escape()`d at
   insertion time, built from trusted (DB/code) data, never from the model's own output.

---

## Consequences

### Positive

- The two existing marker mechanisms (STICKER, mentions) each have documented, verified reasons
  for their ordering — a future refactor pass won't "simplify" them into the same order and
  either break mentions' rendering (harmless but visible bug) or, worse, remove the escape-first
  guarantee protecting them (security regression).
- The generalized rule gives the next marker-pattern implementer (backend-dev or otherwise) a
  concrete question to ask up front instead of copying whichever existing pattern is closer at
  hand.

### Negative / Trade-offs

- Two visibly different code shapes for "a token in AI output that code resolves" is a small
  ongoing cognitive cost — a reader has to know *why* before trusting either as a template.
  Mitigated by this ADR and by the inline docstring in `_resolve_mentions()` (`summary.py:30-42`),
  which already carries a condensed version of this reasoning at the call site.

---

## Alternatives considered

### A: Unify both markers to resolve before `markdown_to_html()`

Rejected: breaks mentions' anchor tag (escaped into inert text) — the entire feature would
silently render as raw markup instead of a clickable name. Not just wrong, but wrong in a way
that fails visibly on the *happy* path (no adversarial input needed).

### B: Unify both markers to resolve after `markdown_to_html()`

Rejected: no functional break for STICKER (the file_id would still pass through
`markdown_to_html()` unaffected, since it contains no markdown-syntax or escapable characters),
but it removes the pipeline-hygiene benefit of keeping the sticker directive out of the text that
downstream formatting operates on, for no corresponding gain — the file_id still never becomes
HTML text either way. Not incorrect, just an unmotivated churn with no upside; left as-is.

### C: Have `markdown_to_html()` itself understand both marker types

Rejected: couples a generic Markdown→HTML formatter (used by four call sites, including plain
admin/command replies — `commands.py`) to two feature-specific concerns (sticker selection,
mention resolution) it has no reason to know about. Keeps the formatter a pure, reusable
primitive; resolution stays the caller's responsibility, as it is today.

---

## Scope note — not the same concern as reply-quote sanitization (Q-1/Q-2)

This ADR covers **output-side** marker resolution: turning something the model *produced* into
final HTML. `sanitize_prompt_content()` (used by Q-1's reply-quote wiring and elsewhere) is an
**input-side** defense: fencing user-authored content so it cannot be mistaken for an instruction
*by* the model. Both happen to sit in the same feature area of this plan and both are described
loosely as "injection defense," but they defend opposite directions of the pipeline and neither
generalizes to the other — a reviewer should not expect one test suite or one helper to cover
both.

---

*Document generated as part of ADR-0005 (summary-mentions-quotes-2026-08-04 plan, item M-3).*
*Architect: specialist-architect (universal baseline).*
