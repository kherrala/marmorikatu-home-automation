#!/bin/bash
# Cron jobs do NOT inherit a container's Docker environment, so the nightly
# backup.sh saw INFLUXDB_URL/INFLUXDB_TOKEN unset and aborted under `set -u`
# (every night since the service was created — the backups dir stayed empty).
# Capture the vars the job needs into a file that backup.sh sources, then run
# cron in the foreground as the container's main process.
set -e
printenv | grep -E '^(INFLUXDB_URL|INFLUXDB_TOKEN|RETENTION_DAYS)=' \
    | sed 's/^/export /' > /etc/backup.env
chmod 600 /etc/backup.env
echo "[entrypoint] captured backup env → /etc/backup.env"
exec cron -f
