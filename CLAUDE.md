# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Building automation data collection and visualization system. Collects data from a WAGO PLC controller (CSV over SSH), Ruuvi Bluetooth sensors (MQTT), Thermia heat pump (MQTT via ThermIQ-ROOM2), and light switch APIs (HTTP polling), stores in InfluxDB, and visualizes with Grafana dashboards. Includes an MCP server for Claude Desktop integration.

## Common Commands

```bash
# Start all services
docker compose up -d

# Start with WAGO sync service (requires SSH key in ./ssh/wago_sync)
docker compose --profile sync up -d

# Restart Grafana after dashboard JSON changes
docker compose restart grafana

# View service logs
docker compose logs -f <service>   # influxdb | grafana | mcp | ruuvi | thermia | lights | sync

# Manual CSV data import
source venv/bin/activate
python scripts/import_data.py              # Full import (clears existing data)
python scripts/import_data.py --incremental # Append new data only
```

## Architecture

Seven Docker services orchestrated via `docker-compose.yml`:

- **influxdb** (InfluxDB 2.7, port 8086) — Time-series database. Bucket: `building_automation`, org: `wago`, token: `wago-secret-token`
- **grafana** (Grafana 10.2, port 3000) — Dashboard visualization with provisioned JSON dashboards
- **mcp** (Python 3.12, port 3001) — MCP server exposing InfluxDB data to Claude Desktop via SSE at `/sse`
- **ruuvi** (Python 3.12) — MQTT subscriber for Ruuvi sensor data (~1s sampling)
- **thermia** (Python 3.12) — MQTT subscriber for Thermia heat pump data via ThermIQ-ROOM2
- **lights** (Python 3.12) — HTTP poller for light switch status (5-min intervals)
- **sync** (Python 3.11, profile: `sync`) — SSH/SCP sync from WAGO controller + incremental CSV import (5-min intervals)

Data flows: WAGO CSV → sync → InfluxDB, Ruuvi → MQTT → ruuvi → InfluxDB, ThermIQ → MQTT → thermia → InfluxDB, Lights API → lights → InfluxDB. Grafana reads from InfluxDB using Flux queries.

## InfluxDB Data Model

All data in bucket `building_automation` with five measurements:

| Measurement | Source | Tags | Sampling | Content |
|-------------|--------|------|----------|---------|
| `hvac` | WAGO CSV (`logfile_dp_*.csv`) | `sensor_group` (ivk_temp, humidity, power, energy, voltage, actuator) | ~2 hours | HVAC temps, humidity, power, energy |
| `rooms` | WAGO CSV (`Temperatures*.csv`) | `room_type` (bedroom, common, basement, pid, energy), `floor` | ~1 hour | Room temps, PID controller outputs |
| `ruuvi` | MQTT | `sensor_id`, `sensor_name`, `data_format`, `sensor_type` | ~1 second | Temp, humidity, pressure, CO2, PM, VOC |
| `thermia` | MQTT (ThermIQ-ROOM2) | `data_type` (temperature, status, alarm, performance, runtime, setting) | ~1 minute | Heat pump temps, component status, alarms, runtimes |
| `lights` | HTTP API | `floor`, `light_name`, `dual_function` | 5 minutes | Switch on/off status |

## Key Files

- **`scripts/import_data.py`** — CSV parser handling Latin-1 encoding, sensor validation, batch writes (5000 points). Maps CSV columns to measurements with proper tags.
- **`scripts/mcp_server.py`** — MCP server with 15 tools (query_data, get_latest, get_statistics, get_heat_recovery_efficiency, get_freezing_probability, get_thermia_status, get_thermia_temperatures, etc.). SSE transport via uvicorn/starlette.
- **`scripts/ruuvi_mqtt_subscriber.py`** — Handles Ruuvi data formats 5 (basic) and 225 (air quality). Pressure unit conversion (Pa↔hPa).
- **`scripts/thermia_mqtt_subscriber.py`** — Subscribes to ThermIQ-ROOM2 MQTT topic, parses hex/decimal register formats, extracts bitfields, writes grouped InfluxDB points.
- **`Dockerfile.thermia`** — Container image for thermia MQTT subscriber service.
- **`scripts/lights_poller.py`** — Polls light switch API, classifies by floor, handles dual-function switches.
- **`grafana/provisioning/dashboards/*.json`** — Grafana dashboard definitions. Each dashboard has a stable UID (e.g., `wago-overview`, `wago-hvac`, `wago-lights`) used in cross-dashboard navigation links.

## Grafana Dashboard Conventions

- Dashboards are provisioned as JSON files — edit JSON directly, then `docker compose restart grafana`
- Each dashboard has navigation links to related dashboards using `/d/<uid>/<slug>` URLs with `includeVars` and `keepTime`
- Flux queries use `v.timeRangeStart`, `v.timeRangeStop`, `v.windowPeriod` for Grafana time range integration
- Field names use ASCII (e.g., `Ulkolampotila`) with display name overrides for Finnish characters (e.g., `Ulkolämpötila`)
- Dashboard tags follow pattern: `building-automation` + topic tag (`wago`, `hvac`, `ruuvi`, `lights`, `thermia`)

## Energy Calculations

The HVAC dashboard contains complex Flux queries for heat recovery efficiency:
- **Sensible heat efficiency**: `η = (T_supply - T_outdoor) / (T_exhaust - T_outdoor) × 100%`
- **Enthalpy efficiency**: Accounts for humidity using saturation vapor pressure (Tetens formula), humidity ratios, and moist air enthalpy
- Data from different sampling rates is aligned to 2-hour boundaries using integer division on nanosecond timestamps
- Outdoor humidity falls back to 85% RH when Ruuvi data is unavailable
- `Tuloilma_asetusarvo` (supply setpoint) is used as a proxy for exhaust air temperature (no dedicated sensor)
- **Freezing probability**: Composite risk score (0-95%) for heat exchanger icing
  - Temperature risk (50%): linear -5°C to -25°C
  - Humidity risk (35%): linear 15% to 30% RH (exhaust side)
  - Exhaust temp risk (15%): linear 5°C to 0°C
  - Exhaust air below 0°C forces 95% probability
  - Available as Grafana gauge ("LTO jäätymisriski") and MCP tool (`get_freezing_probability`)
