# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Building automation data collection and visualization system. Collects data from a WAGO PLC controller (MQTT, see `../marmorikatu-plc/MQTT.md`), Ruuvi Bluetooth sensors (MQTT), and a Thermia heat pump (MQTT via ThermIQ-ROOM2), stores in InfluxDB, and visualizes with Grafana dashboards. Includes an MCP server for Claude Desktop integration.

The legacy CSV-over-SFTP sync (`sync` service) and HTTP-polled lights API (`lights` service) have been superseded by the unified `plc` MQTT subscriber. Both legacy services are commented out in `docker-compose.yml` for emergency rollback.

## Common Commands

```bash
# Start all services (requires SSH key in ./ssh/wago_sync for sync service)
docker compose up -d

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

Active Docker services: influxdb, grafana, mcp, ruuvi, thermia, plc, plus support services (electricity, heating, indoor, weather, news, calendar, kiosk, claude-bridge, remind, playwright-mcp, backup). InfluxDB bucket `building_automation`, org `wago`, token `wago-secret-token`. Data flows: WAGO PLC → MQTT (10 retained `marmorikatu/...` topics) → plc → InfluxDB, Ruuvi → MQTT → ruuvi → InfluxDB, ThermIQ → MQTT → thermia → InfluxDB. Grafana reads from InfluxDB using Flux queries.

See [docs/architecture.md](docs/architecture.md) for full service details, ports, volumes, environment variables, and data collection pipelines.

## InfluxDB Data Model

Seven measurements in bucket `building_automation`: `hvac` (WAGO HVAC + OR-WE-517 energy meters, ~13s), `rooms` (WAGO room temps + underfloor-heating valves, ~13s), `ruuvi` (Bluetooth sensors, ~1s), `thermia` (heat pump, ~30s), `lights` (WAGO controls + outlets, ~13s), `switches` (wall-switch press states, ~13s), `plc_publisher` (heartbeat counters, ~13s).

See [docs/influxdb-data-model.md](docs/influxdb-data-model.md) for complete schema with all tags, fields, types, units, and example queries.

## Kiosk Avatar

The kiosk is a wall-mounted iPad running a TypeScript + Vite + RxJS frontend served by nginx. It displays rotating Grafana dashboards with face-detection-triggered AI voice assistant (Ollama qwen2.5:14b via Claude Bridge + MCP tools). Memory persistence via remind MCP server.

- **Source**: `kiosk/src/` — TypeScript modules, built with `cd kiosk && npm run build`
- **State management**: RxJS `BehaviorSubject` + `scan` reducer (`state/machine.ts`)
- **AI backend**: `scripts/claude_bridge.py` — Ollama primary, Claude fallback, MCP tool routing
- **Memory**: remind MCP server (`Dockerfile.remind`) — stores user preferences between sessions
- **TTS**: Server-side Piper Finnish TTS with browser speechSynthesis fallback

See [docs/kiosk-state-machine.md](docs/kiosk-state-machine.md) for complete business logic, state transitions, acceptance criteria, and timer reference.

## Key Files

- **`scripts/plc_mqtt_subscriber.py`** — Subscribes to the ten retained `marmorikatu/...` MQTT topics published by the WAGO PLC and writes to existing measurements (`rooms`, `hvac`, `lights`) plus new ones (`switches`, `plc_publisher`). Topic schema documented at `../marmorikatu-plc/MQTT.md`.
- **`scripts/mcp_server.py`** — MCP server with 15 tools (query_data, get_latest, get_statistics, get_heat_recovery_efficiency, get_freezing_probability, get_thermia_status, get_thermia_temperatures, etc.). SSE transport via uvicorn/starlette.
- **`scripts/ruuvi_mqtt_subscriber.py`** — Handles Ruuvi data formats 5 (basic) and 225 (air quality). Pressure unit conversion (Pa↔hPa).
- **`scripts/thermia_mqtt_subscriber.py`** — Subscribes to ThermIQ-ROOM2 MQTT topic, parses hex/decimal register formats, extracts bitfields, writes grouped InfluxDB points.
- **`scripts/import_data.py`** *(legacy)* — CSV parser, no longer in the active pipeline. Kept for historical CSV re-import.
- **`scripts/lights_poller.py`** *(legacy)* — Old HTTP poller, no longer in the active pipeline.
- **`grafana/provisioning/dashboards/*.json`** — Grafana dashboard definitions. Each dashboard has a stable UID (e.g., `wago-overview`, `wago-hvac`, `wago-lights`, `energy-meters`) used in cross-dashboard navigation links.

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
