# Heat Recovery Efficiency & Freezing Risk

Documentation for the heat recovery unit (LTO) efficiency calculations and freezing
probability estimation in the HVAC Grafana dashboard (`wago-hvac`).

## Overview

The HVAC ventilation system uses a counter-flow heat recovery unit (LTO,
*lämmöntalteenotto*) to transfer heat from exhaust air to incoming supply air.
The dashboard includes four calculated panels:

1. **LTO hyötysuhde** — Heat recovery efficiency (sensible + enthalpy)
2. **LTO talteen otettu teho ja lämmityspatteri** — Recovered heat power, heating coil power, and waste heat
3. **LTO jäätymisriski** — Heat exchanger freezing probability gauge

## Data Sources

All temperature and humidity data comes from the `hvac` measurement (Casa MVHR
data via WAGO PLC MQTT publisher, ~13-second sampling interval). All values are
emitted in engineering units (°C, %, g/kg, kJ/kg) — no client-side scaling.

| Field Name | Description | Sensor Group |
|------------|-------------|--------------|
| `Ulkolampotila` | Outdoor temperature (°C) | `ivk_temp` |
| `Tuloilma_ennen_lammitysta` | Supply air after HRU, before heating coil (°C) | `ivk_temp` |
| `Tuloilma_jalkeen_lammityksen` | Supply air after heating coil (°C) | `ivk_temp` |
| `Poistoilma` | Extract air, pre-recovery (room return) (°C) | `ivk_temp` |
| `Jateilma` | Exhaust air, post-recovery (°C) | `ivk_temp` |
| `Tuloilmakanava` | Supply duct downstream sensor (°C, separate PT100) | `ivk_temp` |
| `Suhteellinen_kosteus` | Relative humidity, extract side (%) | `humidity` |
| `Kastepiste` | Dew point, extract side (°C) | `humidity` |
| `LTO_hyotysuhde` | Casa MVHR self-reported heat-recovery efficiency (%) | `performance` |
| `Alarm_freezing_danger` | Casa MVHR freezing-danger alarm flag (0/1) | `alarm` |

Outdoor humidity comes from the `ruuvi` measurement (`sensor_name=Ulkolämpötila`,
field `humidity`), with an 85% RH fallback when unavailable.

**Note on legacy fields:** The old WAGO CSV pipeline used `Tuloilma_asetusarvo`
(supply-air setpoint) as a proxy for the exhaust temperature in efficiency
calculations because the CSV had no `Poistoilma` field. The MQTT pipeline
publishes `Poistoilma` directly, so the legacy proxy is retired and dashboards
use `Poistoilma` for the denominator. Historical CSV-era records still contain
`Tuloilma_asetusarvo` for backwards compatibility but it is no longer written.

## Airflow Constant

All power calculations use a constant derived from the ventilation system airflow:

```
Q̇ = ṁ × cp × ΔT

where:
  ṁ  = ρ × V̇ = 1.2 kg/m³ × (414/3600) m³/s = 0.1380 kg/s
  cp = 1.005 kJ/(kg·K)

  Q̇ = 0.1380 × 1.005 × ΔT ≈ 0.1387 kW/K × ΔT
```

The constant **0.1387 kW/K** appears in all power queries.

## Sensible Heat Efficiency

Measures heat recovery based on dry-bulb temperatures only.

### Formula

```
η_sensible = (T_supply_after_HRU - T_outdoor) / (T_extract - T_outdoor) × 100%
```

Where:
- `T_supply_after_HRU` = `Tuloilma_ennen_lammitysta` (air after HRU, before heating coil)
- `T_outdoor` = `Ulkolampotila`
- `T_extract` = `Poistoilma` (room return air, *before* the heat exchanger)

### Validity Filtering

- Division by zero prevented: `T_extract ≠ T_outdoor`
- Results filtered to 0–100% range

### Flux Query

```flux
from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila"
      or r._field == "Tuloilma_ennen_lammitysta"
      or r._field == "Poistoilma")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.Ulkolampotila
      and exists r.Tuloilma_ennen_lammitysta
      and exists r.Poistoilma)
  |> filter(fn: (r) => r.Poistoilma != r.Ulkolampotila)
  |> map(fn: (r) => ({
    _time: r._time,
    _value: (r.Tuloilma_ennen_lammitysta - r.Ulkolampotila)
            / (r.Poistoilma - r.Ulkolampotila) * 100.0,
    _field: "Tuntuva lämpö"
  }))
  |> filter(fn: (r) => r._value > 0.0 and r._value <= 100.0)
  |> group(columns: ["_field"])
```

For comparison, the Casa MVHR's own self-reported `LTO_hyotysuhde` is also
available under `_measurement="hvac" AND sensor_group="performance"`. It will
not necessarily agree with the formula above (the unit's definition may differ
slightly) but provides a useful sanity check.

## Enthalpy Efficiency

Accounts for both sensible and latent heat (humidity) for a more accurate
efficiency figure, especially relevant in cold climates where moisture
freezes on the HRU surfaces.

### Theory

Moist air enthalpy combines sensible and latent heat components:

```
h = cp × T + w × (L + cpv × T)
  = 1.006 × T + w × (2501 + 1.86 × T)   [kJ/kg dry air]
```

Where:
- `T` = dry-bulb temperature (°C)
- `w` = humidity ratio (kg water / kg dry air)
- `L` = latent heat of vaporization at 0°C = 2501 kJ/kg
- `cp` = specific heat of dry air = 1.006 kJ/(kg·K)
- `cpv` = specific heat of water vapor = 1.86 kJ/(kg·K)

### Saturation Vapor Pressure (Tetens Formula)

```
P_sat = 610.78 × 10^(7.5 × T / (237.3 + T))   [Pa]
```

### Humidity Ratio

```
w = 0.622 × (RH/100) × P_sat / (101325 - (RH/100) × P_sat)
```

Where 101325 Pa = standard atmospheric pressure.

### Enthalpy Efficiency Formula

```
η_enthalpy = (h_supply - h_outdoor) / (h_exhaust - h_outdoor) × 100%
```

### Multi-Source Data Join

The enthalpy query joins data from three sources, aligned at the dashboard's
auto-selected `v.windowPeriod`:

1. **HVAC temperatures** — `Ulkolampotila`, `Tuloilma_ennen_lammitysta`, `Poistoilma`
2. **HVAC extract humidity** — `Suhteellinen_kosteus` (inner join)
3. **Ruuvi outdoor humidity** — `humidity` from sensor `Ulkolämpötila` (left join, fallback to 85% RH)

### Humidity Assignments

| Air stream | Temperature | Humidity |
|------------|-------------|----------|
| Outdoor | `Ulkolampotila` | Ruuvi `humidity` (or 85% RH fallback) |
| Supply (after HRU) | `Tuloilma_ennen_lammitysta` | Same as outdoor (no moisture added by HRU) |
| Extract (room return) | `Poistoilma` | `Suhteellinen_kosteus` |

### Flux Query

```flux
import "math"
import "join"

temps = from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila"
      or r._field == "Tuloilma_ennen_lammitysta"
      or r._field == "Poistoilma")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.Ulkolampotila
      and exists r.Tuloilma_ennen_lammitysta
      and exists r.Poistoilma)

hum = from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Suhteellinen_kosteus")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> group()
  |> keep(columns: ["_time", "_value"])

ruuvi = from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "ruuvi"
      and r.sensor_name == "Ulkolämpötila")
  |> filter(fn: (r) => r._field == "humidity")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> group()
  |> keep(columns: ["_time", "_value"])

step1 = join.inner(
  left: temps, right: hum,
  on: (l, r) => l._time == r._time,
  as: (l, r) => ({l with Suhteellinen_kosteus: r._value})
)

join.left(
  left: step1, right: ruuvi,
  on: (l, r) => l._time == r._time,
  as: (l, r) => ({
    _time: l._time,
    Ulkolampotila: l.Ulkolampotila,
    Tuloilma_ennen_lammitysta: l.Tuloilma_ennen_lammitysta,
    Poistoilma: l.Poistoilma,
    Suhteellinen_kosteus: l.Suhteellinen_kosteus,
    RH_outdoor: if exists r._value then r._value else 85.0
  })
)
  |> map(fn: (r) => {
    psat_out = 610.78 * math.pow(x: 10.0,
        y: 7.5 * r.Ulkolampotila / (237.3 + r.Ulkolampotila))
    psat_ext = 610.78 * math.pow(x: 10.0,
        y: 7.5 * r.Poistoilma / (237.3 + r.Poistoilma))
    w_out = 0.622 * (r.RH_outdoor / 100.0) * psat_out
            / (101325.0 - (r.RH_outdoor / 100.0) * psat_out)
    w_ext = 0.622 * (r.Suhteellinen_kosteus / 100.0) * psat_ext
            / (101325.0 - (r.Suhteellinen_kosteus / 100.0) * psat_ext)
    h_out = 1.006 * r.Ulkolampotila
            + w_out * (2501.0 + 1.86 * r.Ulkolampotila)
    h_sup = 1.006 * r.Tuloilma_ennen_lammitysta
            + w_out * (2501.0 + 1.86 * r.Tuloilma_ennen_lammitysta)
    h_ext = 1.006 * r.Poistoilma
            + w_ext * (2501.0 + 1.86 * r.Poistoilma)
    eta = if h_ext != h_out
          then (h_sup - h_out) / (h_ext - h_out) * 100.0
          else 0.0
    return {_time: r._time, _value: eta, _field: "Entalpia"}
  })
  |> filter(fn: (r) => r._value > 0.0 and r._value <= 100.0)
  |> group(columns: ["_field"])
```

## Data Alignment

Both HVAC and Ruuvi data are now aligned via `aggregateWindow(every:
v.windowPeriod, fn: mean)` — Grafana picks `windowPeriod` based on the
selected time range and panel pixel width, giving consistent resolution at
any zoom. The legacy 2-hour bucket alignment (used when WAGO data was
sampled every ~2 h via the CSV pipeline) was retired with the migration to
the MQTT publisher (~13 s sampling).

## Recovered Heat Power

Heat power recovered by the HRU from the exhaust air stream:

```
Q_recovered = 0.1387 × (T_supply_after_HRU - T_outdoor)   [kW]
```

### Flux Query

```flux
from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila"
      or r._field == "Tuloilma_ennen_lammitysta")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.Ulkolampotila
      and exists r.Tuloilma_ennen_lammitysta)
  |> map(fn: (r) => ({
    _time: r._time,
    _value: 0.1387 * (r.Tuloilma_ennen_lammitysta - r.Ulkolampotila),
    _field: "LTO talteen otettu teho"
  }))
  |> filter(fn: (r) => r._value >= 0.0)
  |> group(columns: ["_field"])
```

## Heating Coil Power

Power delivered by the heating coil (ground-source heat pump) to bring supply
air from post-HRU temperature to post-coil temperature:

```
Q_coil = 0.1387 × (T_after_coil - T_supply_after_HRU)   [kW]
```

### Flux Query

```flux
from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Tuloilma_ennen_lammitysta"
      or r._field == "Tuloilma_jalkeen_lammityksen")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.Tuloilma_ennen_lammitysta
      and exists r.Tuloilma_jalkeen_lammityksen)
  |> map(fn: (r) => ({
    _time: r._time,
    _value: 0.1387 * (r.Tuloilma_jalkeen_lammityksen
                       - r.Tuloilma_ennen_lammitysta),
    _field: "Lämmityspatteri (maapiiri)"
  }))
  |> filter(fn: (r) => r._value >= 0.0)
  |> group(columns: ["_field"])
```

## Waste Heat

Heat remaining in exhaust air after the HRU — thermal energy lost to outdoor
air. Ideally this should be low:

```
Q_waste = 0.1387 × (T_exhaust_after_HRU - T_outdoor)   [kW]
```

### Flux Query

```flux
from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila"
      or r._field == "Jateilma")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.Ulkolampotila and exists r.Jateilma)
  |> map(fn: (r) => ({
    _time: r._time,
    _value: 0.1387 * (r.Jateilma - r.Ulkolampotila),
    _field: "Hukkalämpö (ulkoilmaan)"
  }))
  |> filter(fn: (r) => r._value >= 0.0)
  |> group(columns: ["_field"])
```

## Freezing Probability

Estimates the risk of ice formation on the heat exchanger surfaces based on
**dew point proximity** — how close the exhaust air temperature is to its dew
point. When exhaust air reaches its dew point, moisture condenses on HRU
surfaces and freezes if the surface temperature is below 0°C.

### Physical Rationale

The previous algorithm used raw exhaust relative humidity (15–30% RH) as a risk
factor, but humidity alone doesn't determine condensation risk. What matters is
the **dew point margin**: the difference between exhaust air temperature and its
dew point. A small margin means the air is close to saturation and condensation
is imminent. The HVAC system measures both `Kastepiste` (dew point) and
`Jateilma` (exhaust air temperature after HRU), making this approach possible
with existing sensors.

### Three-Component Weighted Risk

| Component | Weight | Low Risk (0%) | High Risk (100%) |
|-----------|--------|---------------|------------------|
| Dew point proximity (`Jateilma - Kastepiste`) × cold-exhaust gate | 60% | margin ≥ 5°C **OR** Jateilma ≥ 5°C | margin ≤ 0°C and Jateilma ≤ 0°C |
| Outdoor temperature | 25% | ≥ -5°C | ≤ -25°C |
| Exhaust air temperature | 15% | ≥ 5°C | ≤ 0°C |

Each component is linearly mapped to 0–100% within its range, then multiplied
by its weight. Total is capped at 95%.

### Cold-Exhaust Gate

The dew-point margin component is **multiplied by a cold-exhaust factor** so
that a small margin only contributes to risk when the exhaust is also
approaching zero. Without this gate, the formula incorrectly fired in mild
conditions (e.g. autumn shoulder season with extract air at 22°C, exhaust at
10°C, dew point at 6°C — physically no freezing possible because exhaust is
well above 0°C, but margin = 4°C would otherwise trigger the dew leg).

```
cold_factor = clamp((5 − Jateilma) / 5, 0, 1)
```

- Jateilma ≥ 5°C → factor = 0 → dew_risk component zeroed
- Jateilma = 2.5°C → factor = 0.5 → half weight
- Jateilma ≤ 0°C → factor = 1 → full weight

### Formulas

```
margin       = Jateilma - Kastepiste
cold_factor  = clamp((5 - Jateilma) / 5, 0, 1)

dew_risk   = clamp((5 - margin) / 5, 0, 1) × cold_factor × 60
temp_risk  = clamp((-5 - Ulkolampotila) / 20, 0, 1) × 25
exh_risk   = clamp((5 - Jateilma) / 5, 0, 1) × 15
total      = dew_risk + temp_risk + exh_risk
```

### Override Rules

- If `Jateilma < 0°C` → probability forced to **60%** (exhaust air already below freezing)
- If `margin < 0°C` AND `Jateilma < 5°C` → probability forced to minimum **80%**
  (condensation + freezing actively occurring; the additional Jateilma constraint
  prevents the floor firing on humid summer days)
- Maximum capped at **95%**

### Casa MVHR Authoritative Signal

The dashboard panel also overlays `Alarm_freezing_danger` (boolean, scaled
to 0/100% for the same axis) — the Casa MVHR's own internally-computed
freezing-risk alarm. This is the authoritative "now" signal; the heuristic
above is a leading-indicator with finer time resolution. They should agree
at high values; persistent divergence is a hint to retune the heuristic
weights.

### Risk Levels

| Probability | Level |
|-------------|-------|
| < 25% | Low |
| 25–50% | Moderate |
| 50–75% | High |
| ≥ 75% | Critical |

### Flux Query (Gauge Panel)

```flux
from(bucket: "building_automation")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila"
      or r._field == "Kastepiste"
      or r._field == "Jateilma")
  |> last()
  |> map(fn: (r) => ({r with _time: now()}))
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.Ulkolampotila
      and exists r.Kastepiste
      and exists r.Jateilma)
  |> map(fn: (r) => {
      margin = r.Jateilma - r.Kastepiste

      cold_factor_raw = (5.0 - r.Jateilma) / 5.0
      cold_factor = if cold_factor_raw < 0.0 then 0.0
                    else if cold_factor_raw > 1.0 then 1.0
                    else cold_factor_raw

      dew_raw = (5.0 - margin) / 5.0
      dew_risk = if dew_raw < 0.0 then 0.0
                 else if dew_raw > 1.0 then 1.0
                 else dew_raw
      dew_score = dew_risk * cold_factor * 60.0

      temp_raw = (-5.0 - r.Ulkolampotila) / 20.0
      temp_risk = if temp_raw < 0.0 then 0.0
                  else if temp_raw > 1.0 then 1.0
                  else temp_raw
      temp_score = temp_risk * 25.0

      exh_raw = (5.0 - r.Jateilma) / 5.0
      exh_risk = if exh_raw < 0.0 then 0.0
                 else if exh_raw > 1.0 then 1.0
                 else exh_raw
      exh_score = exh_risk * 15.0

      total = dew_score + temp_score + exh_score
      prob = if r.Jateilma < 0.0 then 60.0
             else if margin < 0.0 and r.Jateilma < 5.0 then
               (if total > 95.0 then 95.0
                else if total < 80.0 then 80.0
                else total)
             else if total > 95.0 then 95.0
             else total

      return {_time: r._time, _value: prob, _field: "Jäätymisriski"}
  })
```

With MQTT sampling at ~13 s, the panel uses `aggregateWindow(every:
v.windowPeriod)` so the chart resolution scales naturally with the
dashboard zoom, replacing the legacy 2-hour bucketing that was needed for
the slower CSV pipeline.

## Limitations

1. **Outdoor humidity fallback**: When Ruuvi outdoor humidity data is unavailable
   (sensor offline, out of range), 85% RH is used as a conservative default.
   This is reasonable for Finnish outdoor conditions but may overestimate
   enthalpy in dry weather.

2. **Constant airflow assumption**: The 414 m³/h airflow (and derived 0.1387 kW/K
   constant) assumes the ventilation system runs at a fixed speed. Variable-speed
   operation would require actual airflow measurement (the new
   `Tulopuhallin_nopeus` and `Poistopuhallin_nopeus` `sensor_group=performance`
   fields will provide this once the Casa MVHR fan-speed registers are wired).

3. **No condensation modeling**: The enthalpy calculation assumes no condensation
   occurs within the HRU. In practice, moisture may condense on cold HRU surfaces,
   releasing additional latent heat that increases actual efficiency beyond the
   calculated value.

4. **Freezing probability is heuristic**: The dew point proximity model is
   a physically-motivated risk estimate but not a full ice formation model.
   Actual freezing depends on HRU geometry, surface temperatures, defrost cycle
   behavior, and local airflow patterns within the heat exchanger. Use the Casa
   MVHR's own `Alarm_freezing_danger` flag as the authoritative signal.
