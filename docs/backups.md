# Database Backups

Nightly encrypted `pg_dump` of the bot's database, uploaded off-host with
[rclone](https://rclone.org/) (Google Drive by default, but rclone speaks ~70
other backends and only the config below changes).

Everything ships in the repository and runs as a compose service, so a fresh
deploy has backups as soon as the environment is filled in. Nothing runs until
`BACKUP_ENABLED=true`.

```
pg_dump -Fc  ->  verify  ->  manifest  ->  age encrypt  ->  rclone upload  ->  prune
```

| Piece | Where |
|---|---|
| Backup script | `scripts/backup-db.sh` |
| Restore script | `scripts/restore-db.sh` |
| Daily loop | `scripts/backup-scheduler.sh` (container entrypoint) |
| Staleness healthcheck | `scripts/backup-healthcheck.sh` |
| Image | `docker/backup/Dockerfile` — `postgres:16-alpine` + `age` + `rclone` + `jq` |
| Service | `backup` in both compose files (always-on in prod, `--profile backup` in dev) |

---

## Why it is built this way

**The dump is encrypted before it leaves the host, to a public key.** It carries
the full message history of real group chats — private messages, usernames, chat
titles, admin ids. Once that reaches a cloud drive it is readable by every
integration the account has ever granted Drive scope to. Encrypting to an *age
recipient* means the production host can create backups it cannot itself read:
compromising the mini does not hand over the archive.

The trade is real and worth stating plainly: **lose the private key and every
backup is permanently unreadable.** There is no recovery path, by design. Put it
in your password manager before you turn backups on.

**rclone is configured through environment variables, not `rclone.conf`.** In
production `.env` is rendered by the deploy harness from its secrets store,
while the harness refuses to mount paths outside the project's persistent-data
root. Environment config lets the Drive token travel the same route as every
other secret instead of needing a new one.

**It is a container, not a crontab.** The harness only knows `docker compose up
-d`. A host crontab would live outside the repository and outside the deploy, so
it would quietly stop tracking the code.

**Backups are never pruned below `BACKUP_MIN_KEEP` copies.** Age-based retention
alone turns an outage into deletion: on day 31 of failing backups it would
cheerfully remove the last good copy. Pruning also never runs after a failed
upload.

---

## One-time setup

### 1. Generate the age key pair

Run this yourself, on a machine you trust — deliberately not scripted, so the
private key is never written anywhere automatic. **Generate it outside any git
working tree**: `age-keygen -o key.txt` writes to the current directory, and this
repository is public.

```bash
cd ~
age-keygen -o companion-backup-key.txt
```

It prints `Public key: age1...`. Store the **whole file** in 1Password (vault
`Claude-Access`, item `companion-backup-age-key`, field `private key`), then
delete the local copy. Put only the public key into `.env` as
`BACKUP_AGE_RECIPIENT` — it is not a secret and cannot decrypt anything.

Now verify what you stored, **before** anything depends on it. This works even
after the local file is gone, which is exactly when you want it to:

```bash
export OP_SERVICE_ACCOUNT_TOKEN=$(security find-generic-password -a "1password-service-account" -s "claude-wault" -w)
op read "op://Claude-Access/companion-backup-age-key/private key" | age-keygen -y
```

That prints the public key derived from the stored private key — only the public
half is ever displayed. It must equal `BACKUP_AGE_RECIPIENT` in `.env`. Then
prove a real round trip:

```bash
PUB=$(grep '^BACKUP_AGE_RECIPIENT=' .env | cut -d= -f2)
echo probe | age -r "$PUB" -o /tmp/probe.age
op read "op://Claude-Access/companion-backup-age-key/private key" > /tmp/k.txt
chmod 600 /tmp/k.txt
age -d -i /tmp/k.txt /tmp/probe.age
rm -f /tmp/k.txt /tmp/probe.age
```

The second-to-last command must print `probe`. If it does not, stop and fix the
key before enabling backups — every dump taken with a public key whose private
half is lost is unrecoverable.

> These blocks are written to be pasted as-is. They avoid trailing `#` comments
> on purpose: an interactive zsh does not treat `#` as a comment, so a helpful
> `# must print: probe` at the end of a line becomes four extra arguments and the
> command fails. Same reason no placeholder like `age1qqqq...` appears inside a
> runnable line — pasted verbatim it fails with `malformed recipient`.

### 2. Create the rclone remote

`rclone config` needs a browser once, so run it on your laptop, not on the
server. Choose `drive`, and set the scope to **`drive.file`** — that limits the
token to files rclone itself created, so a leak cannot read the rest of the
Drive. Creating your own Google OAuth client id is worth the extra five minutes:
rclone's shared default is heavily rate-limited, and a dedicated client can be
revoked on its own.

Give the production host its **own** remote rather than copying an existing
token: one token on two machines cannot be revoked or attributed separately.

> **`drive.file` does not isolate one OAuth client from another in the same
> Google Cloud project.** Measured on 2026-08-02: a freshly created client, which
> had never uploaded anything, could list *and fully read* files created earlier
> by a different client in the same project — including an unrelated restic
> backup repository. Google's documentation does not describe this either way,
> so the mechanism is inferred (both client ids shared the project number) while
> the behaviour itself is observed directly.
>
> Practical consequence: if you want a stolen production token to be unable to
> touch your other backups, the production client needs its own **Google Cloud
> project**, not merely its own client id within an existing one. Note the
> trade-off — a brand-new project's consent screen starts in *Testing*, where
> refresh tokens die after 7 days, so it must be published before the token is
> durable.
>
> What limits the damage regardless is that the dumps are encrypted before
> upload. A stolen token can delete backups and read filenames and sizes; it
> cannot read the contents.

Then read the values out of `rclone.conf` and put them in `.env`:

```ini
RCLONE_CONFIG_BACKUP_TYPE=drive
RCLONE_CONFIG_BACKUP_SCOPE=drive.file
RCLONE_CONFIG_BACKUP_CLIENT_ID=....apps.googleusercontent.com
RCLONE_CONFIG_BACKUP_CLIENT_SECRET=...
RCLONE_CONFIG_BACKUP_TOKEN={"access_token":"...","refresh_token":"...","expiry":"..."}
```

The name in the middle (`BACKUP`) is the remote name; `BACKUP_REMOTE` must use
the same word in lower case: `backup:telegram-chat-companion`.

> rclone logs `Failed to save new token in config file` on every refresh. That
> is expected with environment-only config — there is no file to write back to.
> The refresh token is long-lived and each run derives its own access token.

If the token is stored in a secrets manager, **check its shape, not just that it
is there.** A presence-or-length probe passes on a bare access token, which works
for under an hour and then fails with nothing to refresh from — a silent failure
that surfaces at the first scheduled run, typically hours later and unattended.
This has already happened once during setup. Assert the structure instead:

```bash
op read "op://<vault>/<item>/token" | jq -e '.refresh_token | length'
```

It prints the length and exits non-zero if `refresh_token` is missing, without
ever printing the value. Generalised: for a JSON-valued secret, non-empty is not
the same as valid.

### 3. Turn it on

```ini
BACKUP_ENABLED=true
BACKUP_SCHEDULE_UTC=03:30
BACKUP_AGE_RECIPIENT=age1qqqq...
BACKUP_REMOTE=backup:telegram-chat-companion
BACKUP_RETENTION_DAYS=30
```

Then redeploy. Confirm with one manual run before trusting the schedule:

```bash
docker compose -p companion exec backup /opt/companion/backup-db.sh
```

---

## Everyday use

```bash
# one backup right now (production)
docker compose -p companion exec backup /opt/companion/backup-db.sh

# local dev, nothing uploaded anywhere
docker compose --profile backup run --rm backup /opt/companion/backup-db.sh --no-upload

# what is on the remote
docker compose -p companion exec backup rclone lsl "$BACKUP_REMOTE"

# is the sidecar keeping up?  (unhealthy => no successful backup in >26h)
docker compose -p companion ps backup
```

Each run leaves two files, locally and on the remote:

| File | Contents |
|---|---|
| `companion-<UTC>.dump.age` | the encrypted dump |
| `companion-<UTC>.manifest.json` | **cleartext**: row counts per table, sha256, alembic revision |

The manifest is deliberately not encrypted, so "did last night's backup actually
contain the messages?" is answerable without fetching the private key. It holds
counts and metadata only — no chat content, no ids.

---

## Restoring

```bash
# newest backup from the remote into a scratch database
docker compose -p companion exec backup /opt/companion/restore-db.sh \
    --latest --identity /path/to/key.txt \
    --target "postgresql://user:pass@postgres:5432/scratch_db"

# keep the private key off disk entirely
op read "op://Claude-Access/companion-backup-age-key/private key" \
  | docker compose -p companion exec -T backup /opt/companion/restore-db.sh \
        --latest --identity - --target "$TARGET_URL"
```

The target must have **pgvector available** (`pgvector/pgvector:pg16`) — the
dump recreates the extension but cannot install it.

Two safety behaviours worth knowing before you need them:

- It **refuses a target that already has tables** unless `--force`. A mistyped
  target cannot quietly destroy the live database.
- It finishes by comparing every table against the manifest's row counts and
  **fails on any mismatch**. `pg_restore` exiting 0 is not the verdict; matching
  row counts is.

### Rehearse it

A backup nobody has restored is a guess. Once a month, restore the newest backup
into a throwaway database and let the manifest comparison run. It takes minutes
and it is the only thing that distinguishes a backup from a file.

The precedent is in this project's own history: during the n8n cutover every
conventional check was green — row counts matched, CI passed, the restore
rehearsal succeeded — while the migrated data still had two minutes to live,
because a retention window was reading a state table as a log. Counts prove
transport. Only driving the real consumer proves the thing works.

---

## What is verified, and what is not

Measured end to end while building this, against scratch databases carrying
synthetic data and 768-dimension pgvector embeddings:

- A full round trip through **real Google Drive** — dump, encrypt, upload,
  download, decrypt, restore — returns embeddings **bit-identical** to the
  source (same md5 over all vectors).
- Retention removes the right copies and `BACKUP_MIN_KEEP` protects the newest
  ones even when they are past the age cutoff.
- Each guard was confirmed against a deliberately broken input, not just a
  healthy one: wrong key → refuses; truncated archive → refuses; tampered
  manifest → reports the mismatch and fails; non-empty target → refuses.

One finding worth carrying: **`pg_restore --list` does not detect truncation.**
The custom format writes its table of contents at the front of the file, so an
archive cut off halfway through the data still lists all its entries and looks
healthy — measured, a dump truncated to 8 KB still reported 22 catalogue
entries. `pg_restore -f /dev/null` reads every data block and is what actually
catches it. Both scripts run both checks.

Not verified, and worth knowing:

- **Nothing has run on the production host yet.** The mini was unreachable while
  this was built (see `internal/mac-mini-prod-THREAD.md`); the first real
  nightly run there still needs watching.
- **No alerting.** A persistent failure shows up as an unhealthy container and
  in the logs, nowhere else. Wiring it into the bot's admin notifications is
  tracked as tech debt.
- **Restore has not been rehearsed against a real production-sized dump** — only
  against synthetic data.
