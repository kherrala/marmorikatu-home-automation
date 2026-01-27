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
| Tehonkulutus | Power consumption (heat pump, auxiliary) |
| Energiankulutus | Energy consumption |

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

## File Structure

```
wago-csv-explorer/
├── docker-compose.yml          # Container orchestration
├── Dockerfile.sync             # Sync container image
├── Dockerfile.ruuvi            # Ruuvi MQTT subscriber image
├── start.sh                    # Quick start script
├── data/                       # CSV data files (git-ignored)
│   ├── Temperatures*.csv       # Room temperature logs
│   └── logfile_dp_*.csv        # HVAC daily logs
├── scripts/
│   ├── import_data.py          # WAGO CSV data import script
│   ├── ruuvi_mqtt_subscriber.py # Ruuvi MQTT to InfluxDB
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
