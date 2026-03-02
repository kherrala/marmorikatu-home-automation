# Floor Heating Temperature Optimizer

Optimizes heating costs for a Thermia Diplomat 8 ground-source heat pump with floor heating (290 m², 3 floors) by adjusting setpoint, EVU mode, and auxiliary heater availability based on electricity spot prices.

The building's concrete floor heating system has significant thermal mass, allowing the optimizer to pre-load heat during cheap price periods and coast during expensive periods without impacting comfort.

## Control Mechanisms

Four control levers via ThermIQ-ROOM2 MQTT interface:

| Control | Register | Hex | MQTT Topic | Payload | Effect |
|---------|----------|-----|------------|---------|--------|
| Indoor setpoint | d50 | `r32` | `write` | `{"r32": 23}` | Sets indoor target (10–30°C) |
| EVU mode | — | — | `set` | `{"EVU": 1}` | Firmware reduces target by `reduction_t` |
| Reduction amount | d59 | `r3b` | `write` | `{"r3b": 2}` | Configures EVU reduction (0–10°C) |
| Aux heater limit | d81 | `r51` | `write` | `{"r51": 0}` | Max electric boiler steps (0–3) |

**Key insight**: EVU mode does NOT block the compressor — it reduces the indoor target by `reduction_t` degrees while the compressor continues running. This makes it a clean way to lower demand during expensive hours.

**Aux heater values**: 0 = disabled, 1 = 3 kW only, 2 = 3 kW + 6 kW (9 kW total).

## Algorithm Overview

### Price Classification (Absolute)

Price thresholds are computed from the **last 30 days of historical prices** (P25 and P75 percentiles), not from the current forecast window. This avoids marking everything as "expensive" when prices are uniformly high, or everything as "cheap" when prices are uniformly low.

Each 15-minute slot in the 36-hour forecast is classified:

- **CHEAP**: price ≤ P25 of 30-day history
- **NORMAL**: P25 < price < P75
- **EXPENSIVE**: price ≥ P75

### Relative Fallback

When the entire forecast has **no EXPENSIVE slots** (all prices below the historical P75, e.g., during mild weather with abundant wind power), a relative fallback activates:

1. Compute the max–min price spread within the forecast window
2. If spread ≥ `MIN_RELATIVE_SPREAD` (default: 2 c/kWh), compute within-window P75
3. Mark slots above within-window P75 as **EXPENSIVE** — the relatively most expensive hours for EVU reduction and aux heater disabling

The normal pre-heat look-ahead then applies before these relatively-expensive blocks, creating a balanced approach: pre-heat during cheap hours before the reduction, then save during the relatively expensive hours. The relative fallback runs before the pre-heat and EVU cap steps so that all schedule adjustments apply correctly.

### Minimum Expensive Block Filter

After relative fallback classification, contiguous EXPENSIVE blocks shorter than `MIN_EXPENSIVE_SLOTS` (default: 2 = 30 min) are downgraded to NORMAL. This prevents EVU and aux heater flicker from isolated 15-minute price spikes that would cause frequent MQTT register writes for negligible savings. The filter runs before EVU cap and pre-heat look-ahead, so filtered-out blocks don't trigger any downstream actions.

### EVU Cap

Consecutive EXPENSIVE slots are capped at `MAX_EVU_HOURS` (default: 3 hours = 12 quarter-hour slots). Beyond this, slots are downgraded to NORMAL. This prevents extended EVU periods during sustained price peaks where comfort would suffer.

### Pre-Heat Look-Ahead

Before each EXPENSIVE block, the preceding `PRE_HEAT_HOURS` (default: 2 hours = 8 slots) of CHEAP slots are promoted to **PRE_HEAT**. Only CHEAP slots are promoted — NORMAL-priced slots are not cheap enough to justify extra heating. This charges the concrete thermal mass before the reduction period.

### Action Mapping

| Tier | d50 Setpoint | EVU | Aux Heaters | Effective Target |
|------|-------------|-----|-------------|-----------------|
| PRE_HEAT | COMFORT_MAX (23°C) | OFF | Default (2) | 23°C |
| CHEAP | COMFORT_DEFAULT (22°C) | OFF | Default (2) | 22°C |
| NORMAL | COMFORT_DEFAULT (22°C) | OFF | Default (2) | 22°C |
| EXPENSIVE (block ≥ 4h) | COMFORT_DEFAULT (22°C) | ON | **0 (disabled)** | 19°C |
| EXPENSIVE (block < 4h, outdoor > −10°C) | COMFORT_DEFAULT (22°C) | ON | Default (2) | 19°C |
| EXPENSIVE (block < 4h, outdoor ≤ −10°C) | COMFORT_DEFAULT (22°C) | ON | **0 (disabled)** | 19°C |

Aux heater disabling is gated by the contiguous EXPENSIVE block length: blocks shorter than `BOILER_DISABLE_MIN_HOURS` (default: 4h) keep aux heaters enabled when outdoor temperature is mild (> −10°C), since the compressor handles the load fine without aux heaters. In cold weather (≤ −10°C), short blocks still disable aux heaters (the cold-weather safety at −15°C overrides this if needed). This reduces unnecessary r51 register writes.

## Safety Protections

### Cold-Weather Constraints on Pre-Heating

| Outdoor Temp | Pre-Heat Limit |
|-------------|---------------|
| Above −10°C | COMFORT_MAX (23°C) — full pre-heat |
| −10°C to −20°C | COMFORT_DEFAULT + 1°C — limited pre-heat |
| Below −20°C | No pre-heat (COMFORT_DEFAULT only) |

### Aux Heater Block-Length Gate

Aux heaters are only disabled during EXPENSIVE blocks that are long enough to justify the register write:

| Block Length | Outdoor > −10°C | Outdoor −15°C to −10°C | Outdoor < −15°C |
|---|---|---|---|
| < 30 min | No action (filtered by min block filter) | No action | No action |
| 30 min – 4h | EVU only, **boiler stays enabled** | EVU + boiler disabled | EVU only, boiler stays enabled (cold safety) |
| ≥ 4h | EVU + boiler disabled | EVU + boiler disabled | EVU only, boiler stays enabled (cold safety) |

The `BOILER_DISABLE_MIN_HOURS` threshold (default: 4h) prevents unnecessary r51 register writes during moderate expensive blocks where the compressor handles the load alone.

### Aux Heater Cold-Weather Protection

Aux heaters are **never disabled** when:

- **Outdoor temp < BOILER_COLD_LIMIT (−15°C)**: The compressor may genuinely need aux heaters to maintain indoor temperature during extreme cold. The Thermia Diplomat 8 provides ~8 kW thermal; a 290 m² house at −25°C may need 12–15 kW.
- **Outdoor temp unavailable**: Fail-safe — if the Ruuvi outdoor sensor is unreachable, aux heaters stay enabled. Never make a safety-reducing decision based on missing data.

### Freeze Risk Assessment

Disabling aux heaters does **not** create a pipe freezing risk:

- The compressor continues running, providing ~8 kW thermal output
- 290 m² of concrete at 22°C stores enormous thermal energy — the house would take **days** to reach 0°C even with all heating stopped
- The optimizer caps EVU duration at 3 hours — insufficient time for meaningful cooling
- Aux heater disabling only occurs when outdoor > −15°C

### Fail-Safe Defaults

All controls are restored to safe defaults:

| Event | Setpoint | EVU | Aux Heaters |
|-------|----------|-----|-------------|
| Insufficient price data (<12 points) | COMFORT_DEFAULT | OFF | Default (2) |
| Service shutdown (SIGINT/SIGTERM) | COMFORT_DEFAULT | OFF | Default (2) |
| MQTT publish failure | No change (retains previous safe state) | | |

### Rate Limiting

Maximum one change per `MIN_HOLD_MINUTES` (default: 15 min, matching the 15-minute price granularity). Prevents rapid oscillation between states.

## Control Flow

Every `CHECK_INTERVAL` (5 minutes):

```
1. Fetch 36-hour price forecast from InfluxDB
2. Fetch 30-day historical prices → compute P25/P75 thresholds
3. Fetch outdoor temperature from Ruuvi sensor
4. Classify each 15-min slot → CHEAP / NORMAL / EXPENSIVE
5. Apply relative fallback (if no EXPENSIVE slots, use within-window P75)
6. Filter short EXPENSIVE blocks (< MIN_EXPENSIVE_SLOTS → NORMAL)
7. Apply EVU cap (max 3h consecutive EXPENSIVE)
8. Apply pre-heat look-ahead (boost CHEAP before EXPENSIVE)
9. Look up current 15-min slot → determine setpoint, EVU, boiler steps
10. Apply block-length gate on aux heaters (short blocks keep boiler enabled)
11. Apply cold-weather constraints (pre-heat limiting, aux heater protection)
12. Rate-limit check → skip publish if too recent
13. Publish changes via MQTT (only if values changed)
14. Log decision to InfluxDB
```

## InfluxDB Measurement

Measurement: `heating_optimizer`

| Field | Type | Description |
|-------|------|-------------|
| `setpoint` | int | Published d50 value (°C) |
| `evu_active` | int | 0 or 1 |
| `boiler_steps` | int | 0, 1, or 2 (elect_boiler_steps_max) |
| `effective_target` | int | setpoint − reduction_t if EVU on, else setpoint |
| `tier` | string | CHEAP, NORMAL, EXPENSIVE, or PRE_HEAT |
| `price` | float | Current electricity price (c/kWh, with VAT) |
| `outdoor_temp` | float | Outdoor temperature (°C) |

## Configuration

All via environment variables (with defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_BROKER` | freenas.kherrala.fi | MQTT broker hostname |
| `MQTT_PORT` | 1883 | MQTT broker port |
| `MQTT_WRITE_TOPIC` | ThermIQ/marmorikatu/write | Topic for register writes (d50, d59, d81) |
| `MQTT_SET_TOPIC` | ThermIQ/marmorikatu/set | Topic for EVU control |
| `COMFORT_MIN` | 20 | Hard floor temperature (°C) |
| `COMFORT_DEFAULT` | 22 | Normal setpoint (°C) |
| `COMFORT_MAX` | 23 | Pre-heat ceiling (°C) |
| `REDUCTION_T` | 3 | EVU reduction degrees, written to d59 at startup |
| `BOILER_STEPS_DEFAULT` | 2 | Normal aux heater steps (2 = 3+6 kW) |
| `BOILER_COLD_LIMIT` | −15 | Below this outdoor temp (°C), never disable aux heaters |
| `PRICE_PERCENTILE_CHEAP` | 25 | Historical percentile threshold for cheap tier |
| `PRICE_PERCENTILE_EXPENSIVE` | 75 | Historical percentile threshold for expensive tier |
| `HISTORY_DAYS` | 30 | Days of historical prices for percentile calculation |
| `PRE_HEAT_HOURS` | 2 | Hours to pre-heat before expensive blocks |
| `MAX_EVU_HOURS` | 3 | Max consecutive hours of EVU/aux heater restriction |
| `MIN_RELATIVE_SPREAD` | 2.0 | Min price spread (c/kWh) to activate relative fallback |
| `MIN_EXPENSIVE_SLOTS` | 2 | Min contiguous EXPENSIVE slots to keep (shorter → NORMAL) |
| `BOILER_DISABLE_MIN_HOURS` | 4 | Min EXPENSIVE block length (hours) to disable aux heaters |
| `CHECK_INTERVAL` | 300 | Main loop interval (seconds) |
| `MIN_HOLD_MINUTES` | 15 | Minimum time between MQTT changes |
| `DRY_RUN` | 0 | Set to 1 to log decisions without publishing MQTT |

## Interaction with Thermia Heat Pump Settings

The optimizer controls the Thermia via ThermIQ-ROOM2. Key heat pump settings that interact with the optimizer:

| Setting | Register | Recommended | Impact |
|---------|----------|-------------|--------|
| `room_factor` | d60 | 2 | How much INDR_T influences supply target. Lower = less oscillation |
| `integral_limit_a1` | d73 | 100 | Compressor start threshold (C×min). Lower = more responsive |
| `integral_limit_a2` | d79 | 80 (×10=800) | Aux heater start threshold. Higher = more buffer before expensive aux heating |
| `integral_hysteresis_a1` | d74 | 7 | Compressor stop hysteresis |
| `heatpump_runtime` | d70 | 30 min | Minimum compressor run time per cycle |
| `start_interval_min` | d76 | 20 min | Minimum gap between compressor starts |
| `heating_curve_slope` | d52 | 28 | Heating curve steepness — affects supply temp vs outdoor temp |

### Indoor Temperature

The indoor temperature is provided to the Thermia via INDR_T from the `indoor_temp_publisher` service, which reads the 15-minute mean from the Ruuvi Olohuone sensor in InfluxDB. The wired indoor sensor is not connected (permanent `indoor_sensor_alm`).

It is critical that INDR_T reflects the actual indoor temperature. A stale or incorrect value causes the firmware to over- or under-compensate via the room factor correction.

## Grafana Integration

### Building Overview (`building_overview.json`)

EVU stat panel reads the latest value from `heating_optimizer.evu_active`.

### Energy Cost (`energy_cost.json`)

"Lämmityksen optimointi" time series panel shows:
- **Electricity price** (bars, right axis) — from `electricity.price_with_tax`
- **Asetusarvo** (orange line) — optimizer setpoint from `heating_optimizer.setpoint`
- **Todellinen tavoite** (green line) — effective target from `heating_optimizer.effective_target`
- **EVU** (red fill) — EVU active periods from `heating_optimizer.evu_active`

## Docker Compose

```yaml
heating:
  build:
    context: .
    dockerfile: Dockerfile.heating
  container_name: wago-heating
  environment:
    - MQTT_BROKER=freenas.kherrala.fi
    - MQTT_PORT=1883
    - MQTT_WRITE_TOPIC=ThermIQ/marmorikatu/write
    - MQTT_SET_TOPIC=ThermIQ/marmorikatu/set
    - INFLUXDB_URL=http://influxdb:8086
    - INFLUXDB_TOKEN=wago-secret-token
    - INFLUXDB_ORG=wago
    - INFLUXDB_BUCKET=building_automation
    - COMFORT_MIN=20
    - COMFORT_DEFAULT=22
    - COMFORT_MAX=23
    - REDUCTION_T=2
    - BOILER_STEPS_DEFAULT=2
    - BOILER_COLD_LIMIT=-15
    - PRICE_PERCENTILE_CHEAP=25
    - PRICE_PERCENTILE_EXPENSIVE=75
    - PRE_HEAT_HOURS=2
    - CHECK_INTERVAL=300
    - MIN_HOLD_MINUTES=15
  depends_on:
    influxdb:
      condition: service_healthy
  restart: unless-stopped
```

## Verification

1. Deploy with `DRY_RUN=1` — logs all decisions without publishing MQTT
2. Check logs: `docker compose logs -f heating`
   - Price fetch working, tiers classified correctly
   - Pre-heat periods aligned before expensive blocks
   - Aux heater disabling only occurs when outdoor > −15°C
   - Setpoint, EVU, and boiler changes respect rate limits
3. Query InfluxDB `heating_optimizer` measurement for decision history
4. Remove DRY_RUN, monitor actual indoor temps vs setpoint vs price

## Replaces

This service replaces `evu_controller.py`, which sent `{"EVU":1}` during price peaks assuming it blocked the compressor. The optimizer improves on this by:
- Using EVU for its intended purpose (firmware-managed temperature reduction)
- Adding pre-heating during cheap hours (thermal mass charging)
- Disabling aux heaters during expensive hours (avoiding costly resistance heating)
- Using historical price percentiles for absolute thresholds (not relative to the forecast window)
- Applying relative fallback classification during extended low-price periods
- Implementing cold-weather safety protections for all controls
