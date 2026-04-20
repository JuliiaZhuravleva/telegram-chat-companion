# Tech Debt

Track deferred improvements. Review monthly.

## Critical
_Blocks feature work or security risk._

- [ ] **TD-001**: Migration drift — `sql/schema.sql` declares `schema_version=4` but alembic has 11 migrations; fresh `psql -f sql/schema.sql` install leaves DB mid-schema
  - **Priority:** Critical
  - **Source:** Architecture audit 2026-04-20 (see [docs/FUNCTIONALITY.md §7.3 A-1](docs/FUNCTIONALITY.md))
  - **Created:** 2026-04-20
- [ ] **TD-002**: `tests/integration/` folder is empty (only placeholder conftest.py) — zero integration tests, despite architecture being chosen to make testcontainers+pgvector testing viable
  - **Priority:** Critical
  - **Source:** Architecture audit 2026-04-20 ([§7.3 A-2](docs/FUNCTIONALITY.md))
  - **Created:** 2026-04-20
- [ ] **TD-003**: `/summary` in DM is silently ignored — no feedback, no response (filter `F.chat.type.in_({"group","supergroup"})` drops the update)
  - **Priority:** Critical (user-facing)
  - **Source:** Live QA 2026-04-20 ([§7.1 UX-1](docs/FUNCTIONALITY.md))
  - **Created:** 2026-04-20
- [ ] **TD-004**: Rule `🗑` delete in admin panel still executes in one click with no confirmation
  - **Priority:** Critical (destructive, no undo)
  - **Source:** Live QA 2026-04-20 ([§7.1 UX-2, §7.4 S-13](docs/FUNCTIONALITY.md))
  - **Created:** 2026-04-20
  - **Note:** Whitelist `❌` remove was fixed in commit 02d63e4 (2026-04-21) with a confirmation step; rule-delete still needs the same treatment

## High
_Causes recurring problems._

- [ ] **TD-005**: Full prioritized recommendations list — ~70 P1 items across UX, features, architecture, security — catalogued in [docs/FUNCTIONALITY.md §7](docs/FUNCTIONALITY.md)
  - **Priority:** High (pick from the P1 subset each sprint)
  - **Source:** Session 2026-04-20 (live QA + static subagent audits)
  - **Created:** 2026-04-20

## Medium
_Slows development but doesn't block._

## Low
_Track for later._

## Resolved
_Keep 90 days then remove._

---

### When to Add
- Skipped tests to meet deadline
- Used workaround instead of proper fix
- Copy-paste instead of abstract
- Disabled linter rules
- Known performance issue deferred
- Bug acknowledged but deprioritized

### Entry Format
```
- [ ] **TD-NNN**: Brief description
  - **Priority:** Critical | High | Medium | Low
  - **Source:** what identified this (review, bug report, etc.)
  - **Created:** YYYY-MM-DD
```
