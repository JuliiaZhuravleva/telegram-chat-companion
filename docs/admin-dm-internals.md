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
| [src/bot/handlers/admin_reactions.py](../src/bot/handlers/admin_reactions.py) | Reactions panel (`adm_react*`) |
| [src/bot/handlers/admin_sticker.py](../src/bot/handlers/admin_sticker.py) | Sticker browser, re-analysis, DM sticker check, description-merge reply (`adm_stk*`) |
| [src/bot/handlers/rules.py](../src/bot/handlers/rules.py) | Rules engine UI (`adm_rules:`, `ar_*`) |
| [src/bot/keyboards/admin*.py](../src/bot/keyboards/) | All inline keyboards; one module per handler module |
| [src/bot/keyboards/access_card.py](../src/bot/keyboards/access_card.py) | The access card's keyboard (`access_keyboard`) |
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
fsm_cancel → admin_sticker → admin_kb → admin_reactions → admin_chat_panel → admin_defaults
→ access_card → admin → rules → stale → commands → callbacks → chat_events → community
→ reactions → media → message
```

`tests/unit/test_routes.py::test_router_include_order` pins this list, so a change to it is a
change to that test too.

Consequences that are easy to break:

- `admin_sticker` precedes `media`, which is why an admin's DM sticker reaches the catalogue
  check ([admin_sticker.py:633](../src/bot/handlers/admin_sticker.py#L633)) instead of
  `media.py`'s silent auto-learn.
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
| `check_admin_direct(bot_config_repo, user_id)` | `admin_kb`, `admin_reactions`, `admin_chat_panel`, `admin_defaults`, `admin_sticker` callbacks | Direct repository read, for handlers that already take `BotConfigRepository` via `FromDishka` and so cannot use the filter's injected flag |

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

Prefixes **must** be matched with the trailing colon (`F.data.startswith("adm_wl:")`),
otherwise `adm_wl` also matches `adm_wl_chats`. Two handlers additionally re-check
`parts[0]` because their prefix is a strict prefix of a sibling (`adm_lang` vs
`adm_lang_set`, `adm_costs` vs `adm_costs_verify`).

The table below is the reader's map. The registry is `ROUTES` in
[src/bot/screens/routes.py](../src/bot/screens/routes.py) (§F.6): every prefix a handler
filters on must be in it, which `test_routes.py` checks against the source.

| Prefix | Params | Handler |
|---|---|---|
| `adm_menu:` | `lang` | main menu |
| `adm_lang:` / `adm_lang_set:` | `lang` / `lang:new_lang` | language screen / write |
| `adm_close:` | `lang` | delete panel message |
| `adm_wl:` | `lang` | whitelist menu |
| `adm_wl_chats:` | `lang:page` | whitelisted chats |
| `adm_wl_rm_ask:` / `adm_wl_rm:` | `lang:chat_id:page` | confirm / set `enabled=false` |
| `adm_wl_pending:` | `lang:page` | pending list |
| `adm_wl_apr:` / `adm_wl_rej:` | `lang:attempt_id:page` | approve / reject from list |
| `adm_approve:` / `adm_reject:` | `lang:attempt_id` | approve / reject from the notification card (`access_card.py`); byte-compatible forever |
| `adm_wl_rejected:` | `lang:page` | rejected list |
| `adm_wl_restore:` | `lang:attempt_id:page` | back to `pending` |
| `adm_wl_del_ask:` / `adm_wl_del:` | `lang:attempt_id:page` | confirm / hard delete |
| `adm_stats:` | `lang:period` | statistics (`1h`/`24h`/`7d`) |
| `adm_costs:` / `adm_costs_verify:` | `lang:period` | costs / OpenAI cross-check |
| `adm_health:` | `lang` | run check + render |
| `adm_notif:` / `adm_nstk:` / `adm_ntog:` | `lang` / `lang` / `lang:type` | notifications menu / sticker cycle / bool toggle |
| `adm_pnl:` / `adm_pnl_menu:` | `lang:page` / `lang:chat_id` | chat picker / chat panel |
| `adm_pnl_tgl:` | `lang:chat_id:code` | generic bool toggle |
| `adm_pnl_tol:` / `adm_pnl_tolcancel:` | `lang:chat_id` | tolerance prompt / cancel |
| `adm_defs:` / `adm_defs_tgl:` | `lang` / `lang:code` | global settings / toggle |
| `adm_kb:` / `adm_kb_menu:` / `adm_kb_toggle:` | — | retired: the KB picker and menu are gone, the switch lives in the chat's memory section |
| `adm_kb_orgs:` / `adm_kb_org_rm:` / `adm_kb_org_undo:` | `lang:chat_id:page` / `lang:chat_id:user_id` / `lang:chat_id:user_id:position` | organizer list / remove at once / put back at `position` |
| `adm_kb_org_list:` / `adm_kb_org_pick:` | `lang:chat_id:page` / `lang:chat_id:user_id` | participant list / add the tapped one |
| `adm_kb_org_add:` / `adm_kb_org_x:` | `lang:chat_id` | add prompt (new message, reply keyboard) / leave it from the failure answer |
| `adm_react:` / `adm_react_menu:` / `adm_react_toggle:` | `lang:page` / `lang:chat_id` / `lang:chat_id:field` | reactions picker / menu / toggle |
| `adm_stk_sets:` / `adm_stk_set:` / `adm_stk_view:` | `lang:page` / `lang:set_name:page` / `lang:file_unique_id` | packs / pack / sticker card |
| `adm_stk_back:` | `lang:set_name:page` | leave card, delete sticker message |
| `adm_stk_reanalyze:` / `adm_stk_clr_ask:` / `adm_stk_clr:` | `lang:file_unique_id` | re-analyse / confirm clear / clear |
| `adm_stk_dmchk:` | `lang:file_unique_id` | analyse a DM-checked sticker |
| `adm_rules:` | `lang:page` | rules chat list |
| `ar_list:` / `ar_view:` / `ar_tog:` / `ar_del_ask:` / `ar_del:` / `ar_add:` / `ar_type:` / `ar_cancel:` | see [rules.py](../src/bot/handlers/rules.py) module docstring | rules CRUD (`ar_cancel:` leaves the config prompt) |
| `noop` | — | inert buttons (section headers, page counters, status badges) |

### F.5 FSM states

`AdminStates` ([src/bot/states/admin.py](../src/bot/states/admin.py)):

| State | Set by | Consumed by | Escape |
|---|---|---|---|
| `awaiting_setting_value` | `adm_pnl_tol:` | `admin_chat_panel.handle_chat_panel_tolerance_input` | `adm_pnl_tolcancel:` button, `/cancel`, or a typed `/command` (filtered out — but see the caption caveat in §F.2) |
| `awaiting_kb_organizer` | `adm_kb_org_add:` (data: `kb_chat_id`, `kb_lang`; the prompt is a **new** message carrying a one-time reply keyboard with a `request_users` button, `request_id=REQUEST_IDS.ORG_PICK`) | `admin_kb.handle_kb_organizer_shared` (the `users_shared` service message, filtered on that `request_id` — a pick from another picker never reaches it — and matched in **any** state, registered **before** the reader, which would otherwise take a pick for an empty message and answer «not found»; without this state it writes nothing and answers stale), then `admin_kb.handle_kb_organizer_add_reply` for a forward or `@username` (clears state on entry, **before** the authority check; a failed lookup re-arms it and answers `org.fail`) | the `✖️ Отмена` keyboard label, `adm_kb_org_x:`, a pick from the participant list, `adm_kb_orgs:`, `/cancel`, or a typed `/command`; the `🔍 Найти в Telegram` label arriving as *text* means the client cannot open the picker — answered with the fallback, state kept; a forwarded message whose text starts with `/` or equals a label is still an organizer add, not a command. Every exit through `admin_kb` clears the state, sends `ReplyKeyboardRemove` on a line of its own and then the organizer list as a new message (a message cannot carry both keyboards). `adm_kb_org_x:` tapped while some *other* dialog is open shows the list and leaves that dialog alone |
| `awaiting_rule_config` | `ar_type:` | `rules.handle_rule_config_input` (`IsAdmin`-gated since TD-049) | `ar_cancel:` button, `/cancel`, any `/command` incl. in a caption, or navigating back to any rules screen |
| `awaiting_sticker_edit`, `awaiting_sticker` | — | — | declared, currently unused |
| `awaiting_rule_words`, `awaiting_rule_text`, `awaiting_rule_react` | — | — | declared for the button-driven rule builder (A3); unused until it lands |

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
| `tests/unit/test_routes.py` | the callback table, 64-byte budgets, routing of legacy / new / garbled payloads, router order, the retired-write and "Invalid data" ratchets |
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

Notification defaults live in two places that must agree:
`AdminRepository._NOTIFICATION_DEFAULTS` and `AbuseNotificationService._NOTIFICATION_DEFAULTS`
— `{"sticker": "on", "unauthorized": True, "jailbreak": True, "blacklist": True,
"ai_fallback": True}`.

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
- **The sticker card deletes messages as it navigates** (previous sticker message via a DB
  lookup, then the card itself) to avoid orphaned stickers accumulating in the DM. Failures
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

Files: [src/bot/screens/hub.py](../src/bot/screens/hub.py) (the hub and `show_hub`),
[src/bot/handlers/admin.py](../src/bot/handlers/admin.py) (whitelist, statistics, costs,
health, notifications, language),
[src/bot/handlers/admin_defaults.py](../src/bot/handlers/admin_defaults.py) (global settings,
which renders from the registry in §A2.1).

`handle_admin_command` and `handle_menu` draw the hub through `show_hub`. `handle_language_set`
still draws it through `admin._MENU_TITLE` + `main_menu_keyboard`, so the hub exists in two
places until this area folds the language screen into `show_hub`;
`TestHubToday` in `test_admin_handler.py` pins the two to the same look meanwhile. `hub.py` also declares
`sample_hub(lang)` and `SAMPLE_HUB_EXEMPT`, which the stale sample draws (§F.6); a redesigned
hub replaces both there.
`AdminRepository.count_attempts(status)` counts what the pending / rejected pages list (same
predicate), for a count on an entry button.

---

## A2 — Chats and chat sections

Files: [src/bot/handlers/admin_chat_panel.py](../src/bot/handlers/admin_chat_panel.py)
(`show_chat`, `show_chats`, the per-chat panel and the tolerance prompt),
[src/bot/handlers/admin_reactions.py](../src/bot/handlers/admin_reactions.py),
[src/bot/screens/sections.py](../src/bot/screens/sections.py) (which panel section each
setting code is shown in — the UI's grouping, deliberately separate from
`settings_fields.FieldGroup`, which the defaults screen and the toggles still read).

### A2.1 Settings-field registry

`settings_fields.py` is the single source of truth for grouping, labels, `callback_data`
codes, value types and the legacy split. Both the chat panel and the defaults screen render
from it.

- **26 fields** = 27 mergeable `ChatConfig` fields (`_CHAT_CONFIG_FIELDS`) − `enabled`.
  `kb_organizer_ids` is excluded from the registry too, but it was never in the merge to
  begin with — it is written straight through `ChatSettingsRepository`.
- **13 legacy** (migration 001 columns that still carry a SQL `DEFAULT`): `ensure_exists()`
  materialises a value on first contact, permanently shadowing `bot_config.default_*`.
  Therefore they never show the inherited marker and never appear on the global screen.
- **13 non-legacy** (nullable, no `DEFAULT`) — NULL honestly means "inherited".
- Only `FieldType.BOOL` fields get a toggle; the rest render read-only, except
  `tolerance_level`, which has a dedicated FSM editor (ADR-0008 Decision 10). A generic
  non-BOOL editor is the deferred F-1 work item.

Every count on this page is prose about a number the code computes (`len(CHAT_SETTINGS_FIELDS)`,
`len(new_fields())`), so treat the code as authoritative and fix the prose when it drifts. It has
drifted twice: the docstrings that said "11 new fields" predated `tolerance_level`, and the 25/12
figures predated `chunks_enabled` (S5b). `tests/unit/test_settings_fields.py` pins the numbers —
if a count here disagrees with that file, that file is right.

Three fields are **link-only** on the chat panel: `kb_enabled`, `reactions_enabled`,
`reactions_history_enabled`. `handle_chat_panel_toggle` explicitly refuses them
(`_LINK_ONLY_KEYS`) so their write path stays in the KB/Reactions handlers and is not
duplicated. On the *defaults* screen they are ordinary toggles — there is no global KB or
Reactions sub-panel to link to.

#### Effective-value resolution

Merge order is YAML → `bot_config.default_*` → `chat_settings` → `ChatConfig`. Three
resolution paths exist and must stay consistent:

- `ChatConfigService.get_config()` — used by the chat panel for everything except the link
  rows.
- `_fresh_effective()` / `_effective_kb_enabled()` / `_resolve_toggles()` — direct raw-row +
  global-default reads used for the KB/Reactions rows, mirroring each other.
- `_resolve_values()` in `admin_defaults.py` — `bot_config.get_defaults()` with the
  `ChatConfig(chat_id=0)` dataclass as fallback (layer 1).

Toggles always flip the **effective** value, never the raw column, so a chat that is on via
the global default turns off on the first tap. `toggle_bool_field` performs the negation
inside the `UPDATE` (`NOT COALESCE(col, $effective)`), which closes the double-tap race, and
returns `None` when no row matched — the handler reports that as an error rather than a
success toast.

#### Cache invalidation

- Per-chat write → `chat_config_service.invalidate(chat_id)`.
- Global default write → `chat_config_service.invalidate_all()` (the shared `_global_cache`;
  per-chat `invalidate()` does not touch it). Easy to get backwards by analogy.

Today both are defensive: `ChatConfigService` is Dishka `Scope.REQUEST`, so no cache survives
a single update (measured 2026-08-06). They become load-bearing if the scope moves to
`Scope.APP` — at which point the two write sites that do **not** invalidate
(`approve_attempt` in `access_card.py`, `handle_wl_remove` in `admin.py`) must be fixed in
the same commit. Tracked as TD-046.

---

## A3 — Rules

Files: [src/bot/handlers/rules.py](../src/bot/handlers/rules.py) (`show_rules`, rules CRUD,
the JSON config prompt), [src/bot/keyboards/rules.py](../src/bot/keyboards/rules.py).
`RulesRepository.count_for_chat(chat_id)` counts every rule of a chat, enabled or not — the
number the list pages through. `ar_tog:` is `kept` and targets `rule`: the card's pause/resume
writes it. What the redesign drops is the list's per-row 🔄, which writes the same payload shape;
once the list stops writing it, an old list button toggles the rule and opens its card. The three `awaiting_rule_*` states (§F.5) are declared for
this area's rule builder.

---

## A4 — Knowledge base organizers

Files: [src/bot/handlers/admin_kb.py](../src/bot/handlers/admin_kb.py) (reads and writes
`chat_settings.kb_organizer_ids`, picks the screen),
[src/bot/screens/orgs.py](../src/bot/screens/orgs.py) (the four screens as data: `orgs`,
`org.list`, `org.add`, `org.fail`),
[src/bot/keyboards/admin_kb.py](../src/bot/keyboards/admin_kb.py) (only what the handlers
match: the picker's `request_id` and the reply labels). The `/remember` and `/kb` keyboards
are in `keyboards/kb.py`, outside the panel. The prompt's state is described in §F.5.

- **Remove is undo, not confirm.** `adm_kb_org_rm:` writes at once; the result carries
  `adm_kb_org_undo:` with the person's former index as a fourth field, so `↩️ Вернуть` puts
  them back in place. A second undo, or an undo after someone re-added them, writes nothing.
  The page shown after either is derived from the index (`orgs.page_of`), not from the payload.
- **Names are looked up per page.** `_names` asks Telegram only for the people on the page
  being drawn, concurrently (`asyncio.gather` over `resolve_display_name`); a name already
  known from the event (a pick, a forward) is not asked again. A failed lookup reads
  «участник {id}».
- **Names are clipped by width, not length** (`orgs.clip` to `orgs.NAME_PX`, 90 % of a
  one-button row by lint's estimate). The estimate is a per-script average; rendered at
  390 px, long mixed names clipped to the full row still overflowed by up to 20 px and fit
  from 92 %. A name of one wide letter repeated still overflows; Telegram's client cuts it.
- **Leaving the prompt is two messages.** `_leave_prompt` sends the line carrying
  `ReplyKeyboardRemove`, then the `orgs` screen with inline buttons as a new message — the
  same in every exit, callback or message.

---

## A5 — Stickers

Files: [src/bot/handlers/admin_sticker.py](../src/bot/handlers/admin_sticker.py),
[src/bot/keyboards/admin_sticker.py](../src/bot/keyboards/admin_sticker.py). Its two traps —
it runs before `media` (§F.2), and its reply handler consumes every text reply in an admin
DM (§F.8) — are shared-machinery facts and stay there.

---

## A6 — Entry and cards

### A6.1 Command menus

What Telegram offers as autocomplete is *not* derived from the handlers at runtime — it is pushed
state living on Telegram's side, declared by `COMMANDS` in
[src/bot/command_registry.py](../src/bot/command_registry.py) and rendered by
[src/bot/commands.py](../src/bot/commands.py).

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

### A6.2 Notification plumbing

| Notification | Sender | Recipients | Gate |
|---|---|---|---|
| Unauthorized | `AbuseNotificationService.notify_unauthorized`, called from `AccessRequestService.submit` (middleware and `my_chat_member`) | all admins | `admin_settings.notifications.unauthorized` + 30-min per-chat in-memory cooldown + no prior rejection |
| Jailbreak / Blacklist / AI fallback | `AbuseNotificationService` | all admins | matching `admin_settings` key |
| New sticker | `StickerLearningService.notify_admins`, called from `media.py` | all admins | `admin_settings.notifications.sticker` ∈ {on, detailed}; skipped when `analysis_failed` or the sticker was already known |
| Health alert | `HealthChecker._send_alert` | **first admin only** | not switchable |

The unauthorized card is sent by `access_requests.py`, for an unknown chat's message (via the
middleware) and for the bot being added to a group (via `my_chat_member`). Its Approve/Reject
buttons are built by `access_keyboard(lang, attempt_id)` in `keyboards/access_card.py` and
handled in `handlers/access_card.py` — the same `approve_attempt` / `reject_attempt` helpers
back both the card and the panel list, and both return `None` when the attempt is no longer
`pending`, which is what produces the "already handled" alert.

The sticker notification sends the sticker first, then the description as a reply to it, and
records the pair in `admin_sticker_notifications` so a later text reply resolves to the right
`file_unique_id` (with a regex over the `🆔` line as a fallback).
