#!/bin/bash
set -euo pipefail

BACKUP_DIR=/backups
RETENTION_DAYS=${RETENTION_DAYS:-30}
DATE=$(date +%Y-%m-%d)
TMP_DIR="/tmp/influx-backup-${DATE}"
OUT_FILE="${BACKUP_DIR}/influx-backup-${DATE}.tar.gz"

echo "[$(date -Iseconds)] Starting backup for ${DATE}..."

influx backup "${TMP_DIR}" \
    --host "${INFLUXDB_URL}" \
    --token "${INFLUXDB_TOKEN}"

tar -czf "${OUT_FILE}" -C /tmp "influx-backup-${DATE}"
rm -rf "${TMP_DIR}"

SIZE=$(du -sh "${OUT_FILE}" | cut -f1)
echo "[$(date -Iseconds)] Backup complete: ${OUT_FILE} (${SIZE})"

# Remove old backups
DELETED=$(find "${BACKUP_DIR}" -name "influx-backup-*.tar.gz" -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)
if [ "${DELETED}" -gt 0 ]; then
    echo "[$(date -Iseconds)] Removed ${DELETED} backup(s) older than ${RETENTION_DAYS} days"
fi
