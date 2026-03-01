# Backup and Recovery

## Backup Service

The `backup` container runs `influx backup` every night at **03:00 (UTC)** and writes a compressed archive to `./backups/` on the host. Backups older than 30 days are pruned automatically.

```
~/marmorikatu-home-automation/backups/
├── influx-backup-2026-03-01.tar.gz
├── influx-backup-2026-03-02.tar.gz
└── ...
```

Configuration (in `docker-compose.yml`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `INFLUXDB_URL` | `http://influxdb:8086` | InfluxDB to back up |
| `INFLUXDB_TOKEN` | `wago-secret-token` | API token |
| `RETENTION_DAYS` | `30` | Days to keep before pruning |

### Trigger a manual backup

```bash
docker exec marmorikatu-backup /usr/local/bin/backup.sh
```

### Check backup logs

```bash
docker exec marmorikatu-backup cat /var/log/backup.log
```

---

## Recovery

### Scenario A — Restoring onto a fresh InfluxDB instance

Use this when the data volume has been lost and InfluxDB has been re-initialised
(e.g. after a server migration or volume deletion).

```bash
# 1. Copy the backup archive to the server if needed
scp backups/influx-backup-2026-03-01.tar.gz kherrala@192.168.1.160:~/

# 2. Stop all services except InfluxDB
ssh kherrala@192.168.1.160
cd ~/marmorikatu-home-automation
docker compose stop ruuvi thermia lights sync electricity indoor heating backup

# 3. Extract the archive
tar -xzf ~/influx-backup-2026-03-01.tar.gz -C /tmp

# 4. Restore — --full replaces org, buckets, tokens and all data
docker exec marmorikatu-influxdb influx restore /tmp/influx-backup-2026-03-01 \
  --host http://localhost:8086 \
  --token wago-secret-token \
  --full

# 5. Restart all services
docker compose start ruuvi thermia lights sync electricity indoor heating backup
```

### Scenario B — Restoring data on top of a running instance

Use this to recover from accidental data deletion while the instance is otherwise healthy.

> **Warning:** InfluxDB 2 restore may fail if a bucket with the same name already exists.
> If so, either delete the target bucket first or use Scenario A with a clean volume.

```bash
# 1. Stop all write services to prevent conflicts during restore
docker compose stop ruuvi thermia lights sync electricity indoor heating backup

# 2. Extract the archive (on the server)
tar -xzf ~/marmorikatu-home-automation/backups/influx-backup-2026-03-01.tar.gz -C /tmp

# 3. Restore data (without --full, preserves existing org/token metadata)
docker exec marmorikatu-influxdb influx restore /tmp/influx-backup-2026-03-01 \
  --host http://localhost:8086 \
  --token wago-secret-token

# 4. Restart write services
docker compose start ruuvi thermia lights sync electricity indoor heating backup
```

### Restoring a specific bucket only

```bash
docker exec marmorikatu-influxdb influx restore /tmp/influx-backup-2026-03-01 \
  --host http://localhost:8086 \
  --token wago-secret-token \
  --org wago \
  --bucket building_automation
```

### Clean up extracted backup after recovery

```bash
rm -rf /tmp/influx-backup-2026-03-01
```
