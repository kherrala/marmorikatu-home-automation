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

Seven Docker services: influxdb, grafana, mcp, ruuvi, thermia, lights, sync. InfluxDB bucket `building_automation`, org `wago`, token `wago-secret-token`. Data flows: WAGO CSV → sync → InfluxDB, Ruuvi → MQTT → ruuvi → InfluxDB, ThermIQ → MQTT → thermia → InfluxDB, Lights API → lights → InfluxDB. Grafana reads from InfluxDB using Flux queries.

See [docs/architecture.md](docs/architecture.md) for full service details, ports, volumes, environment variables, and data collection pipelines.

## InfluxDB Data Model

Five measurements in bucket `building_automation`: `hvac` (WAGO HVAC, ~2h), `rooms` (WAGO room temps, ~1h), `ruuvi` (Bluetooth sensors, ~1s), `thermia` (heat pump, ~30s), `lights` (HTTP polling, 5min).

See [docs/influxdb-data-model.md](docs/influxdb-data-model.md) for complete schema with all tags, fields, types, units, and example queries.

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

The HVAC dashboard contains Flux queries for heat recovery efficiency (sensible + enthalpy), recovered/coil/waste heat power, and freezing probability. Freezing probability uses dew point proximity (`Jateilma - Kastepiste` margin) as primary risk factor (60% weight), with outdoor temperature (25%) and exhaust temperature (15%) as secondary factors. Key details: `Tuloilma_asetusarvo` is used as exhaust temp proxy for efficiency calculations, outdoor humidity falls back to 85% RH, data aligned to 2-hour boundaries via integer division on nanosecond timestamps.

The Energy Cost dashboard (`energy-cost`) estimates electricity consumption from component status data (heat pump compressor + aux heaters, lighting, sauna heater, HVAC fans) and combines with spot electricity prices to show cost breakdowns. Uses configurable dashboard variables for assumed wattages. Cost model: spot price + 0.49 c/kWh margin + 6.09 c/kWh transfer.

See [docs/heat-recovery-efficiency.md](docs/heat-recovery-efficiency.md) for complete formulas, Flux queries, and derivations. See also [docs/heatpump-efficiency.md](docs/heatpump-efficiency.md) for heat pump COP calculations and [docs/thermiq_register_map.md](docs/thermiq_register_map.md) for ThermIQ register definitions.
