# Marmorikatu Home Automation

A building automation data collection and visualization system. Collects data from a WAGO PLC controller, Ruuvi Bluetooth sensors, a Thermia ground-source heat pump, and light switch APIs. Stores everything in InfluxDB, visualizes with Grafana dashboards, and serves a kiosk display with weather, news, and calendar widgets. Includes an MCP server for Claude Desktop integration and a price-aware heating optimizer.

## Architecture

Sixteen Docker services orchestrate data collection, storage, visualization, and smart control.

```
┌─────────────────────┐                    ┌───────────────────────────────────────────┐
│  WAGO Controller    │    SSH/SCP         │            Docker Compose                 │
│  CSV files          │◄───────────────────┤  sync ─────────────┐                      │
└─────────────────────┘    (every 5 min)   │                    │                      │
┌─────────────────────┐                    │                    │                      │
│  Ruuvi Gateway      │    MQTT            │  ruuvi ────────────┤                      │
│  7 BLE sensors      │◄───────────────────┤                    │                      │
└─────────────────────┘                    │                    │                      │
┌─────────────────────┐                    │                    ▼                      │
│  Thermia Heat Pump  │    MQTT            │  thermia ───▶ InfluxDB 2.7 ◀── backup    │
│  ThermIQ-ROOM2      │◄──────────────┬────┤                    │                      │
└─────────────────────┘               │    │  lights ───────────┤                      │
┌─────────────────────┐               │    │                    │                      │
│  Light Switch API   │    HTTP       │    │  electricity ──────┘                      │
│  Building switches  │◄──────────────┼────┤                                           │
└─────────────────────┘               │    │         ┌──────────┴──────────┐            │
┌─────────────────────┐               │    │         ▼                    ▼            │
│  spot-hinta.fi      │    HTTP       │    │  ┌────────────┐     ┌────────────┐        │
│  Electricity prices │◄──────────────┼────┤  │  Grafana   │     │ MCP Server │        │
└─────────────────────┘               │    │  │  :3000     │     │ :3001 (SSE)│        │
                                      │    │  └────────────┘     └─────┬──────┘        │
┌─────────────────────┐               │    │                           │               │
│  FMI Open Data      │◄──────────────┼────┤  weather :3020 ──┐       ▼               │
└─────────────────────┘               │    │                   │  Claude Desktop       │
┌─────────────────────┐               │    │  news :3021 ──────┤                       │
│  YLE RSS            │◄──────────────┼────┤                   │                       │
└─────────────────────┘               │    │  calendar :3022 ──┤                       │
┌─────────────────────┐               │    │                   ▼                       │
│  iCal + PJHOY       │◄──────────────┼────┤  ┌──────────────────────────┐             │
└─────────────────────┘               │    │  │  kiosk (nginx :80/443)   │             │
                                      │    │  │  weather│news│calendar   │             │
                                      │    │  │  claude-bridge :3002     │             │
                                      │    │  └──────────────────────────┘             │
                                      │    │                                           │
                                      │    │  heating ─── price-aware optimizer        │
                                      └────┤  indoor ──── room temp → ThermIQ          │
                                           └───────────────────────────────────────────┘
```

### Services

| Service | Technology | Port | Purpose |
|---------|------------|------|---------|
| influxdb | InfluxDB 2.7 | 8086 | Time-series database |
| grafana | Grafana 12.3 | 3000 | Dashboard visualization |
| sync | Python/SCP | — | WAGO CSV sync + import (profile: `sync`) |
| ruuvi | Python/MQTT | — | Ruuvi Bluetooth sensor data |
| thermia | Python/MQTT | — | ThermIQ heat pump data |
| lights | Python/HTTP | — | Light switch status polling |
| electricity | Python/HTTP | — | Spot electricity price polling |
| heating | Python | — | Price-aware heating optimizer |
| indoor | Python/MQTT | — | Indoor temp publisher to ThermIQ |
| mcp | Python/SSE | 3001 | MCP server for Claude Desktop |
| claude-bridge | Python | 3002 | AI bridge for kiosk (Claude/OpenAI/Ollama) |
| weather | Python | 3020 | FMI weather data + forecasts |
| news | Python | 3021 | YLE news RSS aggregator |
| calendar | Python | 3022 | Family calendar + garbage collection |
| kiosk | nginx | 80/443 | Wall-mounted kiosk display |
| backup | Python | — | InfluxDB backup with 30-day retention |

See [docs/architecture.md](docs/architecture.md) for full details on services, data pipelines, environment variables, and configuration.

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local scripts)

### 1. Start Services

```bash
./start.sh
```

This starts InfluxDB and Grafana, creates a Python venv, installs dependencies, and imports existing CSV data from `./data/`.

To start all services including data collectors:

```bash
docker compose up -d

# With WAGO sync (requires SSH key in ./ssh/wago_sync)
docker compose --profile sync up -d
```

### 2. Access Dashboards

- **Grafana**: http://localhost:3000 (admin/admin)
- **InfluxDB**: http://localhost:8086 (admin/adminpassword)
- **Kiosk**: https://localhost (wall display)

## Data Model

Six InfluxDB measurements in bucket `building_automation`:

| Measurement | Source | Sampling | Content |
|-------------|--------|----------|---------|
| `hvac` | WAGO CSV | ~2 hours | HVAC temperatures, humidity, power, energy, voltages |
| `rooms` | WAGO CSV | ~1 hour | Room temperatures, PID controller outputs |
| `ruuvi` | Ruuvi MQTT | ~1 second | Temperature, humidity, pressure, air quality (CO2, PM, VOC) |
| `thermia` | ThermIQ MQTT | ~30 seconds | Heat pump temperatures, status, alarms, runtimes |
| `lights` | HTTP API | 5 minutes | Light switch on/off status |
| `electricity` | Spot API | 1 hour | Spot electricity prices |

See [docs/influxdb-data-model.md](docs/influxdb-data-model.md) for complete schema with all tags, fields, data types, units, and example Flux queries.

## Grafana Dashboards

Twelve provisioned dashboards:

| Dashboard | UID | Content |
|-----------|-----|---------|
| Temperature Overview | `wago-overview` | Home dashboard with floorplan canvas |
| HVAC | `wago-hvac` | Ventilation temps, heat recovery, freezing risk |
| HVAC Temperature Histogram | `hvac-temp-histogram` | HVAC temperature distributions |
| Room Temperatures | `wago-rooms` | Room temperature trends |
| Room Temperature Histogram | `room-temp-histogram` | Room temperature distributions |
| Light Switch Status | `wago-lights` | Light on/off status by floor |
| Ruuvi Sensors | `ruuvi-sensors` | Ruuvi sensor data, air quality |
| Heat Pump | `thermia-heatpump` | Heat pump temps, COP, runtimes |
| Energy Cost | `energy-cost` | Electricity cost breakdown by component |
| Heating Control | `heating-control` | Heating optimizer status and decisions |

Dashboards are provisioned from JSON files in `grafana/provisioning/dashboards/`. Edit the JSON directly, then:

```bash
docker compose restart grafana
```

See [docs/grafana-dashboards.md](docs/grafana-dashboards.md) for conventions, panel details, and Grafana configuration.

## Kiosk Display

A wall-mounted display served by nginx with a carousel of widgets:

- **Weather** — current conditions and forecast from FMI open data
- **News** — latest headlines from YLE RSS feeds
- **Calendar** — family calendar (iCal) with three-column day view + agenda
- **Garbage collection** — upcoming pickups from PJHOY API

The kiosk includes face detection (face-api.js) to activate the display and supports portrait/landscape modes.

## Heating Optimizer

Price-aware heating control that adjusts the heat pump based on spot electricity prices:

- Pre-heats during cheap hours, reduces during expensive hours
- Configurable comfort range (20–23 °C)
- Reads current temperatures from InfluxDB, writes setpoints via MQTT

See [docs/heating-optimizer.md](docs/heating-optimizer.md) for the control logic, price thresholds, and configuration.

## Energy Calculations

The HVAC dashboard calculates heat recovery efficiency (sensible + enthalpy), recovered/coil/waste heat power, and freezing probability. The Energy Cost dashboard estimates electricity consumption from component status data and combines it with spot prices.

See [docs/heat-recovery-efficiency.md](docs/heat-recovery-efficiency.md) for formulas and Flux queries. See also [docs/heatpump-efficiency.md](docs/heatpump-efficiency.md) for heat pump COP and power estimation.

## MCP Server

An MCP server at `http://localhost:3001/sse` provides 15+ tools for querying building automation data from Claude Desktop.

See [docs/mcp-server.md](docs/mcp-server.md) for setup instructions, tool listing, and example queries.

## Data Import

```bash
# Full import (clears existing data)
source venv/bin/activate
python scripts/import_data.py

# Incremental import (appends new data)
python scripts/import_data.py --incremental
```

### Remote Sync (WAGO Controller)

```bash
# Start sync service (requires SSH key in ./ssh/wago_sync)
docker compose --profile sync up -d
```

## ThermIQ CLI Tool

A command-line tool for reading and writing Thermia heat pump registers via MQTT.

```bash
source venv/bin/activate

python scripts/thermiq_write.py --read        # Read current values
python scripts/thermiq_write.py --list        # List writable registers
python scripts/thermiq_write.py indoor_requested_t 22   # Write by name
python scripts/thermiq_write.py --dry-run hotwater_stop_t 55  # Preview
```

See [docs/thermiq_register_map.md](docs/thermiq_register_map.md) for the complete register map.

## Documentation

| Document | Content |
|----------|---------|
| [docs/architecture.md](docs/architecture.md) | System architecture, Docker services, environment variables |
| [docs/data-pipelines.md](docs/data-pipelines.md) | Data collection pipelines — CSV sync, MQTT, HTTP polling |
| [docs/influxdb-data-model.md](docs/influxdb-data-model.md) | Complete InfluxDB schema — all measurements, tags, fields |
| [docs/grafana-dashboards.md](docs/grafana-dashboards.md) | Dashboard inventory, conventions, panel details |
| [docs/heat-recovery-efficiency.md](docs/heat-recovery-efficiency.md) | Heat recovery formulas, enthalpy calculations, freezing risk |
| [docs/heatpump-efficiency.md](docs/heatpump-efficiency.md) | Heat pump COP and thermal power estimation |
| [docs/thermiq_register_map.md](docs/thermiq_register_map.md) | ThermIQ-ROOM2 register definitions |
| [docs/heating-optimizer.md](docs/heating-optimizer.md) | Price-aware heating control logic and configuration |
| [docs/mcp-server.md](docs/mcp-server.md) | MCP server tools, endpoints, Claude Desktop setup |
| [docs/development.md](docs/development.md) | Setup, SSH config, data management, troubleshooting |
| [docs/backup-recovery.md](docs/backup-recovery.md) | Backup schedule and recovery procedures |

## Stopping Services

```bash
# Stop all containers
docker compose down

# Stop and remove volumes (deletes all data!)
docker compose down -v
```

For troubleshooting, see [docs/development.md](docs/development.md#troubleshooting).

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
