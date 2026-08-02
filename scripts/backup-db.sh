#!/usr/bin/env bash
#
# Take one encrypted backup of the bot's PostgreSQL database and upload it
# off-host.  Safe to run by hand; `backup-scheduler.sh` runs it on a timer.
#
#   ./scripts/backup-db.sh                # dump, encrypt, upload, prune
#   ./scripts/backup-db.sh --no-upload    # local only (no remote touched)
#   ./scripts/backup-db.sh --no-prune     # keep every old copy
#
# Pipeline: pg_dump -Fc -> verify -> manifest -> age-encrypt -> rclone -> prune.
#
# WHY ENCRYPTED BY DEFAULT.  The dump carries the full message history of real
# group chats — private messages, usernames, chat titles, admin ids.  Once it
# reaches a cloud drive, every integration that account ever granted Drive scope
# to can read it.  So the dump is encrypted *before* it leaves this host, with
# an age PUBLIC key: the host can create backups it cannot itself read, and a
# compromise here does not expose the archive.  Keep the private key in a
# password manager and nowhere else — without it the backups are unrecoverable,
# which is the whole point and also the whole risk.
#
# Set BACKUP_ALLOW_PLAINTEXT=true to opt out.  It is deliberately awkward.
#
# Configuration is entirely environment-driven; see config/.env.example and
# docs/backups.md.

set -euo pipefail

# --- configuration ----------------------------------------------------------

PGHOST="${PGHOST:-postgres}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-${POSTGRES_USER:-bot_user}}"
PGDATABASE="${PGDATABASE:-${POSTGRES_DB:-telegram_bot}}"
export PGHOST PGPORT PGUSER PGDATABASE
export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-}}"

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_PREFIX="${BACKUP_PREFIX:-companion}"

# Whitespace/comma-separated list of age public keys.  Multiple recipients let
# you introduce a new key and retire the old one without a window in which
# backups are readable by neither.
BACKUP_AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
BACKUP_ALLOW_PLAINTEXT="${BACKUP_ALLOW_PLAINTEXT:-false}"

# e.g. "backup:telegram-chat-companion".  Empty => keep backups on this host only.
BACKUP_REMOTE="${BACKUP_REMOTE:-}"

BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
BACKUP_REMOTE_RETENTION_DAYS="${BACKUP_REMOTE_RETENTION_DAYS:-$BACKUP_RETENTION_DAYS}"

# Never prune below this many copies, however old they are.  Without it, a run
# of failed backups turns retention into deletion: on day 31 of an outage the
# age rule would happily remove the last good copy along with the rest.
BACKUP_MIN_KEEP="${BACKUP_MIN_KEEP:-3}"

DO_UPLOAD=true
DO_PRUNE=true

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

while [ $# -gt 0 ]; do
    case "$1" in
        --no-upload) DO_UPLOAD=false ;;
        --no-prune)  DO_PRUNE=false ;;
        -h|--help)   sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

log()  { printf '%s %b\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
fail() { log "${RED}ERROR${NC} $*" >&2; exit 1; }

# --- preflight --------------------------------------------------------------
#
# Check everything BEFORE touching the database, so a misconfiguration fails in
# a second instead of after a full dump.

for tool in pg_dump pg_restore psql; do
    command -v "$tool" >/dev/null || fail "$tool not found in PATH"
done

ENCRYPT=true
if [ -z "$BACKUP_AGE_RECIPIENT" ]; then
    if [ "$BACKUP_ALLOW_PLAINTEXT" = "true" ]; then
        ENCRYPT=false
        log "${YELLOW}WARNING${NC} BACKUP_ALLOW_PLAINTEXT=true — the dump will be stored UNENCRYPTED."
        log "${YELLOW}        ${NC} It contains private chat history. Do not put it anywhere shared."
    else
        fail "BACKUP_AGE_RECIPIENT is not set. Generate a key with 'age-keygen', keep the
       private half in your password manager, and put the public half here.
       See docs/backups.md. To store dumps unencrypted anyway (not advised for
       real chat data) set BACKUP_ALLOW_PLAINTEXT=true."
    fi
fi
[ "$ENCRYPT" = true ] && { command -v age >/dev/null || fail "age not found in PATH"; }

if [ "$DO_UPLOAD" = true ] && [ -n "$BACKUP_REMOTE" ]; then
    command -v rclone >/dev/null || fail "rclone not found in PATH"
else
    DO_UPLOAD=false
fi

[ -n "$PGPASSWORD" ] || fail "POSTGRES_PASSWORD / PGPASSWORD is not set"

mkdir -p "$BACKUP_DIR"
[ -w "$BACKUP_DIR" ] || fail "BACKUP_DIR is not writable: $BACKUP_DIR"

STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
BASENAME="${BACKUP_PREFIX}-${STAMP}"
DUMP="${BACKUP_DIR}/${BASENAME}.dump"
MANIFEST="${BACKUP_DIR}/${BASENAME}.manifest.json"

# The plaintext dump is the one thing here that must never outlive the run.
cleanup() { [ "$ENCRYPT" = true ] && rm -f "$DUMP"; }
trap cleanup EXIT

log "Backing up ${PGDATABASE} on ${PGHOST}:${PGPORT} -> ${BACKUP_DIR}"

# --- 1. dump ----------------------------------------------------------------
#
# Custom format (-Fc): compressed, and pg_restore can list/verify its table of
# contents without a database — which is what step 2 uses.  --no-owner and
# --no-privileges keep the dump restorable under a different role name, so a
# restore into a scratch database or a rebuilt host does not need the original
# grants to exist.

pg_dump --format=custom --no-owner --no-privileges --file="$DUMP" \
    || fail "pg_dump failed"

DUMP_BYTES="$(wc -c < "$DUMP" | tr -d '[:space:]')"
log "${GREEN}[1/5]${NC} dumped ${DUMP_BYTES} bytes"

# --- 2. verify the dump is readable ----------------------------------------
#
# A dump pg_dump exited 0 on can still be unusable — truncated by a full disk or
# cut short by a killed connection.
#
# `pg_restore --list` alone does NOT catch that, and it is worth being precise
# about why: the custom format writes its table of contents at the FRONT of the
# file, so an archive truncated halfway through the data still lists all its
# entries and looks healthy. Measured — a dump cut to 8 KB still reported 22
# catalogue entries.
#
# `pg_restore -f /dev/null` is the check that bites: it decompresses and walks
# every data block, so a short read fails ("could not read from input file: end
# of file", exit 1). Verified against both a truncated and an intact archive.
#
# Still not proof of restorability — only scripts/restore-db.sh into a real
# database is that, which is why docs/backups.md asks you to rehearse it.

TOC_ENTRIES="$(pg_restore --list "$DUMP" 2>/dev/null | grep -cv '^;' || true)"
[ "${TOC_ENTRIES:-0}" -gt 0 ] || fail "dump is unreadable — pg_restore --list found no entries"

pg_restore -f /dev/null "$DUMP" >/dev/null 2>&1 \
    || fail "dump is corrupt or truncated — pg_restore could not read it back in full"

log "${GREEN}[2/5]${NC} archive verified end to end (${TOC_ENTRIES} catalogue entries)"

# --- 3. manifest ------------------------------------------------------------
#
# Row counts per table, recorded in cleartext next to the encrypted dump, so
# "did last night's backup actually contain the messages?" is answerable
# without the private key. query_to_xml runs a count over every public table in
# a single round trip.

ROW_COUNTS="$(psql -tAX -c "
    SELECT COALESCE(json_object_agg(table_name, row_count)::text, '{}')
    FROM (
        SELECT table_name,
               (xpath('/row/cnt/text()',
                      query_to_xml(format('SELECT count(*) AS cnt FROM %I.%I',
                                          table_schema, table_name),
                                   false, true, '')))[1]::text::bigint AS row_count
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ) s;
" 2>/dev/null || echo '{}')"

PG_VERSION="$(psql -tAX -c 'SHOW server_version;' 2>/dev/null | tr -d '\n')"
ALEMBIC_REV="$(psql -tAX -c 'SELECT version_num FROM alembic_version LIMIT 1;' 2>/dev/null | tr -d '[:space:]' || true)"

if command -v sha256sum >/dev/null; then
    DUMP_SHA="$(sha256sum "$DUMP" | cut -d' ' -f1)"
else
    DUMP_SHA="$(shasum -a 256 "$DUMP" | cut -d' ' -f1)"
fi

cat > "$MANIFEST" <<EOF
{
  "created_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "database": "${PGDATABASE}",
  "server_version": "${PG_VERSION}",
  "alembic_revision": "${ALEMBIC_REV:-unknown}",
  "dump_format": "custom",
  "dump_bytes": ${DUMP_BYTES},
  "dump_sha256": "${DUMP_SHA}",
  "encrypted": ${ENCRYPT},
  "toc_entries": ${TOC_ENTRIES},
  "row_counts": ${ROW_COUNTS}
}
EOF
log "${GREEN}[3/5]${NC} manifest written (alembic ${ALEMBIC_REV:-unknown}, pg ${PG_VERSION})"

# --- 4. encrypt -------------------------------------------------------------

ARTIFACT="$DUMP"
if [ "$ENCRYPT" = true ]; then
    # Split the recipient list on whitespace and commas into repeated -r flags.
    RECIPIENT_ARGS=()
    for key in ${BACKUP_AGE_RECIPIENT//,/ }; do
        RECIPIENT_ARGS+=(-r "$key")
    done

    age "${RECIPIENT_ARGS[@]}" -o "${DUMP}.age" "$DUMP" || fail "age encryption failed"

    # Refuse to continue with an implausibly small ciphertext rather than
    # uploading a broken artifact over a good one.
    ENC_BYTES="$(wc -c < "${DUMP}.age" | tr -d '[:space:]')"
    [ "$ENC_BYTES" -gt 100 ] || fail "encrypted file is suspiciously small (${ENC_BYTES} bytes)"

    rm -f "$DUMP"
    ARTIFACT="${DUMP}.age"
    # Two array slots per key (-r and the key itself).
    log "${GREEN}[4/5]${NC} encrypted to $(basename "$ARTIFACT") (${ENC_BYTES} bytes, $(( ${#RECIPIENT_ARGS[@]} / 2 )) recipient key(s))"
else
    log "${GREEN}[4/5]${NC} encryption skipped (BACKUP_ALLOW_PLAINTEXT=true)"
fi

# --- 5. upload --------------------------------------------------------------

UPLOADED=false
if [ "$DO_UPLOAD" = true ]; then
    # Manifest first, artifact second: if the run dies between them a missing
    # manifest is a clearer signal than a manifest describing a dump that
    # never arrived.
    rclone copyto "$MANIFEST" "${BACKUP_REMOTE}/$(basename "$MANIFEST")" \
        || fail "rclone upload of the manifest failed"
    rclone copyto "$ARTIFACT" "${BACKUP_REMOTE}/$(basename "$ARTIFACT")" \
        || fail "rclone upload of the dump failed"
    UPLOADED=true
    log "${GREEN}[5/5]${NC} uploaded to ${BACKUP_REMOTE}"
else
    log "${GREEN}[5/5]${NC} upload skipped (no BACKUP_REMOTE, or --no-upload)"
fi

# --- retention --------------------------------------------------------------
#
# Both filenames and pruning are driven by the UTC stamp embedded in the name,
# so retention never depends on a filesystem mtime or on Drive's idea of when a
# file was modified (a re-sync can rewrite those; the name cannot drift).
# Because the stamp is fixed-width UTC, lexicographic order IS chronological.

epoch_to_stamp() {
    # GNU/busybox use -d @epoch; BSD (macOS) uses -r epoch.
    date -u -d "@$1" '+%Y%m%dT%H%M%SZ' 2>/dev/null \
        || date -u -r "$1" '+%Y%m%dT%H%M%SZ'
}

# Emits the names to delete: older than the cutoff, minus the newest MIN_KEEP.
select_expired() {
    local cutoff="$1" min_keep="$2"
    sort | awk -v cutoff="$cutoff" -v keep="$min_keep" '
        { names[NR] = $0 }
        END {
            for (i = 1; i <= NR - keep; i++) {
                # Extract the YYYYmmddTHHMMSSZ stamp from the filename.
                if (match(names[i], /[0-9]{8}T[0-9]{6}Z/)) {
                    stamp = substr(names[i], RSTART, RLENGTH)
                    if (stamp < cutoff) print names[i]
                }
            }
        }'
}

if [ "$DO_PRUNE" = true ] && [ "$BACKUP_RETENTION_DAYS" -gt 0 ] 2>/dev/null; then
    NOW_EPOCH="$(date -u '+%s')"
    LOCAL_CUTOFF="$(epoch_to_stamp $(( NOW_EPOCH - BACKUP_RETENTION_DAYS * 86400 )))"

    # Prune by dump, then remove each orphaned manifest alongside it, so the
    # two never drift apart.
    LOCAL_EXPIRED="$(
        find "$BACKUP_DIR" -maxdepth 1 -name "${BACKUP_PREFIX}-*.dump*" -type f \
            -exec basename {} \; 2>/dev/null | select_expired "$LOCAL_CUTOFF" "$BACKUP_MIN_KEEP"
    )"
    LOCAL_N=0
    for name in $LOCAL_EXPIRED; do
        rm -f "${BACKUP_DIR}/${name}"
        rm -f "${BACKUP_DIR}/${name%%.dump*}.manifest.json"
        LOCAL_N=$(( LOCAL_N + 1 ))
    done
    log "retention: removed ${LOCAL_N} local backup(s) older than ${BACKUP_RETENTION_DAYS}d (keeping >= ${BACKUP_MIN_KEEP})"

    # Only prune the remote when this run actually put something there. A failed
    # upload must never be followed by deletions on the far side.
    if [ "$UPLOADED" = true ] && [ "$BACKUP_REMOTE_RETENTION_DAYS" -gt 0 ] 2>/dev/null; then
        REMOTE_CUTOFF="$(epoch_to_stamp $(( NOW_EPOCH - BACKUP_REMOTE_RETENTION_DAYS * 86400 )))"
        REMOTE_EXPIRED="$(
            rclone lsf --files-only --include "${BACKUP_PREFIX}-*.dump*" "$BACKUP_REMOTE" 2>/dev/null \
                | select_expired "$REMOTE_CUTOFF" "$BACKUP_MIN_KEEP"
        )"
        REMOTE_N=0
        for name in $REMOTE_EXPIRED; do
            rclone deletefile "${BACKUP_REMOTE}/${name}" 2>/dev/null || true
            rclone deletefile "${BACKUP_REMOTE}/${name%%.dump*}.manifest.json" 2>/dev/null || true
            REMOTE_N=$(( REMOTE_N + 1 ))
        done
        log "retention: removed ${REMOTE_N} remote backup(s) older than ${BACKUP_REMOTE_RETENTION_DAYS}d (keeping >= ${BACKUP_MIN_KEEP})"
    fi
fi

# Consumed by the container healthcheck: a backup service that is "up" but has
# not produced a backup in over a day is not doing its job, and should say so
# where `docker ps` will show it.
date -u '+%s' > "${BACKUP_DIR}/.last-success"

log "${GREEN}Backup complete:${NC} $(basename "$ARTIFACT")"
