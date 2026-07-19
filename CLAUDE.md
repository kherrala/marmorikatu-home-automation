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

Core measurements in bucket `building_automation`: `hvac` (WAGO HVAC + OR-WE-517 energy meters, ~13s), `rooms` (WAGO room temps + underfloor-heating valves, ~13s), `ruuvi` (Bluetooth sensors, ~1s), `thermia` (heat pump, ~30s), `lights` (WAGO controls + outlets, ~13s), `switches` (wall-switch press states, ~13s), `plc_publisher` (heartbeat counters, ~13s). Plus derived/support measurements: `lights_optimizer` (decision log), `light_command` (per-command provenance breadcrumb: tags `light_id`/`source`, field `is_on`), `light_override` (Unifi porch holds), `ble` (BLE-identity sightings: tag `mac`/`device_class`, field `rssi`), `heating_optimizer` (spot-price tier), and — when the Presence Service lands — `presence` (per-room `{occupied, confidence, source}`).

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

The HVAC dashboard contains Flux queries for heat recovery efficiency (sensible + enthalpy), recovered/coil/waste heat power, and freezing probability. Sensible LTO efficiency uses `(Tuloilma_ennen_lammitysta − Ulkolampotila) / (Poistoilma − Ulkolampotila)` — `Poistoilma` is real extract air, written by the MQTT publisher; the legacy `Tuloilma_asetusarvo` proxy is retired. Freezing probability uses dew point proximity (`Jateilma − Kastepiste` margin) as primary risk factor (60% weight, gated by `cold_factor = clamp((5 − Jateilma)/5, 0..1)` so it only fires when exhaust is approaching freezing), with outdoor temperature (25%) and exhaust temperature (15%) as secondary factors. The dashboard also overlays the Casa MVHR's own `Alarm_freezing_danger` flag as the authoritative signal. Outdoor humidity falls back to 85% RH; data aligned via `aggregateWindow(every: v.windowPeriod, …)`.

The Energy Cost dashboard (`energy-cost`) estimates electricity consumption from component status data (heat pump compressor + aux heaters, lighting, sauna heater, HVAC fans) and combines with spot electricity prices to show cost breakdowns. Uses configurable dashboard variables for assumed wattages. Sauna detection is heat-direction-aware (ON when temp ≥ 40°C and not falling more than 1.5°C in 30 min) — replaces the previous "temp > 30°C" heuristic which counted hours of cooldown as active heater time. Cost model: spot price + 0.49 c/kWh margin + 6.09 c/kWh transfer.

See [docs/heat-recovery-efficiency.md](docs/heat-recovery-efficiency.md) for complete formulas, Flux queries, and derivations. See also [docs/heatpump-efficiency.md](docs/heatpump-efficiency.md) for heat pump COP calculations and [docs/thermiq_register_map.md](docs/thermiq_register_map.md) for ThermIQ register definitions.

## Heating Optimizer + INDR_T Publisher

`scripts/heating_optimizer.py` classifies electricity spot prices into tiers (CHEAP / NORMAL / EXPENSIVE / PRE_HEAT) and toggles the heat pump's wired EVU input accordingly. It **does not** write the Thermia's persistent registers (setpoint, reduction_t, boiler_steps) — those would wear flash. Instead, `scripts/indoor_temp_publisher.py` reads the latest tier from InfluxDB, computes the **median** of 10 indoor sensors (3 Ruuvis + 7 WAGO room fields, sauna hard-blacklisted, basement excluded), adds a tier-aware bias (default −0.5 / 0 / +2 °C for CHEAP/NORMAL/EXPENSIVE), and publishes the result as `INDR_T` to ThermIQ. The Thermia uses `INDR_T` for room-factor compensation, so a positive bias suppresses heating and a negative bias amplifies it — all via a runtime sensor input, no flash-write. EVU remains a wired hardware signal so it's also flash-free.

See [docs/heating-optimizer.md](docs/heating-optimizer.md) for the tier algorithm, action mapping table, INDR_T bias details, and analytics measurement schema.

## Lights Optimizer

The `lights-optimizer` service is **comfort-first and provenance-aware**: it never fights an active user and only turns lights off on high-confidence culls (daylight waste on window/outdoor/decorative lights, whole-house-away, deep-night overnight, and duration caps on transient rooms), while offering comfort auto-on in the dark for the living core. Every light maps to a behaviour category (living/window/accent/circulation/utility/toilet/bedroom/office/theater/outdoor) plus special blocks: front porch (idx 47), sauna laude LED (idx 4), post-sauna cooldown (idx 1/38/39). Command **provenance** is carried on a side-channel `marmorikatu/light/<idx>/command` breadcrumb (the PLC `/set` accepts only bare `true`/`false` — enriching it was tested and rejected), recorded as the `light_command` measurement; a state change with no matching breadcrumb is inferred to be a physical wall press. Occupancy comes from a normalized `presence` measurement (the separate Presence Service) with kitchen-CO₂ + BLE-identity (`ble` measurement) + astronomical-darkness interim fallbacks. Decisions log to InfluxDB measurement `lights_optimizer` (adds a `manual_locked` field).

See [docs/lights-optimizer.md](docs/lights-optimizer.md) for the category table, provenance/manual-lock model, presence contract, special-case blocks, reason vocabulary, and tunable env vars.

## Kiosk Announcer

The `announcer` service polls InfluxDB for state changes (HVAC freezing alarms, sauna on/off, spot-price tier transitions, lights_optimizer decisions, CO₂/PM2.5 air-quality class transitions, raw light on/off) and pushes Finnish-language announcements to `claude-bridge` over a `/announcements/push` ingress. Connected kiosks subscribe via `/announcements/stream` (SSE) and speak the events through the existing Piper TTS path **without** requiring a face-detection greeting. Verbosity is controlled by `ANNOUNCE_VERBOSITY` (0=critical only, 1=normal, 2=verbose, 3=every individual light) — defaults to 3 for the initial rollout. The kiosk enforces quiet hours (default 22:00–07:00 local) and replays the top 3 events from an overnight digest after the next idle window in the morning.

See [docs/announcer.md](docs/announcer.md) for event classes, priority tiers, env vars, tuning guidance, and operational commands.

## Unifi Protect Webhook

The `unifi-webhook` service listens on `:5645` for HTTP POSTs from the Unifi Protect alarm manager and dispatches each event through a JSON rules table (`config/unifi_webhook_rules.json`, mounted read-only into the container and hot-reloaded on file mtime change). A rule's `match` block is AND-ed against the inbound payload's `alarm_name`, `device`, `trigger_key`, and `event_id` (missing keys = wildcard). Each rule fires a list of actions: `announce` (pushes to claude-bridge's `/announcements/push`, optionally including the camera thumbnail as a data URI plus an `image_duration_s`); `mqtt_publish` (raw topic/payload — fire-and-forget); or `mqtt_pulse` (publish ON now, schedule OFF after `duration_s` — checks InfluxDB for current `lights.is_on` and skips if the light is already on within the last 60s, so it doesn't fight the `lights-optimizer` porch schedule or a manual switch press; a fresh event extends the OFF timer rather than cutting the on-window short). Per-rule `cooldown_s` suppresses burst duplicates. Auth is open inside the LAN by default; set `UNIFI_WEBHOOK_TOKEN` to require an `X-Webhook-Token` header or `?token=` query string.

The default ruleset pairs front-door person detection with both an announcement (with 5-min image overlay) AND an `mqtt_pulse` on the front porch (idx 47) for 5 min — `lights-optimizer` keeps the porch on from sunset to `PORCH_OFF_HOUR` (22:00 in compose), and the webhook covers the after-hours window with detection-driven pulses.

When an announcement event carries an `image`, the kiosk shows a top-right overlay (separate channel from the conversation `screenshot-bubble`) for the requested duration. The bridge broadcasts the image once over SSE but strips it from the history ring buffer so large base64 payloads don't bloat replay/history.
