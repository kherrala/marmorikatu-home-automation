# Floor Heating Temperature Optimizer

Optimizes heating costs for a Thermia ground-source heat pump with floor heating (290 m²) by adjusting setpoint and EVU mode based on electricity spot prices.

## Control Mechanisms

Two control levers via ThermIQ MQTT interface:

| Control | Register | MQTT Topic | Effect |
|---------|----------|------------|--------|
| **d50** `indoor_requested_t` | hex `r32` | `ThermIQ/marmorikatu/write` | Sets indoor target setpoint (10–30°C) |
| **EVU mode** | parameter | `ThermIQ/marmorikatu/set` | Reduces target by `reduction_t` degrees |
| **d59** `reduction_t` | hex `r3b` | `ThermIQ/marmorikatu/write` | Configures EVU reduction amount (0–10°C) |

**Key insight**: EVU mode does NOT block the compressor — it reduces the indoor target by `reduction_t` degrees while the compressor continues running. This makes it a clean way to lower demand during expensive hours.

## Algorithm

### Price Classification

All available forecast prices (up to ~36 hours, 15-minute resolution from spot-hinta.fi via the electricity price poller) are classified using percentiles:

- **CHEAP**: price ≤ P25 of forecast window
- **NORMAL**: P25 < price < P75
- **EXPENSIVE**: price ≥ P75

### Setpoint Schedule

| Tier | d50 Setpoint | EVU | Effective Target |
|------|-------------|-----|-----------------|
| CHEAP | COMFORT_MAX (23°C) | OFF | 23°C |
| NORMAL | COMFORT_DEFAULT (22°C) | OFF | 22°C |
| EXPENSIVE | COMFORT_DEFAULT (22°C) | ON | 22 - reduction_t = 20°C |

### Pre-Heat Look-Ahead

Before each EXPENSIVE block, the preceding `PRE_HEAT_HOURS` (default: 2h = 8 quarter-hour slots) are boosted to COMFORT_MAX, even if NORMAL-priced. This charges the concrete thermal mass before the reduction period.

### Cold Weather Constraints

| Outdoor Temp | Pre-Heat Limit |
|-------------|---------------|
| Above -10°C | COMFORT_MAX (23°C) |
| -20°C to -10°C | COMFORT_DEFAULT + 1 (23°C capped) |
| Below -20°C | No pre-heat (COMFORT_DEFAULT only) |

### Safety

- **Rate limit**: Max one change per MIN_HOLD_MINUTES (15 min, matching price granularity)
- **Fallback**: If price data has fewer than 12 points (3 hours), use COMFORT_DEFAULT with EVU off
- **Shutdown**: Restores COMFORT_DEFAULT + EVU off on SIGINT/SIGTERM

## Control Flow

Every CHECK_INTERVAL (5 min):

1. Fetch price forecast from InfluxDB (now → +36h)
2. Fetch outdoor temperature from Ruuvi sensor via InfluxDB
3. Classify 15-min blocks into CHEAP / NORMAL / EXPENSIVE
4. Apply pre-heat look-ahead before expensive blocks
5. Look up current block's action
6. Apply cold-weather constraints
7. Publish setpoint change to MQTT write topic (if changed)
8. Publish EVU state to MQTT set topic (if changed)
9. Log decision to InfluxDB `heating_optimizer` measurement

## InfluxDB Measurement

Measurement: `heating_optimizer`

| Field | Type | Description |
|-------|------|-------------|
| `setpoint` | int | Published d50 value (°C) |
| `evu_active` | int | 0 or 1 |
| `effective_target` | int | setpoint - reduction_t if EVU on, else setpoint |
| `tier` | string | CHEAP, NORMAL, or EXPENSIVE |
| `price` | float | Current electricity price (c/kWh) |
| `outdoor_temp` | float | Outdoor temperature (°C) |

## Configuration

All via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `COMFORT_MIN` | 20 | Hard floor temperature (°C) |
| `COMFORT_DEFAULT` | 22 | Normal setpoint (°C) |
| `COMFORT_MAX` | 23 | Pre-heat ceiling (°C) |
| `REDUCTION_T` | 2 | EVU reduction degrees, written to d59 at startup |
| `PRICE_PERCENTILE_CHEAP` | 25 | Percentile threshold for cheap tier |
| `PRICE_PERCENTILE_EXPENSIVE` | 75 | Percentile threshold for expensive tier |
| `PRE_HEAT_HOURS` | 2 | Hours to pre-heat before expensive blocks |
| `CHECK_INTERVAL` | 300 | Main loop interval (seconds) |
| `MIN_HOLD_MINUTES` | 15 | Minimum time between MQTT changes |
| `DRY_RUN` | 0 | Set to 1 to log decisions without publishing MQTT |

## Grafana Integration

- **Building Overview** (`building_overview.json`): EVU stat panel reads from `heating_optimizer.evu_active`
- **Energy Cost** (`energy_cost.json`): "Lammityksen optimointi" time series panel shows price bars, setpoint line, effective target line, and EVU state overlay

## Replaces

This service replaces `evu_controller.py`, which sent `{"EVU":1}` during price peaks assuming it blocked the compressor. The optimizer improves on this by:
- Using EVU for its intended purpose (firmware-managed temperature reduction)
- Adding pre-heating during cheap hours (something EVU alone cannot do)
- Configuring the `reduction_t` register at startup for the correct reduction amount
