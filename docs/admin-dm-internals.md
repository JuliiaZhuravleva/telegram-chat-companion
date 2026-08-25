# Admin DM — Implementation Reference

Companion to [admin-dm-guide.md](admin-dm-guide.md), which describes *what* the admin surface
does. This document describes *how* it is wired: routers, permission checks, callback
grammar, state machines, storage, and the traps that have already bitten.

---

## 1. Module map

| File | Owns |
|---|---|
| [src/bot/handlers/admin.py](../src/bot/handlers/admin.py) | `/admin`, `/settings`, `/costs`, main menu, whitelist, statistics, costs + OpenAI verification, health, notifications, language, close |
| [src/bot/handlers/admin_chat_panel.py](../src/bot/handlers/admin_chat_panel.py) | Per-chat settings panel (`adm_pnl*`), including the `tolerance_level` FSM flow |
| [src/bot/handlers/admin_defaults.py](../src/bot/handlers/admin_defaults.py) | Global-settings screen (`adm_defs*`) |
| [src/bot/handlers/admin_kb.py](../src/bot/handlers/admin_kb.py) | Knowledge-base panel and organizer management (`adm_kb*`) |
| [src/bot/handlers/admin_reactions.py](../src/bot/handlers/admin_reactions.py) | Reactions panel (`adm_react*`) |
| [src/bot/handlers/admin_sticker.py](../src/bot/handlers/admin_sticker.py) | Sticker browser, re-analysis, DM sticker check, description-merge reply (`adm_stk*`) |
| [src/bot/handlers/rules.py](../src/bot/handlers/rules.py) | Rules engine UI (`adm_rules:`, `ar_*`) |
| [src/bot/keyboards/admin*.py](../src/bot/keyboards/) | All inline keyboards; one module per handler module |
| [src/bot/settings_fields.py](../src/bot/settings_fields.py) | The 25-field registry shared by the chat panel and the defaults screen |
| [src/bot/command_registry.py](../src/bot/command_registry.py) | Which slash commands exist, where Telegram advertises them, and the pure code↔registry audit |
| [src/bot/commands.py](../src/bot/commands.py) | Pushes that registry to Telegram, deletes stale per-admin scopes, reads back and diffs |
| [src/bot/filters/admin.py](../src/bot/filters/admin.py) | `IsAdmin` message filter |
| [src/bot/utils.py](../src/bot/utils.py) | `check_admin_direct`, `is_bot_chat_admin`, `resolve_display_name`, `safe_edit_text` |
| [src/bot/middleware/access_control.py](../src/bot/middleware/access_control.py) | Whitelist gate, `is_admin` injection, unauthorized notification |
| [src/database/repositories/admin.py](../src/database/repositories/admin.py) | Attempts, stats, admin language, notification settings |

### Router order

`handlers/__init__.py` includes routers in this order, and the order is load-bearing —
in aiogram the first handler whose filters match **consumes** the update:

```
fsm_cancel → admin_sticker → admin_kb → admin_reactions → admin_chat_panel → admin_defaults
→ admin → rules → commands → callbacks → chat_events → reactions → media → message
```

Two consequences that are easy to break:

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

---

## 2. Permission checks — three shapes, all reading `bot_config.admin_ids`

There is no single admin gate. Which one a handler uses depends on what it can inject:

| Mechanism | Where | Notes |
|---|---|---|
| `IsAdmin()` filter | `/admin`, `/settings`, `/costs`, DM sticker handlers | Uses middleware-injected `is_admin` when present, otherwise queries `bot_config` through the Dishka container. Filters run *before* inner middleware, hence the fallback |
| `_guard_admin(kwargs, callback)` | `admin.py`, `rules.py` (`_check_admin`) | Reads `data["is_admin"]` set by `AccessControlMiddleware`, plus a private-chat check; logs `admin_access_denied` for the audit trail |
| `check_admin_direct(bot_config_repo, user_id)` | `admin_kb`, `admin_reactions`, `admin_chat_panel`, `admin_defaults`, `admin_sticker` callbacks | Direct repository read, for handlers that already take `BotConfigRepository` via `FromDishka` and so cannot use the filter's injected flag |

All of them funnel into `parse_admin_ids()` over the `admin_ids` value. **Every** callback
handler additionally verifies `callback.message.chat.type == "private"`.

`AccessControlMiddleware` is what lets an admin DM through a disabled chat row
([access_control.py:58-61](../src/bot/middleware/access_control.py#L58-L61)) — and note the
middleware only understands `Message` and `CallbackQuery`; other update types are ungated by
it (see CLAUDE.md).

---

## 3. Callback-data grammar

Pattern: `adm_{action}:{lang}:{param...}`, language embedded so screens are stateless.
Telegram's 64-byte limit is why settings fields are addressed by a ≤4-char `code`
(`settings_fields.py`) rather than by column name.

Prefixes **must** be matched with the trailing colon (`F.data.startswith("adm_wl:")`),
otherwise `adm_wl` also matches `adm_wl_chats`. Two handlers additionally re-check
`parts[0]` because their prefix is a strict prefix of a sibling (`adm_lang` vs
`adm_lang_set`, `adm_costs` vs `adm_costs_verify`).

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
| `adm_approve:` / `adm_reject:` | `lang:attempt_id` | approve / reject from notification card |
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
| `adm_kb:` / `adm_kb_menu:` / `adm_kb_toggle:` | `lang:page` / `lang:chat_id` / `lang:chat_id` | KB picker / menu / toggle |
| `adm_kb_orgs:` / `adm_kb_org_rm:` | `lang:chat_id:page` / `lang:chat_id:user_id` | organizer list / remove |
| `adm_kb_org_add:` / `adm_kb_org_list:` / `adm_kb_org_pick:` | `lang:chat_id[:page][:user_id]` | add prompt / picker / pick |
| `adm_react:` / `adm_react_menu:` / `adm_react_toggle:` | `lang:page` / `lang:chat_id` / `lang:chat_id:field` | reactions picker / menu / toggle |
| `adm_stk_sets:` / `adm_stk_set:` / `adm_stk_view:` | `lang:page` / `lang:set_name:page` / `lang:file_unique_id` | packs / pack / sticker card |
| `adm_stk_back:` | `lang:set_name:page` | leave card, delete sticker message |
| `adm_stk_reanalyze:` / `adm_stk_clr_ask:` / `adm_stk_clr:` | `lang:file_unique_id` | re-analyse / confirm clear / clear |
| `adm_stk_dmchk:` | `lang:file_unique_id` | analyse a DM-checked sticker |
| `adm_rules:` | `lang:page` | rules chat list |
| `ar_list:` / `ar_view:` / `ar_tog:` / `ar_del_ask:` / `ar_del:` / `ar_add:` / `ar_type:` / `ar_cancel:` | see [rules.py](../src/bot/handlers/rules.py) module docstring | rules CRUD (`ar_cancel:` leaves the config prompt) |
| `noop` | — | inert buttons (section headers, page counters, status badges) |

---

## 4. FSM states

`AdminStates` ([src/bot/states/admin.py](../src/bot/states/admin.py)):

| State | Set by | Consumed by | Escape |
|---|---|---|---|
| `awaiting_setting_value` | `adm_pnl_tol:` | `admin_chat_panel.handle_chat_panel_tolerance_input` | `adm_pnl_tolcancel:` button, `/cancel`, or a typed `/command` (filtered out — but see the caption caveat in §1) |
| `awaiting_kb_organizer` | `adm_kb_org_add:` | `admin_kb.handle_kb_organizer_add_reply` (clears state on entry, **before** the authority check) | picking from the participant list, `/cancel`, or a typed `/command`; a forwarded message whose text starts with `/` is still an organizer add, not a command |
| `awaiting_rule_config` | `ar_type:` | `rules.handle_rule_config_input` (`IsAdmin`-gated since TD-049) | `ar_cancel:` button, `/cancel`, any `/command` incl. in a caption, or navigating back to any rules screen |
| `awaiting_sticker_edit`, `awaiting_sticker` | — | — | declared, currently unused |

Validation posture is *reject, do not clamp*: tolerance input outside `[0.0, 1.0]` and
non-JSON rule configs re-prompt rather than silently coercing.

`handle_admin_sticker_reply` carries `StateFilter(None)` so an active FSM dialog answered as a
*reply* is not eaten as a sticker-description correction.

---

## 4a. Command menus

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

## 5. Storage

### `bot_config` (global key/value)

| Key | Shape | Used for |
|---|---|---|
| `admin_ids` | JSON list of ints | Who is an admin (every check) |
| `admin_settings` | JSON `{"lang": "ru", "notifications": {...}}` | Panel language + notification switches, both global |
| `command_scopes_pushed` | JSON list of ints | Chat ids currently holding a per-admin command menu (§4a) |
| `command_sync_last_alert` | sha256 string or null | Digest of the last command-drift alert sent (§4a) |
| `default_<field>` | scalar | Merge layer 2 for the 12 non-legacy per-chat fields |
| `health_check_*` | scalars | Health scheduler config |

Notification defaults live in two places that must agree:
`AdminRepository._NOTIFICATION_DEFAULTS` and `AbuseNotificationService._NOTIFICATION_DEFAULTS`
— `{"sticker": "on", "unauthorized": True, "jailbreak": True, "blacklist": True,
"ai_fallback": True}`.

### `chat_settings`

Per-chat columns; `enabled` is the whitelist gate and is deliberately **not** in the field
registry (there is no `default_enabled`). `kb_organizer_ids` is a JSONB list written directly
by the KB panel, never merged through `ChatConfig`.

### `unauthorized_attempts`

`status ∈ {pending, approved, rejected}`. Both the pending and rejected pages exclude chats
that are currently enabled, so a stale row cannot resurface after an approval elsewhere.
`has_rejected_attempt(chat_id)` is the "blacklist" predicate the middleware consults before
logging or notifying.

### Others

`health_log` (latest row feeds the health screen; pruned to 30 days),
`response_log` (costs and response counts), `chat_messages` (message/active-chat counts),
`admin_sticker_notifications` (maps a DM message id → `file_unique_id`, so a reply can be
resolved without parsing text), `admin_sticker_session` (wizard session table, largely
vestigial).

---

## 6. Settings-field registry

`settings_fields.py` is the single source of truth for grouping, labels, `callback_data`
codes, value types and the legacy split. Both the chat panel and the defaults screen render
from it.

- **25 fields** = 26 mergeable `ChatConfig` fields − `enabled` − `kb_organizer_ids`.
- **13 legacy** (migration 001 columns that still carry a SQL `DEFAULT`): `ensure_exists()`
  materialises a value on first contact, permanently shadowing `bot_config.default_*`.
  Therefore they never show the inherited marker and never appear on the global screen.
- **12 non-legacy** (nullable, no `DEFAULT`) — NULL honestly means "inherited".
- Only `FieldType.BOOL` fields get a toggle; the rest render read-only, except
  `tolerance_level`, which has a dedicated FSM editor (ADR-0008 Decision 10). A generic
  non-BOOL editor is the deferred F-1 work item.

Several docstrings still say "11 new fields" — they predate `tolerance_level`. The count is
computed from `new_fields()`, so the code is right and the prose is stale.

Three fields are **link-only** on the chat panel: `kb_enabled`, `reactions_enabled`,
`reactions_history_enabled`. `handle_chat_panel_toggle` explicitly refuses them
(`_LINK_ONLY_KEYS`) so their write path stays in the KB/Reactions handlers and is not
duplicated. On the *defaults* screen they are ordinary toggles — there is no global KB or
Reactions sub-panel to link to.

### Effective-value resolution

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

### Cache invalidation

- Per-chat write → `chat_config_service.invalidate(chat_id)`.
- Global default write → `chat_config_service.invalidate_all()` (the shared `_global_cache`;
  per-chat `invalidate()` does not touch it). Easy to get backwards by analogy.

Today both are defensive: `ChatConfigService` is Dishka `Scope.REQUEST`, so no cache survives
a single update (measured 2026-08-06). They become load-bearing if the scope moves to
`Scope.APP` — at which point the two write sites in `admin.py` that do **not** invalidate
(`_do_approve`, `handle_wl_remove`) must be fixed in the same commit. Tracked as TD-046.

---

## 7. Notification plumbing

| Notification | Sender | Recipients | Gate |
|---|---|---|---|
| Unauthorized | `AbuseNotificationService.notify_unauthorized`, called from the middleware | all admins | `admin_settings.notifications.unauthorized` + 30-min per-chat in-memory cooldown + no prior rejection |
| Jailbreak / Blacklist / AI fallback | `AbuseNotificationService` | all admins | matching `admin_settings` key |
| New sticker | `StickerLearningService.notify_admins`, called from `media.py` | all admins | `admin_settings.notifications.sticker` ∈ {on, detailed}; skipped when `analysis_failed` or the sticker was already known |
| Health alert | `HealthChecker._send_alert` | **first admin only** | not switchable |

The unauthorized card's Approve/Reject buttons are built by `access_keyboard(lang,
attempt_id)` in the admin keyboards module — the same `_do_approve` / `_do_reject` helpers
back both the card and the panel list, and both return `None` when the attempt is no longer
`pending`, which is what produces the "already handled" alert.

The sticker notification sends the sticker first, then the description as a reply to it, and
records the pair in `admin_sticker_notifications` so a later text reply resolves to the right
`file_unique_id` (with a regex over the `🆔` line as a fallback).

---

## 8. Rendering conventions and traps

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

---

## 9. Rate limits and cooldowns

| Limit | Value | Scope | Storage |
|---|---|---|---|
| Unauthorized notification | 30 min | per chat | in-memory dict in the middleware instance, pruned above 1000 entries |
| OpenAI cost verification | 10 s | per admin | in-memory dict in `admin.py`, capped at 100 entries, `time.monotonic()` |
| Failed sticker-set registration | 5 min | per set | in-memory TTL dict in `media.py` |

All three are process-local and reset on restart — deliberately, since none of them protects
anything that a restart would make unsafe.

---

## 10. Tests

- `tests/unit/` — one `test_admin_*_handler.py` / `test_admin_*_keyboards.py` pair per admin
  module (keyboard construction, callback parsing, permission-denial paths), plus
  `test_settings_fields.py` for the registry invariants (code length/uniqueness, legacy
  split) and `test_admin_sticker_dm_router_order.py` / `test_admin_tolerance_fsm_escape.py`
  for the two ordering traps in §1 and §4.
- `tests/integration/` — against a real PostgreSQL via testcontainers:
  `test_admin_repository.py` (attempts lifecycle and sticker-notification records),
  `test_admin_chat_panel_toggle.py`, `test_admin_defaults_toggle.py`,
  `test_kb_enabled_toggle.py` (effective-value toggling across the merge layers).
- Mock shapes for callback/message objects and the OWASP findings they guard are described in
  the project memory note *Admin handler test patterns*.

When adding a screen, the two checks that catch most regressions are: the prefix does not
collide with a sibling (trailing colon), and the handler is registered on a router that runs
before the generic `message`/`media` routers if it consumes a plain message.
