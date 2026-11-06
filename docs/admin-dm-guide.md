# Admin DM — Commands and Panel Logic

**Audience:** whoever operates a deployment of this bot, plus developers who need to know
what a screen is *supposed* to do before changing it.

**Scope:** everything a bot admin can do in a direct message with the bot — the commands,
the inline panel behind `/admin`, the messages the bot pushes into that DM on its own, and
the rules that decide what each action changes.

Implementation details (routers, callback grammar, storage keys, permission-check variants,
known traps) live in [admin-dm-internals.md](admin-dm-internals.md). This document stays on
behaviour.

**How it is organised.** One part per area of the panel, in the order the panel is
being rebuilt: **F** is what every screen shares, **A1–A6** are the areas. Sections are
numbered within their part (§A1.2), so one area can grow without renumbering another, and
each area edits only its own part.

| Part | Covers |
|---|---|
| [F](#f--the-whole-panel) | Who is an admin, the DM surface, how a screen reads, out-of-date buttons, what the panel does not do |
| [A1](#a1--hub-requests-status-settings) | Main menu, access requests, statistics / costs / health, global settings, notifications and language |
| [A2](#a2--chats-and-chat-sections) | One chat's settings, reactions |
| [A3](#a3--rules) | The rules engine |
| [A4](#a4--knowledge-base-organizers) | Knowledge base and its organizers |
| [A5](#a5--stickers) | Sticker catalogue, cards, checks and corrections |
| [A6](#a6--entry-and-cards) | Messages the bot sends on its own: the access-request card |

---

## F — The whole panel

### F.1 Who is an admin, and where the panel works

A **bot admin** is a Telegram user id listed in the `admin_ids` row of the `bot_config`
table. That is the only source of truth — there is no "promote from inside the panel" flow,
and chat-level Telegram admin rights grant nothing here. The list is re-read from the
database on every check, so editing it takes effect without a restart (the per-admin command
menu is the exception: it is pushed to Telegram at startup only).

Bot admin ≠ *chat organizer*. Organizers are a per-chat list used by the knowledge base only
(§A4.1), and they are managed *by* a bot admin.

Three rules apply to the whole admin surface:

1. **Private chat only.** `/admin` and `/costs` are ignored in groups, and every panel button
   re-checks both admin status and that the message it is attached to lives in a private chat.
   A panel screenshot forwarded into a group has dead buttons.
2. **Admin DMs bypass the whitelist.** Ordinary chats must be approved before the bot reacts
   at all; an admin's DM is always processed even when its own `chat_settings` row is
   disabled.
3. **The panel is global, not per-admin.** UI language and notification switches are one
   shared record. Two admins see the same settings and change them for each other.

### F.2 The DM surface at a glance

| What the admin does in the DM | What happens |
|---|---|
| `/admin` or `/settings` | Opens the main panel (§A1.1) |
| `/costs` | Shortcut to the 24-hour AI spend screen (§A1.3) |
| `/cancel` | Ends whatever multi-step prompt you are parked in — a rule's JSON config, a tolerance value, an organizer add — and says which. Answers «Нечего отменять» when nothing is open. Works from **every** state: its router is included first, ahead of the ones that own those prompts. A *forwarded* message reading "/cancel" is content, not the command, so forwarding someone into the organizer prompt still works |
| `/start`, `/help` | Ordinary user commands, nothing admin-specific |
| `/summary` | Answers "group chats only" — summaries are never generated for a DM |
| `/kb` | Works on the *DM's own* knowledge base, which is almost always empty. It appears in the command menu |
| `/remember` | **Group chats only** since S2. In a DM it answers with a notice and writes nothing: a fact is stored against the chat it was written in, and the bot answers from the knowledge base of *that* chat, so a fact captured in a DM is one no group retrieval can ever reach. It deliberately does not appear in the command menu — only organizers and bot admins may use it, which no Telegram scope can express |
| Sends a sticker | Catalogue check (§A5.3) — never silently learned |
| Replies with text to a sticker card | Updates that sticker's description via the AI merge (§A5.4) |
| Any other text | Treated as an ordinary message to the bot: it answers only if a trigger word matches or the random chance fires |

And the messages the bot pushes into the DM unprompted:

| Push notification | Trigger | Controlled by |
|---|---|---|
| 🔒 Unauthorized access | A non-whitelisted chat used the bot; carries Approve/Reject buttons | Notifications → *Unauthorized access* |
| 🖼 New sticker learned | The bot analysed a sticker it had never seen; sticker + description, repliable | Notifications → *Stickers* (off / on / detailed) |
| ⚠️ Jailbreak attempt | Anti-abuse pattern matched | Notifications → *Jailbreak* |
| 🚫 Blacklist triggered | Anti-abuse put a user on timeout | Notifications → *Blacklist* |
| 🔄 AI fallback activated | The primary provider failed and a fallback answered | Notifications → *AI Fallback* |
| 🚨 Bot Health Alert | Scheduled health check found a problem | Not switchable; **first admin in the list only** |

Every switchable notification goes to *all* admins. Health alerts go to one.

### F.3 Reading a panel screen

The panel is a single message that is edited in place as you navigate — one screen at a
time, "◀️ Назад / Back" walks up one level, "✖️ Закрыть / Close" deletes the message.
A few screens (a sticker card, an FSM prompt) intentionally send new messages instead.

Conventions used throughout:

- `✅` / `⚫` — a boolean is on / off.
- `· унаследовано` / `· inherited` — this chat has no value of its own; it is currently
  following the global layer, and a global change will move it (§A1.4).
- A row with no visible effect when tapped is a read-only value (an unlabeled section
  header, a page counter, or a setting whose editor is not built yet).
- Numbered buttons (`1 ✅`, `2 ❌`) line up with the numbered entries in the message body
  above them.

### F.4 A button that has outlived its screen

Panel messages stay in the DM for as long as the admin keeps them, and the code that drew
them moves on. A tap on a panel button the bot no longer has a screen for is answered by
redrawing the **main menu** in that same message, with one line on top:

> *Эта кнопка устарела и ничего не изменила. Вот панель сейчас.*
> *This button is out of date and changed nothing. Here is the panel now.*

The line means what it says: nothing was written. If a panel prompt was open at the time
(a rule's JSON, a tolerance value, an organizer add), it is closed too, so the next message
is not swallowed by a prompt the admin can no longer see. Someone who is not a bot admin
gets «Нет доступа / Access denied» and nothing else.

An object that has gone away is a different case: a deleted rule, a request another admin
already handled, a chat that was removed. The screen that owned it says what happened.

### F.5 Things the panel deliberately does not do

- **No admin management.** Adding or removing a bot admin means editing `bot_config.admin_ids`
  directly.
- **No undo on deletes.** Deleting a rejected attempt or a rule is permanent; removing a chat
  from the whitelist is not (it is a switch).
- **No per-admin preferences.** Language and notification switches are shared.
- **No broadcast.** There is no way to send a message to chats from the panel.
- **No editing of the older per-chat options** (trigger words, system prompt, chances,
  intervals) — those are read-only in the panel and set via config or the database.

---

## A1 — Hub, requests, status, settings

### A1.1 Main menu

`/admin` opens twelve destinations plus **✖️ Закрыть / Close**:

| Entry | Purpose | Section |
|---|---|---|
| 📋 Whitelist | Approve, reject and revoke chat access | §A1.2 |
| 📏 Правила / Rules | Per-chat custom rules engine | §A3.1 |
| 🎨 Стикеры / Stickers | Browse the learned sticker catalogue | §A5 |
| 🌍 Глобальные настройки / Global settings | Defaults applied to every chat with no own value | §A1.4 |
| 📊 Статистика / Statistics | Traffic counters for 1h / 24h / 7d | §A1.3 |
| 💰 Расходы / Costs | AI spend, with an optional OpenAI cross-check | §A1.3 |
| 💚 Здоровье / Health | Live health check | §A1.3 |
| 🔔 Уведомления / Notifications | Which pushes the bot sends | §A1.5 |
| 📚 База знаний / Knowledge Base | Per-chat facts and organizers | §A4.1 |
| 😀 Реакции / Reactions | Per-chat reaction module | §A2.2 |
| ⚙️ Настройки чата / Chat settings | The full per-chat settings panel | §A2.1 |
| 🌐 Язык / Language | Panel language (ru/en) | §A1.5 |

### A1.2 Whitelist — access control

The bot answers only in chats whose `chat_settings.enabled` is true. Everything else is an
*attempt*, and attempts are what this section manages.

How a chat lands in the pending list — a message from an unknown chat, or the bot being
added to a group — is described with the card it produces, in §A6.1.

#### The three lists

**💬 Чаты / Chats** — currently whitelisted chats, five per page. Each row is a link to the
chat plus a numbered ❌ that asks for confirmation and then **disables** the chat. This is a
soft switch: settings, history and knowledge base survive, the bot simply stops responding,
and the chat can be re-approved later.

**⏳ Ожидают / Pending** — attempts waiting for a decision. Chats that are already enabled
are filtered out, so the list never shows work that has been done elsewhere.

- **Approve** flips the chat to enabled, copies the chat title/type onto its settings row,
  and marks *every* pending attempt for that chat as approved at once. The screen then offers
  a direct "⚙️ Настройки чата" link into the new chat's panel, because the approved row has
  just left the pending list and there would be nothing to attach it to.
- **Reject** marks the attempt rejected and does nothing else visible.

**🚫 Отклонённые / Rejected** — this is the important one, because *rejection is a block*.
While a chat has any rejected attempt:

- new messages from it are not logged as attempts, and
- no notification is sent, ever — not even after the 30-minute cooldown.

So the admin will not hear from that chat again until the record is dealt with:

- **🔄 Вернуть / Restore** puts the attempt back to pending — notifications resume.
- **🗑 Delete** (with confirmation) removes the record permanently. The chat becomes an
  unknown chat again: its next message produces a fresh attempt and a fresh notification.

### A1.3 Observability screens

#### 📊 Statistics (`adm_stats`)

Deployment-wide counters over a rolling window — 1 hour, 24 hours or 7 days, selected by the
row of period buttons. Five numbers: whitelisted chats (a total, not windowed), active chats,
messages stored, bot responses, unauthorized attempts. There is no per-chat breakdown here.

#### 💰 Costs (`adm_costs`, or `/costs` for the 24-hour view)

What the bot *believes* it spent, computed from its own usage log: a total, a split by task
type (text / embeddings / vision / transcription) and the top eight models. Same three
periods.

**🔍 Сверить (OpenAI) / Verify (OpenAI)** cross-checks that belief against OpenAI's billing
API. Business rules worth knowing:

- It needs **two** settings — an organization-level admin key (`OPENAI_ADMIN_API_KEY`; the
  key the bot generates replies with will not work) and a project id (`OPENAI_PROJECT_ID`).
  If either is missing, the screen names the missing one and links to the page to fix it,
  without spending a request.
- It is rate-limited to **one check per admin per 10 seconds** — it is the only button in the
  panel that calls a metered third-party API.
- The comparison window follows OpenAI, not the button. OpenAI bills in whole daily buckets,
  so a "1h" check may cover a full day; the bot re-measures its own figure over whatever the
  buckets actually cover and prints that window, rather than subtracting two different spans
  and reporting the difference as an error.
- The check **refuses to show a number it cannot trust**: if OpenAI never finishes paginating,
  or returns data for other projects (the project filter did not apply), it reports the
  failure instead of a possibly inflated total. If OpenAI returns no per-project breakdown at
  all, the figure is shown but explicitly marked as unconfirmed in scope.

#### 💚 Health

Opening or refreshing this screen **runs a health check right then**, then shows the newest
result: status, timestamp, database reachability, messages in the last 30 minutes, AI
fallbacks in the last 15 minutes, and a list of issues. It is a snapshot of the deployment,
not of one chat. Scheduled checks that find a problem also push an alert — to the first admin
only.

### A1.4 🌍 Global settings

The second layer of the 26 per-chat options described in §A2.1.

**This is not a template for new chats.** It is the layer underneath every chat: change a
value here and it applies immediately to every chat that has not overridden that option —
existing chats included. A chat's own value always wins. The screen says so in its subtitle,
and the toast after a toggle says "for every chat without its own value", because the older
wording ("defaults for new chats") promised the opposite and made fleet-wide changes look
harmless.

Only the twelve non-legacy options appear here, for the reason given in §A2.1 (the
`· inherited` note).

### A1.5 Panel-wide settings

#### 🔔 Notifications

Five controls, all global:

- **🖼 Стикеры / Stickers** cycles through three states rather than toggling:
  `выкл / off` → `вкл / on` (sticker + description) → `подробно / detailed` (adds the analysis
  collage and the stored RAG fields: contexts, style tags, emoji, whether an embedding
  exists) → back to off.
- **🔒 Unauthorized access**, **⚠️ Jailbreak**, **🚫 Blacklist**, **🔄 AI Fallback** — plain
  on/off.

Everything is on by default (stickers: "on"). Switching a notification off suppresses the
*message* only; the underlying event is still recorded — an unauthorized attempt still lands
in the pending list even with its notification off.

#### 🌐 Language

Switches the panel between Russian and English and immediately re-renders. It is one shared
setting for all admins, and it is unrelated to the language the bot answers chats in (that is
a per-chat option). Panel messages already sent keep the language they were rendered in until
you navigate them.

---

## A2 — Chats and chat sections

### A2.1 ⚙️ Chat settings (per chat)

This screen and 🌍 Global settings (§A1.4) are the same 26 per-chat options at two layers,
and telling them apart is the single most important thing in the panel.

Pick a chat, get every option grouped into 💬 Behaviour, 🧩 Modules, 🎨 Stickers, 📏 Rules,
📚 Knowledge Base, 😀 Reactions.

- **Boolean options toggle in place.** The toggle flips what the chat *currently behaves
  like* (the merged value), then writes that as the chat's own value — so a chat inheriting
  "on" turns off on the first tap. The flip is computed inside the database write, so
  double-tapping cannot collapse two taps into one change.
- **The Knowledge Base and Reactions rows are links**, not toggles: they show status and open
  the panels in §A4.1 and §A2.2, which own those writes.
- **`Уровень приличия стикеров` / sticker tolerance level** has its own small editor: tap it,
  type a number between `0.0` and `1.0`. Out-of-range or non-numeric input is *rejected and
  re-prompted*, never clamped. Commands still work while the prompt is open, and the prompt
  carries a Cancel button — that is the way out, since an invalid value keeps the prompt
  active.
- **Every other non-boolean option is read-only for now** (trigger words, prompt, chances,
  intervals, rules mode). They are displayed so the panel tells the whole truth about the
  chat; a generic editor for them is not built yet.
- **`· inherited`** appears on an option whose per-chat value is unset. Thirteen of the 26
  options can show it. The other thirteen are older columns that always materialise a value
  of their own, so they cannot honestly claim to inherit — and, for the same reason, they are
  absent from the global screen.

### A2.2 😀 Reactions

Per-chat, so it starts with a chat picker.

Two independent switches: the **module** (whether the bot records and uses reactions at all)
and **history recording** (whether reaction events are stored).

The screen also carries a live status line — *is the bot an administrator in that chat?* —
checked against Telegram on every render, never cached. This matters because Telegram simply
does not deliver reaction updates to a non-admin bot and raises no error while doing so: the
module would look enabled and do nothing. Turning the module on while the bot is not an
admin pops an immediate warning, and the status line repeats it on every visit.

---

## A3 — Rules

### A3.1 Rules

A small per-chat rules engine (keyword triggers, spam detection, regex matching) reached from
📏 Правила: pick a chat, then browse its rules five per page.

- A rule row shows `✅`/`⏸`, the rule's name and its type, with a 🔄 (toggle) and 🗑 (delete)
  button next to it; tapping the name opens a detail view with weight, mandatory flag,
  trigger count and the full config.
- **Toggle** switches a rule on/off without deleting it.
- **Delete** asks for confirmation and is irreversible.
- **Add** means: choose a rule type, then send the rule as a **JSON object** (the screen shows
  a worked example). The bot validates that it parses as a JSON object and nothing more —
  a syntactically valid but semantically wrong rule is accepted and simply will not behave as
  intended. `weight` and `mandatory` are read out of that JSON if present.

Rules are also gated by the per-chat `rules_enabled` switch on the chat settings panel.

---

## A4 — Knowledge base organizers

### A4.1 👥 Organizers

Reached from a chat's **🧠 Память** section (§A2), which also holds the knowledge base's
on/off switch; **⬅️ Память** goes back there. There is no separate knowledge-base menu or
chat picker any more.

**Organizers** are the non-admin users allowed to write facts with `/remember` in that chat.
Facts saved by an organizer carry a lower authority than facts saved by a bot admin, which is
what decides who wins when two facts contradict.

The screen lists them three per page, each as **✖️ {name}**. A tap removes that person at
once — no confirmation — and the screen answers «✓ {name} больше не организатор» with
**↩️ Вернуть**, which puts them back where they were in the list. The page you were on is
kept. An empty list says so, and that bot admins can always save facts.

**➕ Добавить** opens the chat's participants, four per page, most active first — the path
that needs nothing from the admin's contacts. People already on the list carry **✓**; a tap
on one of them only shows «Уже организатор». A tap on anyone else adds them and returns to
the list, on the page where they landed, with «✓ {name} теперь организатор». Names come from
Telegram and are cut with «…» to fit a phone-width button.

**✏️ Нет в списке** on that screen sends the add prompt as a new message with a keyboard under
the input field:

1. **🔍 Найти в Telegram** opens Telegram's own person picker (`request_users`) — search
   by name or username across the admin's contacts and chats, the same as choosing whom to
   message. The bot gets the id, name and username of whoever is picked, so this works for
   people who have never written in the chat and for those without a username. If Telegram
   reports that the person is not in the chat now, they are added anyway with a ⚠️ line —
   an organizer may join later. Clients that cannot open the picker send the label as text;
   the bot answers with the alternatives below and keeps the prompt open.
2. **Forward a message** from them.
3. **Send their `@username`.** There is no username lookup in the Bot API, so this only works
   for usernames the bot has already recorded posting *in that chat*.

When a forward or an `@username` names nobody the bot can add, the answer says which of four
things happened — the forward hides its author (forward privacy), the forward came from a
channel or a group rather than a person, the `@username` is known but not from this chat, or
nobody by that name was found — with **👥 Из участников** and **✖️ Отмена** under it. The
prompt stays open, so the next forward or `@username` is read too.

Every way out of the prompt — an add, **✖️ Отмена** on either keyboard, a pick from the
participant list, going back to the organizers — removes the keyboard on a short line of its
own («✓ … теперь организатор.» or «Добавление организатора отменено.») and then sends the
organizer list as a new message. `/cancel` ends the prompt too. A pick made from a keyboard
that outlived its prompt adds nobody and says so.

---

## A5 — Stickers

The sticker catalogue is filled automatically: when sticker learning is on for a chat, any
new sticker seen there is downloaded, analysed by a vision model and stored. The panel is
where an admin inspects and corrects that.

### A5.1 Browse

**🎨 Стикеры** lists sticker packs with a learned/total counter, ten per page. Opening a pack
lists its stickers as `<emoji> <status> (<uses>x)`, where status is one of:

- `✅` the description (it shows the text itself),
- `⏳ Не выполнен / Not analyzed` — never analysed,
- `⚠️ Ошибка / Failed` — analysis was attempted and failed.

### A5.2 Sticker card

Tapping a sticker sends the sticker itself followed by a card with the description, emotion,
character, suggested contexts, usage counters (total and bot-initiated), format flags and any
admin notes. Navigating away deletes both messages, so the DM does not accumulate orphaned
stickers.

Two actions:

- **🔄 Запустить заново / Run analysis** — re-runs vision now. The card first collapses to
  "⏳ Анализирую…" with the buttons hidden (so a second tap cannot start a second run), then
  becomes either the updated description or a named failure — download error, API error,
  content blocked, empty response — with a Retry button.
- **🧹 Очистить анализ / Clear analysis** — after a confirmation, wipes the vision-generated
  fields (description, emotion, character, contexts). **Admin notes and usage counters are
  kept.** The card re-renders as `⏳ not analyzed` so it is obvious what state the sticker is
  now in.

### A5.3 Checking a sticker by sending it

An admin can just send a sticker into the DM. If it is already in the catalogue, the bot
replies with the same card as above. If it is not, the bot says so and offers a single
**🔍 Проанализировать / Analyze** button — analysis is always an explicit, visible action here,
never a silent background learn. (The catalogue's automatic learning path deliberately does
not run for an admin's own DM.)

If the analysis succeeds, the sticker's pack is registered too, so it shows up in the pack
browser like any automatically learned sticker.

### A5.4 Correcting a description by replying

Every sticker card and every "new sticker" notification ends with an invitation to reply.
A text reply to such a message is fed, together with the existing description, to the AI,
which merges the two into one description. Failure modes are surfaced rather than swallowed:
if the content filter blocks the text, the bot says so and asks for a rephrase; if the merge
fails for another reason, the note is stored and the bot suggests re-running the analysis.

Note that replying to *anything else* in the admin DM does nothing — the reply is consumed
by this handler and quietly dropped when no sticker can be identified.

---

## A6 — Entry and cards

### A6.1 The access-request card

A chat reaches the panel in one of two ways, and both produce the same card with
**✅ Одобрить / ❌ Отклонить**.

**Someone writes** in a chat the bot has not been approved for. The bot stays silent, records
an attempt (chat, user, first 200 characters of the message) and DMs every admin.

**The bot is added to a group** (since TD-025). Previously this queued nothing and notified
nobody — the group was invisible here until a human happened to post in it, while the server
log claimed it was "awaiting whitelist approval". Now the membership update itself files the
request, and the card is headed **➕ Bot added to a new chat** rather than *Unauthorized
access*, because there is no message to go looking for.

Three things deliberately do NOT produce a card: a promotion inside a chat the bot is already
in (member → administrator is not a new chat); re-adding the bot to a chat that is still
enabled; and anything happening in a **private chat** — Telegram sends the same membership
update when a user blocks or unblocks the bot, and an unblock looks exactly like a join. A
stranger who actually writes still produces a card through the first path.

Re-adding the bot to a chat that was approved and then removed DOES file a fresh request: the
removal disabled the chat, and re-consent is intentional — otherwise anyone able to add the
bot could re-enable a chat with no admin in the loop.

To avoid flooding, at most one notification per chat every **30 minutes**, shared across both
paths — so "added, then someone posted" is one card, not two. The cooldown is in memory, so a
restart clears it and a chat can end up with two pending rows; approving either one clears
them all.

An approve/reject taken from a *notification card* behaves identically to the same action in
the panel; the card's buttons are then replaced by a status badge, so a second admin tapping
the stale card gets "Запрос уже обработан / Request already handled" instead of a double
action.

The lists the request lands in, and what approving or rejecting changes, are in §A1.2.
