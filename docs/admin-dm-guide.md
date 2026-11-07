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

### A1.1 The hub

`/admin` sends the hub as a new message; every "⬅️ Панель / Panel" button brings it back in
place. It is four buttons and one status line:

```
🛠 Панель
💚 Всё работает · 💰 расход за сутки $0.04
```

The line reads what is already recorded: the verdict of the **last** health check and the AI
spend over the last 24 hours. While access requests wait, **🙏 Запросы · N** sits beside
🗂 Чаты and opens them in one tap; the Chats button itself never carries the count.
Opening the panel never runs a check, so it costs nothing and does not change what the next
alert says; «🔁 Обновить» on the status screen (§A1.3) is the one place a check runs on
demand. Before the first check the line says «❔ Проверок пока не было». If the database
cannot be asked at all, the hub shows its title alone rather than a line it cannot back.
A day under one cent keeps four decimals, so it does not read as $0.00.

| Button | Leads to | Section |
|---|---|---|
| 🗂 Чаты / Chats | The chat list; rejected chats, and requests too, are reached from there | §A1.2, §A2 |
| 🙏 Запросы · N / Requests · N — only while requests wait, beside Chats | The first waiting request; its back button leads to the chat list | §A1.2 |
| 📦 Паки стикеров / Sticker packs | The sticker catalogue | §A5 |
| 📊 Состояние / Status | The last health check, statistics, costs | §A1.3 |
| ⚙️ Общие настройки / General settings | Settings for every chat, notifications, panel language | §A1.4, §A1.5 |
| ✖️ Закрыть / Close | Deletes the panel message | — |

Everything that belongs to one chat is reached through that chat (§A2), not from the hub.

### A1.2 Requests and rejected chats

The bot answers only in chats whose `chat_settings.enabled` is true. Everything else is an
*attempt*, and attempts are what this section manages. How a chat lands among the requests —
a message from an unknown chat, or the bot being added to a group — is described with the
card it produces, in §A6.1.

The chat list (§A2) carries the way in: **🙏 Запросы доступа / Access requests** and
**🚫 Отклонённые / Rejected**; while requests wait, the hub has its own button to them (§A1.1). Both show **one chat per page** — its title, id and type, who wrote and the first
50 characters of what they wrote — with the two actions directly under it and ◀️ n/N ▶️ to
walk the list. A number in the text never has to be matched to a button. A button pointing
past the end of a list that has since shrunk shows the last entry.

#### Requests

Chats that are already enabled are filtered out, so the list never shows work that has been
done elsewhere.

- **👍 Одобрить / Approve** enables the chat, copies its title and type onto its settings row,
  marks *every* waiting request from that chat approved at once, and opens **the new chat's
  own screen** with «✓ Новый чат подключён» — that is where its settings are. That is all it
  writes: nothing is posted to the group and no command menu is pushed. What follows is the
  bot at work there — answering, and greeting or reacting where those are on — and none of that is taken back by turning the
  chat off again — turning off moves only `enabled`, the attempt stays approved, and other
  admins' copies of the card answer a later tap with «Запрос уже решён».
- **❌ Отклонить / Reject** marks the request rejected and shows the next one with
  «✓ Отклонено»; after the last one, the chat list with the same line.
- If another admin, or the card in the DM, got there first, the requests are redrawn with
  «✓ Заявку уже разобрали» and the request is not acted on twice.

#### Rejected — a rejection is a block

While a chat has any rejected attempt:

- new messages from it are not logged as attempts, and
- no notification is sent, ever — not even after the 30-minute cooldown.

The screen says so in one line («Бот молчит в этих чатах и не присылает от них заявок»). The
admin will not hear from that chat again until the record is dealt with:

- **↩️ Вернуть в запросы / Back to requests** puts it back among the requests — notifications
  resume. «✓ Запрос возвращён», and that render carries **🙏 Запросы доступа · N** on top: the
  list where the chat now waits for «👍 Одобрить», one tap away.
- **🗑 Забыть / Forget** asks first, then removes the record permanently: the chat becomes an
  unknown chat again, and its next message produces a fresh request and a fresh notification.
  «✓ Отказ забыт». «⬅️ Не забывать» returns to the same page.

A record someone else already removed is answered with «✓ Запись не найдена».

#### Turning the bot off in one chat

A chat's own screen (§A2) carries **🔌 Отключить**, and so does an approved access card
(§A6.3). It asks first — «Отключить бота здесь?» — and says in three lines what happens and how
to come back:

1. the bot stops answering there and the chat leaves «🗂 Чаты»; its settings are kept;
2. right away, «↩️ Вернуть» on the list brings it back;
3. later, the way that chat really has (the first that applies):
   - an admin's own private chat — the bot answers an admin there whether the chat is on or
     not, and such a chat files no request: the question is «Отключить этот чат?», it says that
     turning off only takes the chat out of «🗂 Чаты», and the notice after it is «✓ Чат «…»
     убран из «🗂 Чаты», бот отвечает в нём, как раньше»;
   - a group or a private chat — someone writes there, an access request appears (at most one
     every half hour — the cooldown that also spaces the cards), and approving it brings the bot back;
   - a channel — posts never reach the whitelist gate, so only removing the bot and adding it
     again files a request;
   - a chat with **any rejected attempt on record**, even an old one from before it was
     approved — it never asks again by itself (§A1.2, a rejection is a block) and lands in
     «🚫 Отклонённые»; the way back is «↩️ Вернуть в запросы» there, then «👍 Одобрить» among
     the requests. The screen says so («Верните его из «🚫 Отклонённые» в запросы и одобрите»)
     instead of promising a request that will not come.

A chat that is off already — an approved card tapped after another admin turned it off — is
told so instead of asked («Бот здесь уже отключён»), with the same "later" line and only
«⬅️ Чат»; the chat's own screen shows «🔌 Отключить бота» only while the bot is on there. An old
question tapped after that writes nothing and offers no «↩️ Вернуть» (it would turn the chat
*on*): «Бот в чате «…» уже был отключён, ничего не изменилось».

Settings, history and knowledge base all survive; the switch is `enabled=false`, nothing more,
and the approved attempt stays approved. Confirming shows the chat list (its first page — both
entry points carry page 0) with «✓ Бот отключён в чате «…»» and **↩️ Вернуть** as the top row — on that
render only; that page holds one chat fewer so the keyboard does not grow, and ▶ from it keeps
pages of that size, so no chat is skipped on the way to the next one. «↩️ Вернуть» sets
`enabled=true` again and redraws the list with «✓ Бот снова работает в чате «…»» and no undo
row. Tapped when the chat is already on (twice, or after another admin), it writes nothing and
says the same; a chat whose settings row is gone is answered with «Этого чата нет в
настройках, ничего не изменилось» — also what «🔌 Отключить» says then, rather than claiming
the bot was turned off. «⬅️ Не отключать» returns to the chat's screen, from the chat's screen
and from the access card alike.

### A1.3 Status, statistics, costs

#### 📊 Status

Shows the **last recorded** health check and when it ran (in the bot's display time zone:
`14:00` today, `03.11 14:00` on another day): the verdict, then each problem with the
provider's own error and the advice the alert carried, then whether the database answers,
messages in the last 30 minutes and fallback-AI answers in the last 15 minutes. A
check that found a warning or a failure leads with the problems. It is a snapshot of the
deployment, not of one chat.

**🔁 Обновить / Refresh** runs a check right then and shows its result. If the check itself
fails, the screen shows the last recorded one, whose time says how old it is. The advice
lines are stored with the check in Russian, so an English panel shows them in Russian.

Scheduled checks that find a problem also push an alert — to the first admin only.

From here: **📈 Статистика** and **💰 Расходы**, both opening on 24 hours.

#### 📈 Statistics

Deployment-wide counters over 1 hour, 24 hours or 7 days, picked by the row of period buttons
(the current one is marked `•`). Five numbers: whitelisted chats (a total, not windowed),
active chats, messages stored, bot responses, unauthorized attempts. There is no per-chat
breakdown here.

#### 💰 Costs (`/costs` sends the 24-hour view as a new message)

What the bot *believes* it spent, computed from its own usage log: a total, the top eight
models, and a split by task (text / embeddings / vision / transcription / community), each
with its number of calls in brackets. Same three periods.

**🔍 Сверить с OpenAI / Check against OpenAI** cross-checks that belief against OpenAI's
billing API. Business rules worth knowing:

- It needs **two** settings — an organization-level admin key (`OPENAI_ADMIN_API_KEY`; the
  key the bot generates replies with will not work) and a project id (`OPENAI_PROJECT_ID`).
  If either is missing, the screen names the missing one and says where to get it, without
  spending a request.
- It is rate-limited to **one check per admin per 10 seconds** — it is the only button in the
  panel that calls a metered third-party API. A press inside that window is answered with a
  short note and leaves the screen as it was.
- The comparison window follows OpenAI, not the button. OpenAI bills in whole daily buckets,
  so a "1 hour" check may cover a full day; the bot re-measures its own figure over whatever
  the buckets actually cover and prints that window («Окно: последние N ч»), rather than
  subtracting two different spans and reporting the difference as an error.
- The check **refuses to show a number it cannot trust**: if OpenAI never finishes paginating,
  or returns data for other projects (the project filter did not apply), it reports the
  failure, with «🔁 Повторить», instead of a possibly inflated total. If OpenAI returns no
  per-project breakdown at all, the figure is shown but explicitly marked as unconfirmed in
  scope.

### A1.4 ⚙️ General settings → 🌍 All chats

**⚙️ Общие настройки / General settings** — from the hub, or `/settings` as a new message —
holds what belongs to the bot as a whole rather than to one chat: **🌍 Для всех чатов /
All chats** (this section), **🔔 Уведомления / Notifications** and **🌐 Язык / Language**
(§A1.5).

*All chats* is the second layer of the per-chat options described in §A2.1.

**This is not a template for new chats.** It is the layer underneath every chat: change a
value here and it applies immediately to every chat that has not overridden that option —
existing chats included. A chat's own value always wins. The screen says so under its title,
and the toast after a switch says «✓ Включено для всех чатов без своего значения» (or
«Выключено…»), because the older wording ("defaults for new chats") promised the opposite
and made fleet-wide changes look harmless.

It has the same seven sections as a chat's own panel — 🗣 Поведение, 🧠 Память, 🎙 Голос и
фото, 🎨 Стикеры, 😀 Реакции, 👋 Сообщество, 📏 Правила. A section shows its yes/no options as
switches (✅ on, ⚪ off; a switch redraws the same section) and its other values as lines of
text — sticker and photo chances, sticker frankness, which rules apply — which are set via
config or the database. Only the options that can honestly be set globally appear; the
legacy ones are left out for the reason given in §A2.1 (the `· inherited` note).

### A1.5 ⚙️ General settings → notifications and language

#### 🔔 Notifications

Two controls, both global:

- **🖼 Новые стикеры / New stickers** cycles through three states rather than toggling:
  `выкл / off` → `кратко / brief` (sticker + description) → `подробно / detailed` (adds the
  analysis collage and the stored RAG fields: contexts, style tags, emoji, whether an
  embedding exists) → back to off.
- **Заявки чатов / Chat requests** — the access card (§A6.1), on or off. While it is off the
  screen says so: «Заявки чатов сейчас не присылаются: они ждут в «🗂 Чаты».»

Both are on by default (stickers: brief). Switching a notification off suppresses the
*message* only; the underlying event is still recorded — a request still lands in the list
with its notification off. Bot failures (health alerts) always arrive and have no switch.

The jailbreak, blacklist and AI-fallback switches are no longer on this screen. They keep
whatever value was last stored, and an old button for one of them is answered as out of date
(§F.4).

#### 🌐 Language

Switches the panel between Russian and English. Picking one saves it and shows General
settings in the new language with «✓ Язык панели: …». It is one shared setting for all
admins, and it is unrelated to the language the bot answers chats in (that is a per-chat
option). Panel messages already sent keep the language they were rendered in until you
navigate them.

---

## A2 — Chats and chat sections

### A2.1 🗂 Chats and one chat's settings

This screen and 🌍 Global settings (§A1.4) are the same per-chat options at two layers,
and telling them apart is the single most important thing in the panel.

**The chat list** (`🗂 Чаты`) shows the connected chats, most active first, each with its
message count for the last 24 hours. Waiting access requests get a row of their own on top,
`🙏 Запросы доступа · N`, and the page holds one chat fewer; `🚫 Отклонённые · N` shares the
bottom row with «⬅️ Панель». Right after «🔌 Отключить» the undo row `↩️ Вернуть` goes on top
too, and that page holds one chat fewer again (§A1.2), so the keyboard never grows; ▶ and ◀
from there keep that page size, so paging on skips no chat. With no chats at all the screen says how a chat gets here (add the bot
to a group, a request arrives) and still shows that row when a request is waiting.

**One chat** opens on a short screen: the chat's name and id, one status line (the bot on or
off there, and what calls it), a link that opens the chat, then six section buttons —
🗣 Поведение, 🧠 Память, 🎙 Голос и фото, 🎨 Стикеры, 😀 Реакции, 👋 Сообщество — plus
`📏 Правила · N` (the rules list, §A3.1) and `🔌 Отключить бота` (remove the chat from the
whitelist, §A1.2; shown only while the bot is on there). Every section has exactly one back button, to this screen; this screen's
back button goes to the chat list.

| Section | Switches | Shown, not switched here |
|---|---|---|
| 🗣 Поведение | stay out unless relevant, abuse protection, read links | trigger words, how often it joins in, reply language; `📝 Инструкция` opens the per-chat instruction editor |
| 🧠 Память | save messages, search the chat archive, remember its conversations, knowledge base | `👥 Организаторы · N` opens the organizers list (§A4.1) |
| 🎙 Голос и фото | voice, video notes, collapse transcripts, understand pictures | — |
| 🎨 Стикеры | learn from stickers, sticker for a sticker, sticker for a photo | the three percentages; `🔞 Откровенность` (§A2.3) |
| 😀 Реакции | reactions in the chat, keep people's reactions | whether the bot is a chat admin (§A2.2) |
| 👋 Сообщество | community mode and its five parts, advice | — |

- **A switch flips in place and says what changed** (`✓ Включено: База знаний` above the
  section). It flips what the chat *currently behaves like* (the merged value) and writes
  that as the chat's own value, so a chat inheriting "on" turns off on the first tap. The
  flip happens inside the database write, so a double tap is two changes, never one lost.
- **The knowledge base and both reaction switches are ordinary switches now**, in 🧠 Память
  and 😀 Реакции. They used to be links to separate pickers; those pickers are gone.
- **Values that are shown but not switched** (words, percentages, intervals, language) change
  only in the database for now; the section says so in one line.
- **`· общее`** (`· all`) marks a value the chat takes from the global settings. Only the 21
  options whose per-chat column can be empty can show it. The other 13 are older columns that
  always hold a value of their own, so they can never honestly claim to inherit, and for the
  same reason they are absent from the global screen. The knowledge base says it in the
  section's footnote instead (`База знаний включена в общих настройках.`): its button shares
  a row with `👥 Организаторы · N`, and the marker does not fit half a phone row.
- **A chat that has no settings row** (it left, or was never connected) opens the chat list
  instead, with a line saying nothing changed. A switch or an explicitness value tapped or
  typed for such a chat lands there too; nothing is written.

`/panel` is the shortcut to one chat: `/panel` with a `t.me/c/…` link, an `@username`, a
numeric id or part of the title. One match opens the chat; several show up to four of them
(and say so when there are more, so the admin narrows the title) with `🗂 Все чаты`; none says
so. Only connected chats are found. `/panel` with nothing after it explains the forms.

### A2.2 😀 Reactions

A section of one chat (§A2.1), no longer a separate picker.

Two switches: **reactions in the chat** (whether the bot records and uses reactions at all)
and **keep people's reactions** (whether reaction events are stored). The second is shown
only while the first is on.

The section also carries a live status line — *is the bot an administrator in that chat?* —
checked against Telegram on every visit, never cached. This matters because Telegram simply
does not deliver reaction updates to a non-admin bot and raises no error while doing so: the
module would look enabled and do nothing. Turning reactions on while the bot is not an admin
pops an immediate warning (the switch still goes on), and the status line repeats it on every
visit.

### A2.3 🔞 Sticker explicitness

`🔞 Откровенность` in 🎨 Стикеры shows the current level with the nearest preset name, or the
bare number for a value of the chat's own. Tapping it offers five presets — Строго 0,
Скромно 0.25, Обычно 0.5, Смелее 0.75, Всё можно 1 — that save on a tap and return to the
section with a notice, and `✏️ Своё число`, which asks for a number between `0.0` and `1.0`
(a comma works as the decimal point). An out-of-range or non-numeric value is *rejected and
asked again* in one new message, never clamped. Commands still work while the question is
open, and it carries `✖️ Отмена` — the way out, since an invalid value keeps it open. Only the
newest question keeps its buttons. Going anywhere else in the panel — a button on an older
message, `/panel`, or another screen opening a chat — closes the question too, so a later
message is not taken for a value.

### A2.4 👋 Community

While community mode is off, the section shows only the mode switch and the advice switch
(advice works without the mode). Turning the mode on brings back newcomer, welcome back,
farewell, departures and joke. A tap on one of those from an older screen, made while the
mode is off, changes nothing and says so; the same holds for the reactions history switch
while reactions are off.

---

## A3 — Rules

### A3.1 Rules

A small per-chat rules engine. 📏 Правила on a chat's screen opens that chat's rules, three a page.

- **One switch for all of the chat's rules** sits at the top: «✅ Все правила включены» /
  «⚪ Правила выключены». It is the same `rules_enabled` value as on the chat settings panel.
  While the chat has no value of its own the label ends in «· общее» and the global default
  applies; a tap writes the chat's own value, the opposite of what the chat was doing.
- The mode (how several matching rules combine) is a line of text. A rule's weight shows in its
  row only under a weight mode, the only place it means anything.
- A row shows ✅/⏸ and the rule's name and opens the rule's card: what sets it off and what it
  does in words, how often it fired, and **🧩 Как записано (JSON)**, which shows the rule as
  the bot stores it — its `type`, its config, and `weight` / `mandatory` when they are not the
  defaults — under one line saying what that is: the lines above give the gist of the same rule
  in words, a rule cannot be edited, and a new one can be made from such text through
  «➕ Добавить» → «🧩 Настроить в JSON»; pasted there unchanged, it makes the same rule.
  **🧩 Скрыть запись (JSON)** hides it again.
- **On the card**: ⏸ На паузу / ▶ Возобновить, and 🗑 Удалить, which asks first and cannot be
  undone. While the chat's rules are off the card says so and carries the same switch.
- **➕ Новое правило** builds a rule with buttons:
  - what sets it off — words (typed, by comma or line, up to 20 words of up to 64 characters,
    then "inside a message" or "the word alone"), a person (picked from the chat's most active
    writers; the rule fires on each of their messages), stickers in the chat a minute (5, 10, 20)
    or messages from one person a minute (10, 20, 40);
  - what it does — tell the admins, warn (a ready text or a typed one), reply with a typed text
    (`{first_name}`, `{username}` and `{rule_name}` are filled in), or react (a ready emoji or a
    typed one; Telegram accepts only its own reaction set). A typed text is one message, at most
    4096 characters.
  The new rule opens on its card with «✓ Правило создано». A refused answer says why and keeps
  the question open; ✖️ Отмена leaves it.
- **🧩 Настроить в JSON** in the builder takes the whole rule as a JSON object, for what the buttons do not
  cover (regular expressions, exclusions, weight). The type goes in a `type` field, `weight` and
  `mandatory` are read out of the object. An unknown type or action, a reaction Telegram
  would refuse, and a keyword rule without a `keywords` list of words, are refused here —
  otherwise the rule would be created and silently never act.
  The rest of the config is not checked. Cancel returns to the builder's first step.

What a rule does in the chat:

- **Tell the admins** sends each admin a card in the panel's language: the chat, the author, what
  matched and the start of the message, with 📏 Открыть правило, which sends the rule's card as a
  new message so the alert stays as the record.
- **Warn** without a text of its own posts «⚠️ Сработало автоматическое правило чата.»
- **A sticker rule** counts stickers in the whole chat, fires only on a sticker that brings the
  count to the limit, and then rests for its interval: one flood, one action.

---

## A4 — Knowledge base organizers

### A4.1 👥 Organizers

Reached from a chat's **🧠 Память** section (§A2), which also holds the knowledge base's
on/off switch; **⬅️ Память** goes back there. There is no separate knowledge-base menu or
chat picker any more.

**Organizers** are the non-admin users allowed to write facts with `/remember` in that chat.
Facts saved by an organizer carry a lower authority than facts saved by a bot admin, which is
what decides who wins when two facts contradict.

The screen lists them three per page, each as **👤 {name}**; someone Telegram says is not in
the group carries « · не в чате», explained in a line under the list. A tap on a name opens
that person's card and changes nothing. (Until 2026-11 the same tap removed the person at
once, which is the mistake the card exists to prevent.) An empty list says so, and that bot
admins can always save facts.

**The card** («👤 {name} · {chat}») shows the person's name as plain text, their `@username`
when Telegram gives one right now, the id, what Telegram says about them right now (one
`getChatMember`: in the chat, not in the chat, never joined, removed and banned, or «Telegram
не ответил») and what an organizer can do — for someone outside the group, as something they
can do once inside («Сохранять факты командой /remember сможет, когда будет в чате.»). For
someone who never joined, Telegram's membership answer carries no name; the bot then asks
for their private chat with it (`getChat`), which names anyone who has ever opened the bot,
and only after that falls back to the name it remembered. It asks the same when Telegram
answers the membership question with any other refusal (for instance after the bot was
removed from the group), but not when Telegram did not answer at all. When no name was ever known, the
title reads «участник» with the id in a copyable code span instead of a bare number
(Telegram would turn that into a dead phone link), and the body does not repeat the name or
the id. More generally, the body has a name line only when it adds something to the title:
the first and last name when the title shows a username, and a username Telegram gave just
now. Its buttons, at most four plus the way back:

- **💬 Написать лично**, first on every card: it opens the private chat with the person.
  It goes to `t.me/{username}` for a valid username (4 to 32 letters, digits or `_`) that
  Telegram gave just now, and otherwise by id (`tg://openmessage?user_id=…`), which on the
  phone opened the chat even with someone the bot had never seen (2026-11-07; Telegram
  Desktop and Web not tried). A username the bot remembered from earlier is never the button's
  target and gets no `@username` beside the name: it may since have been freed and taken by
  someone else. Where it is the name, the card and invite titles show it as «участник» with
  the `@username` in a code span, so that Telegram does not turn it into a mention of
  whoever holds it now (inferred from how Telegram treats bot text, not measured on this
  card). The notices after a remove or an undo still show it as plain text. The name itself is not a link: Telegram silently drops a `tg://user`
  mention for people the bot cannot see, so it worked on some cards and not others. Should
  Telegram ever refuse the button, the card arrives without it and the bot logs a warning.
- **✉️ Пригласить в чат**, only for someone who is not in the chat or never joined. A banned
  person gets a line instead: a link will not let them in until the ban is lifted in the
  group's settings. When Telegram did not answer, there is no invite either: the person may
  well be inside.
- **✖️ Убрать из организаторов** removes at once — no confirmation — and returns to the list
  with «✓ {name} больше не организатор» and **↩️ Вернуть**, which puts them back where they
  were.
- **⬅️ Организаторы** goes back to the page the person is on.

**The invite** («✉️ Приглашение · {name}») makes a fresh link for one person, valid for seven
days, and shows it in a copyable line. It is named «Организатор · {name}» (cut to Telegram's 32
characters), so the group's own list of invite links says what it was for.

- **📤 Переслать** opens Telegram's share sheet with the link: the admin sends it to the
  person from their own account, which always works.
- **🤖 Отправить от бота** makes another fresh link and sends it to the person as a private
  message from the bot, with the chat's name. Telegram lets a bot write first only to someone
  who has opened a private chat with it and not blocked it; otherwise (including someone the
  bot has never met) the screen says the bot cannot write first, names both causes, and
  points to **📤 Переслать** — the link shown still works. Any other failure says Telegram
  did not deliver it, and the link shown still works.
- **⬅️ Организатор** goes back to the card.

When the bot has no right to invite members (it is not a group admin, or lacks «Пригласительные
ссылки»), the card comes back with a line saying that right is granted in the group's
administrator settings and nothing else changed. The invite re-checks the person each time:
if they joined meanwhile, the card says they are already in the chat and no link is made. A
card or invite for someone removed from the organizers meanwhile returns to the list with
«Этого человека уже нет среди организаторов.»

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

**🎨 Стикеры** opens the pack list, four packs per page, most-learned first. Each row reads
`<pack title> (N из M)`: N stickers of the pack are analysed, M is the pack's size in
Telegram. Opening a pack lists its stickers, most used first, as
`<emoji> <short description> · <uses>`; a sticker never analysed says *Ещё не разобран*, a
failed one *Ошибка разбора*. **⬅️ Паки** goes back to the list.

Pack buttons carry a 12-character key derived from the pack name instead of the name itself
(a pack name may be 64 characters, more than a button can carry). Buttons sent before the
change still open their pack.

### A5.2 Sticker card

Tapping a sticker replaces the list with the sticker itself and, under it, its card:

- a title — the character, or the first clause of the description — then the full description;
- emotion, character, when it fits, how often it was used (and how often by the bot);
- the explicitness line: `Откровенность: 0.30 · пройдёт в чате с допуском 0.50` (or
  *не пройдёт*), measured against the global default tolerance; `(вручную)` when an admin set it;
  *не оценена · бот его не отправит* when there is no score at all;
- admin notes; with *detailed* sticker notifications also tags, emoji, embedding and the id.

Buttons: **🔁 Разобрать заново** | **🧹 Очистить**, **🔞 Откровенность**, and a back button
named after the pack (**⬅️ Паки** for a sticker whose pack is not registered). A sticker that
was never analysed shows only **🔁 Разобрать**. Leaving a card opened from a pack takes its
sticker message with it, so the DM does not collect orphaned stickers.

- **🔁 Разобрать заново** re-runs vision now. The card turns into "⏳ Разбираю стикер…"
  with no buttons, so a second tap cannot start a second run, then comes back with
  "✓ Разбор обновлён" — or an error that says why: the download failed, the model did not
  answer, the model refused because of the content, or the answer was empty. The error offers
  **🔁 Повторить** and **⬅️ Стикер**; a content refusal also offers **🔞 Откровенность**,
  because the score can still be set by hand.
- **🧹 Очистить** asks first, in the same message. **⬅️ Не очищать** returns to the card;
  confirming wipes the vision-generated fields (description, emotion, character, contexts)
  and shows the card with "✓ Разбор очищен". **Admin notes and usage counters are kept.**
- **🔞 Откровенность** opens the score screen: the current value and who set it, one-tap
  values `0.00 0.25 0.50 0.75 1.00` (the current one marked `•`), **✏️ Своё число**,
  **🔁 Пересчитать оценку** and **⬅️ Стикер**. A tap sets the value and returns to the card
  with "✓ Откровенность 0.75". *Своё число* asks for a number from 0.0 to 1.0 (a comma works
  too); the answer arrives as the card in a new message and the prompt loses its
  **✖️ Отмена**, so no stale cancel is left behind; a wrong number asks again.
  *Пересчитать оценку* drops a hand-set value and runs one fresh analysis — a sticker with no
  score is one the bot never sends, so the reset alone would silence it.

A sticker deleted meanwhile is not an error: the pack list opens with
"Этого стикера уже нет в базе." A pack that is gone does the same with its own line.

### A5.3 Checking a sticker by sending it

An admin can just send a sticker into the DM. If it is already in the catalogue, the bot
replies to it with the same card as above. If it is not, the bot says so and offers a single
**🔍 Разобрать** button — analysis is always an explicit, visible action here, never a silent
background learn. (The catalogue's automatic learning path deliberately does not run for an
admin's own DM.) The result replaces the button's message: the card with "✓ Стикер разобран",
or the same per-reason error with **🔁 Повторить**. If the sticker the button belongs to is no
longer there, the bot asks to send it again.

If the analysis succeeds, the sticker's pack is registered too, so it shows up in the pack
browser like any automatically learned sticker.

### A5.4 Correcting a description by replying

Every sticker card and every new-sticker notification ends with an invitation to reply.
A text reply to such a message — or to the sticker the bot sent with it — is fed, together with the
existing description, to the AI, which merges the two into one description. The bot answers
with "✓ Описание обновлено" and the new text, or "⚠️ Описание не обновлено" and the reason:
the merge failed and the note was saved (re-run the analysis to use it), the model refused
because of the content (the score can be set by hand), or something else failed (try again).

Once a card has turned into a list, a reply to that message is no longer a correction.
Replying to *anything else* in the admin DM does nothing — the reply is consumed by this
handler and quietly dropped when no sticker can be identified.

### A5.5 The new-sticker notification

With sticker notifications on, every sticker learned in a chat arrives as the sticker plus a
card replying to it: "Новый стикер:" with the description, the pack and the explicitness line,
and the same **🔁 Разобрать | 🧹 Очистить**, **🔞 Откровенность** buttons as a catalogue card.
It has no back button — it is not part of the panel's tree. Its buttons work exactly like the
card's, and navigating from it into the catalogue leaves the sticker it replies to in place.

---

## A6 — Entry and cards

Everything here happens outside the panel: what a person meets on `/start` and `/help`, the
group-side commands, and the cards the bot sends admins unprompted. Every screen follows the
chat's language; every card follows the admins' panel language (🌐 Language, §A1.5).

### A6.1 `/start`, `/help` and the way into the panel

| Where, who | What they get |
|---|---|
| DM, anyone | 🤖 **Чат-компаньон** — one paragraph on what the bot does, **➕ Добавить в свой чат** (Telegram's own "pick a group" link) and **📖 Что я умею** |
| DM, bot admin | 🤖 header, "the panel opens with the button or /admin", **🛠 Панель** and **📖 Что я умею** |
| DM, bot admin, link `t.me/<bot>?start=admin` | Opens the panel's main menu directly. For anyone else that link is a plain `/start` |
| DM, a stranger whose DM is not approved | **Nothing**, deliberately unchanged: the whitelist gate stops the update before any command runs, records an attempt and sends the admins an access card (§A6.3, same 30-minute cooldown). The stranger gets no reply |
| Group, `/start` | The group help below — `/start` means nothing different in a group |
| Group, `/help` | 🤖 header, how to call the bot (one of the chat's trigger words, in the chat's script, or a reply), then **only the features this chat has on**: voice/video-note transcription, memory, summaries, `/kb`, `/callme`. Buttons **📋 Саммари 100 / 500** (only when the chat saves messages) and **✖️ Закрыть**; they belong to whoever asked |
| DM, `/help` or 📖 | 📖 **Что я умею** with **⬅️ Старт** back to the greeting |

**📋 Саммари** posts a *new* summary under the help; the help itself stays, so it can be pressed
again once that summary is out. A press while a summary for the same help is still being made
gets a short "Уже готовлю саммари…" toast and starts nothing, so a double tap is one paid call,
not two. A button whose data can no longer be read answers with a short toast in the chat's
language ("Эта кнопка устарела…") — these buttons are in front of ordinary members, so they
never show the panel's stale screen.

**`/admin` or `/settings` in a group**, sent by a bot admin, answers with one line —
"Панель открывается в личке" — and a **🛠 Открыть панель** link that opens the DM on the panel.
It needs the panel's own `/admin` handler to be limited to DMs, which is part of the panel's
change (§A1); until both are in, the panel's handler takes the command first and stays silent
in groups.

### A6.2 The command menu

| Who | Menu |
|---|---|
| Group chats | `/help`, `/summary`, `/kb`, `/callme` |
| Private chats | `/start`, `/help` |
| A bot admin's DM | `/start`, `/help`, `/kb`, `/admin`, `/cancel` |

`/settings`, `/costs`, `/summary500` and `/remember` still work and are not advertised. The menu
lives on Telegram's side and is pushed at startup, so **a change here reaches every user on the
next deploy**.

### A6.3 The access-request card

A chat reaches the panel in one of two ways, and both produce the same card with
**👍 Одобрить / ❌ Отклонить**.

**Someone writes** in a chat the bot has not been approved for. The bot stays silent, records
an attempt (chat, user, first 200 characters of the message) and DMs every admin a card headed
🙏 **Новый чат просит доступ**, quoting the first 100 characters.

**The bot is added to a group.** The membership update itself files the request, and the card
is headed ➕ **Бота добавили в новый чат**, because there is no message to go looking for. The
group also gets one line from the bot — "Привет! Я начну работать, когда админ бота одобрит этот
чат." — in the chat's language, so the person who added it is not left wondering. That line is
not sent to channels, to a chat that was rejected before, while the 30-minute card cooldown
for that chat is running, or when the request could not be filed at all (the bot cannot tell
whether that chat was rejected, so it promises nothing). A request filed with a hitch — the
admins got a card but its row or its buttons failed — still gets the line.

Three things deliberately do NOT produce a card: a promotion inside a chat the bot is already
in (member → administrator is not a new chat); re-adding the bot to a chat that is still
enabled; and anything happening in a **private chat** — Telegram sends the same membership
update when a user blocks or unblocks the bot, and an unblock looks exactly like a join. A
stranger who actually writes still produces a card through the first path.

Re-adding the bot to a chat that was approved and then removed DOES file a fresh request: the
removal disabled the chat, and re-consent is intentional — otherwise anyone able to add the
bot could re-enable a chat with no admin in the loop.

To avoid flooding, at most one card per chat every **30 minutes**, shared across both paths —
so "added, then someone posted" is one card, not two. The cooldown is in memory, so a restart
clears it and a chat can end up with two pending rows; approving either one clears them all.

**After a tap** the card stays readable and gets a status line on top — ✓ *Одобрено*, or
✓ *Отклонено* with what that means — and the buttons that lead on: **⚙️ Настройки чата** and
**🔌 Отключить** side by side for the approved chat (the second asks the same question as the
chat's screen, §A1.2 — it is a way back, not an undo: what approving already set in motion stays,
§A1.2), **🚫 Отклонённые** for the rejected list. A card older than 48 hours keeps its
text and only has its buttons swapped. A second tap, or a tap on a card whose request was already
resolved from the panel, shows the state the request is actually in with a "Запрос уже решён"
toast — never a second action. The lists the request lands in, and what approving or rejecting
changes, are in §A1.2.

### A6.4 Health and spend cards

**Health.** When the scheduled check finds a problem, **every bot admin** gets a card:
🔥 **Сбой бота** (something critical) or ⚠️ **Перебои в работе бота**, with the time in UTC, one
line per problem — the provider's error and a 💡 hint under it when there is one — and the
message and fallback counts. **📊 Состояние** opens the health screen (§A1.3). An unchanged
problem is repeated as a reminder ("…всё ещё идут") at most every 6 hours, and when it clears
for good the admins get 💚 **Бот снова здоров** naming what stopped failing. The whole card is
in the panel language, problem lines and hints included: "База данных недоступна: нет
соединения", "Запасной ИИ включался 7 раз за 15 минут", "Вызовы ИИ не прошли за 15 минут:
расшифровка ×5 (gemini), с 03.11 12:00 UTC (2 ч 5 мин)". Only the provider's own error text under a line
stays as the provider wrote it.

**Spend.** When the day's AI spend passes the daily limit (`bot_config.daily_spend_limit_usd`;
no limit set means no card), every bot admin gets
💰 **Дневной лимит расходов превышен** — how much, against what limit, and that the bot keeps
answering — with **💰 Расходы** for the 24-hour view. **Once per UTC day**, however many replies
follow. Nothing about spend is posted into group chats any more.
