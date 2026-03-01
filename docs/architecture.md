# System Architecture

Building automation data collection and visualization system. Collects data from
four sources, stores in InfluxDB, and visualizes with Grafana dashboards. Includes
an MCP server for Claude Desktop integration.

## Data Flow

```
┌─────────────────────┐                    ┌──────────────────────────────┐
│  WAGO Controller    │                    │      Docker Compose          │
│  192.168.1.10       │                    │                              │
│                     │    SSH/SCP         │  ┌────────────────────────┐  │
│  /media/sd/CSV_Files│◄───────────────────┤  │  sync container        │  │
│  ├── Temperatures*  │    (every 5 min)   │  │  - Smart file sync     │  │
│  └── logfile_dp_*   │                    │  │  - Incremental import  │  │
└─────────────────────┘                    │  └───────────┬────────────┘  │
                                           │              │               │
┌─────────────────────┐                    │              │               │
│  Ruuvi Gateway      │                    │  ┌───────────┴────────────┐  │
│  CC:F1:A2:8E:F8:8A  │    MQTT            │  │                        │  │
│                     │◄───────────────────┤  │  ruuvi container       │  │
│  7 Ruuvi sensors    │  freenas:1883      │  │  - MQTT subscriber     │  │
└─────────────────────┘                    │  │  - Real-time data      │  │
                                           │  └───────────┬────────────┘  │
┌─────────────────────┐                    │              │               │
│  Thermia Heat Pump  │                    │  ┌───────────┴────────────┐  │
│  ThermIQ-ROOM2      │    MQTT            │  │                        │  │
│                     │◄───────────────────┤  │  thermia container     │  │
│  Ground-source HP   │  freenas:1883      │  │  - MQTT subscriber     │  │
└─────────────────────┘                    │  │  - Register parsing    │  │
                                           │  └───────────┬────────────┘  │
┌─────────────────────┐                    │              │               │
│  Light Switch API   │                    │  ┌───────────┴────────────┐  │
│  localhost:8080      │    HTTP            │  │                        │  │
│                     │◄───────────────────┤  │  lights container      │  │
│  Building switches  │  (every 5 min)     │  │  - HTTP poller         │  │
└─────────────────────┘                    │  └───────────┬────────────┘  │
                                           │              │               │
                                           │              ▼               │
                                           │  ┌────────────────────────┐  │
                                           │  │  InfluxDB 2.7          │  │
                                           │  │  - Time series DB      │  │
                                           │  │  - Flux query language  │  │
                                           │  └───────────┬────────────┘  │
                                           │              │               │
                                           │         ┌────┴────┐         │
                                           │         ▼         ▼         │
                                           │  ┌────────────┐ ┌────────┐  │
                                           │  │ Grafana    │ │  MCP   │  │
                                           │  │ 12.3       │ │ Server │  │
                                           │  │ :3000      │ │ :3001  │  │
                                           │  └────────────┘ └───┬────┘  │
                                           └──────────────────────┼──────┘
                                                                  │
                                                                  ▼
                                                          Claude Desktop
```

## Docker Services

Seven services orchestrated via `docker-compose.yml`:

### influxdb — Time-Series Database

| Property | Value |
|----------|-------|
| Image | `influxdb:2.7` |
| Container | `wago-influxdb` |
| Port | `8086:8086` |
| Volumes | `influxdb-data:/var/lib/influxdb2`, `influxdb-config:/etc/influxdb2` |
| Health check | `curl -f http://localhost:8086/ping` (10s interval) |

Environment:

| Variable | Value |
|----------|-------|
| `DOCKER_INFLUXDB_INIT_MODE` | `setup` |
| `DOCKER_INFLUXDB_INIT_USERNAME` | `admin` |
| `DOCKER_INFLUXDB_INIT_ORG` | `wago` |
| `DOCKER_INFLUXDB_INIT_BUCKET` | `building_automation` |
| `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN` | *(configured in docker-compose.yml)* |

### grafana — Dashboard Visualization

| Property | Value |
|----------|-------|
| Image | `grafana/grafana:12.3.2` |
| Container | `wago-grafana` |
| Port | `3000:3000` |
| Volumes | `grafana-data:/var/lib/grafana`, `./grafana/provisioning:/etc/grafana/provisioning`, `./floorplan:/usr/share/grafana/public/build/img/floorplan:ro` |
| Depends on | `influxdb` (healthy) |

Environment:

| Variable | Purpose |
|----------|---------|
| `GF_SECURITY_ADMIN_USER` | Admin username |
| `GF_SECURITY_ADMIN_PASSWORD` | Admin password |
| `GF_USERS_ALLOW_SIGN_UP` | `false` |
| `GF_DATE_FORMATS_DEFAULT_TIMEZONE` | `Europe/Helsinki` |
| `GF_DATE_FORMATS_*` | Finnish date format (`DD/MM/YYYY HH:mm:ss`) |
| `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` | Points to `building_overview.json` |

### sync — WAGO CSV Sync (Optional)

| Property | Value |
|----------|-------|
| Dockerfile | `Dockerfile.sync` |
| Container | `wago-sync` |
| Profile | `sync` (must be explicitly enabled) |
| Volumes | `./data:/data`, `./scripts:/scripts:ro`, `./ssh:/ssh:ro` |
| Restart | `unless-stopped` |
| Depends on | `influxdb` (healthy) |

Syncs CSV files from the WAGO PLC via SSH/SCP and runs incremental import.

Environment:

| Variable | Purpose |
|----------|---------|
| `INFLUXDB_URL` | InfluxDB connection URL |
| `INFLUXDB_TOKEN` | API authentication token |
| `INFLUXDB_ORG` | Organization name |
| `INFLUXDB_BUCKET` | Target bucket |
| `DATA_DIR` | Local CSV storage path (`/data`) |
| `SSH_KEY` | Path to SSH private key (`/ssh/wago_sync`) |
| `SYNC_INTERVAL` | Sync frequency in seconds (`300`) |
| `REMOTE_HOST` | WAGO PLC IP address |
| `REMOTE_USER` | SSH username |
| `REMOTE_PATH` | Remote CSV directory |

### ruuvi — Ruuvi MQTT Subscriber

| Property | Value |
|----------|-------|
| Dockerfile | `Dockerfile.ruuvi` |
| Container | `wago-ruuvi` |
| Restart | `unless-stopped` |
| Depends on | `influxdb` (healthy) |

Subscribes to Ruuvi gateway MQTT topics and writes sensor data to InfluxDB.
Also forwards indoor temperature (from the Olohuone sensor) to the ThermIQ
heat pump via MQTT.

Environment:

| Variable | Purpose |
|----------|---------|
| `MQTT_BROKER` | MQTT broker hostname |
| `MQTT_PORT` | MQTT broker port (`1883`) |
| `MQTT_TOPIC` | Ruuvi gateway topic pattern |
| `INFLUXDB_URL` | InfluxDB connection URL |
| `INFLUXDB_TOKEN` | API authentication token |
| `INFLUXDB_ORG` | Organization name |
| `INFLUXDB_BUCKET` | Target bucket |
| `RUUVI_SENSOR_NAMES` | JSON map of MAC → friendly name |

### thermia — ThermIQ MQTT Subscriber

| Property | Value |
|----------|-------|
| Dockerfile | `Dockerfile.thermia` |
| Container | `wago-thermia` |
| Restart | `unless-stopped` |
| Depends on | `influxdb` (healthy) |

Subscribes to ThermIQ-ROOM2 MQTT data topic, periodically sends read commands
to request register dumps, parses hex/decimal register formats, extracts
bitfields, and writes grouped InfluxDB points.

Environment:

| Variable | Purpose |
|----------|---------|
| `MQTT_BROKER` | MQTT broker hostname |
| `MQTT_PORT` | MQTT broker port (`1883`) |
| `MQTT_TOPIC` | ThermIQ command topic (for sending read commands) |
| `MQTT_DATA_TOPIC` | ThermIQ data topic (register responses) |
| `READ_INTERVAL` | Register read request interval in seconds (`30`) |
| `INFLUXDB_URL` | InfluxDB connection URL |
| `INFLUXDB_TOKEN` | API authentication token |
| `INFLUXDB_ORG` | Organization name |
| `INFLUXDB_BUCKET` | Target bucket |

### mcp — MCP Server

| Property | Value |
|----------|-------|
| Dockerfile | `Dockerfile.mcp` |
| Container | `wago-mcp` |
| Port | `3001:3001` |
| Restart | `unless-stopped` |
| Depends on | `influxdb` (healthy) |

SSE-based MCP server for Claude Desktop integration. Exposes InfluxDB data
through structured tools.

Environment:

| Variable | Purpose |
|----------|---------|
| `INFLUXDB_URL` | InfluxDB connection URL |
| `INFLUXDB_TOKEN` | API authentication token |
| `INFLUXDB_ORG` | Organization name |
| `INFLUXDB_BUCKET` | Target bucket |
| `MCP_PORT` | Server port (`3001`) |

### lights — Light Switch Poller

| Property | Value |
|----------|-------|
| Dockerfile | `Dockerfile.lights` |
| Container | `wago-lights` |
| Restart | `unless-stopped` |
| Depends on | `influxdb` (healthy) |
| Extra hosts | `host.docker.internal:host-gateway` |

Polls an HTTP API for light switch status and writes to InfluxDB.

Environment:

| Variable | Purpose |
|----------|---------|
| `LIGHTS_API_URL` | Light switch API endpoint |
| `POLL_INTERVAL` | Polling frequency in seconds (`300`) |
| `INFLUXDB_URL` | InfluxDB connection URL |
| `INFLUXDB_TOKEN` | API authentication token |
| `INFLUXDB_ORG` | Organization name |
| `INFLUXDB_BUCKET` | Target bucket |

## Related Documentation

- [data-pipelines.md](data-pipelines.md) — How data flows from each source into InfluxDB
- [grafana-dashboards.md](grafana-dashboards.md) — Dashboard inventory, conventions, and Grafana configuration
- [mcp-server.md](mcp-server.md) — MCP server tools, endpoints, and Claude Desktop setup
- [development.md](development.md) — Setup, service management, and troubleshooting
- [backup-recovery.md](backup-recovery.md) — Backup schedule, manual backup trigger, and recovery procedures
