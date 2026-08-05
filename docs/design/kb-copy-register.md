# Knowledge Base — terminology + copy register (G2)

Status: **decision, implementation-ready.** Consumed by A4 (`/remember` + `/kb` +
`adm_kb_*` admin sub-router). Gates A4 — do not invent copy ad hoc while
implementing; anything not covered here that comes up during A4 should return
to this doc (edit + re-link), not be improvised inline.

Plan item: `G2` in `docs/plans/knowledge-base-research-2026-07-23.execution.md`.
Source PRD: `docs/plans/knowledge-base-research-2026-07-23.md` §3.1–3.5.
Cross-ref: `docs/decisions/ADR-0003-*` (G1 — schema/lifecycle), which this doc
treats as the field-name source of truth (`topic`, `subject`, `predicate`,
`value`, `status`, `source`).

Bilingual throughout: **ru + en**, `chat_config.language` selects. Register:
casual, first-person chat-companion voice (matches `/help`, `/start` —
see "Voice reference" below), not corporate/formal.

---

## 0. Voice reference (evidence, not invented)

Existing bot voice, read from `src/bot/handlers/commands.py` (`_HELP_TEXT`,
`_START_TEXT`): friendly, casual, emoji-led headers, contractions in English,
occasional wink (`😉`). Admin panel (`src/bot/keyboards/admin.py` `_L`) is
terser/utilitarian — emoji + short noun, no sentences. KB copy follows
**whichever register its surface already has**: `/kb` and `/remember` are
user-facing chat commands → `/help`-style voice; `adm_kb_*` is admin panel →
`_L`-style terse noun labels. Don't blend the two.

---

## 1. Terminology lock: "organizer" role

**Decided by Julia (2026-07-24, plan clarifying_question #5a):** term = **«организатор»** (ru) / **"organizer"** (en).

Rules:
- Lowercase common noun in prose, not a title-cased badge (`организатор`, not
  `Организатор ⭐`) — matches how the source PRD itself uses it throughout
  §1–3.4, and matches this codebase's plain-noun convention (no existing
  role display uses decorative title-casing).
- **"organizer" ≠ "admin".** Two distinct, non-overlapping display concepts:
  - `bot_config.admin_ids` → bot admin (rank 4, existing `IsAdmin()` filter,
    existing copy already calls this "админ"/"admin" implicitly via
    `IsAdmin` — no display string exists to collide with, verified: no
    plain-text "админ"/"admin" role label found in `src/bot/**`).
  - `chat_settings.kb_organizer_ids` → **new** role, chat-scoped, KB-specific
    authority rank 3 (per ADR-0003/source §3.2 authority table). Never
    display it as "admin" — a chat's Telegram admins (rank 2,
    `getChatAdministrators`) are a *different* rank than KB organizers
    (rank 3). Conflating the two in copy would misrepresent the authority
    model G1 is defining. If a screen needs to show rank, spell out the
    noun each time ("организатор" / "Telegram-админ чата"), never a shared
    "admin" label.
- Do not pluralize awkwardly in en; "organizers" is fine, "organizer's" (DM
  inbox) reads more naturally as "organizer inbox" if ever needed (PH2, not
  this item).

---

## 2. Copy pattern conventions (which existing pattern to extend, where)

Verified from the codebase — three copy patterns already coexist; use
the one matching each surface, don't introduce a fourth:

| Surface | Existing pattern | File | KB extends it in |
|---|---|---|---|
| Admin panel main-menu buttons | `_L: dict[str, dict[str,str]]` + `_t(key, lang)` helper | `src/bot/keyboards/admin.py` | §3 below (new `_L["kb"]` entry) |
| Admin sub-router (sticker-style) button/menu text | inline ternary `"ru" if lang=="ru" else "en"` | `src/bot/keyboards/admin_sticker.py`, `src/bot/handlers/admin_sticker.py` | §4 below (`adm_kb_*`) |
| User-facing chat commands (`/help`, `/start`, `/summary`) | module-level `_XXX_TEXT: dict[str, str]` (or nested for parameterized blocks like `_FEATURES`), formatted via `.format(...)`, rendered through `markdown_to_html` | `src/bot/handlers/commands.py` | §5–6 below (`/kb`, `/remember`) |

**No dedicated i18n/copy module exists in this project** (`src/i18n/`,
`src/copy/` — neither present, confirmed by `find`). Runtime copy lives
inline in the handler/keyboard files above, which are A4's implementation
files, not mine. Per my scope restriction I do **not** edit those files
directly — the dict literals below are the literal contract; A4 splices
them in. **If A4 finds the `_L`/`_XXX_TEXT` fragmentation across 3 files
worth consolidating into a real `src/bot/i18n.py` module, that's an
architecture call — flag it to architect, don't do it silently inside this
item.**

---

## 3. Admin panel entry (main menu button)

Add one new `_L` key to `src/bot/keyboards/admin.py`:

```python
"kb": {"ru": "📚 База знаний", "en": "📚 Knowledge Base"},
```

Icon: 📚 (books). Checked against every icon already in `_L` (📋 📏 🎨 ⚙️ 📊
🌐 💰 💚 🔔 ✖️ ◀️ 🇷🇺 🇬🇧) — no collision. **Deliberately not 🧠** — `🧠 Память`
already means the RAG/chat-memory feature in `/help`'s `_FEATURES` dict
(`src/bot/handlers/commands.py`); source PRD §3.5 explicitly says KB and RAG
"не смешивать" (don't conflate) — reusing 🧠 for KB would visually contradict
that separation.

Placement in `main_menu_keyboard`: new row inserted **after the
"notifications" row, before the "language" row** (i.e. grouped with the
other operational-settings rows, not bolted to the end after "close").
Single-button row (matches how "health"/"notifications"/"language" are each
already solo rows — don't force it into stickers/defaults' 2-column pairing,
that pairing is content-driven, not a hard grid rule).

Submenu (mirrors `adm_stk` menu pattern in `admin_sticker.py`, inline
ternary since it's the sub-router pattern, not `_L`):

```
text = "📚 База знаний" if lang == "ru" else "📚 Knowledge Base"
```

Buttons on the `adm_kb:{lang}:0` submenu screen:

| Label (ru) | Label (en) | callback_data |
|---|---|---|
| `👥 Организаторы` | `👥 Organizers` | `adm_kb_orgs:{lang}:0` |
| `{icon} Сбор фактов: {status}` — see toggle convention below | same | `adm_kb_toggle:{lang}` |
| `◀️ Назад` | `◀️ Back` | `adm_menu:{lang}` |

---

## 4. `kb_enabled` toggle — reuse the existing boolean-toggle convention

**Do not invent new toggle copy** (e.g. "Включить/Выключить" verbs) — this
codebase already has one established boolean-toggle convention, in
`notifications_keyboard` (`src/bot/keyboards/admin.py`):

```python
status = "✅" if enabled else "⚫"
text = f"{icon} {label}: {status}"
```

Apply verbatim for `kb_enabled`:

```python
icon = "📚"
label = {"ru": "Сбор фактов", "en": "Fact collection"}[lang]
status = "✅" if kb_enabled else "⚫"
text = f"{icon} {label}: {status}"
```

Rationale for "Сбор фактов" / "Fact collection" rather than "База знаний":
the toggle governs whether the bot *writes new facts* (manual + future
autocollection), not whether the KB itself is visible — `/kb` view stays
readable regardless of `kb_enabled` (matches source §3.5's read/write
independence — retrieval reads `status='active'` rows whether or not new
writes are currently permitted). If backend-dev's implementation ties
`/kb` visibility to `kb_enabled` too, flag that back to this doc — the
copy above assumes it doesn't.

Confirmation toast after tapping (`callback.answer(...)`, existing pattern
in `handle_notification_toggle`):

```python
ru = "Сбор фактов включён" if kb_enabled_new else "Сбор фактов выключен"
en = "Fact collection enabled" if kb_enabled_new else "Fact collection disabled"
```

---

## 5. Organizer management screen (`adm_kb_orgs`)

Title (edit_text on the submenu, inline-ternary pattern):

```
"👥 Организаторы чата" if lang == "ru" else "👥 Chat organizers"
```

Empty state:

```
ru: "Пока не назначено ни одного организатора. Добавьте через кнопку ниже."
en: "No organizers assigned yet. Add one with the button below."
```

List row (one per organizer, paginated — reuse `sticker_sets_keyboard`'s
pagination footer shape: `◀️` / `{page+1}/{total_pages}` / `▶️`):

```
text = f"{display_name}"          # @username, else first_name, else "участник {user_id}"/"member {user_id}"
callback_data = f"adm_kb_org_rm:{lang}:{user_id}"   # numeric id only — 64B budget
```

Tapping a row removes that organizer directly (single tap = remove; this is
an admin-only low-stakes list edit, not a destructive irreversible action —
no confirm dialog needed, unlike whitelist removal's `wl_confirm_yes/no`
which guards a different, higher-blast-radius action). **If backend-dev
disagrees this needs a confirm step, that's a UX-risk call worth a NEEDS_WORK
round-trip on A4, not a silent change.**

Add-organizer row (bottom, above pagination):

```
ru: "➕ Добавить организатора"
en: "➕ Add organizer"
callback_data: f"adm_kb_org_add:{lang}"
```

Add flow prompts the admin to reply with a `@username` or forward a message
from the target user (this is a backend-dev/frontend-dev interaction-design
call for *how* the input is collected — I only specify the copy for the
prompt and the two outcomes):

```
prompt ru: "Перешлите сообщение от нового организатора или отправьте его @username."
prompt en: "Forward a message from the new organizer, or send their @username."

success ru: "✅ {name} добавлен(а) в организаторы."
success en: "✅ {name} added as organizer."

not_found ru: "🤔 Не нашёл такого участника в этом чате."
not_found en: "🤔 Couldn't find that member in this chat."
```

---

## 6. `/kb` view — copy register (group = terse, DM = bold-title + sections)

Grouping key is `topic` per ADR-0003/source §3.1 DDL comment
(`topic — группировка: 'event:летний-митап' | 'general'`). Default/null
topic displays as:

```
ru: "Общее"
en: "General"
```

### 6a. DM (rich — bold title + sectioned)

```
📚 **База знаний чата «{chat_title}»**

**{topic_1}**
• {subject} — {predicate}: {value}
  _обновлено {date}, {author_label}_
• {subject} — {predicate}: {value}
  _обновлено {date}, {author_label}_

**{topic_2}**
• ...

◀️ 1/3 ▶️
```

(English mirrors structurally: `📚 **Chat Knowledge Base — "{chat_title}"**`,
`_updated {date}, {author_label}_`.)

- Rendered via `markdown_to_html` (existing helper, same as `/help`) — don't
  hand-roll HTML tags for the bold title/topic headers.
- `{author_label}`: `@username` if present, else first name, else
  `"участник"` / `"member"` (fallback rarely hit — Telegram messages
  virtually always carry a `from_user.first_name`).
- `{date}` format: **`dd.mm.yyyy`** (e.g. `24.07.2026`) — no existing date-format
  convention was found anywhere in `src/services/text` or `src/utils`
  (verified by grep), so this is a **new** pattern this doc is locking, not
  a reuse of one. If a project-wide date format gets established later
  elsewhere, reconcile.
- Page size: cap at **5 facts per page** in DM (sections make each fact
  taller — 2 lines — so this keeps a page within one screen without
  scrolling on a phone).
- Pagination footer: reuse existing `◀️ {page+1}/{total} ▶️` shape (see
  `sticker_sets_keyboard`), `callback_data = f"kb_view:{lang}:{page}"`.
  **Not** under the `adm_` namespace — `/kb` is visible to all chat members
  (Julia's decision #5), it is not an admin surface.

Empty state (DM):

```
ru: "📚 База знаний пока пуста. Организаторы могут добавить факты через /remember или из админ-панели."
en: "📚 The knowledge base is empty. Organizers can add facts via /remember or from the admin panel."
```

### 6b. Group (terse — flat, no bold headers)

Groups strip the topic-header/blank-line structure — one flat bulleted
block, single line per fact (no provenance sub-line — provenance is a
DM-only detail, keeps the group message short):

```
📚 База знаний ({total} факт(а/ов)):
• {subject} — {value}
• {subject} — {value}
• {subject} — {value}
...
◀️ 1/2 ▶️
```

```
en:
📚 Knowledge base ({total} facts):
• {subject} — {value}
...
```

- Page size: cap at **8 facts per page** in group (flat lines are shorter,
  and group chats are more tolerant of a slightly longer single message
  than a DM feed, but still bounded — avoid a wall of text).
- No `{predicate}` label shown in group mode (keeps it terse; DM has room
  to spell out `predicate: value`, group shows just the resolved value).
  **If A4 finds `subject` alone is ambiguous without `predicate` for some
  fact shapes** (e.g. two predicates under one subject, like «место» AND
  «дата» both under subject «мероприятие»), that's a data-shape edge case —
  bounce it back to this doc rather than silently adding the predicate
  back in group mode (changes the terse/rich contract this doc is locking).

Empty state (group):

```
ru: "📚 База знаний этого чата пока пуста."
en: "📚 This chat's knowledge base is empty."
```

---

## 7. `/remember` — copy register

`/remember`, used as a reply to a message (source PRD §3.6 item 6:
"явный жест сохранения"). Phase 1 has **no extraction/reconciliation** (PH2
scope) — a manual `/remember` in Phase 1 inserts directly as an `active`
fact, no `pending` queue. Confirmation copy assumes direct-active insert;
if backend-dev's Phase-1 implementation routes `/remember` through a
pending state instead, that's a scope deviation from the source PRD's own
Phase-1 description — flag it, don't silently reword this section to match.

**Input-format ambiguity (flagging, not deciding):** the source PRD doesn't
specify whether `/remember` takes free text (parsed into subject/predicate/
value by a cheap model — expensive for a "manual, no-extraction" phase) or
requires explicit syntax like `/remember тема: значение`. This is a
backend-dev/frontend-dev implementation decision I don't own. The copy below
covers **both** a success case and a malformed-input case so A4 has strings
ready either way; if the input model turns out to need more error cases,
extend this section rather than improvising new strings inline.

```
success ru: "✅ Сохранено: **{subject}** — {value}"
success en: "✅ Saved: **{subject}** — {value}"

malformed ru: "🤔 Не смог распознать факт. Формат: `/remember тема: значение` (в ответ на сообщение)."
malformed en: "🤔 Couldn't parse that. Format: `/remember topic: value` (as a reply to a message)."

no_reply ru: "↩️ Используйте /remember в ответ на сообщение, которое нужно сохранить."
no_reply en: "↩️ Use /remember as a reply to the message you want to save."
```

Rendered as plain `message.reply(...)` (matches `admin_sticker.py`'s
`handle_admin_sticker_reply` confirmation-reply pattern — no HTML needed for
the malformed/no_reply cases; the success case's `**bold**` needs
`markdown_to_html` like `/help`, or plain `*bold*`→HTML conversion — reuse
whichever helper the surrounding command already uses, don't add a second
markdown renderer).

---

## 8. Explicitly OUT OF SCOPE for this item

- **PH2's 3-button confirmation row** (✅ принять / ✏️ править / ❌
  отклонить) — belongs to Phase 2 (autocollection suggestion queue), not
  this item. Flagging the mobile-truncation risk here for whoever picks up
  PH2's decomposition: **existing keyboards in this codebase cap at 2
  buttons per row** (verified — every multi-button row in
  `admin.py`/`admin_sticker.py` is ≤2 across `main_menu_keyboard`,
  `language_keyboard`, `access_keyboard`). A 3-button row is a **new**
  pattern with no precedent; on narrow phones a 3-way row risks truncated
  labels. When PH2 is decomposed, its designer item should pick between
  (a) shortening labels to single emoji + no text, or (b) stacking 2+1
  across two rows, and should NOT assume 3-in-a-row is safe by default.
- **Announcement copy** ("Обновление: место X → Y (по сообщению
  @организатора)", source §3.4 bullet 2) — Phase 3 (`PH3`), not this item.
- **Event card copy** (pinned, source §2.1) — Phase 3 (`PH3`).
- **Digest copy** (source §3.4 bullet: "за неделю я узнал...") — Phase 4
  scope-adjacent, not this item.

---

## 9. Implementation checklist for A4 (self-check before shipping)

- [ ] `_L["kb"]` added to `admin.py`, positioned after "notifications" row.
- [ ] `adm_kb:*`, `adm_kb_orgs:*`, `adm_kb_org_add:*`, `adm_kb_org_rm:*`,
      `adm_kb_toggle:*` callback namespace wired, all `≤64` bytes with real
      numeric IDs substituted (spot-check the longest realistic case:
      `adm_kb_org_rm:ru:9999999999` = 27 bytes — comfortably under budget).
- [ ] `/kb` uses `kb_view:{lang}:{page}` — **not** `adm_`-prefixed (public
      command, all members).
- [ ] Group vs DM `/kb` render paths genuinely diverge (terse flat vs.
      bold-title sectioned) — don't ship one path and fake the other.
- [ ] `/remember` success path renders `**bold**` via the same markdown
      helper `/help` already uses (`markdown_to_html`), not a new one.
- [ ] Date format `dd.mm.yyyy` applied consistently in both `/kb` DM
      provenance lines.
