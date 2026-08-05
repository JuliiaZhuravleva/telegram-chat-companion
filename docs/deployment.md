# Deployment

**Merging to `main` deploys to production.** No one runs a deploy command; a scheduled job on the
production host notices the branch moved and ships it, typically within five minutes.

```
merge to main
   ↓
every GitHub check run on that exact commit green      ← lint · typecheck · test · gitleaks
   ↓
migration rehearsed on a throwaway copy of the LIVE database
   ↓
build → migrate in a one-off container → start services
```

The deploy harness itself lives outside this repository (it also runs unrelated projects, holds the
secrets and knows the host). This document is the half that *is* this repository's business: what
the code must keep true for the pipeline to work, and what the gates do and do not catch.

---

## What merging means now

The review checkpoint is the **merge**, not the deploy. Previously a human decided when to ship;
now the merge button is that decision, and it applies to any pull request — including one from an
outside contributor. Green CI plus a clean migration rehearsal is the entire guard between a merged
PR and the production bot.

Practical consequence: prefer merging when you can watch, and treat "approve" and "deploy" as the
same act rather than two.

**`main` is protected by a ruleset** (active since 2026-08-04): a pull request is required, the
four checks below must pass, and force-push and deletion are blocked. That is what makes "the merge
is the deliberate act" a property of the platform rather than a habit of whoever holds push access
— worth stating explicitly, because for the first days of this pipeline it was only the habit, and
every guarantee on this page still begins one step later, at *"a commit appeared on `main`"*.

One consequence to keep in view: the ruleset pins the four check names too, so renaming a CI job is
a **three**-place change — the workflow, the harness config on the host, and the ruleset's required
contexts. Rename one of the three and you either stall the deployer or block every merge.

---

## The contract this repository must keep

| What | Why it is load-bearing |
|---|---|
| CI job names `lint`, `typecheck`, `test` (`ci.yml`) and `gitleaks` (`secrets-scan.yml`) | The deployer waits for check runs **by name**. Renaming a job does not fail the deploy — it makes it wait forever, because "no red checks" is also true when the check never ran. Rename here ⇒ rename in the harness config. |
| The **contents** of those jobs, not only their names | Renaming a job fails safe. *Gutting* one fails unsafe — see "Changes to `.github/` are held" below. |
| A single linear `alembic` head | Branched heads mean `alembic upgrade head` is ambiguous; the release runs it unattended. |
| Migrations are **forward-only** | There is no automatic rollback (below). A migration that destroys data is a one-way door. |
| `docker-compose.prod.yml` stays **standalone**, not a layer over `docker-compose.yml` | Compose merges list options (`ports`, `volumes`) by *appending*, so a layered file would keep the dev port publications in production. |
| Service names `postgres`, `bot`, `backup` | The harness is configured with these names: which service is the database, which one the migration runs in. Renaming a service breaks the release. |
| `${COMPANION_DATA_DIR}` remains the single parametrized data root | Every persistent bind mount must live under it. The harness refuses to deploy a compose file that bind-mounts a host path outside the project and its data root, and the rehearsal repoints this one variable to give the throwaway database its own disk. |
| No service asks for host-level privileges | `privileged`, `cap_add`, `devices`, `device_cgroup_rules`, `security_opt`, and `network_mode`/`pid`/`ipc`/`userns_mode` set to `host` each reach past the container without a single bind-mount line, so the deployer rejects a compose that renders any of them — and rejects just as hard when it cannot parse the rendered layout at all, because an unattended deploy is exactly where "someone will read the compose first" stops being true. `cap_drop` is fine; that is hardening. Neither compose file uses a rejected key today. |
| `alembic upgrade head` stays idempotent | It runs twice on a deploy: once in a one-off container (so a bad migration aborts cleanly instead of crash-looping the bot), then again from the image `CMD`. |

---

## A new setting does not arrive with the merge

The container's environment is rendered on the host from a mapping the harness owns; this
repository never carries the values. So adding a variable to `config/.env.example` and merging it
changes nothing in production. A merged feature that depends on a new variable **deploys green and
stays dark** — reporting its missing setting — until someone adds one line to that mapping on the
host and re-runs the release.

That is the intended trade (no secrets in the repository), but it makes any new setting a two-part
change: the code here, and one line the operator applies. Say so in the pull request description —
the deploy gives no hint, and neither does a green checkmark.

Observed 2026-08-04: PR #17 added the admin panel's cost-verification button, merged, and deployed
cleanly. The button reported the missing setting until `OPENAI_ADMIN_API_KEY` and
`OPENAI_PROJECT_ID` were added to the host mapping and the release was re-run. Nothing was broken
in between — which is the point, and also why it is easy to miss.

---

## Changes to `.github/` are held for a human

A workflow file is not ordinary code: it *is* the gate that authorises the deploy, and it lives in
the repository the gate protects. GitHub runs a `push` workflow from the **pushed commit**, so once
a commit that edits `.github/` is on `main`, the checks approving it are the ones that commit
brought. Keep the job names, replace the bodies with `exit 0`, and the deployer sees four green
checks. The pull request looks green too: `pull_request` takes the workflow from the pull request's
own merge commit, so a fork PR is judged by the workflow that fork supplied.

`pull_request_target` is *not* the answer to that, despite running the base branch's workflow: it
also hands the run repository secrets, which is exactly what must not happen to code from a fork.
This repository does not use it anywhere, and that is the correct state.

So the deployer refuses to ship it automatically: a commit touching `.github/` is **held**, the
maintainer is notified, and shipping it requires a deliberate manual release. It fails closed — if
the check itself cannot be evaluated (API error, truncated diff), the commit is held rather than
waved through.

**Read diffs that touch `.github/` with your eyes, not by the checkmarks.** Green checks on such a
PR are evidence about the workflow the PR itself supplied.

This does not extend to application code or migrations, and cannot: a deployer ships what was
merged. The rehearsal proves a migration *runs*; it does not prove a migration is *benign*. For
anything from an untrusted contributor, the review is the control — see below.

---

## What the gates catch — and what they don't

**GitHub CI** runs the test suite against an **empty** database. That is the right thing for tests
and the reason it cannot be the only gate: a migration that only fails on real rows — a `NOT NULL`
added to a column that already has nulls, a unique index over values that turn out not to be
unique, a backfill that never finishes at real volume — is green here and breaks production.

**The rehearsal** closes exactly that gap. Before production is touched, the harness dumps the live
database (read-only), restores it into a throwaway instance with its own network and disk, verifies
the copy really landed (table count, current revision, exact row counts of the largest tables —
an empty copy would otherwise "pass" any migration), and runs the real migration there. A failure
stops the deploy with production untouched.

**Neither gate catches runtime behaviour.** A change that passes tests, migrates cleanly and then
misbehaves against real traffic will deploy. The bot's container healthcheck and the post-deploy
check will report it; nothing reverts it.

---

## Failure modes

| What failed | What happens |
|---|---|
| A check run is red | No deploy. That commit is marked handled — it is not retried every few minutes. Push a fix; the new commit starts the pipeline over. |
| A check never reports (renamed/removed job) | No deploy, and a warning after the configured wait. This is the failure mode to expect after touching workflow files. |
| The commit touches `.github/` | **Held**, not shipped: the maintainer is notified and must release it deliberately. Also what happens when the check cannot be evaluated at all — it fails closed. |
| The rehearsal fails | No deploy, production untouched. The failure output identifies the migration. Note the deployed *code* and the checkout on the host can now differ — converge by fixing forward, not by resetting. |
| The build or release fails | Production keeps running the previous images (the build fails before anything is recreated), but `main` has already moved. Re-release a fix; do not expect a revert to happen on its own. |
| A service is unhealthy after the deploy | Reported, **not** acted on. Deliberate: an automatic revert triggered by a false-positive health check is its own outage. |

---

## No automatic rollback

Redeploying an older image does not un-run a migration that already succeeded, so "roll back" is
not a safe generic action and none is wired up. Recovery is one of:

- revert the commit on `main` — the next cycle ships the revert, which is the normal path; or
- have the operator release a known-good commit by hand.

Either way, if the bad deploy moved the schema, the fix has to be a **new forward migration**.

---

## Related

- [Database Backups](backups.md) — the nightly encrypted dump; also the restore path if a migration
  does destroy something.
- `docker-compose.prod.yml` — the production topology, with the reasoning for each difference from
  the dev compose in comments.
