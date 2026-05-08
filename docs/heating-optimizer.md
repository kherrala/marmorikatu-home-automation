# Spot-Price Tier Classifier (analytics-only)

Classifies upcoming electricity spot-price slots into CHEAP / NORMAL / EXPENSIVE / PRE_HEAT and writes the result to InfluxDB. The actual heat-pump steering for the Thermia Diplomat 8 (floor heating, 290 m², 3 floors) is done by `indoor_temp_publisher.py`, which biases the `INDR_T` sensor reading sent to the heat pump based on the live spot price (continuously, with seasonal percentile thresholds and a per-room PID demand counter-bias).

The concrete floor's thermal mass lets us pre-load heat during cheap periods and coast during expensive ones without impacting comfort.

## Architecture

```
                ┌──────────────────────────────────┐
                │  scripts/heating_optimizer.py    │
                │  - classify each 15-min slot     │
                │  - write tier+price+outdoor      │
                │    to InfluxDB (analytics only)  │
                └────────────────┬─────────────────┘
                                 │ tier (read by dashboards/MCP)
                                 ▼
                ┌──────────────────────────────────┐
                │  scripts/indoor_temp_publisher   │
                │  - median of 10 indoor sensors   │
                │  - price→bias (seasonal P25/P85, │
                │    4-season scale)               │
                │  - per-room PID counter-bias     │
                │  - publish INDR_T to ThermIQ     │
                └────────────────┬─────────────────┘
                                 │ INDR_T = median + bias
                                 ▼
                          [ Thermia heat pump ]
                          (no flash writes,
                           no MQTT register cmds)
```

## Control Mechanisms

The optimizer **does not command the heat pump**. It only writes analytics. All command paths were retired:

| Channel | Old role | Now |
|---|---|---|
| `ThermIQ/.../set {"EVU": 1}` | Hard tier-driven kill switch | Not written. EVU on this unit only triggers a `reduction_t`-based target shift, not a compressor block, so it added little value over the publisher's INDR_T bias. |
| `r32` (d50, indoor_requested_t) | Setpoint | Not written — would wear flash. Whatever was last set manually persists. |
| `r3b` (d59, reduction_t) | EVU-mode target reduction | Not written. |
| `r51` (d81, elect_boiler_steps_max) | Max electric boiler steps | Not written. The Thermia firmware decides aux activation from its own integrator vs A1/A2 limits. |
| `ThermIQ/.../set {"INDR_T": 22.0}` | Indoor-air sensor reading | **This is the active control mechanism**, owned by `indoor_temp_publisher`. Runtime sensor input — no flash wear. |

Aux heaters were previously computed and toggled by tier; now they're left to the firmware's own logic since the publisher's INDR_T bias drives the same effective behaviour through room-factor compensation.

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

The following values are **computed and logged** to InfluxDB
(`heating_optimizer` measurement) for dashboard analytics. Only **EVU** is
actually commanded to the heat pump. Setpoint / aux-heater columns are
kept in the table because the publisher reads `tier` to choose the
INDR_T bias that achieves the same effect.

| Tier | Logged setpoint | EVU (wired) | Logged aux | Logged effective | Publisher INDR_T bias |
|---|---|---|---|---|---|
| PRE_HEAT | 23 °C | OFF | 2 | 23 °C | **−0.5 °C** (publish lower → produce more) |
| CHEAP | 22 °C | OFF | 2 | 22 °C | **−0.5 °C** |
| NORMAL | 22 °C | OFF | 2 | 22 °C | 0 |
| EXPENSIVE (block ≥ 4h) | 22 °C | ON | 0 | 19 °C | **+2.0 °C** (publish higher → produce less) |
| EXPENSIVE (block < 4h, outdoor > −10 °C) | 22 °C | ON | 2 | 19 °C | **+2.0 °C** |
| EXPENSIVE (block < 4h, outdoor ≤ −10 °C) | 22 °C | ON | 0 | 19 °C | **+2.0 °C** |

The "logged setpoint" and "logged aux" columns are now historical/
analytics-only. The actual heat-pump-side levers are: EVU (wired) and
the published INDR_T value, which is `median(indoor sensors) + tier_bias`.

Aux heater logic in the optimizer's classifier still gates by block
length and outdoor temperature for analytics — see the
[Aux Heater Block-Length Gate](#aux-heater-block-length-gate) section below — but no register write happens.

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

(Historical: this gate originally also prevented unnecessary `r51` register writes during moderate expensive blocks. With register writes retired the gate now only affects the logged `boiler_steps` analytic; the dashboard still shows it.)

## Tier-Aware INDR_T Bias

`scripts/indoor_temp_publisher.py` performs the heavy lifting of "tell the heat pump to make more / less heat." On each tick (default 5 min) it:

1. Computes the **median** of 10 indoor temperature sensors over the last 15 min:
   - 3 Ruuvis: Olohuone, Keittiö, Takka
   - 7 WAGO room fields: 4 bedrooms (`MH_*`) + 3 common areas (`Ylakerran_aula`, `Keittio`, `Eteinen`)
   - Sauna is **hard-blacklisted in code** regardless of env-var override.
2. Reads the latest `tier` from the `heating_optimizer` measurement.
3. Applies a per-tier bias (env vars):
   - `TIER_BIAS_CHEAP_C` (default −0.5)
   - `TIER_BIAS_PRE_HEAT_C` (default −0.5)
   - `TIER_BIAS_NORMAL_C` (default 0)
   - `TIER_BIAS_EXPENSIVE_C` (default +2.0)
4. Publishes `INDR_T = median + bias` to `ThermIQ/marmorikatu/set`.

The Thermia treats `INDR_T` as a sensor input and uses it for room-factor compensation. With `room_factor=2` and `setpoint=22 °C`, an `INDR_T` of `24 °C` (median 22 + EXPENSIVE bias 2) computes a supply target of `curve(outdoor) − 4 °C` — strongly suppressing heating without writing any persistent register. When tier transitions back to NORMAL, the bias drops to 0 and the heat pump resumes its natural curve-driven response.

See the **"INDR_T sent vs sensor median (bias visualisation)"** panel on the heating-control dashboard for live visibility into the bias being applied — yellow line is what's sent, blue dashed is the raw median.

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
9. Look up current 15-min slot → determine tier, intended setpoint, EVU, boiler steps
10. Apply block-length gate on aux heaters (only affects logged value now)
11. Apply cold-weather constraints (pre-heat limiting, aux heater protection)
12. Rate-limit check → skip log if too recent
13. **Toggle EVU wire** if changed (the only command sent to the heat pump)
14. Log decision to InfluxDB (`heating_optimizer` measurement)
15. Publisher (separate service) reads tier and applies INDR_T bias
```

## InfluxDB Measurement

Measurement: `heating_optimizer` (analytics-only — three fields, no commanded values)

| Field | Type | Description |
|-------|------|-------------|
| `tier` | string | `CHEAP`, `NORMAL`, `EXPENSIVE`, or `PRE_HEAT` — read by dashboards/MCP |
| `price` | float | Current slot's electricity spot price (c/kWh, with tax) |
| `outdoor_temp` | float | Outdoor temperature (°C) at classification time |

The retired fields `setpoint`, `evu_active`, `boiler_steps`, and `effective_target` are no longer written. Historical points containing them remain in the bucket but get no new updates.

## Configuration

All via environment variables (with defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `PRICE_PERCENTILE_CHEAP` | 25 | Historical percentile threshold for cheap tier |
| `PRICE_PERCENTILE_EXPENSIVE` | 75 | Historical percentile threshold for expensive tier |
| `MIN_CHEAP_THRESHOLD` | 3.0 | Lower clamp on cheap threshold (c/kWh) |
| `MAX_CHEAP_THRESHOLD` | 6.0 | Upper clamp on cheap threshold (c/kWh) |
| `MIN_EXPENSIVE_THRESHOLD` | 5.0 | Lower clamp on expensive threshold (c/kWh) |
| `MAX_EXPENSIVE_THRESHOLD` | 15.0 | Upper clamp on expensive threshold (c/kWh) |
| `PRE_HEAT_HOURS` | 2 | Hours of PRE_HEAT lookahead before EXPENSIVE blocks |
| `MAX_EXPENSIVE_HOURS` | 3 | Max consecutive EXPENSIVE slots — tail reclassified to NORMAL |
| `MIN_EXPENSIVE_SLOTS` | 2 | Min contiguous EXPENSIVE slots to keep (shorter → NORMAL) |
| `MIN_RELATIVE_SPREAD` | 2.0 | Min price spread (c/kWh) to activate relative fallback |

The classifier wakes on each 15-min price-slot boundary (no `CHECK_INTERVAL` env var anymore). The retired controls — MQTT topics, COMFORT_*, REDUCTION_T, BOILER_*, MIN_HOLD_MINUTES, DRY_RUN — are gone since nothing is being commanded.

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

The indoor temperature is provided to the Thermia via `INDR_T` from the
`indoor_temp_publisher` service. As of the flash-wear-mitigation refactor
(see [Tier-Aware INDR_T Bias](#tier-aware-indr_t-bias) above), it
publishes the **median of 10 indoor sensors** with a tier-aware bias
applied. The wired indoor sensor is not connected (permanent
`indoor_sensor_alm`), so `INDR_T` is the only indoor reference the heat
pump sees.

It is critical that the publisher's median + bias reflects what the
home actually wants — a stale, biased-the-wrong-way, or single-sensor
reading causes the firmware's room-factor compensation to over- or
under-produce heat.

## Grafana Integration

### Building Overview (`building_overview.json`)

EVU stat panel reads the latest value from `heating_optimizer.evu_active` (still meaningful — EVU is the one signal still actively commanded).

### Heating Control (`heating_control.json`)

The dashboard now has an **"INDR_T sent vs sensor median (bias visualisation)"** panel showing:

- Yellow line — `thermia.indoor_temp` (what we're sending the heat pump = median + bias)
- Blue dashed — same-formula median across the 10 indoor sensors **without** the bias

The gap between the two visualises the live tier bias. Other panels
("Asetusarvo", "Todellinen tavoite", "Lammitysteho") still work but
are now logged-intended values rather than commanded register values.

### Energy Cost (`energy_cost.json`)

"Lämmityksen optimointi" time series panel shows:
- **Electricity price** (bars, right axis) — from `electricity.price_with_tax`
- **Asetusarvo** (orange line) — optimizer's intended setpoint (analytic only)
- **Todellinen tavoite** (green line) — effective target (analytic only)
- **EVU** (red fill) — EVU active periods from `heating_optimizer.evu_active` (still real)

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
