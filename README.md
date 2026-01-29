# WAGO Building Automation Data Explorer

A data visualization system for building automation measurement data from WAGO controllers and Ruuvi sensors. Imports CSV data and MQTT sensor data into InfluxDB and provides interactive Grafana dashboards for exploring HVAC, room temperature, and environmental data.

## Architecture

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
                                           │              │               │
                                           │              ▼               │
                                           │  ┌────────────────────────┐  │
                                           │  │  InfluxDB 2.7          │  │
                                           │  │  - Time series DB      │  │
                                           │  │  - Flux query language │  │
                                           │  └───────────┬────────────┘  │
                                           │              │               │
                                           │              ▼               │
                                           │  ┌────────────────────────┐  │
                                           │  │  Grafana 10.2          │  │
                                           │  │  - Dashboards          │  │
                                           │  │  - Data exploration    │  │
                                           │  └────────────────────────┘  │
                                           └──────────────────────────────┘
```

## Components

| Component | Technology | Port | Purpose |
|-----------|------------|------|---------|
| Time Series DB | InfluxDB 2.7 | 8086 | Store measurement data |
| Visualization | Grafana 10.2 | 3000 | Interactive dashboards |
| Data Import | Python 3.12 | - | CSV parsing and import |
| Remote Sync | Shell/SCP | - | Fetch data from WAGO controller |
| Ruuvi MQTT | Python/paho-mqtt | - | Subscribe to Ruuvi sensor data |

## Quick Start

### 1. Start Services

```bash
./start.sh
```

This will:
- Start InfluxDB and Grafana containers
- Create Python virtual environment
- Install dependencies
- Import existing CSV data from `./data/`

### 2. Access Dashboards

- **Grafana**: http://localhost:3000 (admin/admin)
- **InfluxDB**: http://localhost:8086 (admin/adminpassword)

## Data Model

### Measurements

The system uses two InfluxDB measurements:

#### `hvac` - HVAC System Data
From `logfile_dp_*.csv` files (daily logs, 2-hour intervals)

| Tag | Values | Description |
|-----|--------|-------------|
| sensor_group | ivk_temp, humidity, power, energy, voltage, actuator | Sensor category |

| Field | Unit | Description |
|-------|------|-------------|
| Ulkolampotila | °C | Outdoor temperature |
| Tuloilma_ennen_lammitysta | °C | Supply air before heating |
| Tuloilma_asetusarvo | °C | Supply air setpoint |
| Tuloilma_jalkeen_lammityksen | °C | Supply air after heating |
| Jateilma | °C | Exhaust air temperature |
| Tuloilma_jalkeen_jaahdytyksen | °C | Supply air after cooling |
| RH_lampotila | °C | RH sensor temperature |
| Suhteellinen_kosteus | % | Relative humidity |
| Kastepiste | °C | Dew point |
| Lampopumppu_teho | kW | Heat pump power |
| Lisavastus_teho | kW | Auxiliary heater power |
| Lampopumppu_energia | kWh | Heat pump energy |
| Lisavastus_energia | kWh | Auxiliary heater energy |

#### `rooms` - Room Temperature Data
From `Temperatures*.csv` files (annual logs, hourly intervals)

| Tag | Values | Description |
|-----|--------|-------------|
| room_type | bedroom, common, basement, pid, energy | Room category |

| Field | Unit | Description |
|-------|------|-------------|
| MH_Seela | °C | Bedroom - Seela |
| MH_Aarni | °C | Bedroom - Aarni |
| MH_aikuiset | °C | Bedroom - Adults |
| MH_alakerta | °C | Bedroom - Downstairs |
| Ylakerran_aula | °C | Upstairs hallway |
| Keittio | °C | Kitchen |
| Eteinen | °C | Entrance |
| Kellari | °C | Basement |
| Kellari_eteinen | °C | Basement entrance |
| *_PID | % | PID controller output (0-100%) |

#### `ruuvi` - Ruuvi Sensor Data
Real-time data from Ruuvi Bluetooth sensors via MQTT gateway

| Tag | Description |
|-----|-------------|
| sensor_id | MAC address of the sensor |
| sensor_name | Friendly name (e.g., "Keittiö") |
| data_format | Ruuvi data format (5 = basic, 225 = air quality) |
| sensor_type | basic or air_quality |

**Basic sensors (dataFormat 5):**

| Field | Unit | Description |
|-------|------|-------------|
| temperature | °C | Temperature |
| humidity | % | Relative humidity |
| pressure | hPa | Atmospheric pressure |
| voltage | V | Battery voltage |
| rssi | dBm | Signal strength |
| accel_x, accel_y, accel_z | g | Acceleration |
| movement_counter | - | Movement detection counter |

**Air quality sensors (dataFormat 225):**

| Field | Unit | Description |
|-------|------|-------------|
| temperature | °C | Temperature |
| humidity | % | Relative humidity |
| pressure | hPa | Atmospheric pressure |
| co2 | ppm | Carbon dioxide |
| pm1_0, pm2_5, pm4_0, pm10_0 | µg/m³ | Particulate matter |
| voc | index | Volatile organic compounds |
| nox | index | Nitrogen oxides |
| rssi | dBm | Signal strength |

**Configured sensors:**

| MAC Address | Name | Type |
|-------------|------|------|
| D1:86:61:6E:DF:E4 | Sauna | Basic |
| D3:1D:6A:1E:7C:4E | Takka | Basic |
| D7:6C:BC:6D:29:46 | Olohuone | Basic |
| E6:DC:F8:EC:78:3B | Keittiö | Air Quality |
| EE:3A:F4:B9:74:E5 | Jääkaappi | Basic |
| EF:AA:DF:C0:4F:8C | Pakastin | Basic |
| F1:19:ED:0F:9A:F6 | Ulkolämpötila | Basic |

## Data Import

### Manual Import

```bash
# Full import (clears existing data)
source venv/bin/activate
python scripts/import_data.py

# Incremental import (appends new data)
python scripts/import_data.py --incremental
```

### Import Script Features

- **Encoding**: Handles Latin-1 (ISO-8859-1) encoded CSV files
- **Validation**: Filters invalid sensor readings (temperature: -50 to 100°C)
- **Batch Processing**: Writes 5000 points per batch for efficiency
- **Incremental Mode**: Only processes files modified since last sync

## Remote Sync (WAGO Controller)

### Prerequisites

1. Generate SSH key (RSA for dropbear compatibility):
   ```bash
   ssh-keygen -t rsa -b 4096 -f ./ssh/wago_sync -N ""
   ```

2. Copy key to WAGO controller:
   ```bash
   ssh-copy-id -o PubkeyAcceptedAlgorithms=+ssh-rsa -i ./ssh/wago_sync.pub admin@192.168.1.10
   ```

3. Test connection:
   ```bash
   ssh -i ./ssh/wago_sync \
       -o PubkeyAcceptedAlgorithms=+ssh-rsa \
       -o HostKeyAlgorithms=+ssh-rsa \
       admin@192.168.1.10 "ls /media/sd/CSV_Files/"
   ```

### Start Sync Service

```bash
# Start all services including sync
docker compose --profile sync up -d

# View sync logs
docker compose logs -f sync

# Manual sync
docker compose exec sync /scripts/sync_and_import.sh
```

### Sync Configuration

Environment variables (in `docker-compose.yml`):

| Variable | Default | Description |
|----------|---------|-------------|
| SYNC_INTERVAL | 300 | Seconds between syncs (5 min) |
| REMOTE_HOST | 192.168.1.10 | WAGO controller IP |
| REMOTE_USER | admin | SSH username |
| REMOTE_PATH | /media/sd/CSV_Files/ | Remote data path |

### How Sync Works

1. **Check for changes**: Lists remote files via SSH, compares sizes with local
2. **Download changed files**: Only fetches new or modified CSV files via SCP
3. **Incremental import**: Runs import script to add new data to InfluxDB
4. **Repeat**: Waits for SYNC_INTERVAL, then repeats

## Ruuvi MQTT Service

The Ruuvi service subscribes to MQTT messages from a Ruuvi Gateway and stores sensor data in InfluxDB.

### Start Ruuvi Service

```bash
# Start Ruuvi service (requires InfluxDB to be running)
docker compose up -d ruuvi

# View logs
docker compose logs -f ruuvi
```

### Configuration

Environment variables (in `docker-compose.yml`):

| Variable | Default | Description |
|----------|---------|-------------|
| MQTT_BROKER | freenas.kherrala.fi | MQTT broker hostname |
| MQTT_PORT | 1883 | MQTT broker port |
| MQTT_TOPIC | ruuvi/CC:F1:A2:8E:F8:8A/# | Topic subscription pattern |
| RUUVI_SENSOR_NAMES | (JSON) | MAC to friendly name mapping |

### Sensor Name Mapping

Sensor names are configured via the `RUUVI_SENSOR_NAMES` environment variable as JSON:

```json
{
  "D1:86:61:6E:DF:E4": "Sauna",
  "E6:DC:F8:EC:78:3B": "Keittiö",
  "F1:19:ED:0F:9A:F6": "Ulkolämpötila"
}
```

### Clear Ruuvi Data

To reset Ruuvi data and start fresh:

```bash
# Delete all Ruuvi data from InfluxDB
curl -X POST "http://localhost:8086/api/v2/delete?org=wago&bucket=building_automation" \
  -H "Authorization: Token wago-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "start": "1970-01-01T00:00:00Z",
    "stop": "2100-01-01T00:00:00Z",
    "predicate": "_measurement=\"ruuvi\""
  }'

# Restart the service
docker compose restart ruuvi
```

## Grafana Dashboards

### Building Automation Overview

Main dashboard for WAGO building automation data:

| Panel | Description |
|-------|-------------|
| Makuuhuoneiden lämpötilat | Bedroom temperatures |
| Yleiset tilat | Common areas (hallway, kitchen, entrance) |
| Kellarin lämpötilat | Basement temperatures |
| Suhteellinen kosteus | Relative humidity |
| Lämmitystarve kerroksittain | Heating demand by floor (PID sum) |
| Lämmityksen tila huoneittain | Heating status timeline per room |
| IVK Ulko- ja jäteilma | Outdoor and exhaust air temperatures |
| IVK Tuloilma ja asetusarvot | Supply air and setpoints |
| RH lämpötila ja kastepiste | RH temperature and dew point |

### Energy Consumption

Dashboard for HVAC heat recovery efficiency and energy consumption:

| Panel | Description |
|-------|-------------|
| LTO hyötysuhde (tuntuva lämpö) | Heat recovery efficiency (sensible heat) |
| LTO hyötysuhde (entalpia) | Heat recovery efficiency (enthalpy-based) |
| LTO talteen otettu teho | Recovered heat power (kW) |
| Tehonkulutus | Power consumption (heat pump, auxiliary) |
| Energiankulutus (kumulatiivinen) | Cumulative energy consumption |

### Ruuvi Sensors

Dashboard for Ruuvi Bluetooth sensor data:

| Panel | Description |
|-------|-------------|
| Lämpötila | Temperature from all sensors |
| Ilmankosteus | Humidity from all sensors |
| Ilmanpaine | Atmospheric pressure |
| Hiilidioksidi (CO₂) | CO₂ levels with thresholds (800/1200 ppm) |
| Pienhiukkaset (PM) | PM1.0, PM2.5, PM4.0, PM10 |
| VOC ja NOx | Air quality indices |
| Paristojännite | Battery voltage with low battery warning |
| Signaalivoimakkuus (RSSI) | Bluetooth signal strength |
| Nykyiset lämpötilat | Current temperature stat panel |

### Time Range

Default: Last 7 days (Building), Last 24 hours (Ruuvi). Use Grafana time picker to adjust.

## Energy Calculations

The Energy Consumption dashboard calculates HVAC heat recovery (LTO) efficiency using data from multiple sources.

### Data Alignment

Due to different sampling rates, data must be aligned before calculations:

| Source | Sampling Rate | Notes |
|--------|---------------|-------|
| HVAC temperatures | Every 5 minutes | Irregular start times |
| HVAC humidity | Every 2 hours | On fixed schedule |
| Ruuvi sensors | ~1 second | May have gaps during outages |

All timestamps are truncated to 2-hour clock boundaries (00:00, 02:00, 04:00, etc.) using integer division on nanosecond timestamps:

```flux
time(v: ((int(v: r._time) / 7200000000000) * 7200000000000))
```

Data within each 2-hour bucket is averaged, then joined across sources.

### System Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| Airflow rate | 414 m³/h | Ventilation system airflow |
| Air density | 1.2 kg/m³ | At standard conditions |
| Mass flow rate | 0.138 kg/s | = 414 × 1.2 / 3600 |
| Specific heat (dry air) | 1.006 kJ/(kg·K) | cp for air at ~20°C |

### Sensible Heat Efficiency

Calculates efficiency based on dry air temperature differences only:

```
η_sensible = (T_supply - T_outdoor) / (T_exhaust - T_outdoor) × 100%
```

Where:
- **T_supply** = `Tuloilma_ennen_lammitysta` - Supply air temperature after heat recovery (before heating coil)
- **T_outdoor** = `Ulkolampotila` - Outdoor air temperature
- **T_exhaust** = `Tuloilma_asetusarvo` - Supply air setpoint (proxy for indoor/exhaust air temperature before HRU)

This represents how much of the temperature difference between exhaust and outdoor air is recovered to the supply air.

### Enthalpy-based Efficiency

Accounts for both sensible and latent heat by calculating moist air enthalpy:

```
η_enthalpy = (h_supply - h_outdoor) / (h_exhaust - h_outdoor) × 100%
```

#### Enthalpy Calculation

Moist air specific enthalpy (kJ/kg dry air):

```
h = 1.006 × T + w × (2501 + 1.86 × T)
```

Where:
- **T** = Temperature (°C)
- **w** = Humidity ratio (kg water / kg dry air)
- **1.006** = Specific heat of dry air (kJ/(kg·K))
- **2501** = Latent heat of vaporization at 0°C (kJ/kg)
- **1.86** = Specific heat of water vapor (kJ/(kg·K))

#### Humidity Ratio Calculation

```
w = 0.622 × (RH/100) × p_sat / (p_atm - (RH/100) × p_sat)
```

Where:
- **RH** = Relative humidity (%)
- **p_atm** = Atmospheric pressure (101325 Pa)
- **p_sat** = Saturation vapor pressure (Pa)

#### Saturation Vapor Pressure (Tetens Formula)

```
p_sat = 610.78 × 10^(7.5 × T / (237.3 + T))
```

Where **T** is temperature in °C.

#### Data Sources for Enthalpy Calculation

| Variable | Source | Measurement | Fallback |
|----------|--------|-------------|----------|
| T_outdoor | HVAC | `Ulkolampotila` | Required |
| T_supply | HVAC | `Tuloilma_ennen_lammitysta` | Required |
| T_exhaust | HVAC | `Tuloilma_asetusarvo` (supply setpoint as proxy) | Required |
| RH_exhaust | HVAC | `Suhteellinen_kosteus` | Required |
| RH_outdoor | Ruuvi | `Ulkolämpötila` sensor, `humidity` field | 85% RH |

**Note:** When Ruuvi outdoor humidity data is unavailable, a fallback value of 85% RH is used (typical for Finnish winter conditions). This allows the enthalpy calculation to continue during sensor outages.

### Recovered Heat Power

Calculates instantaneous heat power recovered by the heat exchanger:

```
Q = ṁ × cp × ΔT = 0.1387 kW/K × (T_supply - T_outdoor)
```

Where:
- **ṁ × cp** = 0.138 kg/s × 1.006 kJ/(kg·K) ≈ 0.1387 kW/K
- **ΔT** = Temperature rise across the heat recovery unit

The coefficient 0.1387 kW/K means that for every 1°C temperature rise in the supply air, approximately 139 W of heat is recovered.

### Efficiency Thresholds

The efficiency graphs display threshold lines:

| Threshold | Value | Meaning |
|-----------|-------|---------|
| Green | < 50% | Below expected performance |
| Yellow | 50-70% | Normal operating range |
| Red | > 70% | Good heat recovery performance |

Note: Higher efficiency is better. Typical rotary heat exchangers achieve 70-85% efficiency.

### Sensor Notes

- `Tuloilma_asetusarvo` (supply air setpoint) is used as a proxy for exhaust air temperature before the HRU, as there is no dedicated sensor for this measurement
- `Jateilma` measures exhaust air *after* the heat recovery unit (post-HRU) and is not suitable for efficiency calculation
- `Suhteellinen_kosteus` measures relative humidity at the exhaust side of the HRU

## MCP Server for Claude Desktop

An MCP (Model Context Protocol) server is included for integrating with Claude Desktop, enabling natural language queries about building automation data.

### Available Tools

| Tool | Description |
|------|-------------|
| `describe_schema` | Get complete data model with all measurements, fields, and units |
| `list_measurements` | List available measurements (hvac, rooms, ruuvi) |
| `describe_measurement` | Get details about a specific measurement |
| `query_data` | Execute custom Flux queries |
| `get_latest` | Get most recent values for specified fields |
| `get_statistics` | Get min/max/mean/count for a field over time |
| `get_time_range` | Get data availability for a measurement |
| `get_heat_recovery_efficiency` | Calculate HRU efficiency with summary stats |
| `get_energy_consumption` | Get energy consumption summary |
| `get_room_temperatures` | Get all room temps and heating demand |
| `get_air_quality` | Get CO2, PM2.5, VOC, NOx from kitchen sensor |
| `compare_indoor_outdoor` | Compare indoor vs outdoor temperatures |

### Setup with Docker

1. Build the MCP container:
   ```bash
   docker compose build mcp
   ```

2. Configure Claude Desktop (`~/.config/claude/claude_desktop_config.json` on Linux/Mac or `%APPDATA%\Claude\claude_desktop_config.json` on Windows):
   ```json
   {
     "mcpServers": {
       "building-automation": {
         "command": "/path/to/wago-csv-explorer/scripts/mcp-claude-desktop.sh"
       }
     }
   }
   ```

3. Restart Claude Desktop

### Setup without Docker (Local Python)

1. Configure Claude Desktop:
   ```json
   {
     "mcpServers": {
       "building-automation": {
         "command": "/path/to/wago-csv-explorer/scripts/mcp-local.sh"
       }
     }
   }
   ```

2. The script will automatically create a virtual environment and install dependencies

### Example Queries in Claude Desktop

- "What's the current outdoor temperature?"
- "Show me the heat recovery efficiency for the last week"
- "What's the air quality in the kitchen?"
- "Compare indoor and outdoor temperatures over the last 24 hours"
- "How much energy has the heat pump consumed this month?"
- "List all room temperatures and heating demand"

## File Structure

```
wago-csv-explorer/
├── docker-compose.yml          # Container orchestration
├── Dockerfile.sync             # Sync container image
├── Dockerfile.ruuvi            # Ruuvi MQTT subscriber image
├── Dockerfile.mcp              # MCP server image
├── start.sh                    # Quick start script
├── data/                       # CSV data files (git-ignored)
│   ├── Temperatures*.csv       # Room temperature logs
│   └── logfile_dp_*.csv        # HVAC daily logs
├── scripts/
│   ├── import_data.py          # WAGO CSV data import script
│   ├── ruuvi_mqtt_subscriber.py # Ruuvi MQTT to InfluxDB
│   ├── mcp_server.py           # MCP server for Claude Desktop
│   ├── mcp-claude-desktop.sh   # Docker wrapper for Claude Desktop
│   ├── mcp-local.sh            # Local Python wrapper for Claude Desktop
│   ├── sync_and_import.sh      # Remote sync script
│   └── requirements.txt        # Python dependencies
├── ssh/
│   ├── README.md               # SSH setup instructions
│   └── wago_sync               # SSH private key (git-ignored)
└── grafana/
    └── provisioning/
        ├── datasources/
        │   └── influxdb.yml
        └── dashboards/
            ├── dashboards.yml
            ├── building_overview.json  # WAGO building automation
            ├── energy_consumption.json # Heat recovery efficiency & energy
            └── ruuvi_sensors.json      # Ruuvi sensor data
```

## Troubleshooting

### No data in Grafana

1. Check InfluxDB health: `curl http://localhost:8086/ping`
2. Verify data exists:
   ```bash
   curl -X POST "http://localhost:8086/api/v2/query?org=wago" \
     -H "Authorization: Token wago-secret-token" \
     -H "Content-Type: application/vnd.flux" \
     -d 'from(bucket: "building_automation") |> range(start: -1d) |> limit(n: 5)'
   ```
3. Re-run import: `python scripts/import_data.py`

### SSH connection fails

- Ensure RSA key is used (dropbear compatibility)
- Add legacy algorithm options:
  ```bash
  ssh -o PubkeyAcceptedAlgorithms=+ssh-rsa -o HostKeyAlgorithms=+ssh-rsa ...
  ```

### Sync container errors

- Check SSH key exists: `ls -la ./ssh/wago_sync`
- View logs: `docker compose logs sync`
- Test SSH manually from host first

## Stopping Services

```bash
# Stop all containers
docker compose down

# Stop and remove volumes (deletes all data!)
docker compose down -v
```

## License

Internal use only.
