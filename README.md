# WAGO Building Automation Data Explorer

A data visualization system for building automation measurement data from WAGO controllers, Ruuvi sensors, and a Thermia ground-source heat pump. Imports CSV data and MQTT sensor data into InfluxDB and provides interactive Grafana dashboards for exploring HVAC, room temperature, heat pump, and environmental data.

## Architecture

Seven Docker services: InfluxDB, Grafana, sync (WAGO CSV), ruuvi (MQTT), thermia (MQTT), lights (HTTP polling), and MCP server (Claude Desktop integration). Data flows from four sources into InfluxDB, visualized via Grafana dashboards.

| Service | Technology | Port | Purpose |
|---------|------------|------|---------|
| influxdb | InfluxDB 2.7 | 8086 | Time-series database |
| grafana | Grafana 12.3 | 3000 | Dashboard visualization |
| sync | Python/SCP | — | WAGO CSV sync + import |
| ruuvi | Python/MQTT | — | Ruuvi Bluetooth sensor data |
| thermia | Python/MQTT | — | ThermIQ heat pump data |
| lights | Python/HTTP | — | Light switch status polling |
| mcp | Python/SSE | 3001 | MCP server for Claude Desktop |

See [docs/architecture.md](docs/architecture.md) for full details on services, data pipelines, environment variables, and configuration.

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

Five InfluxDB measurements in bucket `building_automation`:

| Measurement | Source | Sampling | Content |
|-------------|--------|----------|---------|
| `hvac` | WAGO CSV | ~2 hours | HVAC temperatures, humidity, power, energy, voltages |
| `rooms` | WAGO CSV | ~1 hour | Room temperatures, PID controller outputs |
| `ruuvi` | Ruuvi MQTT | ~1 second | Temperature, humidity, pressure, air quality (CO2, PM, VOC) |
| `thermia` | ThermIQ MQTT | ~30 seconds | Heat pump temperatures, status, alarms, runtimes |
| `lights` | HTTP API | 5 minutes | Light switch on/off status |

See [docs/influxdb-data-model.md](docs/influxdb-data-model.md) for complete schema with all tags, fields, data types, units, and example Flux queries.

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

See [docs/development.md](docs/development.md) for SSH key setup, sync configuration, sensor name mapping, data management, and troubleshooting.

## Grafana Dashboards

Seven provisioned dashboards:

| Dashboard | UID | Content |
|-----------|-----|---------|
| Temperature Overview | `wago-overview` | Home dashboard with floorplan canvas |
| HVAC | `wago-hvac` | Ventilation temps, heat recovery, freezing risk |
| HVAC lämpötilojen jakauma | `hvac-temp-histogram` | HVAC temperature histograms |
| Huonelämpötilojen jakauma | `room-temp-histogram` | Room temperature histograms |
| Light Switch Status | `wago-lights` | Light on/off status by floor |
| Ruuvi Sensors | `ruuvi-sensors` | Ruuvi sensor data, air quality |
| Maalämpöpumppu | `thermia-heatpump` | Heat pump temps, COP, runtimes |

Dashboards are provisioned from JSON files — edit directly, then `docker compose restart grafana`. See [docs/grafana-dashboards.md](docs/grafana-dashboards.md) for conventions, panel details, and Grafana configuration.

## Energy Calculations

The HVAC dashboard calculates heat recovery efficiency (sensible + enthalpy), recovered/coil/waste heat power, and freezing probability.

See [docs/heat-recovery-efficiency.md](docs/heat-recovery-efficiency.md) for complete formulas, Flux queries, and derivations. See also [docs/heatpump-efficiency.md](docs/heatpump-efficiency.md) for heat pump COP and power estimation.

## MCP Server for Claude Desktop

An MCP server at `http://localhost:3001/sse` provides 15 tools for querying
building automation data from Claude Desktop.

See [docs/mcp-server.md](docs/mcp-server.md) for setup instructions, tool listing, and example queries.

## ThermIQ CLI Tool

A command-line tool for reading and writing Thermia heat pump registers via MQTT.

```bash
source venv/bin/activate

# Read current register values from the heat pump
python scripts/thermiq_write.py --read

# List all writable registers and parameters
python scripts/thermiq_write.py --list

# Write a setting (by name or register number)
python scripts/thermiq_write.py indoor_requested_t 22
python scripts/thermiq_write.py d50 22

# Preview without publishing
python scripts/thermiq_write.py --dry-run hotwater_stop_t 55
```

See [docs/thermiq_register_map.md](docs/thermiq_register_map.md) for the complete register map.

## Documentation

| Document | Content |
|----------|---------|
| [docs/architecture.md](docs/architecture.md) | System architecture, Docker services, environment variables |
| [docs/data-pipelines.md](docs/data-pipelines.md) | Data collection pipelines — CSV sync, MQTT, HTTP polling |
| [docs/influxdb-data-model.md](docs/influxdb-data-model.md) | Complete InfluxDB schema — all measurements, tags, fields, example queries |
| [docs/grafana-dashboards.md](docs/grafana-dashboards.md) | Dashboard inventory, conventions, panel details, Grafana configuration |
| [docs/heat-recovery-efficiency.md](docs/heat-recovery-efficiency.md) | Heat recovery formulas, enthalpy calculations, freezing risk, Flux queries |
| [docs/heatpump-efficiency.md](docs/heatpump-efficiency.md) | Heat pump COP and thermal power estimation |
| [docs/thermiq_register_map.md](docs/thermiq_register_map.md) | ThermIQ-ROOM2 register definitions |
| [docs/mcp-server.md](docs/mcp-server.md) | MCP server tools, endpoints, Claude Desktop setup |
| [docs/development.md](docs/development.md) | Setup, SSH config, data management, troubleshooting, file structure |

## Stopping Services

```bash
# Stop all containers
docker compose down

# Stop and remove volumes (deletes all data!)
docker compose down -v
```

For troubleshooting, see [docs/development.md](docs/development.md#troubleshooting).

## License

Internal use only.
