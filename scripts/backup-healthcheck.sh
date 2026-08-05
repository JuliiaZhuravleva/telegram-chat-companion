#!/usr/bin/env bash
#
# Container healthcheck for the `backup` service.
#
# A backup sidecar that is "Up" tells you nothing — the interesting failure is a
# container running happily while every nightly run errors out. This reports
# unhealthy once no backup has succeeded for BACKUP_STALE_HOURS, which makes a
# silent outage visible in `docker ps` instead of only in logs nobody reads.
#
# Measured from the newer of .last-success and .started-at, so a container that
# has simply not reached its first scheduled run yet is healthy, not stale.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_ENABLED="${BACKUP_ENABLED:-false}"
# 26h, not 24h: a 24h threshold would flap every night in the minutes between
# the deadline and that night's run.
BACKUP_STALE_HOURS="${BACKUP_STALE_HOURS:-26}"

# Backups intentionally off — the container is idling as designed.
[ "$BACKUP_ENABLED" != "true" ] && exit 0

read_stamp() { [ -f "$1" ] && tr -cd '0-9' < "$1" || echo 0; }

last_success="$(read_stamp "${BACKUP_DIR}/.last-success")"
started_at="$(read_stamp "${BACKUP_DIR}/.started-at")"

reference=$(( last_success > started_at ? last_success : started_at ))
[ "$reference" -gt 0 ] || exit 0  # nothing to judge yet

age_hours=$(( ( $(date -u '+%s') - reference ) / 3600 ))

if [ "$age_hours" -ge "$BACKUP_STALE_HOURS" ]; then
    if [ "$last_success" -gt 0 ]; then
        echo "UNHEALTHY: last successful backup was ${age_hours}h ago"
    else
        echo "UNHEALTHY: no backup has ever succeeded (${age_hours}h since start)"
    fi
    exit 1
fi

exit 0
