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

## Data Collection Pipelines

### WAGO CSV Sync Pipeline

1. The `sync` service connects to the WAGO PLC at `192.168.1.10` via SSH
2. SCP copies CSV files from `/media/sd/CSV_Files/` to the local `./data/` directory
3. The import script (`scripts/import_data.py`) runs in incremental mode:
   - Reads CSV files with Latin-1 encoding
   - Normalizes headers (handles BOM, degree symbols)
   - Maps CSV columns to InfluxDB fields via `HVAC_SENSOR_MAP` and `ROOM_SENSOR_MAP`
   - Groups fields by sensor group or room type into single InfluxDB points
   - Tracks per-file line counts in `.import_state.json` to avoid re-importing
   - Batch writes (5000 points per batch)
4. Two CSV file patterns:
   - `logfile_dp_*.csv` → `hvac` measurement (6 sensor groups)
   - `Temperatures*.csv` → `rooms` measurement (5 room types)

### Ruuvi MQTT Pipeline

1. Ruuvi Gateway publishes sensor data to MQTT topic `ruuvi/<gateway_mac>/<sensor_mac>`
2. The `ruuvi` service subscribes and parses JSON payloads
3. Data format 5 (basic): temperature, humidity, pressure, acceleration, voltage
4. Data format 225 (air quality): adds CO2, PM, VOC, NOx
5. Pressure auto-converted from Pa to hPa if value > 10000
6. Each message written immediately to InfluxDB (synchronous writes)
7. Indoor temperature from Olohuone sensor forwarded to ThermIQ via MQTT `set` topic

### ThermIQ MQTT Pipeline

1. The `thermia` service sends periodic read commands to `ThermIQ/ThermIQ-room2/read`
2. ThermIQ-ROOM2 responds with register dump on `ThermIQ/marmorikatu/data`
3. Registers parsed from decimal (`dDD`) or hex (`rXX`) keys
4. Data split into 6 InfluxDB points per message:
   - `temperature`: simple registers + combined integer/decimal pairs
   - `status`: bitfield extraction from registers d13, d16, d17
   - `alarm`: bitfield extraction from registers d19, d20
   - `performance`: direct register values
   - `runtime`: hour counters
   - `setting`: configuration values (d79 has ×10 multiplier)

### Lights HTTP Pipeline

1. The `lights` service polls `http://host.docker.internal:8080/api/lights` every 5 minutes
2. Response JSON contains switch status for all light switches
3. Each switch classified by floor (0/1/2) based on `light_id` mapping
4. Dual-function switches produce two data points (primary + secondary)
5. Written to InfluxDB `lights` measurement with floor and name tags

## Grafana Dashboards

Seven provisioned dashboards in `grafana/provisioning/dashboards/`:

| File | UID | Title | Content |
|------|-----|-------|---------|
| `building_overview.json` | `wago-overview` | Temperature Overview | Home dashboard with canvas floorplan, all temperature sources |
| `hvac_dashboard.json` | `wago-hvac` | HVAC | Ventilation temps, heat recovery efficiency, freezing risk, power |
| `hvac_temp_histogram.json` | `hvac-temp-histogram` | HVAC lämpötilojen jakauma | HVAC temperature distribution histograms |
| `lights_status.json` | `wago-lights` | Light Switch Status | Light switch on/off status by floor |
| `room_temp_histogram.json` | `room-temp-histogram` | Huonelämpötilojen jakauma | Room temperature distribution histograms |
| `ruuvi_sensors.json` | `ruuvi-sensors` | Ruuvi Sensors | Ruuvi sensor data, air quality |
| `thermia_heatpump.json` | `thermia-heatpump` | Maalämpöpumppu | Heat pump temps, COP, power, runtimes |

### Dashboard Conventions

- All dashboards tagged `building-automation` plus a topic tag (`wago`, `hvac`, `ruuvi`, `lights`, `thermia`)
- Cross-dashboard navigation links use `/d/<uid>/<slug>` URLs with `includeVars` and `keepTime`
- Flux queries use Grafana variables: `v.timeRangeStart`, `v.timeRangeStop`, `v.windowPeriod`
- Field names use ASCII internally; display name overrides add Finnish characters
- The Temperature Overview dashboard is configured as the Grafana home page

### Editing Dashboards

Dashboards are provisioned from JSON files — they cannot be saved from the
Grafana UI. To modify:

1. Edit the JSON file directly
2. Run `docker compose restart grafana`
3. Verify in the browser

## MCP Server

SSE-based MCP server for Claude Desktop integration at `http://localhost:3001/sse`.
See [mcp-server.md](mcp-server.md) for the full tool listing, endpoints, setup
instructions, and example queries.

## Key Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Service orchestration |
| `scripts/import_data.py` | CSV parser and InfluxDB importer |
| `scripts/mcp_server.py` | MCP server with 15 tools |
| `scripts/ruuvi_mqtt_subscriber.py` | Ruuvi MQTT → InfluxDB |
| `scripts/thermia_mqtt_subscriber.py` | ThermIQ MQTT → InfluxDB |
| `scripts/lights_poller.py` | Light switch API → InfluxDB |
| `grafana/provisioning/dashboards/*.json` | Grafana dashboard definitions |
| `grafana/provisioning/datasources/influxdb.yml` | InfluxDB datasource config |
| `Dockerfile.*` | Container images for each Python service |

## Starting the System

```bash
# Core services (InfluxDB + Grafana + data collectors)
docker compose up -d

# Include WAGO sync (requires SSH key in ./ssh/wago_sync)
docker compose --profile sync up -d

# Manual data import
source venv/bin/activate
python scripts/import_data.py              # Full import (clears existing data)
python scripts/import_data.py --incremental # Append new data only
```
