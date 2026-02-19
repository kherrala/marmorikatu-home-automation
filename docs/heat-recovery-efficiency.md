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

All temperature and humidity data comes from the `hvac` measurement (WAGO CSV data,
~2-hour sampling interval):

| Field Name | Description | Sensor Group |
|------------|-------------|--------------|
| `Ulkolampotila` | Outdoor temperature (°C) | `ivk_temp` |
| `Tuloilma_ennen_lammitysta` | Supply air after HRU, before heating coil (°C) | `ivk_temp` |
| `Tuloilma_jalkeen_lammityksen` | Supply air after heating coil (°C) | `ivk_temp` |
| `Tuloilma_asetusarvo` | Supply air setpoint / exhaust proxy (°C) | `ivk_temp` |
| `Jateilma` | Exhaust air after HRU (°C) | `ivk_temp` |
| `Suhteellinen_kosteus` | Relative humidity, exhaust side (%) | `humidity` |

Outdoor humidity comes from the `ruuvi` measurement (`sensor_name=Ulkolämpötila`,
field `humidity`), with an 85% RH fallback when unavailable.

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
η_sensible = (T_supply_after_HRU - T_outdoor) / (T_exhaust - T_outdoor) × 100%
```

Where:
- `T_supply_after_HRU` = `Tuloilma_ennen_lammitysta` (air after HRU, before heating coil)
- `T_outdoor` = `Ulkolampotila`
- `T_exhaust` = `Tuloilma_asetusarvo` (supply setpoint used as exhaust proxy — see [Limitations](#limitations))

### Validity Filtering

- Division by zero prevented: `T_exhaust ≠ T_outdoor`
- Results filtered to 0–100% range

### Flux Query

```flux
from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila"
      or r._field == "Tuloilma_ennen_lammitysta"
      or r._field == "Tuloilma_asetusarvo")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.Ulkolampotila
      and exists r.Tuloilma_ennen_lammitysta
      and exists r.Tuloilma_asetusarvo)
  |> filter(fn: (r) => r.Tuloilma_asetusarvo != r.Ulkolampotila)
  |> map(fn: (r) => ({
    _time: r._time,
    _value: (r.Tuloilma_ennen_lammitysta - r.Ulkolampotila)
            / (r.Tuloilma_asetusarvo - r.Ulkolampotila) * 100.0,
    _field: "Tuntuva lämpö"
  }))
  |> filter(fn: (r) => r._value > 0.0 and r._value <= 100.0)
  |> group(columns: ["_field"])
```

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

The enthalpy query joins data from three sources, all aligned to 2-hour
boundaries (see [Data Alignment](#data-alignment)):

1. **HVAC temperatures** — `Ulkolampotila`, `Tuloilma_ennen_lammitysta`, `Tuloilma_asetusarvo`
2. **HVAC exhaust humidity** — `Suhteellinen_kosteus` (inner join)
3. **Ruuvi outdoor humidity** — `humidity` from sensor `Ulkolämpötila` (left join, fallback to 85% RH)

### Humidity Assignments

| Air stream | Temperature | Humidity |
|------------|-------------|----------|
| Outdoor | `Ulkolampotila` | Ruuvi `humidity` (or 85% RH fallback) |
| Supply (after HRU) | `Tuloilma_ennen_lammitysta` | Same as outdoor (no moisture added by HRU) |
| Exhaust | `Tuloilma_asetusarvo` | `Suhteellinen_kosteus` |

### Flux Query

```flux
import "math"
import "join"

temps = from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila"
      or r._field == "Tuloilma_ennen_lammitysta"
      or r._field == "Tuloilma_asetusarvo")
  |> map(fn: (r) => ({r with
      _time: time(v: ((int(v: r._time) / 7200000000000) * 7200000000000))
    }))
  |> group(columns: ["_time", "_field"])
  |> mean()
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.Ulkolampotila
      and exists r.Tuloilma_ennen_lammitysta
      and exists r.Tuloilma_asetusarvo)

hum = from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Suhteellinen_kosteus")
  |> map(fn: (r) => ({r with
      _time: time(v: ((int(v: r._time) / 7200000000000) * 7200000000000))
    }))
  |> group(columns: ["_time"])
  |> mean()
  |> group()
  |> keep(columns: ["_time", "_value"])

ruuvi = from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "ruuvi"
      and r.sensor_name == "Ulkolämpötila")
  |> filter(fn: (r) => r._field == "humidity")
  |> map(fn: (r) => ({r with
      _time: time(v: ((int(v: r._time) / 7200000000000) * 7200000000000))
    }))
  |> group(columns: ["_time"])
  |> mean()
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
    Tuloilma_asetusarvo: l.Tuloilma_asetusarvo,
    Suhteellinen_kosteus: l.Suhteellinen_kosteus,
    RH_outdoor: if exists r._value then r._value else 85.0
  })
)
  |> map(fn: (r) => {
    psat_out = 610.78 * math.pow(x: 10.0,
        y: 7.5 * r.Ulkolampotila / (237.3 + r.Ulkolampotila))
    psat_exh = 610.78 * math.pow(x: 10.0,
        y: 7.5 * r.Tuloilma_asetusarvo / (237.3 + r.Tuloilma_asetusarvo))
    w_out = 0.622 * (r.RH_outdoor / 100.0) * psat_out
            / (101325.0 - (r.RH_outdoor / 100.0) * psat_out)
    w_exh = 0.622 * (r.Suhteellinen_kosteus / 100.0) * psat_exh
            / (101325.0 - (r.Suhteellinen_kosteus / 100.0) * psat_exh)
    h_out = 1.006 * r.Ulkolampotila
            + w_out * (2501.0 + 1.86 * r.Ulkolampotila)
    h_sup = 1.006 * r.Tuloilma_ennen_lammitysta
            + w_out * (2501.0 + 1.86 * r.Tuloilma_ennen_lammitysta)
    h_exh = 1.006 * r.Tuloilma_asetusarvo
            + w_exh * (2501.0 + 1.86 * r.Tuloilma_asetusarvo)
    eta = if h_exh != h_out
          then (h_sup - h_out) / (h_exh - h_out) * 100.0
          else 0.0
    return {_time: r._time, _value: eta, _field: "Entalpia"}
  })
  |> filter(fn: (r) => r._value > 0.0 and r._value <= 100.0)
  |> group(columns: ["_field"])
```

## Data Alignment

HVAC data (~2-hour sampling) and Ruuvi data (~1-second sampling) use different
rates. To join them, all timestamps are aligned to 2-hour boundaries using
integer division on nanosecond timestamps:

```flux
_time: time(v: ((int(v: r._time) / 7200000000000) * 7200000000000))
```

Where `7200000000000` = 2 hours × 3600 seconds × 1,000,000,000 nanoseconds.

This floors each timestamp to the nearest 2-hour boundary (00:00, 02:00, 04:00, ...),
then groups by that aligned time to compute means within each window before joining.

The sensible heat efficiency panel uses `aggregateWindow(every: v.windowPeriod)`
instead, since it only uses HVAC data (no cross-measurement joins needed).

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

Estimates the risk of ice formation on the heat exchanger surfaces when
outdoor air is very cold and exhaust air carries moisture.

### Three-Component Weighted Risk

| Component | Weight | Low Risk (0%) | High Risk (100%) |
|-----------|--------|---------------|------------------|
| Outdoor temperature | 50% | -5°C | -25°C |
| Exhaust humidity | 35% | 15% RH | 30% RH |
| Exhaust air temperature | 15% | 5°C | 0°C |

Each component is linearly mapped to 0–100% within its range, then multiplied
by its weight. The total is capped at 95%.

### Override Rule

If exhaust air temperature (`Jateilma`) drops below 0°C, the probability is
forced to **95%** regardless of other components. This indicates the HRU is
already at or below freezing on the exhaust side.

### Risk Levels

| Probability | Level |
|-------------|-------|
| < 25% | Low |
| 25–50% | Moderate |
| 50–75% | High |
| >= 75% | Critical |

### Flux Query (Gauge Panel)

```flux
from(bucket: "building_automation")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila"
      or r._field == "Suhteellinen_kosteus"
      or r._field == "Jateilma")
  |> last()
  |> map(fn: (r) => ({r with _time: now()}))
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.Ulkolampotila
      and exists r.Suhteellinen_kosteus
      and exists r.Jateilma)
  |> map(fn: (r) => {
      temp_raw = (-5.0 - r.Ulkolampotila) / 20.0
      temp_risk = if temp_raw < 0.0 then 0.0
                  else if temp_raw > 1.0 then 1.0
                  else temp_raw
      temp_score = temp_risk * 50.0

      hum_raw = (r.Suhteellinen_kosteus - 15.0) / 15.0
      hum_risk = if hum_raw < 0.0 then 0.0
                 else if hum_raw > 1.0 then 1.0
                 else hum_raw
      hum_score = hum_risk * 35.0

      exh_raw = (5.0 - r.Jateilma) / 5.0
      exh_risk = if exh_raw < 0.0 then 0.0
                 else if exh_raw > 1.0 then 1.0
                 else exh_raw
      exh_score = exh_risk * 15.0

      total = temp_score + hum_score + exh_score
      prob = if r.Jateilma < 0.0 then 95.0
             else if total > 95.0 then 95.0
             else total

      return {_time: r._time, _value: prob, _field: "Jäätymisriski"}
  })
```

The gauge uses `-6h` range with `last()` to get the most recent WAGO data point,
since WAGO data is sampled approximately every 2 hours.

## Limitations

1. **Exhaust temperature proxy**: There is no dedicated exhaust air temperature
   sensor before the HRU. `Tuloilma_asetusarvo` (supply air setpoint) is used as
   a proxy for exhaust air temperature, which is approximately correct since
   exhaust air is room-temperature air being expelled.

2. **Outdoor humidity fallback**: When Ruuvi outdoor humidity data is unavailable
   (sensor offline, out of range), 85% RH is used as a conservative default.
   This is reasonable for Finnish outdoor conditions but may overestimate
   enthalpy in dry weather.

3. **Sampling rate differences**: HVAC data is logged every ~2 hours, while Ruuvi
   data arrives every ~1 second. The 2-hour boundary alignment averages Ruuvi
   data within each window, which is appropriate but means short-duration events
   are smoothed out.

4. **Constant airflow assumption**: The 414 m³/h airflow (and derived 0.1387 kW/K
   constant) assumes the ventilation system runs at a fixed speed. Variable-speed
   operation would require actual airflow measurement.

5. **No condensation modeling**: The enthalpy calculation assumes no condensation
   occurs within the HRU. In practice, moisture may condense on cold HRU surfaces,
   releasing additional latent heat that increases actual efficiency beyond the
   calculated value.

6. **Freezing probability is heuristic**: The three-component weighted model is
   a simplified risk estimate, not a physical model of ice formation. Actual
   freezing depends on HRU geometry, surface temperatures, and defrost cycle
   behavior.
