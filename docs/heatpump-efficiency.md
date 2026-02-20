# Heat Pump Efficiency Calculation

Documentation for the Thermia Diplomat 8 efficiency and power estimation panels
in the Grafana dashboard (`thermia-heatpump`).

## Overview

The heat pump dashboard includes three calculated panels that estimate thermal
power output and efficiency from temperature measurements and component status:

1. **Lämpötilaero** — Temperature differentials (ΔT)
2. **Arvioitu lämpöteho** — Estimated thermal power (stacked)
3. **Hyötysuhde (COP)** — Coefficient of Performance

## Data Sources

| Data | Measurement | Tag | Fields |
|------|-------------|-----|--------|
| Temperatures | `thermia` | `data_type=temperature` | `supply_temp`, `return_temp`, `brine_in_temp`, `brine_out_temp` |
| Component status | `thermia` | `data_type=status` | `compressor` (0/1), `aux_heater_3kw` (0/1), `aux_heater_6kw` (0/1) |

All data originates from the ThermIQ-ROOM2 MQTT interface, sampled approximately
once per minute.

## Thermia Diplomat 8 Nominal Specifications

| Parameter | Value |
|-----------|-------|
| Nominal heating output | 8.13 kW |
| Compressor electrical input | 2.3 kW |
| COP at B0/W35 | 4.6 |
| Heating circuit flow rate | 0.47 l/s |
| Brine circuit flow rate | 0.19 l/s (spec minimum) |
| Auxiliary heaters | 3 kW + 6 kW electric |
| Compressor type | Fixed-speed (on/off) |
| Circulation pumps | Fixed-speed (on/off) |

## Panel 1: Temperature Differentials (Lämpötilaero)

### Formula

```
ΔT_heating = supply_temp - return_temp    [°C]
ΔT_brine   = brine_in_temp - brine_out_temp  [°C]
```

### Optimal Ranges

| Circuit | Optimal ΔT | Shown as |
|---------|-----------|----------|
| Heating (lämmityspiiri) | 7–10 °C | Green threshold band |
| Brine (liuospiiri) | ~3 °C | Green threshold line |

Large heating ΔT indicates high heat transfer; too large may indicate
insufficient flow. Brine ΔT reflects ground heat extraction rate.

## Panel 2: Estimated Thermal Power (Arvioitu lämpöteho)

### Approach

Heat output is estimated from the heating circuit temperature differential
and the known flow rate:

```
P_heat = flow_heating × Cp_water × ΔT_heating
       = 0.47 l/s × 4.18 kJ/(kg·K) × ΔT_heating
       = 1.965 × ΔT_heating  [kW]
```

### Why not use brine ΔT for ground power?

The datasheet brine flow rate of 0.19 l/s produces unrealistically low ground
power estimates (P_ground = 0.722 × ΔT_brine), which leads to an overestimated
compressor power and COP values around 1.3 — clearly wrong for a ground-source
heat pump. The 0.19 l/s figure appears to be a minimum specification rather than
the actual operating flow rate. Without a physical flow meter on the brine
circuit, we cannot determine the true flow rate.

### Calculation Method

The chart shows thermal power output, not electrical consumption:

```
P_heat = 1.965 × ΔT_heating                 (total heat pump thermal output)
P_aux  = 3.0 kW × aux_3kw_status + 6.0 kW × aux_6kw_status
```

The stacked chart shows two layers (bottom to top):

1. **Lämpöteho** (green) — Heat pump thermal output: 1.965 × ΔT
2. **Lisävastukset** (red) — Auxiliary electric heaters: 0, 3, 6, or 9 kW

The compressor electrical input (2.3 kW) is not shown here as it is not
thermal output — it is accounted for in the COP calculation instead.

### Assumption: Fixed Compressor Power

The Thermia Diplomat 8 uses a fixed-speed compressor, so its electrical
consumption is approximately constant regardless of operating conditions.
In reality, compressor power varies slightly with refrigerant pressures
(which depend on supply and brine temperatures), but 2.3 kW is a reasonable
average for the B0/W35 operating point.

## Panel 3: Coefficient of Performance (Hyötysuhde / COP)

### Formula

```
COP_hp     = P_heat / P_compressor
           = (1.965 × ΔT_heating) / 2.3

COP_system = (P_heat + P_aux) / (P_compressor + P_aux)
```

Where:
- **COP_hp** (Lämpöpumpun COP) — Heat pump alone, ignoring auxiliary heaters
- **COP_system** (Järjestelmän COP) — Total system including auxiliary heaters

### Filtering

COP is only calculated when the compressor is running (`compressor == 1`).
When the compressor is off, no data points are produced to avoid meaningless
values.

### Reference Line

A dashed threshold line at COP 4.6 marks the nominal efficiency at B0/W35.

### Interpretation

| COP Range | Meaning |
|-----------|---------|
| > 5.0 | Favorable conditions (mild outdoor temp, low supply temp) |
| 4.0–5.0 | Normal operation |
| 3.0–4.0 | Higher supply temperatures or cold ground |
| < 3.0 | Hot water production or auxiliary heaters active |

When auxiliary heaters are running, the system COP drops significantly since
electric heaters have COP = 1.0, pulling down the weighted average.

## Flux Query Structure

The power and COP panels join temperature and status data:

```flux
temps = from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "temperature")
  |> filter(fn: (r) => r._field == "supply_temp" or r._field == "return_temp")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")

status = from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "status")
  |> filter(fn: (r) => r._field == "compressor" or ...)
  |> aggregateWindow(every: v.windowPeriod, fn: last, createEmpty: false)
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")

join(tables: {temps: temps, status: status}, on: ["_time"])
  |> map(fn: (r) => ({...calculated fields...}))
```

Key points:
- Temperature data uses `mean` aggregation; status data uses `last`
- Both are pivoted to wide format before joining on `_time`
- Status fields (integers) require `float(v:)` conversion for arithmetic
- The power panel uses three separate queries (A, B, C) to ensure stable
  stacking order in Grafana

## Limitations

1. **Compressor power is assumed constant** at 2.3 kW. Actual power varies
   with operating pressures (±10–15%).

2. **Heating flow rate is assumed constant** at 0.47 l/s. The fixed-speed
   circulation pump provides approximately constant flow, but actual flow
   depends on system hydraulic resistance.

3. **Ground power is derived**, not measured. It equals P_heat − P_compressor,
   inheriting errors from both the heating flow assumption and the fixed
   compressor power.

4. **No accounting for defrost cycles** or other transient operating modes
   that temporarily reduce efficiency.

5. **COP is instantaneous**, not seasonal (SCOP). Seasonal performance
   integrates over varying conditions throughout the heating season.
