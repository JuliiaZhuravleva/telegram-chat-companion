# Admin DM — Implementation Reference

Companion to [admin-dm-guide.md](admin-dm-guide.md), which describes *what* the admin surface
does. This document describes *how* it is wired: routers, permission checks, callback
grammar, state machines, storage, and the traps that have already bitten.

**How it is organised.** The same parts as the guide: **F** is the machinery every screen
shares (routers, permissions, callback grammar, states, the screen system, storage, traps,
tests), **A1–A6** hold what belongs to one area of the panel. Sections are numbered within
their part (§F.6, §A2.1), so an area adds to its own part without renumbering anyone else's.

---

## F — Shared machinery

### F.1 Module map

| File | Owns |
|---|---|
| [src/bot/handlers/admin.py](../src/bot/handlers/admin.py) | `/admin`, `/settings`, `/costs`, main menu, whitelist, statistics, costs + OpenAI verification, health, notifications, language, close |
| [src/bot/handlers/access_card.py](../src/bot/handlers/access_card.py) | Approve / reject from the access card (`adm_approve:`, `adm_reject:`) and the `approve_attempt` / `reject_attempt` operations the whitelist list shares |
| [src/bot/handlers/admin_chat_panel.py](../src/bot/handlers/admin_chat_panel.py) | Per-chat settings panel (`adm_pnl*`), including the `tolerance_level` FSM flow |
| [src/bot/handlers/admin_defaults.py](../src/bot/handlers/admin_defaults.py) | Global-settings screen (`adm_defs*`) |
| [src/bot/handlers/admin_kb.py](../src/bot/handlers/admin_kb.py) | Knowledge-base organizers of one chat (`adm_kb_org*`, `adm_kb_orgs:`) |
| [src/bot/handlers/admin_sticker.py](../src/bot/handlers/admin_sticker.py) | Sticker browser, re-analysis, DM sticker check, description-merge reply (`adm_stk*`) |
| [src/bot/handlers/rules.py](../src/bot/handlers/rules.py) | Rules engine UI (`adm_rules:`, `ar_*`) |
| [src/bot/keyboards/admin_kb.py](../src/bot/keyboards/admin_kb.py) | The add-organizer prompt's reply keyboard and the labels it sends back. Every inline keyboard of the panel is built from a screen (§F.6) |
| [src/bot/keyboards/kb.py](../src/bot/keyboards/kb.py) | `/remember` undo and `/kb` paging — knowledge-base keyboards that are *not* panel screens |
| [src/bot/screens/](../src/bot/screens/) | The screen system every redesigned screen uses: model, delivery, callback table, stale fallback, rules lint, sample registry (§F.6) |
| [src/bot/access_requests.py](../src/bot/access_requests.py) | Files an attempt and sends the access card, for both the middleware and `my_chat_member` (§A6.2) |
| [src/bot/settings_fields.py](../src/bot/settings_fields.py) | The 26-field registry shared by the chat panel and the defaults screen |
| [src/bot/command_registry.py](../src/bot/command_registry.py) | Which slash commands exist, where Telegram advertises them, and the pure code↔registry audit |
| [src/bot/commands.py](../src/bot/commands.py) | Pushes that registry to Telegram, deletes stale per-admin scopes, reads back and diffs |
| [src/bot/filters/admin.py](../src/bot/filters/admin.py) | `IsAdmin` message filter |
| [src/bot/utils.py](../src/bot/utils.py) | `check_admin_direct`, `is_bot_chat_admin`, `resolve_display_name`, `safe_edit_text` |
| [src/bot/middleware/access_control.py](../src/bot/middleware/access_control.py) | Whitelist gate, `is_admin` injection, unauthorized notification |
| [src/database/repositories/admin.py](../src/database/repositories/admin.py) | Attempts, stats, admin language, notification settings |

`scripts/dump_screens.py` (§F.6) is the one script that reads the screen system offline.

### F.2 Router order

`handlers/__init__.py` includes routers in this order, and the order is load-bearing —
in aiogram the first handler whose filters match **consumes** the update:

```
fsm_cancel → admin_sticker → admin_kb → admin_chat_panel → admin_defaults
→ access_card → admin → rules → stale → commands → callbacks → chat_events → community
→ reactions → media → message
```

`tests/unit/test_routes.py::test_router_include_order` pins this list, so a change to it is a
change to that test too.

Consequences that are easy to break:

- `admin_sticker` precedes `media`, which is why an admin's DM sticker reaches the catalogue
  check ([admin_sticker.py:985](../src/bot/handlers/admin_sticker.py#L985)) instead of
  `media.py`'s silent auto-learn — unless an open prompt reads the sticker (§F.5).
- `admin_chat_panel` precedes `commands`, which is why the tolerance FSM handler carries
  `~F.text.startswith("/")` — without it the open state would swallow every admin command
  until a valid float arrived.
- `fsm_cancel` precedes **everything**, which is the only reason `/cancel` works at all.
  Measured (TD-049): appended last instead, `/cancel` sent while `awaiting_rule_config`
  is open is consumed by `rules` and answered «Невалидный JSON» — the cancel handler is
  registered, loaded and never reached. Note the slash guards that make commands escape an
  FSM prompt must test `caption` as well as `text`: aiogram resolves a command from
  `text or caption`, so `/help` under a photo is a real command that `~F.text.startswith("/")`
  lets straight through. `/cancel` itself carries one content filter for the same family of
  reason — `F.forward_origin.is_(None)`. A FORWARDED message reading "/cancel" is content, not
  a command, and without the guard the escape hatch swallowed exactly the input `admin_kb`'s
  organizer handler carves forwards out for.
- `stale` comes right after `rules`, the last router that owns an `adm_` / `ar_` prefix, and
  before `commands`. Every panel router therefore gets its chance first, and a panel payload
  that none of them takes is answered by the catch-all (§F.6) instead of spinning. A new
  router that owns a panel prefix must be included **above** `stale`: below it, every one of
  its buttons draws the hub with the out-of-date line.

### F.3 Permission checks — three shapes, all reading `bot_config.admin_ids`

There is no single admin gate. Which one a handler uses depends on what it can inject:

| Mechanism | Where | Notes |
|---|---|---|
| `IsAdmin()` filter | `/admin`, `/settings`, `/costs`, DM sticker handlers, the stale catch-all | Uses middleware-injected `is_admin` when present, otherwise queries `bot_config` through the Dishka container. Filters run *before* inner middleware, hence the fallback |
| `_guard_admin(kwargs, callback)` | `admin.py`, `rules.py` (`_check_admin`) | Reads `data["is_admin"]` set by `AccessControlMiddleware`, plus a private-chat check; logs `admin_access_denied` for the audit trail |
| `check_admin_direct(bot_config_repo, user_id)` | `admin_kb`, `admin_chat_panel`, `admin_defaults`, `admin_sticker` callbacks | Direct repository read, for handlers that already take `BotConfigRepository` via `FromDishka` and so cannot use the filter's injected flag |

All of them funnel into `parse_admin_ids()` over the `admin_ids` value. **Every** callback
handler additionally verifies `callback.message.chat.type == "private"`.

`AccessControlMiddleware` is what lets an admin DM through a disabled chat row
([access_control.py:58-61](../src/bot/middleware/access_control.py#L58-L61)) — and note the
middleware only understands `Message` and `CallbackQuery`; other update types are ungated by
it (see CLAUDE.md).

### F.4 Callback-data grammar

Pattern: `adm_{action}:{lang}:{param...}`, language embedded so screens are stateless.
Telegram's 64-byte limit is why settings fields are addressed by a ≤4-char `code`
(`settings_fields.py`) rather than by column name.

Prefixes **must** be matched with the trailing colon (`F.data.startswith("adm_wl_rm:")`),
otherwise `adm_wl_rm` also matches `adm_wl_rm_ask`. With the colon, a prefix that is a
strict prefix of a sibling (`adm_lang` / `adm_lang_set`, `adm_costs` / `adm_costs_verify`,
`adm_defs` / `adm_defs_sec`) needs no second check of `parts[0]`.

The table below is the reader's map. The registry is `ROUTES` in
[src/bot/screens/routes.py](../src/bot/screens/routes.py) (§F.6): every prefix a handler
filters on must be in it, which `test_routes.py` checks against the source.

| Prefix | Params | Handler |
|---|---|---|
| `adm_menu:` | `lang` | the hub |
| `adm_lang:` / `adm_lang_set:` | `lang` / `lang:new_lang` | language screen / save, then general settings in the new language |
| `adm_close:` | `lang` | delete panel message |
| `adm_wl:` / `adm_wl_chats:` | — | **retired** (the old whitelist submenu and its chat list); answered by the stale catch-all |
| `adm_wl_rm_ask:` / `adm_wl_rm:` | `lang:chat_id:page` | confirm / set `enabled=false`, then the chat list at `page` |
| `adm_wl_pending:` | `lang:page` | requests, one per page |
| `adm_wl_apr:` / `adm_wl_rej:` | `lang:attempt_id:page` | approve / reject from list |
| `adm_approve:` / `adm_reject:` | `lang:attempt_id` | approve / reject from the notification card (`access_card.py`); byte-compatible forever |
| `adm_wl_rejected:` | `lang:page` | rejected chats, one per page |
| `adm_wl_restore:` | `lang:attempt_id:page` | back to `pending` |
| `adm_wl_del_ask:` / `adm_wl_del:` | `lang:attempt_id:page` | confirm / hard delete |
| `adm_stats:` | `lang:period` | statistics (`1h`/`24h`/`7d`) |
| `adm_costs:` / `adm_costs_verify:` | `lang:period` | costs / OpenAI cross-check |
| `adm_health:` | `lang[:now]` | last recorded check; `now` (🔁 Обновить) runs one first |
| `adm_settings:` | `lang` | general settings |
| `adm_notif:` / `adm_nstk:` / `adm_ntog:` | `lang` / `lang` / `lang:type` | notifications menu / sticker cycle / bool toggle |
| `adm_pnl:` / `adm_pnl_menu:` | `lang:page` / `lang:chat_id` | chat picker / chat panel |
| `adm_pnl_tgl:` | `lang:chat_id:code` | generic bool toggle |
| `adm_pnl_tol:` / `adm_pnl_tolcancel:` | `lang:chat_id` | tolerance prompt / cancel |
| `adm_defs:` / `adm_defs_sec:` / `adm_defs_tgl:` | `lang:0` / `lang:section` / `lang:code` | the seven sections for all chats / one section / toggle, then that section again |
| `adm_kb:` / `adm_kb_menu:` / `adm_kb_toggle:` | — | retired: the KB picker and menu are gone, the switch lives in the chat's memory section |
| `adm_kb_orgs:` / `adm_kb_org_rm:` / `adm_kb_org_undo:` | `lang:chat_id:page` / `lang:chat_id:user_id` / `lang:chat_id:user_id:position` | organizer list / remove at once / put back at `position` |
| `adm_kb_org:` | `lang:chat_id:user_id` | one organizer's card (`org.card`); writes nothing. The list's name buttons carry it: until 2026-11 they carried `adm_kb_org_rm:`, which still removes |
| `adm_kb_org_inv:` / `adm_kb_org_dm:` | `lang:chat_id:user_id` | make a one-person, seven-day invite link and show it (`org.invite`) / make another and send it to the person from the bot. Both re-check that the person is an organizer and not in the chat |
| `adm_kb_org_list:` / `adm_kb_org_pick:` | `lang:chat_id:page` / `lang:chat_id:user_id` | participant list / add the tapped one |
| `adm_kb_org_add:` | `lang:chat_id` | add prompt (new message, reply keyboard). The failure answer's own `adm_kb_org_x:` is gone: its «Отмена» is the reply keyboard's, and an old button reaches the catch-all |
| `adm_react:` / `adm_react_menu:` / `adm_react_toggle:` | — | retired: the reactions switches are a section of the chat screen (§A2.2) |
| `adm_stk_sets:` / `adm_stk_pk:` / `adm_stk_view:` | `lang:page` / `lang:pack_key:page` / `lang:file_unique_id` | packs / pack by its 12-hex key / sticker + card as new messages |
| `adm_stk_set:` | `lang:set_name:page` | legacy pack button: still parsed, no longer emitted |
| `adm_stk_card:` / `adm_stk_score:` | `lang:file_unique_id` | card in place / explicitness screen |
| `adm_stk_reanalyze:` / `adm_stk_clr_ask:` / `adm_stk_clr:` | `lang:file_unique_id` | re-analyse / confirm clear / clear |
| `adm_stk_expset:` / `adm_stk_expedit:` / `adm_stk_expcancel:` / `adm_stk_expreset:` | `lang:file_unique_id:preset_index` / `lang:file_unique_id` | one-tap score / typed-score prompt / cancel it / re-score |
| `adm_stk_dmchk:` | `lang:file_unique_id` | analyse a DM-checked sticker, and its retry |
| `adm_rules:` | `lang:page` | rules chat list |
| `ar_list:` / `ar_view:` / `ar_tog:` / `ar_del_ask:` / `ar_del:` / `ar_add:` / `ar_type:` / `ar_cancel:` | see [rules.py](../src/bot/handlers/rules.py) module docstring | rules CRUD (`ar_cancel:` leaves the config prompt) |
| `noop` | — | inert buttons (section headers, page counters, status badges) |

### F.5 FSM states

`AdminStates` ([src/bot/states/admin.py](../src/bot/states/admin.py)):

Every state is a typed-input prompt: the admin's next message is read as its answer. The
last two columns are the two tables described under *Leaving a prompt* below.

| State | Set by | Answer read by | Own taps (`PROMPT_OWN_TAPS`) | Screens (`PROMPT_SCREENS`) |
|---|---|---|---|---|
| `awaiting_setting_value` | `adm_pnl_tol:` | `admin_chat_panel.handle_chat_panel_tolerance_input` | `adm_pnl_tol:`, `adm_pnl_tolcancel:` | `tol.in`, `tol.invalid` |
| `awaiting_system_prompt` | `adm_pnl_sp:` | `admin_chat_panel.handle_chat_panel_sp_input` | `adm_pnl_sp:`, `adm_pnl_spx:`, `adm_pnl_spok:`, `adm_pnl_spre:` | `sp.prompt`, `sp.preview`, `sp.refused`, `sp.redo` |
| `awaiting_kb_organizer` | `adm_kb_org_add:` (data: `kb_chat_id`, `kb_lang`; the prompt is a **new** message carrying a one-time reply keyboard with a `request_users` button, `request_id=REQUEST_IDS.ORG_PICK`) | `admin_kb.handle_kb_organizer_shared` (the `users_shared` service message, filtered on that `request_id` — a pick from another picker never reaches it — and matched in **any** state, registered **before** the reader, which would otherwise take a pick for an empty message and answer «not found»; without this state it writes nothing and answers stale), then `admin_kb.handle_kb_organizer_add_reply` for a forward or `@username` (clears state on entry, **before** the authority check; a failed lookup re-arms it and answers `org.fail`). The `🔍 Найти в Telegram` label arriving as *text* means the client cannot open the picker — answered with the fallback, state kept. A forwarded message whose text starts with `/` or equals a label is still an organizer add, not a command | `adm_kb_org_add:`, `adm_kb_org_list:`, `adm_kb_org_pick:`, `adm_kb_orgs:` | `org.add`, `org.fail`, `org.list` |
| `awaiting_sticker_score` | `adm_stk_expedit:` (data: `exp_file_unique_id`, `exp_lang`, `exp_prompt_id`; the prompt is the score screen edited in place) | `admin_sticker.handle_sticker_explicitness_input` (admin re-checked in the body; a valid number clears the state and sends the card as a new message, an invalid one re-prompts in a new message and moves `exp_prompt_id`; either way the answered prompt loses its keyboard) | `adm_stk_expedit:`, `adm_stk_expcancel:` | `stk.score_in` |
| `awaiting_rule_config` | `ar_cfg:`; `ar_type:` from old buttons | `rules.handle_rule_config_input` (`IsAdmin`-gated since TD-049) | every `ar_` tap | `rule.cfg`, `rule.cfg_bad` |
| `awaiting_rule_words` | `ar_b_type:` with the keywords kind | `rules.handle_rule_words_input` | every `ar_` tap | `rule.words` |
| `awaiting_rule_text` | `ar_b_act:` with a warning or a reply | `rules.handle_rule_text_input` | every `ar_` tap | `rule.text` |
| `awaiting_rule_react` | `ar_b_act:` with a typed reaction | `rules.handle_rule_react_input` (a sticker counts as its emoji) | every `ar_` tap | `rule.react`, `rule.react_in` |
| `awaiting_rule_who` | `ar_b_find:` (the person search: a reply keyboard, `REQUEST_IDS.RULE_WHO`) | `rules.handle_builder_found`, plus the handlers for its «Отмена» and for the label sent back as text | every `ar_` tap | `rule.who_find` |
| `awaiting_sticker_edit`, `awaiting_sticker` | nothing | — | none: any tap ends them | none |

`ar_` is a family: the middleware leaves every rules tap to `rules.py`, which ends its own
prompts (§A3).

The ways out of every prompt: `/cancel` (`fsm_cancel` is the first router, §F.2), a typed
`/command`, the prompt's own cancel button, and any tap that is not one of its own taps. Each
reader skips a `text` that starts with `/`, and the rules, sticker-score and organizer readers
skip such a `caption` too (§F.2). The tolerance reader does not: a photo captioned `/help`
there is read as a wrong number (TD-134). The instruction editor reads `text` only, so a photo
never reaches it. Every exit through
`admin_kb` clears the state, sends `ReplyKeyboardRemove` on a line of its own and then the
organizer list as a new message (a message cannot carry both keyboards).

#### Leaving a prompt: `PROMPT_OWN_TAPS`, `PROMPT_SCREENS` and `prompt_exit`

The panel is buttons on many messages, so the admin can leave a prompt by tapping any older
one. Each area used to close its prompts from its own handlers only. A tap in another area left
the prompt armed, and the next text — maybe hours later, meant for something else — became a
tolerance value, a line of a chat's instruction, or a rule. Two tables and a middleware fix
that. Both tables live next to the states in
[src/bot/states/admin.py](../src/bot/states/admin.py), keyed by the state string:

- **`PROMPT_OWN_TAPS`**: state → the callback prefixes that keep the prompt open (`is_own_tap`
  matches by prefix). An entry that ends in `_`, like `ar_`, is a family of prefixes. An empty
  row means every tap ends the prompt.
- **`PROMPT_SCREENS`**: state → the screen ids the prompt shows.
- **`note_screen(message, screen_id, text, *, editable=True)`** records which message holds the
  open prompt. `show_markup` calls it on every delivery; `admin_chat_panel` calls it by hand
  for the instruction editor's replies it sends without `show`: the last piece of a split
  screen (`_send_split`) and the refusal (`_reply_refused`). A prompt screen becomes the
  chat's record (`PromptMessage`: the ids, the screen id, its title line, `editable`). Any other
  screen delivered into that same message drops the record. `editable` is false when the
  screen was sent with a reply keyboard. The record is in process memory, one per chat, like
  the FSM's `MemoryStorage`, so a restart loses both together.
- **`PromptExitMiddleware`** ([src/bot/middleware/prompt_exit.py](../src/bot/middleware/prompt_exit.py))
  is an outer middleware on the main router's `callback_query`, registered in
  `handlers/__init__.py` before any router is included. It runs once per tap, before any
  filter. When the state has a row in `PROMPT_OWN_TAPS` and the payload is not one of its own
  taps (and not `noop`), it clears the state, sets `data["raw_state"]` to `None` (aiogram read
  the state before the middleware, and a `StateFilter` further down must see it gone) and logs
  `Prompt left by a tap elsewhere` with the state and the action. The tap's handler then runs
  with no state.

The prompt's message would still be asking for input. So the middleware also closes it:

- It takes the record (`take_prompt_message`) **before** the handler runs, so a prompt that the
  handler opens records its own message and is not closed.
- **After** the handler, in a `finally`, it edits the recorded message to the screen's title
  and «Ввод закрыт.» («Input closed.»), in italics, with no buttons, and logs
  `prompt_message_closed`. The language is the tapped payload's second field.
- The title (`title_line`) is the screen's first non-empty line. A line without `<b>` gives no
  title: that is an error reply, so its closed form is «Ввод закрыт.» alone and does not repeat
  «… отправьте ещё раз». A bold line whose tags do not close on it, or that is longer than 200
  characters, is sent as escaped plain text, cut to 200.

It leaves the message as it is when:

- the tapped message **is** the prompt's message — its handler is drawing a new screen there;
- the record shows a screen of a different prompt (`take_prompt_message` checks it against
  `PROMPT_SCREENS[state]`);
- the message carries a **reply keyboard** (`org.add`, `rule.who_find`): Telegram cannot edit
  it, so the middleware logs `prompt_message_left_open` with `reason="reply keyboard"`;
- the edit fails (deleted, too old, Telegram unreachable): `prompt_message_left_open` with the
  error as the reason. «message is not modified» is swallowed by `safe_edit_text`.

The organizer prompt's reply keyboard is not taken away here. That needs a message of its own,
and one more message on every tap that leaves the prompt would be noise. It stays until its next
use, and every use removes it.

**The invariant for a new state.** A new `AdminStates` state needs a row in `PROMPT_OWN_TAPS`
**and** a row in `PROMPT_SCREENS`. If the bot arms it (`set_state(AdminStates.<name>)`
anywhere in `src`), its `PROMPT_SCREENS` row must not be empty, and every screen in it needs a
registered sample. It also needs a decision about stickers (next section) and a row in
`_STICKER_TAKER` in `tests/unit/test_routes.py`. What fails when a row is missing (measured on a
copy of the tree, 2026-11-07):

| Mutation | Fails |
|---|---|
| a new state with no row anywhere | `test_every_prompt_state_has_an_allowlist`, `test_every_prompt_state_lists_its_screens` (the table's keys against `AdminStates`), `test_an_allowlist_names_routes[<state>]` (`KeyError`), `test_a_sticker_in_the_dm_goes_to_whoever_reads_it` («a state without a row») |
| an armed state's `PROMPT_SCREENS` row emptied | `test_every_state_the_bot_sets_shows_a_screen` (it greps `src` for `set_state(AdminStates.…)`) |

Two more guards hold the rows' contents: `test_an_allowlist_names_routes` (every own tap is a
routed prefix, or a family that covers some) and `test_a_prompt_screen_does_not_end_its_prompt`
(every button on a prompt's sample screens is one of its own taps). The middleware's behaviour
is in `test_routes.py` (the state) and `tests/unit/test_prompt_message_closed.py` (the message).

No test checks that a `PROMPT_SCREENS` row names the screen the handler really shows, or that
a prompt is delivered through `show` / `note_screen` at all. A prompt sent with a bare
`message.answer` still has its state ended, but its message is never closed.

#### A sticker while a prompt is open: `STICKER_ANSWERS`

An admin's sticker in the DM goes to the stickers area's catalogue check
(`admin_sticker.handle_admin_sticker_check`). `admin_sticker` is included before every other
panel router and before `media` (§F.2), so without a guard that handler would also take a
sticker meant as a prompt's answer. `STICKER_ANSWERS` in `states/admin.py` lists the prompts
that read a sticker; `takes_a_sticker(state, forwarded=…)` asks it, and the catalogue check's
filter `_no_prompt_takes_it` steps aside when it says yes. The filter reads `raw_state`, the
name `PromptExitMiddleware` clears.

| State | Takes | Why |
|---|---|---|
| `awaiting_rule_react` | any sticker | a sticker stands for its emoji: `rules.handle_rule_react_input` reads `sticker.emoji` and checks it like a typed one |
| `awaiting_sticker_score` | any sticker | its own handler answers that the number goes in as text. It is registered earlier in the same router, so it gets the sticker first anyway; the row keeps the table honest |
| `awaiting_kb_organizer` | a **forwarded** sticker only | a forward names its sender, whatever it carries. A sticker the admin sends themself is neither a forward nor a `@username` |

Every other prompt leaves a sticker to the catalogue check, the same as with no prompt open, and
the prompt stays open (the check takes no FSM context). A new state that should read a sticker
needs a row here, and `_STICKER_TAKER` names the handler that takes one in every state.

Validation posture is *reject, do not clamp*: tolerance input outside `[0.0, 1.0]` and
non-JSON rule configs re-prompt rather than silently coercing.

`handle_admin_sticker_reply` carries `StateFilter(None)` so an active FSM dialog answered as a
*reply* is not eaten as a sticker-description correction.

Every `AdminStates` state is also cleared by the stale catch-all (§F.6) — a state that
belongs to any other `StatesGroup` is left alone.

### F.6 Screens

[src/bot/screens/](../src/bot/screens/) is what every redesigned screen is built on. An area
writes a builder that returns data; this package delivers it, routes its buttons, catches the
buttons nobody handles any more, and checks it against the panel's design rules.

#### A screen is data

`model.py` imports nothing from aiogram. A `Screen` holds an `id`, its HTML `text`, `rows` of
`Button`s, and optionally a reply keyboard (`reply`, rows of `ReplyButton`, one of which may
carry a `RequestUsers` picker), `remove_reply`, a `notice`, `link_preview` and an input
`placeholder`. A `Button` has exactly one action — `callback`, `url` or `copy_text`, refused
otherwise — plus a `kind` (`action`, `back`, `cancel`, `exit`, `pager`, `noop`), an optional
colour `style`, and an optional `goes_to` for when the target cannot be read off the payload.
Because a screen is plain data, the rules lint and the dump script read every screen with no
bot, database or event loop.

#### Delivery: `render.py`

`show(target, screen, *, toast=None, alert=False, answered=False, new_message=False)` is the
one delivery path, and the rules it applies are the ones every handler used to get wrong one
at a time:

- From a button (`CallbackQuery`): the query is answered **exactly once** — with `toast`, as an
  alert when `alert=True`, or not at all when the handler already answered it before a long
  operation (`answered=True`) — and the button's own message is edited in place. "Message is
  not modified" is tolerated. Any other refused edit, or an inaccessible message, falls back
  to a new message.
- From a `Message`, with `new_message=True`, or for a screen with a reply keyboard or
  `remove_reply`: always a new message, since a reply keyboard cannot be edited onto an
  existing one.
- Text longer than 4096 UTF-16 units **after parsing** is split with `split_html`, keyboard
  on the last piece, and logs `screen_too_long` — builders cap their lists, so reaching this
  means a real list outgrew its cap.

`show_markup(...)` is the same delivery for text and a keyboard that are not a `Screen` yet;
the stable entries below use it while they still render through today's keyboard functions.
`with_notice(text, notice=…)` puts an escaped `<i>✓ …</i>` line above the text after a change;
`notice_raw` takes trusted HTML instead and is what the stale line uses.

Shared builders: `back(target_id, callback, lang, *, name=None)` labels the button with its
destination's name from `routes.BACK_NAMES` (a destination with no fixed name, such as one
sticker pack, passes `name=`); `cancel(callback, lang)`; `pager(page, pages, make_cb)` returns
`◀ · n/m · ▶` wrapping around at both ends, or `None` for a single page; and
`page_slice(total, page, per_page)` returns `(page, pages, offset)` with an out-of-range page
clamped, because an old button can carry a page the list no longer has.

#### Stable entries and `data=`

Four functions open a screen from anywhere, whichever area owns it:

```python
show_hub(target, lang, *, notice=None, notice_raw=None, data)        # screens/hub.py
show_chat(target, lang, chat_id, *, notice=None, data)               # handlers/admin_chat_panel.py
show_chats(target, lang, page=0, *, notice=None, data)               # handlers/admin_chat_panel.py
show_rules(target, lang, chat_id, *, notice=None, data)              # handlers/rules.py
```

`data` is the calling handler's `**kwargs`. The entry resolves its own repositories from the
request's Dishka container in there (`screens.deps.resolve(data, Repo)`), so a caller never
needs to know what a screen reads. Their signatures are shared by every area; their bodies
belong to the area that owns the screen.

**The trap `deps.py` exists for** (measured through a real `Dispatcher` with
`setup_dishka(auto_inject=True)`): Dishka's inject wrapper takes `dishka_container` as a named
parameter of its own, so a handler that declares any `FromDishka[...]` parameter never sees
it in `**kwargs`, while a handler that declares none does. `data=kwargs` would then work from
`handle_menu` and fail from `handle_admin_command`, and a unit test calling either handler
directly with mocks passes both. `ContainerAliasMiddleware`, installed on the root router in
`handlers/__init__.py`, copies the container under a second key; `container_of(data)` reads
that first, then the original, and raises `LookupError` when neither is there — an error, not
an empty screen. `tests/unit/test_screens_entries.py` feeds `/admin` and the menu button
through a real dispatcher, with a control showing the command path breaks without the alias.

aiogram does the same to its own `state`: a handler that declares `state: FSMContext` gets it
as that parameter and not in `**kwargs`. An entry that has to leave a prompt (`show_rules`
leaves `awaiting_rule_config`, as the rules-list button does; `show_stale` leaves any
`AdminStates` prompt) would find no state and leave the prompt armed to swallow the admin's
next message. The same middleware copies `state` to `screen_state`, and `deps.state_of(data)`
reads either key and returns `None` when there is no context. The entries test proves it with
a handler that declares `state`, and a control without the alias where the prompt survives.

#### Callback table: `routes.py`

`ROUTES` maps every panel prefix to the screen it opens (an id, or a function of the payload's
parts) and a status:

| Status | Meaning |
|---|---|
| `kept` | Live today and stays. Old buttons with this prefix open the new screen. |
| `new` | Added by the redesign. Until its area lands the handler, a tap reaches the stale catch-all. |
| `legacy` | Live today, not named by the redesign; its owning area decides. |
| `compat` | Still parsed so old buttons work, never written again. |
| `retired` | The redesign drops it. Today's handler stays until its area removes it; after that an old button reaches the catch-all. Never written by `cb`. |

A prefix is kept wherever the screen's meaning survives, so the callback grammar — not the old
keyboard functions — is the compatibility surface for buttons already sitting in admins' DMs.

- `cb(prefix, *parts)` builds a payload and refuses an unknown prefix, a `compat` or `retired`
  one (that would mint a button stale on the day it is sent), and anything over 64 **bytes**.
- `parse(data, n)` returns the parts, or `None` when the shape is wrong.
- `target_of(callback, current_id)` says where a button goes, for the lint and the dump.
- `BACK_NAMES` names the destinations a back button can have; `REQUEST_IDS` holds the
  `request_users` ids; `LEGACY_SAMPLES` has one real-shaped payload for every prefix that can
  be sitting in a DM today.

`tests/unit/test_routes.py` checks that every prefix ends in `:` and none is a prefix of
another; that the worst-case payload of every writable prefix fits 64 bytes; that every prefix
a handler filters on, or a keyboard writes, is in `ROUTES`; that every payload an old button
can carry reaches a handler, and a live one (not the catch-all) for `kept` prefixes and for a
`legacy` prefix that some file still writes (a legacy prefix its area stops writing may lose
its handler); that garbled panel payloads do reach the catch-all; and the router order of §F.2.

Two ratchets in the same file hold the line while the areas work, each frozen at the state
the areas start from: no file may start writing a `retired` or `compat` prefix
(`RETIRED_WRITES_TODAY` lists the file/prefix pairs that still do; a write is
`callback_data=`, a screen `Button`'s `callback=`, or `cb(...)`), and no file may add an
untranslated `"Invalid data"`-style popup (`INVALID_ALERTS_TODAY`, counts per file). A payload
a live handler cannot parse is a stale button and owes the user `show_stale`, so those counts
only go down; an area that removes its pairs or popups may lower the numbers in the same change.
The walk it uses (`tests/unit/screen_routing.py`, built on `lint.first_handler`) evaluates
each handler's registered filters in aiogram's order over the real router tree without
calling anything; async filters such as `IsAdmin` count as passing.

#### Stale fallback: `stale.py`

The `stale` router (placement in §F.2) takes `adm_` / `ar_` payloads that no earlier router
took, and answers by who tapped and where:

- a bot admin in the private chat: `show_stale` clears the FSM state if it belongs to
  `AdminStates`, logs `stale_button`, and draws the hub in that message under the out-of-date
  line, with no popup;
- a bot admin anywhere else (a panel button forwarded or posted into a group, or on an inline
  message, which has no chat at all): an alert saying the panel opens in the private chat with
  the bot. "Access denied" would be false and send them looking for a permission problem. The
  filter is `~(F.message.chat.type == "private")`, not `!= "private"`: the latter cannot
  resolve without a message and would drop the inline case into "access denied";
- anyone else: an "access denied" alert.

The last two draw nothing.

What reaches it: `retired` prefixes once their area has removed the handler, `new` prefixes
whose area has not landed, and payloads no live handler's filters accept. A live handler whose filter matches has **consumed** the update
(aiogram), so the catch-all never sees it: a handler that cannot use its payload must call
`show_stale(callback, lang, data=kwargs)` itself instead of returning silently. "Object gone"
— a deleted rule, a request already resolved, a removed chat — is not stale: the owning handler
shows the parent screen with a notice that says what happened.

#### Design rules as code: `lint.py`

`check_screen(screen, sample, lang, *, route=None)` returns every rule the screen breaks as
`"R<n>: …"` strings. The rule ids are stable, because a sample's exemptions name them:

| Rule | Checks |
|---|---|
| R1 | at most 3 buttons per row |
| R2 | at most 5 rows, 6 when one is a pager |
| R3 | navigation by kind: a `panel` screen has one back, last in the last row; a `prompt` has one cancel; a `root` has one close and no back; a `card` or `group` message at most one back |
| R4 | a back button names its destination, never a bare "Назад" / "Back" |
| R5 | the first line is an emoji and a bold title |
| R6 | every callback fits 64 bytes |
| R7 | no callback writes a `retired` or `compat` prefix (read from `ROUTES`, always checked); every other callback reaches a live handler, not the catch-all (needs `route`) |
| R8 | Russian copy addresses the reader as «вы», with no singular imperatives or nagging words |
| R9 | no Cyrillic in English copy, no Latin words in Russian, outside user data |
| R10 | each label fits its share of a 390 px phone row |
| R11 | a toggle starts with its state glyph; a link never does |
| R12 | HTML tags are balanced and known; the text fits 4096 UTF-16 units after parsing |
| R13 | `custom_emoji` recognises the keyboard as a screen |

R10 measures a label with `label_width_px` against `budget(n)`, the widest text a row of `n`
buttons shows uncut: 342, 163.5 and 104 px. A label cut at run time — a chat title, a person's
name, a rule's words — is cut to `clip_room(n)`, which is `budget(n) - CLIP_MARGIN_PX` (2 px:
340, 161.5 and 102 px). `CLIP_MARGIN_PX` is the bound the measured table in the rules test holds
the estimator to; the worst label measured reads 0.2 px narrow. `clip_room` is the one source
of that width: the chat screens (`chat.fit_label`), the rules screens (`rules._room`) and the
organizer list (`orgs.NAME_PX = clip_room(1)`) all call it, and none keeps a margin of its own.
The sticker screens are the exception: a pack title on the pack list and a sticker's
description on a pack's page are cut by **character count** (`PACK_LABEL_CHARS` = 24,
`ROW_LABEL_CHARS` = 22 in [screens/stickers.py](../src/bot/screens/stickers.py)), not by width.
Ordinary titles fit (an all-capitals Russian title with `(120 из 120)` measures about 300 px of
342), but 24 wide letters in a row do not: `Ш` or `W` repeated measures 400–434 px.
`test_r10_a_clipped_label_uses_its_row` feeds each of the three a long name and checks the cut
label ends with «…» and lands between 20 px under the one-button budget and the budget itself,
so a clipper that drifts back to its own smaller width fails.

`route` maps a payload to the handler that would take it: the tests pass `first_match`, the
dump passes `first_handler` over `src.bot.handlers.router`. The retired/compat half of R7 reads
the table instead of the router on purpose: a retired prefix still has its handler today and
loses it when its area merges, and a verdict that flips with somebody else's merge would fail
in a branch that cannot fix it.

For the same reason the rules test checks R7 under two routings: today's tree (`first_match`),
and the tree once every area has landed (a `new` prefix counts as handled, a `retired` one
reaches the catch-all). A registered sample must pass, with every exemption still needed,
under both.

A sample may exempt a rule only with a reason (`exempt={"R2": "why"}`; an empty reason is
refused), and `test_every_exemption_is_needed` fails when an exemption no longer fires, since a
leftover exemption hides the next real violation of that rule.

#### Samples and the registry

`registry.py` holds `Sample(screen_id, section, title, build, nav, user_data, exempt)`. An area
registers its screens with `@sample(screen_id, section=…, title=…, nav=…)` on a
`build(lang) -> Screen` in `samples/<area>.py`; `load_all()` imports every module in
`samples/`, so adding the file is the whole registration. `nav` is one of `panel`, `root`,
`prompt`, `card`, `group`; `section` is one of `registry.SECTIONS`; `user_data` lists strings
that stand for chat titles and people's names and are exempt from the language rules.
`make_sample(...)` builds a `Sample` without registering it.

Sample data comes from `samples/fake.py` only, and every value there is invented — the
repository is public. Its chat id is the longest id a supergroup can have, so a payload that
fits with it fits with any real one.

`samples/foundation.py` registers one screen, `stale`: the hub under the out-of-date line. It
draws `hub.sample_hub(lang)` and carries `hub.SAMPLE_HUB_EXEMPT`, both declared in
`screens/hub.py`, which the hub area owns: today they are today's hub and the five rules it
breaks (nine rows, no title emoji, four retired payloads, mixed-language labels, a label that
truncates), each with its reason. Redesigning the hub means changing those two names in
`hub.py`; the stale sample follows without an edit to the foundation's file. It also holds
`DESIGN_COPIES`, ten screens of the redesign's target look rebuilt as data and **not**
registered, for one assertion: the lint passes them, under the landed routing, with no
exemptions at all. A rule that flags the design is wrong, not the design.

`tests/unit/test_screen_rules.py` asserts that every registered sample passes in both languages,
that every rule has a known-bad control it flags, that the design copies pass, and that every
screen id in the design has a sample. The last is a ratchet keyed by samples module: an id is an
expected failure naming its owner while `samples/<area>.py` does not exist, and a hard failure
once it does. Exemptions are per rule, so a sample exempt from R7 for its retired writes would
skip the routing check on its other buttons too; a separate test runs R7's routing half on every
sample with exemptions ignored. Every in-place toggle the design draws (✅ / ⚪ / ⏸, including the
rule card's pause) is checked to have a prefix `cb()` will write. `pytest tests/unit/test_screen_rules.py -rx` lists what is still outstanding; when
every area has landed that list is empty.

#### The dump

```
python3 scripts/dump_screens.py [--out PATH] [--lang ru|en]    # default /tmp/screens-dump.json
```

Writes every registered sample as JSON for the screen-map renderer, offline:
`{"screens": [{id, section, title, text_ru, text_en, surface, kind, rows, replaces}], "kept": {}}`,
where each row is a list of `{label, goes_to}`. `replaces` is always an empty list: which old
screens a new one takes over belongs to the design, not the code, but the renderer reads the
key on every item and stops on a missing one. `goes_to` is the button's own `goes_to`, otherwise
«ссылка» for a URL, «копирует текст» for a copy button, «нет действия» for `noop`, and the
target of its payload for everything else; reply-keyboard rows follow the inline ones. The
same rule violations the test reports are printed on stderr, and the exit code stays 0: the
dump is a picture, the test is the gate.

#### Measured, so it is not rediscovered

- The custom-emoji layer draws an icon for "⬅️ Панель" but leaves a bare "◀" / "▶" pager arrow
  as plain text. `test_screens_render.py` pins both, so a change in either direction is seen.
- `show_hub` currently draws today's hub, and `TestHubToday` in `test_admin_handler.py` pins
  it to `main_menu_keyboard` and the old title, so moving the hub onto the new path changed
  nothing visible. That file belongs to the hub area, which replaces the pins when it
  redesigns the hub.

#### The screen system's tests

| Test | Covers |
|---|---|
| `tests/unit/test_screen_rules.py` | the rules on every sample, one control per rule, the design copies, the sample ratchet, the section table |
| `tests/unit/test_routes.py` | the callback table, 64-byte budgets, routing of legacy / new / garbled payloads, router order, the retired-write and "Invalid data" ratchets; the two prompt tables, `PromptExitMiddleware`'s effect on the state, and who takes a sticker in each state (§F.5) |
| `tests/unit/test_prompt_message_closed.py` | what `PromptExitMiddleware` does to the prompt's message: closed once, in the tap's language, left alone behind a reply keyboard or on an edit error (§F.5) |
| `tests/unit/test_screens_render.py` | delivery: edit vs new message, the fallbacks, toasts answered once, splitting, the builders |
| `tests/unit/test_screens_entries.py` | the container and the FSM context reaching an entry through a real dispatcher, the stale catch-all's three answers, every entry refusing without `data` |
| `tests/unit/test_dump_screens.py` | the dump carries every key the renderer reads |
| `TestHubToday` in `tests/unit/test_admin_handler.py` (hub area), `test_rules_show_rules.py` (rules area), `test_admin_chat_panel_entries.py` (chats area) | what each stable entry draws today; each owning area changes its file with its screen |
| `tests/unit/test_screen_counts.py`, `tests/integration/test_screen_counts.py` | `count_attempts` and `count_for_chat`, the latter against real PostgreSQL |
| `tests/unit/test_access_card.py` | the access card after its move |

### F.7 Storage

#### `bot_config` (global key/value)

| Key | Shape | Used for |
|---|---|---|
| `admin_ids` | JSON list of ints | Who is an admin (every check) |
| `admin_settings` | JSON `{"lang": "ru", "notifications": {...}}` | Panel language + notification switches, both global |
| `command_scopes_pushed` | JSON list of ints | Chat ids currently holding a per-admin command menu (§A6.1) |
| `command_sync_last_alert` | sha256 string or null | Digest of the last command-drift alert sent (§A6.1) |
| `default_<field>` | scalar | Merge layer 2 for the 13 non-legacy per-chat fields |
| `health_check_*` | scalars | Health scheduler config |

Notification defaults live in two places that must agree on `unauthorized`:
`AdminRepository._NOTIFICATION_DEFAULTS` — `{"sticker": "on", "unauthorized": True,
"jailbreak": True, "blacklist": True, "ai_fallback": True}` — and the module-level
`_NOTIFICATION_DEFAULTS` in `services/abuse/notifications.py`, now `{"unauthorized": True}`.
The last three keys of the first have had no reader since their senders were removed (§A6.4).

#### `chat_settings`

Per-chat columns; `enabled` is the whitelist gate and is deliberately **not** in the field
registry (there is no `default_enabled`). `kb_organizer_ids` is a JSONB list written directly
by the KB panel, never merged through `ChatConfig`.

#### `unauthorized_attempts`

`status ∈ {pending, approved, rejected}`. Both the pending and rejected pages exclude chats
that are currently enabled, so a stale row cannot resurface after an approval elsewhere.
`has_rejected_attempt(chat_id)` is the "blacklist" predicate the middleware consults before
logging or notifying.

#### Others

`health_log` (latest row feeds the health screen; pruned to 30 days),
`response_log` (costs and response counts), `chat_messages` (message/active-chat counts),
`admin_sticker_notifications` (maps a DM message id → `file_unique_id`, so a reply can be
resolved without parsing text), `admin_sticker_session` (wizard session table, largely
vestigial).

### F.8 Rendering conventions and traps

- **`parse_mode=HTML` is the bot-wide default.** Every dynamic value in an admin screen must
  be `html.escape()`d or sent with `parse_mode=None`. Chat titles, usernames, model names,
  sticker descriptions and health issue messages all go through `escape()`.
- **`safe_edit_text()`** ([bot/utils.py](../src/bot/utils.py)) suppresses exactly
  `TelegramBadRequest("message is not modified")` and nothing else. Refresh buttons and
  re-opening the same page hit this constantly; `admin.py` inlines the same suppression in
  several handlers that predate the helper.
- **Pagination clamps out-of-range pages** by re-querying the last page (`_render_wl_*`,
  the pickers) rather than showing an empty screen.
- **Numbered buttons** must be built with `_numbered_button()` so the button number matches
  the body numbering produced by `enumerate(items, start=offset + 1)`.
- **Chat titles are lazily backfilled**: when `chat_settings.chat_title` is NULL the whitelist
  and rules screens call `bot.get_chat()` and persist the result. This is the ADR'd
  "handlers may call the Bot API as a fallback" exception; the counter is at 3 call sites,
  which is the extraction threshold.
- **Bare numeric ids must be wrapped in `<code>`** — Telegram autolinks 9–11-digit integers as
  dead "phone" links, and button labels cannot be marked up at all.
- **`build_chat_url()`** ([src/utils/telegram.py](../src/utils/telegram.py)) is the only place
  that constructs `t.me/c/...`, `t.me/{username}` and `tg://user?id=` links; it returns `None`
  for chats that cannot be linked (old-style groups), and callers fall back to plain text
  plus a `noop` button.
- **A sticker card takes only its own sticker with it.** `adm_stk_view:` deletes the list,
  sends the sticker and then the card, and records both ids in
  `admin_sticker_notifications`. Leaving that card looks the sticker up by the card's own
  message id (`get_card_sticker_msg`), deletes it only when the card is not a reply (a
  notification or a DM check replies to a sticker the admin keeps), then releases the record
  (`release_card`) so a reply to what is now a list is not a correction. A lookup by "the
  admin's latest sticker" would delete a notification that arrived while browsing. Failures
  are suppressed — a message the admin already deleted must not break navigation.
- **`handle_admin_sticker_reply` consumes every text reply in an admin DM** (filters:
  `F.reply_to_message, F.text, private, IsAdmin, StateFilter(None)`). When it cannot resolve
  a `file_unique_id` it returns early — and, because aiogram has already consumed the update,
  no other handler sees it. Any future "reply to X in the admin DM" feature has to be
  registered before this handler or fold into it.

### F.9 Rate limits and cooldowns

| Limit | Value | Scope | Storage |
|---|---|---|---|
| Unauthorized notification | 30 min | per chat | in-memory dict in the middleware instance, pruned above 1000 entries |
| OpenAI cost verification | 10 s | per admin | in-memory dict in `admin.py`, capped at 100 entries, `time.monotonic()` |
| Failed sticker-set registration | 5 min | per set | in-memory TTL dict in `media.py` |

All three are process-local and reset on restart — deliberately, since none of them protects
anything that a restart would make unsafe.

### F.10 Tests

- `tests/unit/` — one `test_admin_*_handler.py` / `test_admin_*_keyboards.py` pair per admin
  module (keyboard construction, callback parsing, permission-denial paths), plus
  `test_settings_fields.py` for the registry invariants (code length/uniqueness, legacy
  split) and `test_admin_sticker_dm_router_order.py` / `test_admin_tolerance_fsm_escape.py`
  for the two ordering traps in §F.2 and §F.5. The screen system's own tests are listed
  in §F.6.
- `tests/integration/` — against a real PostgreSQL via testcontainers:
  `test_admin_repository.py` (attempts lifecycle and sticker-notification records),
  `test_admin_chat_panel_toggle.py`, `test_admin_defaults_toggle.py`,
  `test_kb_enabled_toggle.py` (effective-value toggling across the merge layers).
- Mock shapes for callback/message objects and the OWASP findings they guard are described in
  the project memory note *Admin handler test patterns*.

When adding a screen, the two checks that catch most regressions are: the prefix does not
collide with a sibling (trailing colon), and the handler is registered on a router that runs
before the generic `message`/`media` routers if it consumes a plain message.

---

## A1 — Hub, requests, status, settings

| File | Owns |
|---|---|
| [src/bot/screens/hub.py](../src/bot/screens/hub.py) | `hub` (`build_hub`, `show_hub`, `sample_hub`), `requests`, `rejected`, `rejected.forget_ask`, `chat.off_ask` |
| [src/bot/screens/status.py](../src/bot/screens/status.py) | `status` / `status.bad`, `stats`, `costs`, `costs.verify` / `.missing` / `.error`; `verdict()` (the hub's status line), `PERIODS`, `INTERVALS` |
| [src/bot/screens/settings.py](../src/bot/screens/settings.py) | `settings`, `notif`, `lang`, `defs`, `defs.section`; `SECTION_TOGGLES` (which field codes each section switches) |
| [src/bot/handlers/admin.py](../src/bot/handlers/admin.py) | `/admin`, `/settings`, `/costs` and every callback above except `adm_defs*` |
| [src/bot/handlers/admin_defaults.py](../src/bot/handlers/admin_defaults.py) | `adm_defs:`, `adm_defs_sec:`, `adm_defs_tgl:` |
| [src/bot/screens/samples/hub.py](../src/bot/screens/samples/hub.py) | One sample per state that changes a screen's rows or id |

The area has no keyboard module any more: `keyboards/admin.py` and `keyboards/admin_defaults.py`
are gone, and their buttons are the rows of the screens above. The whitelist submenu
(`adm_wl:`), its chat list (`adm_wl_chats:`, `_render_wl_chats`, `chats_list_keyboard`) went
with them; the chat list is A2's `show_chats`, and old buttons for the two retired prefixes
are answered by the stale catch-all (`routes.py` marks them `retired`).

**Guards are filters.** Every callback is registered through `admin._on(prefix)` (prefix +
`F.message.chat.type == "private"` + `IsAdmin()`) or `admin_defaults._GUARDS`, and each command
carries `F.chat.type == "private"` + `IsAdmin()`. Nothing in a handler body checks who taps
or where, so a stranger's tap or a tap in a group falls through to `stale`, which says why.
`TestFilters` in `test_admin_handler.py` and `TestRegistration` in
`test_admin_defaults_handler.py` assert the registration rather than calling a handler as a
stranger.

**A payload that does not parse is stale.** Missing or non-integer ids, a period outside
`PERIODS`, a section outside `SECTIONS`, a field code that is not a non-legacy yes/no, a
notification type other than `unauthorized`, a language other than `ru`/`en` — each goes to
`show_stale(callback, lang, data=kwargs)` and writes nothing. An object that is *gone* is not
stale: a request already handled redraws the requests with `ALREADY_HANDLED`, a rejection
already forgotten redraws the rejected chats with `NOT_FOUND`.

**The hub never runs a check.** `show_hub` resolves `AdminRepository` and
`ResponseLogRepository` from `data` and reads `get_latest_health_check()`,
`get_total_cost(24 h)` and `count_attempts("pending")`. Any failure there (no container, a
database error) logs `hub_status_unavailable` and draws the hub without its status line — it
is also the stale fallback's screen and must not fail itself. `HealthChecker.run_check_now()`
is called from one place, `adm_health:{lang}:now`, after `callback.answer()` (a check takes
seconds); if it raises, `health_refresh_failed` is logged and the last recorded row is shown.
Check times are rendered in `DISPLAY_TZ`.

**One per page.** `admin._one(fetch, page)` asks the repository for one row; a page past the
end of a list that has shrunk is clamped to the last row by a second query.
`AdminRepository.count_attempts(status)` uses the same predicate as the page queries.

**Approve enables before it renders.** `adm_wl_apr:` calls `access_card.approve_attempt`, which
enables the chat and resolves every waiting attempt for it, then `_invalidate` (defensive —
the service is request-scoped, TD-046), then `admin_chat_panel.show_chat(..., notice=…)`, which
reads the chat's values fresh and so already shows it connected. `adm_wl_rej:` calls
`reject_attempt` and shows the next request, or `show_chats` once `count_attempts("pending")`
is zero. `adm_approve:` / `adm_reject:` on the access card are A6's and untouched.

**Turning a chat off** (`adm_wl_rm:`) is `chat_settings_repo.set_field(chat_id, "enabled",
False)`, `_invalidate`, then `show_chats(callback, lang, page, notice=…)` — the page travels in
the payload from the chat list.

**Language has one hub path.** `adm_lang_set:` saves and draws `settings` in the new language;
the hub is drawn only by `show_hub`.

**Notifications.** `_PANEL_TOGGLES = {"unauthorized"}`: the jailbreak, blacklist and AI-fallback
switches are no longer drawn and an `adm_ntog:` naming one is stale. Their stored values stay
in `admin_settings`, but nothing reads them: the notifications they switched were never sent
(§A6.4).
The sticker cycle (`adm_nstk:`) is `next_sticker_mode` over off → on → detailed.

**Defaults.** `adm_defs_tgl:` flips `default_{key}` in `bot_config`, calls
`ChatConfigService.invalidate_all()` (the shared global layer — never `invalidate(chat_id)`),
and redraws the field's section (`SECTION_OF`) with a toast. Keep
`handle_defaults_toggle(callback, bot_config_repo, chat_config_service, **kwargs)` callable
positionally with no `data`: `tests/integration/test_admin_defaults_toggle.py` does exactly
that, and a stale payload there must still answer (`show_stale` without a container draws the
hub without its line).

**OpenAI cross-check.** `_verify_on_cooldown` is per admin, 10 s, on the monotonic clock, and
bounded to 100 entries; the cooldown toast is the whole answer and leaves the screen as it was.
Both `OPENAI_ADMIN_API_KEY` and `OPENAI_PROJECT_ID` are checked before any request
(`costs.missing` names the absent ones). Our own figure is re-measured over the span OpenAI's
buckets actually cover, and `costs.verify` prints that window.

Tests: `test_admin_handler.py` (handlers), `test_admin_keyboards.py` (the builders' payloads,
each ≤ 64 bytes at the widest invented ids), `test_admin_defaults_handler.py`,
`test_costs_handler.py` (cooldown, the env names, the measured window).

---

## A2 — Chats and chat sections

Files:

| File | Holds |
|---|---|
| [src/bot/handlers/admin_chat_panel.py](../src/bot/handlers/admin_chat_panel.py) | `show_chat`, `show_chats`, every `adm_pnl*:` handler, the tolerance prompt, the instruction editor, `/panel` |
| [src/bot/screens/chat.py](../src/bot/screens/chat.py) | Pure builders over a `ChatView`: the chat list, `/panel` results, one chat's root, its six sections, the tolerance screens; the copy and the toggle labels |
| [src/bot/screens/samples/chat.py](../src/bot/screens/samples/chat.py) | One sample per state that changes a screen's rows or labels |
| [src/bot/screens/sections.py](../src/bot/screens/sections.py) | Which section each setting code is shown in (`SECTION_OF`), the old group tokens (`LEGACY_GROUPS`), the `· общее` rule (`shows_common`) |
| [src/bot/settings_fields.py](../src/bot/settings_fields.py) | The field registry (§A2.1) |

### A2.1 Settings-field registry

`settings_fields.py` is the single source of truth for `callback_data` codes, value types and
the legacy split. The defaults screen renders its grouping and labels from it; the chat panel
takes its grouping from `sections.SECTION_OF` and its labels from `screens.chat.LABELS`, so
the panel's wording can change without moving the defaults screen.

- **34 fields** = the mergeable `ChatConfig` fields (`_CHAT_CONFIG_FIELDS`) − `enabled`.
  `kb_organizer_ids` is excluded too, but it was never in the merge to begin with — it is
  written straight through `ChatSettingsRepository`.
- **13 legacy** (migration 001 columns that still carry a SQL `DEFAULT`): `ensure_exists()`
  materialises a value on first contact, permanently shadowing `bot_config.default_*`.
  Therefore they never show `· общее` and never appear on the global screen.
- **21 non-legacy** (nullable, no `DEFAULT`) — NULL honestly means "inherited", and only these
  show `· общее` (`sections.shows_common`: `not field.legacy and row[key] is None`).
- Only `FieldType.BOOL` fields get a toggle. `tolerance_level` has its own presets and FSM
  prompt (ADR-0008 Decision 10), `system_prompt` its own editor; every other non-bool value is
  shown read-only.

Every count on this page is prose about a number the code computes (`len(CHAT_SETTINGS_FIELDS)`,
`len(new_fields())`), so treat the code as authoritative and fix the prose when it drifts. It has
drifted three times: "11 new fields" predated `tolerance_level`, 25/12 predated `chunks_enabled`
(S5b), and 26/13 predated the seven community fields. `tests/unit/test_settings_fields.py` pins
the numbers — if a count here disagrees with that file, that file is right.

**Superseded: the link-only fields (ADR-0010 Decision 3, ADR-0006 Decision 2).** Until the
redesign, `kb_enabled`, `reactions_enabled` and `reactions_history_enabled` were link rows on
the chat panel and `handle_chat_panel_toggle` refused them (`_LINK_ONLY_KEYS`), so that their
only writers stayed in the KB and Reactions pickers and the write path was not duplicated. The
redesign retires those pickers (`adm_react:` / `adm_react_menu:` / `adm_react_toggle:` with
their handlers and keyboard are deleted here; `adm_kb:` / `adm_kb_menu:` belong to §A4), which
removes the reason: the three are now ordinary switches in 🧠 Память and 😀 Реакции, written by the same
`toggle_bool_field` as every other bool, and the panel is again their single writer.
`test_kb_enabled_code_now_reaches_chat_settings_write` (integration) is the old
`…never_reaches…` test inverted on purpose. Two things the old split carried survive it: the
three switches still show the raw-column + global-default value (below), so the memory section
and the KB area cannot disagree, and switching reactions on still runs the live admin check
(ADR-0004 Decision 5).

#### Effective-value resolution

Merge order is YAML → `bot_config.default_*` → `chat_settings` → `ChatConfig`. Three
resolution paths exist and must stay consistent:

- `ChatConfigService.get_config()` — everything the chat panel shows, except the three
  switches below.
- `_fresh_effective()` (keys and fallbacks in `_LIVE_DEFAULTS`) — raw column, then
  `bot_config.default_*`, then the bot's own fallback, for `kb_enabled`,
  `reactions_enabled`, `reactions_history_enabled`. Mirrors `admin_kb._effective_kb_enabled`.
- `_resolve_values()` in `admin_defaults.py` — `bot_config.get_defaults()` with the
  `ChatConfig(chat_id=0)` dataclass as fallback (layer 1).

Toggles always flip the **effective** value, never the raw column, so a chat that is on via
the global default turns off on the first tap. `toggle_bool_field` performs the negation
inside the `UPDATE` (`NOT COALESCE(col, $effective)`), which closes the double-tap race, and
returns `None` when no row matched — the handler then shows the chat list with `_CHAT_GONE`
rather than a success notice. Every write re-reads the chat (`_load_view`) before drawing, so the notice
and the switch never show a value from before the write.

#### Cache invalidation

- Per-chat write → `chat_config_service.invalidate(chat_id)`.
- Global default write → `chat_config_service.invalidate_all()` (the shared `_global_cache`;
  per-chat `invalidate()` does not touch it). Easy to get backwards by analogy.

Today both are defensive: `ChatConfigService` is Dishka `Scope.REQUEST`, so no cache survives
a single update (measured 2026-08-06). They become load-bearing if the scope moves to
`Scope.APP`. Then the one write site that does **not** invalidate must be fixed in the same
commit: approving from the access card (`handle_approve_notification` → `approve_attempt`, both
in `access_card.py`). Approving from the requests screen (`handle_wl_approve`) and turning a chat
off (`handle_wl_remove`, both in `admin.py`) already call `_invalidate` after the write.
Tracked as TD-046.

### A2.2 Screens, callbacks and entries

| Callback | Payload | Screen |
|---|---|---|
| `adm_pnl:` | `lang:page` | `chats` / `chats.empty` |
| `adm_pnl_menu:` | `lang:chat_id` | `chat` |
| `adm_pnl_grp:` | `lang:chat_id:section` | a section; `rules` hands over to `show_rules` (§A3) |
| `adm_pnl_tgl:` | `lang:chat_id:code` | the code's own section (`SECTION_OF`), with a notice |
| `adm_pnl_tol:` | `lang:chat_id` / `lang:chat_id:in` | `tol.prompt` / `tol.in` |
| `adm_pnl_tolset:` | `lang:chat_id:value` | `chat.stickers`, with a notice |
| `adm_pnl_tolcancel:` | `lang:chat_id` | `chat.stickers` |
| `adm_pnl_sp*:` | `lang:chat_id[:rev]` | the instruction editor, then `behavior` |

- **The list** is `AdminRepository.get_enabled_chats_page_by_activity(page, per_page)`, with
  `count_attempts("pending" / "rejected")` for the entry row. `screens.chats_per_page` is 3
  when that row is shown and 4 otherwise. A page past the end is fetched again as the last page.
- **Old group tokens** map through `sections.LEGACY_GROUPS` (`modules`, `kb` → memory,
  `rules` → the rules screen), so a button drawn before the redesign still lands somewhere real.
- **Back is the tree parent**: a section → its chat, a chat → the list, the list → the hub.
  The old `:p` origin token (back to a KB/Reactions picker) is gone with the pickers.
- **The reactions section asks Telegram every time it is drawn**
  (`utils.is_bot_chat_admin`; the bot id from `dp["bot_id"]`, else `bot.me()`). Switching
  `rx` on asks once more and pops `REACTIONS_NOT_ADMIN_ALERT` if the answer is no; the
  write stands either way.
- **Hidden switches refuse stale taps** (`_HIDDEN_WHILE_OFF`): the five community parts while
  `cm` is off, `rh` while `rx` is off. Their buttons are not drawn then, so a tap is from an
  older screen; it writes nothing and re-renders the section under a line saying so.
- **Tolerance input** is `AdminStates.awaiting_setting_value` with `tol_chat_id`, `tol_lang` and
  `tol_prompt_id` in the FSM data. Each new answer first takes the keyboard off the previous
  prompt (`_drop_prompt_keyboard`, `TelegramBadRequest` suppressed — a deliberate raw edit, see
  `test_edit_call_sites_are_guarded.py`), so only the newest prompt can act. A rejected value
  sends one new prompt and records its id; an accepted one clears the state and sends the
  stickers section as one new message.
- **Leaving the number prompt closes it** (`_close_number_prompt`). `tol.in` is edited in
  place, so older panel messages keep live buttons, and the state used to outlive the screen
  and read the admin's next text as a value. The list, a chat, a section, a switch, the presets
  screen, a preset, `/panel` and the `show_chat` / `show_chats` entries all call it first. It
  clears `awaiting_setting_value` only. For a tap this now repeats what `PromptExitMiddleware`
  has done before the handler ran (§F.5): any tap outside `adm_pnl_tol:` / `adm_pnl_tolcancel:`
  ends the prompt, in this area or another, and the instruction editor's
  `awaiting_system_prompt` and its draft end the same way. The call still matters for `/panel`
  and for the entries opened by a message, which the middleware does not see.
- **Unparsable payloads** — a non-integer chat id or page, an unknown section or code, a
  non-bool code on the toggle, a preset outside 0..1 — go to `show_stale`. The two cancel
  handlers (`adm_pnl_tolcancel:`, `adm_pnl_spx:`) clear their own state first, so the way out
  never depends on the payload.
- **A chat with no settings row** shows the list with `_CHAT_GONE` above it, whichever way it
  was reached: opening the chat or a section, a switch, the presets, a preset, a typed value
  (`_save_tolerance`), or the number prompt about to ask again. Nothing is written. Only the
  instruction editor (`adm_pnl_sp*:`) still answers `_NO_ROW` as an alert.
- **`· общее` on the knowledge base is a footnote line** (`_KB_COMMON_ON` / `_KB_COMMON_OFF`),
  not the button's suffix (`_toggle(..., marked=False)`). The button shares its row with
  `👥 Организаторы · N`; «⚪ База знаний · общее» estimates at 164.6 px, past the 163.5 px a
  half row shows uncut (`clip_room(2)` is 161.5 px), and the rendered dump cut it. The line is still
  driven by `shows_common`, and the name stays «База знаний» everywhere.
- **`show_chat(target, lang, chat_id, *, notice=None, data)`** resolves five dependencies from
  `data` (`ChatSettingsRepository`, `BotConfigRepository`, `ChatConfigService`,
  `RulesRepository`, `AdminRepository`); **`show_chats(target, lang, page=0, *, notice=None,
  data)`** resolves `AdminRepository`. Both edit for a callback and send for a message.
- **`/panel`** is `Command("panel")` + `IsAdmin()` + `F.chat.type == "private"` (the chat-type
  guard is a filter, never a body check). A reference goes through `parse_chat_reference` and
  must resolve to a row with `enabled`; a title goes to `find_enabled_chats_by_title(query,
  limit=FIND_SHOWN + 1)`, the extra row being how the screen knows to say the list was cut.

Reaction *updates* from chats are `handlers/reactions.py`, which keeps its own
`chat_config.enabled` guard: `AccessControlMiddleware` does not understand `message_reaction`
(CLAUDE.md, Gotchas).

---

## A3 — Rules

Files: [src/bot/handlers/rules.py](../src/bot/handlers/rules.py) (the routes are listed in its
module docstring), [src/bot/screens/rules.py](../src/bot/screens/rules.py) (pure builders),
[src/bot/screens/samples/rules.py](../src/bot/screens/samples/rules.py). There is no
`keyboards/rules.py` any more. `show_rules` is the stable entry other areas open a chat's list by.

- **The switch for all rules** (`ar_all:`) flips the *effective* value through
  `ChatSettingsRepository.toggle_bool_field(chat_id, "rules_enabled", effective)`, as the panel
  toggles do, so a chat that inherited the default gets a value of its own; «· общее» comes from
  `shows_common(FIELDS_BY_CODE["re"], row)`. No settings row is a stale tap, logged as
  `rules_toggle_no_row`. With a rule id it redraws that rule's card.
- **`ar_tog:`** is `kept` and targets `rule`: the card's pause/resume writes it and redraws the
  card. The list no longer writes it (`test_rules_handler.py` pins that), so an old list button
  toggles the rule and lands on its card. `RulesRepository.get_rules_page` gives the page and the
  total; a page past the end is clamped and fetched again.
- **The builder keeps its draft in FSM data** (`_DRAFT_KEYS`, all `rule_*`). Each step checks that
  the draft exists and belongs to the payload's chat; otherwise the tap is stale, never a rule
  made in another chat. `ar_add:` resets only the draft keys, in one mapping: `rule_chat_id` is
  itself a draft key, and passing it again as a keyword argument raised on every tap.
- **Five prompt states** (`_RULE_STATES`: words, text, reaction, the person search, JSON).
  `PromptExitMiddleware` leaves every `ar_` tap to this file (§F.5), and every callback handler
  starts by leaving them (`_leave_config_prompt`) except the four in `_STATE_SETTERS`, each with
  its reason; `test_fsm_escape.py` walks the module and holds both halves. A typed answer that
  is accepted sends one new message and takes the keyboard off the prompt (`_retire_prompt`, by
  `rule_prompt_id`); a refused one sends the error with its own Cancel, and that becomes the
  prompt.
- **What the builder writes, beyond the obvious**: `user_specific` gets
  `conditions.any_message = true`, since without keywords the engine never fires it;
  `spam_detect` gets `max_chars_per_message: 0`, since the engine's default of 4000 would also
  fire it on one long message; both counted kinds get a 60-second window.
- **Validation**: words ≤ 20 × ≤ 64 characters; a typed reaction goes through
  `ReactionSelector.select()` and is stored in Telegram's spelling; a text ≤ 4096; the JSON
  prompt checks the type and action against `_VALID_RULE_TYPES` / `_VALID_RULE_ACTIONS` and a
  `set_reaction` emoji, and pops `type`, `weight` and `mandatory` out of the config.
- **The executor** (`services/rules/executor.py`) imports the screens lazily. `notify_admin` sends
  `rule_card` (`card.rule`) in `admin_settings.lang`, with the chat's title when the message
  carries one; its button is `ar_view:…:n`, which answers with a new message. `custom_response`
  is sent with `parse_mode=None`, so the values go in unescaped — escaping them showed
  `Tom &amp; Jerry`. `warn_user` without a text posts `WARN_FALLBACK("ru")`: the chat's
  language is unknown.
- **`sticker_flood`** (`services/rules/engine.py`) fires only when `message.sticker` is set: the
  count is chat-wide, so any message used to re-fire it for the rest of the window. After firing,
  the rule rests for `interval_seconds`. The rest lives in module-level `_STICKER_FLOOD_FIRED`
  because the engine is built per update. It is checked before the count query, so a resting
  rule costs nothing and cannot be picked over a rule that will act. It is claimed in
  `_claim_flood_cooldowns`, after the mode has picked and with no await between check and stamp,
  so two stickers evaluated together act once and a match that was not picked starts no rest.
  It is process-local, so a restart forgets it.
- **A sticker in `awaiting_rule_react` is the reaction.** `admin_sticker`'s DM sticker check runs
  earlier in the router order and used to take it, so a sticker did nothing for the rule. Its
  filter `_no_prompt_takes_it` now steps aside for this state (`STICKER_ANSWERS`, §F.5), and
  `handle_rule_react_input` reads `sticker.emoji` like a typed one.

---

## A4 — Knowledge base organizers

Files: [src/bot/handlers/admin_kb.py](../src/bot/handlers/admin_kb.py) (reads and writes
`chat_settings.kb_organizer_ids`, picks the screen),
[src/bot/screens/orgs.py](../src/bot/screens/orgs.py) (the six screens as data: `orgs`,
`org.card`, `org.invite`, `org.list`, `org.add`, `org.fail`),
[src/bot/keyboards/admin_kb.py](../src/bot/keyboards/admin_kb.py) (only what the handlers
match: the picker's `request_id` and the reply labels). The `/remember` and `/kb` keyboards
are in `keyboards/kb.py`, outside the panel. The prompt's state is described in §F.5.

- **Remove is undo, not confirm.** `adm_kb_org_rm:` writes at once; the result carries
  `adm_kb_org_undo:` with the person's former index as a fourth field, so `↩️ Вернуть` puts
  them back in place. A second undo, or an undo after someone re-added them, writes nothing.
  The page shown after either is derived from the index (`orgs.page_of`), not from the payload.
- **A tap on a name opens the card and writes nothing** (`adm_kb_org:` →
  `handle_kb_organizer_card`). Removing is a button on the card; the list's name buttons
  removed at once until 2026-11, and old ones in chats still do (`adm_kb_org_rm:` unchanged).
  `test_admin_org_card.py` takes the callback off the list the handler really draws, so
  pointing it back at the removal fails a test (`set_field` awaited).
- **Membership is one `getChatMember`** (`_seen`): `kicked` → `removed`; `left`, or a
  `restricted` member with `is_member=False` → `left`; a `member not found` Bad Request →
  `never_joined`; any other failure → `unknown`, logged. `left` covers someone who never
  joined as well (Telegram does not tell them apart), hence the card's neutral «Сейчас не в
  чате». The same call feeds the list's « · не в чате» and the card's name, `@username` and
  `t.me` link (only for a name matching `orgs.USERNAME_RE`, `{4,32}` because collectible
  usernames can be four characters; anything else gets no button). Only a username from
  *this* answer reaches the card: when Telegram names nobody (`never_joined`, `unknown`)
  `_names` takes the name from the store but leaves `_Name.username` empty, since a stored
  username may have been freed and re-taken and `t.me/{it}` would open a stranger. The
  stored `@nick` still serves as the list's and the title's name (`orgs.display_name`
  prefers it) — a label, not a link.
- **`getChat` is the second live name** (`_private`): when `getChatMember` answers with a Bad
  Request it named nobody, so `getChat(user_id)` is asked. It returns the private chat, with
  name and username, for anyone who has opened the bot, and «chat not found» for everyone
  else (expected: debug log; any other failure: warning). Its username is as live as a
  member's and reaches the `t.me` link. Not asked after a network failure, which would only
  wait twice. The order is: Telegram now (either call) → the store → «участник <id>».
- **The person link is a button, never the text** (`orgs.person_url`, `💬 Написать лично`,
  always the first row): `t.me/{username}` for a live username, else
  `tg://openmessage?user_id=`. Measured on the dev bot 2026-11-07, taps on Julia's phone:
  a `tg://user?id=` text mention is accepted but silently dropped for a person the bot
  cannot see (kept as a `text_mention` for one it can); a `tg://user` URL button is refused
  with the whole message — `BUTTON_USER_INVALID` (never seen) or
  `BUTTON_USER_PRIVACY_RESTRICTED` (seen, with privacy settings); `tg://openmessage` is kept
  as a text link and accepted as a button for both, and the button opened the private chat
  in both cases. Desktop and Web are untested, so the body name is plain text and the card
  has one link, labelled.
- **A refused link costs the button, not the card** (`_show_card`, at the three places the
  card is shown). A refused button fails the edit and the new-message fallback alike, so
  `render.show` raises. A `TelegramBadRequest` whose text matches `_LINK_REFUSED`
  (`BUTTON_[A-Z_]+` or a whole-word upper-case `URL`; case-sensitive, so entity errors
  that mention `"url"` do not count) re-shows the card once via `orgs.without_person_link`,
  with `answered=True`, and logs «Organizer card: link button refused». Anything else
  propagates; «message is not modified» never reaches it (`safe_edit_text` suppresses it).
- **The card's role line follows the state**: `CARD_ROLE` for `in_chat` and `unknown`,
  `CARD_ROLE_OUTSIDE` («…сможет, когда будет в чате.») for `orgs.ABSENT`, because
  `/remember` only works from inside the group.
- **The «участник <id>» fallback keeps the id in `<code>`** (`orgs.title_name`) in the card
  and invite titles, outside the `<b>` so lint R5's plain-text title still holds, and the
  card's body drops the name line and `CARD_ID` as repeats. A bare 9-11 digit number in a message is autolinked by
  Telegram as a phone number.
- **The invite only for `left` and `never_joined`** (`orgs.INVITABLE`). A banned person is
  refused at the door, so the card says the ban must be lifted; `unknown` might be someone
  already inside. `_invite` re-asks Telegram on every tap, so an old `✉️` button for someone
  who joined, was banned or cannot be looked up lands on the card with a line and makes no
  link. `createChatInviteLink` gets `member_limit=1`, `expire_date` now + 7 days and a name
  clipped to 32 UTF-16 units (`orgs.invite_link_name`). A failure matching
  `CHAT_ADMIN_REQUIRED` / `not enough rights` returns the card with `orgs.NO_INVITE_RIGHT`;
  any other is logged and said (`LINK_FAILED`). Nothing is written in any case.
- **`🤖 Отправить от бота` is always offered and makes a fresh link.** Nothing stored says
  whether the person ever opened a private chat with the bot, which Telegram requires before a
  bot may write first, so the refusal is answered on the invite screen (`DM_FORBIDDEN`, naming
  both causes — no chat with the bot, or blocked — and pointing to `📤 Переслать`). A refusal
  is `_cannot_write_first`: a 403 (`TelegramForbiddenError`), or a 400 matching
  `chat not found` / `PEER_ID_INVALID`, which is what Telegram answers for someone the bot has
  never met (the same text `src/bot/commands.py` handles for a per-admin scope). Any other
  failure is logged and said (`DM_FAILED`). The link shown before is not revoked: it expires
  on its own.
- **The invite link is a credential and is never logged.** Log events carry `chat_id` and
  `user_id`; a failure's text goes through `_error_text` — Telegram's description or the class
  name, the link in hand replaced by value and any invite-link shape by regex — and no
  `exc_info` (structlog's `dict_tracebacks` renders frame locals, which hold the link). The
  test captures every log entry over the made, sent, 403 and failed paths and checks none
  carries a link, with a failure text that does quote one as the control.
- **Names are looked up per page.** `_names` asks Telegram only for the people on the page
  being drawn, concurrently (`asyncio.gather` over `resolve_display_name`); a name already
  known from the event (a pick, a forward) is not asked again. A failed lookup falls back to
  `OrganizerNameRepository.names` (table `kb_organizer_names`, migration 038: the stored name,
  else the latest one seen in the chat); «участник {id}» only when no name was ever known.
- **Names are clipped by width, not length** (`orgs.clip` to `orgs.NAME_PX`, which is
  `clip_room(1)`: 340 px, §F.6). The estimate is measured against the renderer per letter, so a
  long mixed Cyrillic and Latin name needs no margin of its own. A script missing from the
  estimator's table reads wide and is cut early, never late.
- **Leaving the prompt is two messages.** `_leave_prompt` sends the line carrying
  `ReplyKeyboardRemove`, then the `orgs` screen with inline buttons as a new message — the
  same in every exit, callback or message.

---

## A5 — Stickers

Files: [src/bot/handlers/admin_sticker.py](../src/bot/handlers/admin_sticker.py) (handlers),
[src/bot/screens/stickers.py](../src/bot/screens/stickers.py) (pure builders for `stk.*` and
`card.sticker`, samples in `screens/samples/stickers.py`),
[src/database/repositories/stickers.py](../src/database/repositories/stickers.py). Its two
traps — it runs before `media` (§F.2), and its reply handler consumes every text reply in an
admin DM (§F.8) — are shared-machinery facts and stay there.

- **Packs are addressed by key, not name.** A set name may be 64 bytes, which cannot fit a
  payload; buttons carry `pack_key(set_name)` = the first 12 hex digits of its md5, and
  `get_set_by_key` matches `left(md5(set_name), 12)` — a sequential scan, no index, no
  migration. The two halves of that formula are pinned to each other by an integration test.
  `adm_stk_set:` (the name form) is still parsed for buttons already in chats.
- **One card renderer.** `build_stk_card` serves view, clear, score, re-analysis, the DM
  check and the DM analysis; `build_card_sticker` is the new-sticker notification sent by
  `StickerLearningService.notify_admins`, with the same action rows and no back. Every path
  that leaves a card on screen calls `save_notification`, which is idempotent per
  (chat, message, sticker), so a reply to any card resolves.
- **Gone is not stale.** A sticker or pack deleted meanwhile renders the pack list with
  `GONE_STICKER` / `GONE_PACK`; only an unparseable payload goes to `show_stale`.
  `adm_stk_back:` is retired, and an old one gets the stale answer.
- **Re-score is reset plus one re-analysis.** A NULL score means the bot never sends the
  sticker, so `reset_explicitness_to_auto` alone would silence it until something else
  re-analysed it.
- **Analysis errors carry their reason** (`download`, `vision`, `content_filter`, `empty`)
  from `ReanalyzeResult.reason` / `StickerLearningResult.failure_reason` to `build_stk_error`.
  The DM variant retries through `adm_stk_dmchk:`; once a row exists it re-analyses rather
  than learning again.

---

## A6 — Entry and cards

Files: [src/bot/handlers/commands.py](../src/bot/handlers/commands.py) (`/start`, `/help`,
`/summary`, `/kb`, `/remember`, `/callme`, the group `/admin` hint),
[src/bot/handlers/callbacks.py](../src/bot/handlers/callbacks.py) (`help_summary:`,
`help_close:`, `help_dm:`, `help_callme:`),
[src/bot/handlers/chat_events.py](../src/bot/handlers/chat_events.py) (`my_chat_member`),
[src/bot/handlers/access_card.py](../src/bot/handlers/access_card.py),
[src/bot/access_requests.py](../src/bot/access_requests.py). The screens are built in
[src/bot/screens/entry.py](../src/bot/screens/entry.py) (people) and
[src/bot/screens/cards.py](../src/bot/screens/cards.py) (cards to admins), with samples in
`screens/samples/entry.py`. `keyboards/help.py` and `keyboards/access_card.py` are gone; the
only keyboard module left here is `keyboards/kb.py`, which renders the `/kb` pager row that
`entry.kb_pager` builds.

### A6.1 Command menus

What Telegram offers as autocomplete is *not* derived from the handlers at runtime — it is pushed
state living on Telegram's side, declared by `COMMANDS` in
[src/bot/command_registry.py](../src/bot/command_registry.py) and rendered by
[src/bot/commands.py](../src/bot/commands.py).

| Scope | Commands |
|---|---|
| `GROUPS` (`BotCommandScopeAllGroupChats`) | help, summary, kb, callme |
| `PRIVATE` (`BotCommandScopeAllPrivateChats`) | start, help |
| `ADMIN` (`BotCommandScopeChat` per bot admin) | start, help, kb, admin, cancel |

`settings`, `costs`, `panel`, `summary500` and `remember` have handlers and empty scopes.
`tests/unit/test_command_registry.py::EXPECTED_SCOPES` pins this table exactly. **Every change
to it is pushed to every user on the next deploy** — the startup sync rewrites all three
language variants of every scope — so it is a release decision, not a refactor.

- **Scopes**: `BotCommandScopeAllGroupChats`, `BotCommandScopeAllPrivateChats`, and one
  `BotCommandScopeChat` per bot admin. Each is pushed three times — `ru`, `en`, and once with **no**
  `language_code`. That last one is not optional: Telegram resolves a scope by the user's language
  and then by the language-less variant, so without it a client on any third locale sees an empty
  menu. Measured on the dev bot before the fix: `get_my_commands(language_code=None)` returned `[]`.
- **`admin_only` is cross-checked, scope is not.** `discover_handler_commands()` reads the `Command`
  filters and detects `IsAdmin` by `isinstance`; chat-type `MagicFilter`s are not introspected
  (their operation chain is a private aiogram detail), so where a command is advertised stays
  declared.
- **Stale scopes**: the Bot API cannot enumerate the scopes a bot has set, so the ids pushed to are
  remembered in `bot_config.command_scopes_pushed`; ids no longer in `admin_ids` get
  `delete_my_commands` for every language variant. A `TelegramBadRequest` there ("chat not found")
  counts as *cleaned*, not failed — treating it as a failure kept a mistyped id in the list forever
  and reported drift on every restart.
- **Drift alerts** are deduped by a sha256 of the problem list stored in
  `bot_config.command_sync_last_alert`, and that key is cleared on a clean run so a recurrence
  alerts again. `notify_command_drift()` is therefore called on both the clean and the drifted path.

### A6.2 Entry handlers

- **`/start admin` is registered before plain `/start`.** aiogram stops at the first handler
  whose filters match, and `CommandStart()` matches `/start admin` too. `handle_start_panel`
  carries `CommandStart(deep_link=True, magic=F.args == entry.PANEL_DEEP_LINK)`,
  `F.chat.type == "private"` and `IsAdmin()`; a non-admin with the same link falls through to
  `handle_start`, which draws `start.member`. The admin path calls `screens.hub.show_hub`.
- **A stranger's `/start` never reaches these handlers** while their DM is not enabled:
  `AccessControlMiddleware` stops the update, files the attempt and the admins get the access
  card; the stranger gets no reply. This is the behaviour the redesign kept on purpose.
- **The group `/admin` hint depends on the panel's router.** `handle_admin_in_group` is
  `Command("admin", "settings")` + group/supergroup + `IsAdmin()` in the `commands` router,
  which is included after `admin`. The panel's own `/admin` handler has no chat-type filter
  today, so it consumes the group update and returns; the hint becomes reachable once that
  handler gets its private-chat filter (§A1). `tests/unit/test_commands_entry.py` pins the
  hint's filters and the router order, and also walks the **real** router tree with a group
  `/admin` from a bot admin to see which handler takes it. That walk is an `xfail` naming §A1
  while the panel's handler is the taker, and turns into a plain pass the moment the filter
  lands — no edit needed here.
- **Public buttons answer an unreadable payload with a toast, not `show_stale`.**
  `help_summary:`, `help_close:`, `help_dm:` and `help_callme:` sit in front of ordinary
  members; the stale screen is the admin panel. They log `public_button_unreadable` and answer
  `_OLD_BUTTON` in the chat's language. `kb_view:` with a bad page shows page 0.
  `adm_approve:` / `adm_reject:` are admin buttons and *do* call `show_stale`.
- **`help_summary:` posts a new message** through `ProgressNotice` and `split_html`; it no longer
  edits the help into a summary, so the old continuation tracking (`_CONTINUATIONS`) and
  `summary_keyboard` are gone with their tests. A summary is a paid call that takes seconds,
  so the handler claims `(chat id, help message id)` in the module-level
  `_SUMMARIES_IN_FLIGHT` set — checked and added with no `await` in between — and releases
  it in a `finally`. A second press on the same help while the first runs gets the
  `_SUMMARY_BUSY` toast and calls no model. Per process, like the call it guards.
- **`/summary` with an unreadable or too large count** (`/summary abc`, `/summary 5000`) answers
  `summary.invalid` and calls no model; a count under 20 keeps its one-line "too few" reply.

### A6.3 The greeting when the bot is added

`handle_my_chat_member` posts `entry.group_added` into the chat after
`AccessRequestService.submit` when all of these hold: the update is a join, the chat is
whitelistable and not already enabled (so a request was actually filed), the chat type is
`group` or `supergroup` (never a channel), and `submit`'s `suppressed` is in the allow-list
`_GREET_AFTER`: `None`, `"db_error"` (the row failed, the card went out) or
`"keyboard_error"` (the row is queued, the card has no buttons) — in those the rejection
check ran and passed, so "wait for approval" is true. `"cooldown"`, `"rejected"` and
`"error"` (submit raised, possibly before the rejection check) stay silent, and so does any
outcome added later until someone puts it on the list. The language is the chat row's `language`. A failed send is
logged as `group_added_greeting_failed` and swallowed — the card to the admins is the part
that matters, and it has already gone out.

### A6.4 Notification plumbing

| Notification | Sender | Recipients | Gate |
|---|---|---|---|
| Access card | `AccessRequestService.submit` builds `cards.access_card` in the admin language; `AbuseNotificationService.notify_unauthorized(text=…, reply_markup=…)` delivers | all admins | `admin_settings.notifications.unauthorized` + 30-min per-chat in-memory cooldown + no prior rejection |
| New sticker | `StickerLearningService.notify_admins`, called from `media.py` | all admins | `admin_settings.notifications.sticker` ∈ {on, detailed}; skipped when `analysis_failed` or the sticker was already known |
| Health alert | `HealthChecker._send_alert` | **all admins** | not switchable; de-duplicated by fingerprint |
| Spend card | `SpendLimitService.notify_admins_if_exceeded`, from `reply_flow.finish_reply` | all admins | over `daily_spend_limit_usd`; once per UTC day |

There is no jailbreak, blacklist or AI-fallback notification: their senders were never called
and were removed in `21f0c77`. `notify_unauthorized` is the one method
`AbuseNotificationService` has left. The `jailbreak`, `blacklist` and `ai_fallback` defaults in
`AdminRepository._NOTIFICATION_DEFAULTS` are read by nothing. AI-path failures reach the
admins as the health alert.

`AbuseNotificationService` never imports the screen layer: the caller hands it finished text
and markup. Services that build a card themselves (`spend_limit`, `health/checker`) import
`screens.cards` and `screens.render` **inside the sending function**, because
`screens.render` imports `reply_flow`, which imports the services — a module-level import
closes the cycle.

**Access card.** `handlers/access_card.py` resolves the tap with `approve_attempt` /
`reject_attempt` (shared with the panel's request list; both return `None` when the attempt is
no longer `pending`) and redraws the card as `card.access.ok` / `card.access.no`. Whether the
card was the "added" kind is read from its own heading (`cards.is_added_card`), because the
attempt row does not record it. A card older than `TEXT_EDIT_WINDOW` (48 h) keeps its text and
gets only the new markup. A double tap redraws to the real state with the "already resolved"
toast; when nothing changed, `show` swallows Telegram's "message is not modified".

**Spend card.** `bot_config.spend_card_sent_day` holds the UTC date of the last card (a plain
KV row, no migration). The day is **claimed before sending**, under a process-wide
`asyncio.Lock`, so concurrent replies past the limit produce one card, and a send that fails is
not retried that day — a retry on every reply is exactly how the health alert once sent 37
messages in a day. Per-admin failures are logged (`spend_card_send_failed`); the check runs
after `post_send` has written the reply's own cost row. Nothing is posted into the group.

**Health card.** `_send_alert` renders `cards.health_card` / `cards.recovered_card` in the
language from `admin_settings` (now part of `_load_config`), splits the text with `split_html`
and puts the 📊 button on the last piece. It returns True when at least one admin got every
piece, and `alert_sent` is recorded from that.

The whole card is in the admin language, and **what is stored and compared did not change**.
`_run_check` builds its issues with `db_issue`, `fallback_issue` and `ai_failure_issue`:
`HealthIssue` subclasses (`DbIssue`, `FallbackIssue`, `AIFailureIssue`) that keep the exact
English `message`, the same `key`, `detail` and `hint` as before, and additionally carry the
facts — the cause, the count, the task, the provider, since when. `_issue_text` words the line
from those facts through `cards.db_down_line` / `fallback_line` / `failure_line`; a plain
`HealthIssue` shows its message. Hints come from `diagnosis.hint_for` in Russian and are part
of the `ai_failure:…` key, so they are never reworded at the source: `cards.hint_line` looks
the English one up in `_HINT_EN`, keyed by the Russian text, and
`test_every_diagnosis_hint_has_an_english_line` fails when a hint is added without one.
`detail` is the provider's own error and is shown as is. The recovery card is worded from the
**stored key** of each cleared issue (`cards.cleared_line`: `db`, `ai_fallback`,
`ai_failure:<task>`); a key it does not know shows the stored message. `_issue_row` stores
the same fields as before (the facts are not persisted), and the messages and fingerprints are
pinned to the previous release's wording by `test_what_is_stored_and_compared_did_not_change`,
so the deploy causes no extra alert. The
§A1.3 health screen still lists the stored messages, which are English.

The sticker notification sends the sticker first, then the description as a reply to it, and
records the pair in `admin_sticker_notifications` so a later text reply resolves to the right
`file_unique_id` (with a regex over the `🆔` line as a fallback).
