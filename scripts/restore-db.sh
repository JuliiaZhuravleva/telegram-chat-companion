#!/usr/bin/env bash
#
# Restore the bot's database from a backup produced by scripts/backup-db.sh.
#
#   # newest backup on the remote, into a scratch database (the rehearsal)
#   ./scripts/restore-db.sh --latest --identity ~/age-key.txt --target "$SCRATCH_URL"
#
#   # a specific local file, keeping the private key off disk entirely
#   op read "op://Claude-Access/companion-backup-age-key/private key" \
#       | ./scripts/restore-db.sh --identity - /backups/companion-20260803T033000Z.dump.age
#
# A backup nobody has ever restored is a guess, not a backup. Rehearse this
# against a scratch database on a schedule — docs/backups.md says how, and the
# manifest comparison at the end is what makes the rehearsal meaningful rather
# than merely uneventful.
#
# The target must have the pgvector extension AVAILABLE (image
# pgvector/pgvector:pg16); the dump recreates it but cannot install it.
#
# Refuses to write into a database that already has tables unless --force is
# given, so a mistyped target cannot quietly destroy a live database.

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

SOURCE=""
USE_LATEST=false
IDENTITY="${BACKUP_AGE_IDENTITY_FILE:-}"
TARGET_URL="${RESTORE_TARGET_URL:-}"
FORCE=false
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
BACKUP_PREFIX="${BACKUP_PREFIX:-companion}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"

while [ $# -gt 0 ]; do
    case "$1" in
        --latest)   USE_LATEST=true ;;
        --identity) IDENTITY="$2"; shift ;;
        --target)   TARGET_URL="$2"; shift ;;
        --force)    FORCE=true ;;
        -h|--help)  sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*)         echo "Unknown option: $1" >&2; exit 2 ;;
        *)          SOURCE="$1" ;;
    esac
    shift
done

log()  { printf '%s %b\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
fail() { log "${RED}ERROR${NC} $*" >&2; exit 1; }

command -v pg_restore >/dev/null || fail "pg_restore not found in PATH"
command -v psql >/dev/null || fail "psql not found in PATH"

[ -n "$TARGET_URL" ] || fail "no target. Pass --target postgresql://user:pass@host:port/db
       (or set RESTORE_TARGET_URL). This is deliberately never defaulted —
       restoring is destructive and must name its victim explicitly."

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

# --- 1. locate the backup ---------------------------------------------------

if [ "$USE_LATEST" = true ]; then
    [ -n "$BACKUP_REMOTE" ] || fail "--latest needs BACKUP_REMOTE set"
    command -v rclone >/dev/null || fail "rclone not found in PATH"

    # Filenames embed a fixed-width UTC stamp, so lexicographic order is
    # chronological — no reliance on remote mtimes, which a re-sync can rewrite.
    LATEST="$(rclone lsf --files-only --include "${BACKUP_PREFIX}-*.dump*" "$BACKUP_REMOTE" \
                | sort | tail -1)"
    [ -n "$LATEST" ] || fail "no backups found at ${BACKUP_REMOTE}"

    log "Newest remote backup: ${LATEST}"
    rclone copyto "${BACKUP_REMOTE}/${LATEST}" "${WORKDIR}/${LATEST}" || fail "download failed"
    rclone copyto "${BACKUP_REMOTE}/${LATEST%%.dump*}.manifest.json" \
        "${WORKDIR}/${LATEST%%.dump*}.manifest.json" 2>/dev/null || true
    SOURCE="${WORKDIR}/${LATEST}"
fi

[ -n "$SOURCE" ] || fail "no backup given. Pass a file path, or --latest to fetch from ${BACKUP_REMOTE:-the remote}."
[ -f "$SOURCE" ] || fail "backup file not found: $SOURCE"

MANIFEST="${SOURCE%%.dump*}.manifest.json"

# --- 2. decrypt -------------------------------------------------------------

DUMP="${WORKDIR}/restore.dump"

case "$SOURCE" in
    *.age)
        command -v age >/dev/null || fail "age not found in PATH"
        [ -n "$IDENTITY" ] || fail "this backup is encrypted — pass --identity <file>, or
       --identity - to read the private key from stdin (which keeps it off disk)."

        if [ "$IDENTITY" = "-" ]; then
            KEYFILE="${WORKDIR}/identity"
            ( umask 077; cat > "$KEYFILE" )
            IDENTITY="$KEYFILE"
        fi
        [ -f "$IDENTITY" ] || fail "identity file not found: $IDENTITY"

        age -d -i "$IDENTITY" -o "$DUMP" "$SOURCE" \
            || fail "decryption failed — wrong key, or the file is not an age archive"
        log "${GREEN}[1/4]${NC} decrypted"
        ;;
    *)
        cp "$SOURCE" "$DUMP"
        log "${YELLOW}[1/4]${NC} backup is not encrypted"
        ;;
esac

# --- 3. verify before touching the target -----------------------------------
#
# Both checks, in this order, and both before the target is touched: --list
# fails fast on a file that is not an archive at all, and the full read catches
# truncation, which --list cannot (the custom format's table of contents sits at
# the front of the file, so a half-written archive still lists cleanly).

TOC_ENTRIES="$(pg_restore --list "$DUMP" 2>/dev/null | grep -cv '^;' || true)"
[ "${TOC_ENTRIES:-0}" -gt 0 ] || fail "archive is unreadable — pg_restore --list found no entries"

pg_restore -f /dev/null "$DUMP" >/dev/null 2>&1 \
    || fail "archive is corrupt or truncated — refusing to restore from it"

log "${GREEN}[2/4]${NC} archive verified end to end (${TOC_ENTRIES} catalogue entries)"

EXISTING="$(psql "$TARGET_URL" -tAX -c \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null \
    | tr -d '[:space:]')" || fail "cannot connect to the target database"

if [ "${EXISTING:-0}" -gt 0 ] && [ "$FORCE" != true ]; then
    fail "target already has ${EXISTING} table(s) in schema public.
       Restoring would drop and replace them. Re-run with --force if that is
       what you want, or point --target at an empty database."
fi

# --- 4. restore -------------------------------------------------------------

log "Restoring into the target database..."

# --clean --if-exists so a --force restore replaces existing objects rather than
# colliding with them. pg_restore reports non-fatal issues (e.g. an extension
# comment it may not re-create) as errors, so its exit code alone is not a
# verdict — step 5 compares actual row counts instead.
set +e
pg_restore --clean --if-exists --no-owner --no-privileges \
    --dbname "$TARGET_URL" "$DUMP" 2>"${WORKDIR}/restore.err"
RESTORE_RC=$?
set -e

if [ "$RESTORE_RC" -ne 0 ]; then
    log "${YELLOW}pg_restore exited ${RESTORE_RC}${NC} — last lines of its output:"
    tail -20 "${WORKDIR}/restore.err" >&2
fi
log "${GREEN}[3/4]${NC} pg_restore finished"

# --- 5. compare against the manifest ----------------------------------------
#
# The check that makes a rehearsal worth running: not "did the command exit 0"
# but "does the restored database hold the rows the backup claimed to contain".

if [ -f "$MANIFEST" ] && command -v jq >/dev/null; then
    MISMATCH=0
    while IFS=$'\t' read -r table expected; do
        [ -n "$table" ] || continue

        # The manifest is fetched from the backup remote, so it is NOT trusted
        # input: anyone able to write there can choose these table names, and
        # they get interpolated into SQL below. Without this guard a planted
        # name like `t"; DROP TABLE chat_messages; --` executes as its own
        # statement — verified, it really does create the table it asks for.
        # A table name here is always a plain identifier; anything else is an
        # attack or a corrupt file, and both mean "do not trust this restore".
        case "$table" in
            *[!A-Za-z0-9_]* | "")
                log "${RED}  REFUSED${NC} manifest table name is not a plain identifier: ${table}"
                MISMATCH=$(( MISMATCH + 1 ))
                continue
                ;;
        esac

        actual="$(psql "$TARGET_URL" -tAX -c "SELECT count(*) FROM \"${table}\";" 2>/dev/null \
                  | tr -d '[:space:]')"
        if [ "${actual:-missing}" != "$expected" ]; then
            log "${RED}  MISMATCH${NC} ${table}: manifest ${expected}, restored ${actual:-missing}"
            MISMATCH=$(( MISMATCH + 1 ))
        fi
    done < <(jq -r '.row_counts | to_entries[] | "\(.key)\t\(.value)"' "$MANIFEST")

    if [ "$MISMATCH" -eq 0 ]; then
        log "${GREEN}[4/4]${NC} verified: every table matches the manifest row counts"
    else
        fail "${MISMATCH} table(s) do not match the manifest — treat this restore as failed"
    fi
else
    log "${YELLOW}[4/4]${NC} no manifest (or jq missing) — row counts NOT verified."
    log "${YELLOW}      ${NC} Check a table you recognise by hand before trusting this restore."
fi

log "${GREEN}Restore complete.${NC}"
