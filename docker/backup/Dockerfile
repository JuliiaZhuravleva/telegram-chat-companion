# Backup sidecar: pg_dump + age + rclone, nothing else.
#
# Based on postgres:16-alpine purely for its client binaries — the server is
# never started here, the entrypoint is replaced below. The MAJOR VERSION MUST
# TRACK THE SERVER (pgvector/pgvector:pg16 in both compose files): pg_dump
# refuses to dump a server newer than itself, so bumping Postgres without
# bumping this line breaks backups the moment the database restarts.
FROM postgres:16-alpine

# age    — encrypts the dump to a public key before it leaves the host
# rclone — uploads to Google Drive (or any of its ~70 other backends)
# jq     — reads the manifest during a restore-rehearsal comparison
# bash   — the scripts use arrays and [[ ]]; busybox ash is not enough
RUN apk add --no-cache bash age rclone jq ca-certificates tzdata

# restore-db.sh ships too: this container is where pg_restore, age, rclone and
# the compose-network route to Postgres already exist, so a restore or a
# restore-rehearsal runs here rather than needing a client set up on the host.
COPY scripts/backup-db.sh scripts/backup-scheduler.sh scripts/backup-healthcheck.sh \
     scripts/restore-db.sh /opt/companion/
RUN chmod +x /opt/companion/*.sh

# Remotes are configured entirely through RCLONE_CONFIG_<NAME>_* environment
# variables, which the deploy harness renders into .env from its secrets store.
# Pointing rclone at /dev/null keeps it from looking for — or half-writing — a
# config file it has no business owning. On token refresh it logs a harmless
# "Failed to save new token" notice; Google refresh tokens are long-lived and
# each run re-derives its own access token, so nothing is lost.
ENV RCLONE_CONFIG=/dev/null \
    BACKUP_DIR=/backups

# Runs as root so it can always write to the host bind mount, whose ownership
# under Colima/virtiofs is not something this image can predict. The container
# has no published ports and its only inbound surface is the compose network.
ENTRYPOINT []
CMD ["/opt/companion/backup-scheduler.sh"]
