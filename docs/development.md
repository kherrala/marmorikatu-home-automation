# Development & Operations Guide

Setup instructions, service management, data operations, and troubleshooting.

## Prerequisites

- Docker and Docker Compose
- Python 3.12+ (for local development / manual import)
- SSH key for WAGO controller access (sync service only)

## Starting Services

### Quick Start

```bash
./start.sh
```

This will start InfluxDB and Grafana containers, create a Python virtual
environment, install dependencies, and import existing CSV data from `./data/`.

### Docker Compose

```bash
# Core services (InfluxDB + Grafana + data collectors)
docker compose up -d

# Include WAGO sync (requires SSH key in ./ssh/wago_sync)
docker compose --profile sync up -d

# Start individual services
docker compose up -d ruuvi
docker compose up -d thermia
docker compose up -d lights
docker compose up -d mcp

# View service logs
docker compose logs -f <service>   # influxdb | grafana | mcp | ruuvi | thermia | lights | sync
```

### Access Points

- **Grafana**: http://localhost:3000 (admin/admin)
- **InfluxDB**: http://localhost:8086 (admin/adminpassword)
- **MCP Server**: http://localhost:3001/mcp

## WAGO Controller SSH Setup

The sync service requires an RSA SSH key (the WAGO controller runs dropbear,
which requires RSA).

### 1. Generate SSH Key

```bash
ssh-keygen -t rsa -b 4096 -f ./ssh/wago_sync -N ""
```

### 2. Copy Key to Controller

```bash
ssh-copy-id -o PubkeyAcceptedAlgorithms=+ssh-rsa -i ./ssh/wago_sync.pub admin@192.168.1.10
```

### 3. Test Connection

```bash
ssh -i ./ssh/wago_sync \
    -o PubkeyAcceptedAlgorithms=+ssh-rsa \
    -o HostKeyAlgorithms=+ssh-rsa \
    admin@192.168.1.10 "ls /media/sd/CSV_Files/"
```

### 4. Start Sync Service

```bash
docker compose --profile sync up -d

# View sync logs
docker compose logs -f sync

# Manual sync
docker compose exec sync /scripts/sync_and_import.sh
```

### Sync Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNC_INTERVAL` | `300` | Seconds between syncs (5 min) |
| `REMOTE_HOST` | `192.168.1.10` | WAGO controller IP |
| `REMOTE_USER` | `admin` | SSH username |
| `REMOTE_PATH` | `/media/sd/CSV_Files/` | Remote data path |

### How Sync Works

1. Lists remote files via SSH, compares sizes with local copies
2. Downloads only new or modified CSV files via SCP
3. Runs `import_data.py --incremental` to add new data to InfluxDB
4. Waits for `SYNC_INTERVAL`, then repeats

## Ruuvi MQTT Service

### Sensor Name Mapping

Sensor names are configured via the `RUUVI_SENSOR_NAMES` environment variable as JSON:

```json
{
  "D1:86:61:6E:DF:E4": "Sauna",
  "D3:1D:6A:1E:7C:4E": "Takka",
  "D7:6C:BC:6D:29:46": "Olohuone",
  "E6:DC:F8:EC:78:3B": "Keittiö",
  "EE:3A:F4:B9:74:E5": "Jääkaappi",
  "EF:AA:DF:C0:4F:8C": "Pakastin",
  "F1:19:ED:0F:9A:F6": "Ulkolämpötila"
}
```

### Indoor Temperature Forwarding

The Ruuvi service forwards the Olohuone sensor's temperature to the ThermIQ
heat pump via MQTT (`ThermIQ/marmorikatu/set` topic). Temperature values outside
19–25°C are rejected as invalid.

## Thermia MQTT Service

### Register Format

The ThermIQ module publishes heat pump registers as a flat JSON object. Register
keys can be in hex (`rXX`) or decimal (`dDD`) format — both are handled
automatically:

```json
{"r00": -5, "r01": 21, "r02": 3, "r05": 35, "r0d": 3, "r10": 7, ...}
```

Bitfield registers (r0d, r10, r11, r13, r14) are expanded into individual
boolean fields for component status and alarm states.

See [thermiq_register_map.md](thermiq_register_map.md) for the complete
register map.

## Data Import

### Manual Import

```bash
source venv/bin/activate

# Full import (clears existing data)
python scripts/import_data.py

# Incremental import (appends new data)
python scripts/import_data.py --incremental
```

### Import Script Features

- **Encoding**: Handles Latin-1 (ISO-8859-1) encoded CSV files with BOM handling
- **Validation**: Filters invalid sensor readings (temperature: -50 to 100°C,
  humidity: 0–100%, power: 0–100 kW, absolute values > 1×10¹⁰)
- **Batch Processing**: Writes 5000 points per batch for efficiency
- **Incremental Mode**: Tracks per-file line counts in `.import_state.json`
  to avoid re-importing previously processed lines
- **Header Normalization**: Handles various degree symbol encodings (°, º, \xba)

## Data Management

### Clear Measurement Data

```bash
# Delete all Ruuvi data
curl -X POST "http://localhost:8086/api/v2/delete?org=wago&bucket=building_automation" \
  -H "Authorization: Token wago-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "start": "1970-01-01T00:00:00Z",
    "stop": "2100-01-01T00:00:00Z",
    "predicate": "_measurement=\"ruuvi\""
  }'

# Replace "ruuvi" with "hvac", "rooms", "thermia", or "lights" as needed
```

### Verify Data Exists

```bash
curl -X POST "http://localhost:8086/api/v2/query?org=wago" \
  -H "Authorization: Token wago-secret-token" \
  -H "Content-Type: application/vnd.flux" \
  -d 'from(bucket: "building_automation") |> range(start: -1d) |> limit(n: 5)'
```

## Stopping Services

```bash
# Stop all containers
docker compose down

# Stop and remove volumes (deletes all data!)
docker compose down -v
```

## File Structure

```
marmorikatu-home-automation/
├── docker-compose.yml          # Container orchestration
├── Dockerfile.sync             # Sync container image
├── Dockerfile.ruuvi            # Ruuvi MQTT subscriber image
├── Dockerfile.thermia          # Thermia MQTT subscriber image
├── Dockerfile.mcp              # MCP server image
├── Dockerfile.lights           # Light switch poller image
├── start.sh                    # Quick start script
├── data/                       # CSV data files (git-ignored)
│   ├── Temperatures*.csv       # Room temperature logs
│   └── logfile_dp_*.csv        # HVAC daily logs
├── scripts/
│   ├── import_data.py          # WAGO CSV data import script
│   ├── ruuvi_mqtt_subscriber.py # Ruuvi MQTT to InfluxDB
│   ├── thermia_mqtt_subscriber.py # ThermIQ heat pump MQTT to InfluxDB
│   ├── lights_poller.py        # Light switch API poller
│   ├── mcp_server.py           # MCP server for Claude Desktop (SSE)
│   ├── sync_and_import.sh      # Remote sync script
│   └── requirements.txt        # Python dependencies
├── ssh/
│   ├── README.md               # SSH setup instructions
│   └── wago_sync               # SSH private key (git-ignored)
├── floorplan/                  # Building floorplan images for Canvas panel
├── docs/                       # Documentation
│   ├── architecture.md         # System architecture
│   ├── influxdb-data-model.md  # InfluxDB schema reference
│   ├── heat-recovery-efficiency.md # Heat recovery & freezing risk
│   ├── heatpump-efficiency.md  # Heat pump COP calculations
│   ├── thermiq_register_map.md # ThermIQ register definitions
│   ├── mcp-server.md           # MCP server tools & setup
│   └── development.md          # This file
└── grafana/
    └── provisioning/
        ├── datasources/
        │   └── influxdb.yml
        └── dashboards/
            ├── dashboards.yml
            ├── building_overview.json  # Temperature overview (home)
            ├── hvac_dashboard.json     # HVAC & heat recovery
            ├── hvac_temp_histogram.json # HVAC temp distributions
            ├── room_temp_histogram.json # Room temp distributions
            ├── ruuvi_sensors.json      # Ruuvi sensor data
            ├── thermia_heatpump.json   # Thermia heat pump
            └── lights_status.json      # Light switch status
```

## Troubleshooting

### No data in Grafana

1. Check InfluxDB health: `curl http://localhost:8086/ping`
2. Verify data exists (see [Verify Data Exists](#verify-data-exists) above)
3. Re-run import: `python scripts/import_data.py`
4. Check that the Grafana datasource is configured correctly in
   `grafana/provisioning/datasources/influxdb.yml`

### SSH connection fails

- Ensure RSA key is used (dropbear compatibility)
- Add legacy algorithm options:
  ```bash
  ssh -o PubkeyAcceptedAlgorithms=+ssh-rsa -o HostKeyAlgorithms=+ssh-rsa ...
  ```
- Check key permissions: `chmod 600 ./ssh/wago_sync`

### Sync container errors

- Check SSH key exists: `ls -la ./ssh/wago_sync`
- View logs: `docker compose logs sync`
- Test SSH manually from host first
- Verify WAGO controller is reachable: `ping 192.168.1.10`

### MQTT connection issues

- Verify broker is reachable: `mosquitto_sub -h freenas.kherrala.fi -t '#' -C 1`
- Check container logs: `docker compose logs ruuvi` or `docker compose logs thermia`
- Ensure InfluxDB is healthy before starting MQTT services

### Grafana dashboard changes not visible

- Dashboards are provisioned from JSON files — changes in Grafana UI are not saved
- Edit the JSON file directly, then `docker compose restart grafana`
- Check for JSON syntax errors in `docker compose logs grafana`
