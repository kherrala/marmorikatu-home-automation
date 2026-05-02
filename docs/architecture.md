# System Architecture

Building automation data collection and visualization system. Collects data from
three MQTT sources, stores in InfluxDB, and visualizes with Grafana dashboards.
Includes an MCP server for Claude Desktop integration.

## Data Flow

```
┌─────────────────────┐                    ┌──────────────────────────────┐
│  WAGO PFC200 PLC    │                    │      Docker Compose          │
│  192.168.1.10       │                    │                              │
│                     │  MQTT (10 retained │  ┌────────────────────────┐  │
│  marmorikatu/...    │  topics, ~13s)     │  │  plc container         │  │
│  (lights, switches, │◄───────────────────┤  │  - MQTT subscriber     │  │
│   heating, cooling, │  freenas:1883      │  │  - Per-topic dispatch  │  │
│   outlets, temps,   │                    │  │  - Existing schema     │  │
│   ventilation, 2×   │                    │  └───────────┬────────────┘  │
│   energy meters,    │                    │              │               │
│   status)           │                    │              │               │
└─────────────────────┘                    │              │               │
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

### plc — WAGO PLC MQTT Subscriber

| Property | Value |
|----------|-------|
| Dockerfile | `Dockerfile.plc` |
| Container | `marmorikatu-plc` |
| Restart | `unless-stopped` |
| Depends on | `influxdb` (healthy) |

Subscribes to all ten retained `marmorikatu/...` MQTT topics published by the
WAGO PLC's `pMqttPublish` POU. Each topic is parsed by a dedicated handler and
written to InfluxDB using the existing measurement schema so dashboards, MCP
tools, and the heating optimizer continue to work unchanged.

Topic protocol: see `../marmorikatu-plc/MQTT.md`.

Environment:

| Variable | Purpose |
|----------|---------|
| `MQTT_BROKER` | MQTT broker hostname (`freenas.kherrala.fi`) |
| `MQTT_PORT` | MQTT broker port (`1883`) |
| `MQTT_TOPIC_PREFIX` | Topic prefix to subscribe under (`marmorikatu`) |
| `INFLUXDB_URL` | InfluxDB connection URL |
| `INFLUXDB_TOKEN` | API authentication token |
| `INFLUXDB_ORG` | Organization name |
| `INFLUXDB_BUCKET` | Target bucket |

### Legacy services (disabled)

The following services are commented out in `docker-compose.yml` and replaced
by the `plc` MQTT subscriber. They are kept in the file for emergency rollback.

- **sync** (`Dockerfile.sync`, `scripts/import_data.py`) — Polled CSV files from
  the PLC over SSH/SCP every 5 minutes. Replaced by retained MQTT topics.
- **lights** (`Dockerfile.lights`, `scripts/lights_poller.py`) — Polled an HTTP
  light-switch API every 5 minutes. Replaced by `marmorikatu/lights` and
  `marmorikatu/outlets` topics, which write to the same `lights` measurement.

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

## Related Documentation

- [data-pipelines.md](data-pipelines.md) — How data flows from each source into InfluxDB
- [grafana-dashboards.md](grafana-dashboards.md) — Dashboard inventory, conventions, and Grafana configuration
- [mcp-server.md](mcp-server.md) — MCP server tools, endpoints, and Claude Desktop setup
- [development.md](development.md) — Setup, service management, and troubleshooting
- [backup-recovery.md](backup-recovery.md) — Backup schedule, manual backup trigger, and recovery procedures
