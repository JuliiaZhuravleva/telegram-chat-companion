#!/usr/bin/env bash
#
# Entrypoint for the `backup` container: run backup-db.sh once a day at
# BACKUP_SCHEDULE_UTC and otherwise stay out of the way.
#
# WHY A SLEEP LOOP AND NOT CRON.  The production host is a Colima VM driven by a
# deploy harness that only speaks `docker compose up -d`; a host crontab would
# live outside the repository and outside that deploy, so it would silently fail
# to follow the code. A supervised container is the unit the harness already
# knows how to start, restart and report on.
#
# The schedule is UTC on purpose — a local-time schedule skips or repeats a run
# twice a year at the DST boundary.

set -euo pipefail

BACKUP_ENABLED="${BACKUP_ENABLED:-false}"
BACKUP_SCHEDULE_UTC="${BACKUP_SCHEDULE_UTC:-03:30}"
BACKUP_RUN_ON_START="${BACKUP_RUN_ON_START:-false}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '%s [scheduler] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

# Shut down promptly on `docker compose down` / `stop`. Bash only runs a trap
# once the foreground command returns, so a bare `sleep 28000` would sit through
# SIGTERM until Docker lost patience and SIGKILLed the container ten seconds
# later. Backgrounding the sleep and `wait`ing on it makes the signal land
# immediately — the wait is interrupted, the trap runs, the container exits.
term_handler() { log "Signal received — shutting down"; exit 0; }
trap term_handler TERM INT

mkdir -p "$BACKUP_DIR"

# The healthcheck compares "now" against the newer of .last-success and this
# file, so a freshly started container is not reported unhealthy for the hours
# before its first scheduled run.
date -u '+%s' > "${BACKUP_DIR}/.started-at"

if [ "$BACKUP_ENABLED" != "true" ]; then
    log "BACKUP_ENABLED is not 'true' — backups are OFF."
    log "Set BACKUP_ENABLED=true (plus BACKUP_AGE_RECIPIENT and BACKUP_REMOTE) to turn them on."
    log "See docs/backups.md. Idling."
    # Idle rather than exit: exiting under `restart: unless-stopped` produces a
    # crash-loop that looks like a broken deployment instead of a disabled one.
    exec sleep infinity
fi

case "$BACKUP_SCHEDULE_UTC" in
    [0-2][0-9]:[0-5][0-9]) ;;
    *) log "FATAL: BACKUP_SCHEDULE_UTC must look like HH:MM (got '${BACKUP_SCHEDULE_UTC}')"; exit 1 ;;
esac

# 10# forces base 10 — '08' and '09' are invalid octal and would otherwise abort
# the arithmetic at 08:00 and 09:00 UTC every day.
TARGET_SECS=$(( 10#${BACKUP_SCHEDULE_UTC%%:*} * 3600 + 10#${BACKUP_SCHEDULE_UTC##*:} * 60 ))

log "Daily backup scheduled for ${BACKUP_SCHEDULE_UTC} UTC"

run_backup() {
    log "Starting backup run"
    if "${SCRIPT_DIR}/backup-db.sh"; then
        log "Backup run finished OK"
    else
        # Never exit on failure: the next night's attempt may well succeed, and
        # a crash-looping container loses the logs that explain the failure.
        # The container healthcheck is what surfaces a persistent outage.
        log "Backup run FAILED (exit $?) — will retry at the next scheduled time"
    fi
}

[ "$BACKUP_RUN_ON_START" = "true" ] && run_backup

while true; do
    # Seconds since midnight UTC, computed arithmetically so this works
    # identically under busybox, GNU and BSD date.
    now_secs=$(( 10#$(date -u +%H) * 3600 + 10#$(date -u +%M) * 60 + 10#$(date -u +%S) ))
    sleep_secs=$(( TARGET_SECS - now_secs ))
    [ "$sleep_secs" -le 0 ] && sleep_secs=$(( sleep_secs + 86400 ))

    log "Next run in $(( sleep_secs / 3600 ))h $(( (sleep_secs % 3600) / 60 ))m"
    # `|| true` because a signal makes wait return non-zero, and `set -e` would
    # otherwise turn an ordinary shutdown into a failure exit code.
    sleep "$sleep_secs" & wait $! || true

    run_backup
done
