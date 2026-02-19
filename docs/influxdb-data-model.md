# InfluxDB Data Model

Complete schema reference for the building automation InfluxDB database.

## Database Configuration

| Parameter | Value |
|-----------|-------|
| Engine | InfluxDB 2.7 |
| Bucket | `building_automation` |
| Organization | `wago` |
| Retention | Default (infinite) |
| URL | `http://localhost:8086` |

## Measurements Overview

| Measurement | Source | Sampling Rate | Tags | Description |
|-------------|--------|---------------|------|-------------|
| `hvac` | WAGO CSV (`logfile_dp_*.csv`) | ~2 hours | `sensor_group` | HVAC system temperatures, humidity, power, energy |
| `rooms` | WAGO CSV (`Temperatures*.csv`) | ~1 hour | `room_type`, `floor` | Room temperatures and PID controller outputs |
| `ruuvi` | MQTT (Ruuvi Gateway) | ~1 second | `sensor_id`, `sensor_name`, `data_format`, `sensor_type` | Bluetooth sensor data |
| `thermia` | MQTT (ThermIQ-ROOM2) | ~30 seconds | `data_type` | Heat pump temperatures, status, alarms, runtimes |
| `lights` | HTTP API polling | 5 minutes | `light_id`, `light_name`, `floor`, `floor_name`, `switch_type` | Light switch on/off status |

---

## Measurement: `hvac`

HVAC system data from the WAGO PLC controller, imported from CSV files with
Latin-1 encoding.

### Tags

| Tag | Values |
|-----|--------|
| `sensor_group` | `ivk_temp`, `humidity`, `power`, `energy`, `voltage`, `actuator` |

### Fields by Sensor Group

#### `sensor_group=ivk_temp` — Ventilation Temperatures

| Field | Unit | CSV Column | Description |
|-------|------|------------|-------------|
| `Ulkolampotila` | °C | `IVK ulkolämpö[c°]` | Outdoor temperature |
| `Tuloilma_ennen_lammitysta` | °C | `IVK tulo ennen lämmitystä[c°]` | Supply air after heat recovery, before heating coil |
| `Tuloilma_asetusarvo` | °C | `IVK positolämpötila[c°]` | Supply air setpoint (also used as exhaust temp proxy) |
| `Tuloilma_jalkeen_lammityksen` | °C | `IVK tulo jälkeen lämmityksen[c°]` | Supply air after heating coil |
| `Jateilma` | °C | `IVK Jäteilma[c°]` | Exhaust air after heat recovery unit |
| `Tuloilma_jalkeen_jaahdytyksen` | °C | `Tuloilma jäähdytyksen jälkeen[c°]` | Supply air after cooling (summer mode) |
| `RH_lampotila` | °C | `RH Lämpötila[c°]` | RH sensor temperature reading |

#### `sensor_group=humidity` — Humidity Sensors

| Field | Unit | CSV Column | Description |
|-------|------|------------|-------------|
| `Suhteellinen_kosteus` | % | `RH suht kosteus[%]` | Relative humidity (exhaust side) |
| `Kastepiste` | °C | `RH kastepiste[c°]` | Dew point temperature |
| `TH_anturi_lampotila` | °C | `TH Lämpötila[c°]` | TH sensor temperature |

#### `sensor_group=power` — Electrical Power

| Field | Unit | CSV Column | Description |
|-------|------|------------|-------------|
| `Lampopumppu_teho` | kW | `P Lämpöpumppu[Kw]` | Heat pump power consumption |
| `Lisavastus_teho` | kW | `P Lisävastus[kw]` | Auxiliary heater power consumption |

#### `sensor_group=energy` — Cumulative Energy

| Field | Unit | CSV Column | Description |
|-------|------|------------|-------------|
| `Lampopumppu_energia` | kWh | `E Lämpöpumppu[Kwh]` | Heat pump cumulative energy |
| `Lisavastus_energia` | kWh | `E Lisävastus[Kwh]` | Auxiliary heater cumulative energy |

#### `sensor_group=voltage` — Mains Voltage

| Field | Unit | CSV Column | Description |
|-------|------|------------|-------------|
| `U1_jannite` | V | `U1[V]` | Phase 1 voltage |
| `U2_jannite` | V | `U2[V]` | Phase 2 voltage |
| `U3_jannite` | V | `U3[V]` | Phase 3 voltage |

#### `sensor_group=actuator` — Heating Valve

| Field | Unit | CSV Column | Description |
|-------|------|------------|-------------|
| `Toimilaite_asetusarvo` | °C | `Toimilaite SP[c°]` | Heating valve setpoint |
| `Toimilaite_pakotus` | °C | `Toimilaite pakotus[c°]` | Heating valve override status |
| `Toimilaite_ohjaus` | °C | `Toimilaite ohjaus[c°]` | Heating valve control output |

### Validation Rules

- Temperature fields (`ivk_temp`): -50°C to 100°C
- Humidity (`kosteus` fields): 0–100%
- Power: 0–100 kW
- Values outside range or > 1×10¹⁰ are discarded as sensor errors

### Example Query

```flux
// Latest outdoor temperature
from(bucket: "building_automation")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "hvac"
      and r.sensor_group == "ivk_temp")
  |> filter(fn: (r) => r._field == "Ulkolampotila")
  |> last()
```

---

## Measurement: `rooms`

Room temperature data and PID controller outputs from the WAGO controller,
imported from `Temperatures*.csv` files.

### Tags

| Tag | Values | Description |
|-----|--------|-------------|
| `room_type` | `bedroom`, `common`, `basement`, `pid`, `energy` | Category of data |
| `floor` | `0`, `1`, `2` | Floor level (0=basement, 1=ground, 2=upstairs) |

### Fields by Room Type

#### `room_type=bedroom` — Bedroom Temperatures

| Field | Unit | Floor | Description |
|-------|------|-------|-------------|
| `MH_Seela` | °C | 2 | Bedroom - Seela (upstairs) |
| `MH_Aarni` | °C | 2 | Bedroom - Aarni (upstairs) |
| `MH_aikuiset` | °C | 2 | Bedroom - Adults (upstairs) |
| `MH_alakerta` | °C | 1 | Bedroom - Downstairs guest room |

#### `room_type=common` — Common Area Temperatures

| Field | Unit | Floor | Description |
|-------|------|-------|-------------|
| `Ylakerran_aula` | °C | 2 | Upstairs hallway |
| `Keittio` | °C | 1 | Kitchen |
| `Eteinen` | °C | 1 | Entrance hall |

#### `room_type=basement` — Basement Temperatures

| Field | Unit | Floor | Description |
|-------|------|-------|-------------|
| `Kellari` | °C | 0 | Basement main area |
| `Kellari_eteinen` | °C | 0 | Basement entrance |

#### `room_type=pid` — PID Controller Outputs

| Field | Unit | Floor | Description |
|-------|------|-------|-------------|
| `MH_Seela_PID` | % | 2 | Seela room heating demand |
| `MH_Aarni_PID` | % | 2 | Aarni room heating demand |
| `MH_aikuiset_PID` | % | 2 | Adults room heating demand |
| `MH_alakerta_PID` | % | 1 | Downstairs room heating demand |
| `Ylakerran_aula_PID` | % | 2 | Upstairs hallway heating demand |
| `Keittio_PID` | % | 1 | Kitchen heating demand |
| `Eteinen_PID` | % | 1 | Entrance heating demand |
| `Kellari_PID` | % | 0 | Basement heating demand |
| `Kellari_eteinen_PID` | % | 0 | Basement entrance heating demand |

#### `room_type=energy` — Building Energy Totals

| Field | Unit | Floor | Description |
|-------|------|-------|-------------|
| `Lisalammitin_vuosienergia` | kWh | — | Auxiliary heater annual energy |
| `Maalampopumppu_vuosienergia` | kWh | — | Heat pump annual energy |

### Example Query

```flux
// All room temperatures, latest values
from(bucket: "building_automation")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "rooms"
      and r.room_type == "bedroom")
  |> last()
```

---

## Measurement: `ruuvi`

Bluetooth sensor data from Ruuvi tags via an MQTT gateway. Two data formats
are supported with different field sets.

### Tags

| Tag | Values | Description |
|-----|--------|-------------|
| `sensor_id` | MAC addresses (e.g., `D7:6C:BC:6D:29:46`) | Hardware identifier |
| `sensor_name` | `Sauna`, `Takka`, `Olohuone`, `Keittiö`, `Jääkaappi`, `Pakastin`, `Ulkolämpötila` | Human-readable name |
| `data_format` | `5`, `225` | Ruuvi data format version |
| `sensor_type` | `basic`, `air_quality` | Sensor capability level |

### Sensor Inventory

| Name | MAC | Type | Location |
|------|-----|------|----------|
| Sauna | `D1:86:61:6E:DF:E4` | basic | Sauna room |
| Takka | `D3:1D:6A:1E:7C:4E` | basic | Fireplace area |
| Olohuone | `D7:6C:BC:6D:29:46` | basic | Living room |
| Keittiö | `E6:DC:F8:EC:78:3B` | air_quality | Kitchen |
| Jääkaappi | `EE:3A:F4:B9:74:E5` | basic | Inside refrigerator |
| Pakastin | `EF:AA:DF:C0:4F:8C` | basic | Inside freezer |
| Ulkolämpötila | `F1:19:ED:0F:9A:F6` | basic | Outdoor |

### Fields — Data Format 5 (Basic)

Available from all sensors:

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `temperature` | float | °C | Temperature |
| `humidity` | float | % | Relative humidity |
| `pressure` | float | hPa | Atmospheric pressure (auto-converted from Pa if > 10000) |
| `accel_x` | float | g | X-axis acceleration |
| `accel_y` | float | g | Y-axis acceleration |
| `accel_z` | float | g | Z-axis acceleration |
| `voltage` | float | V | Battery voltage |
| `tx_power` | int | dBm | TX power |
| `movement_counter` | int | — | Movement detection counter |
| `rssi` | int | dBm | Bluetooth signal strength |

### Fields — Data Format 225 (Air Quality)

Available from the Keittiö sensor only:

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `temperature` | float | °C | Temperature |
| `humidity` | float | % | Relative humidity |
| `pressure` | float | hPa | Atmospheric pressure |
| `co2` | int | ppm | CO2 concentration |
| `pm1_0` | float | µg/m³ | PM1.0 particulate matter |
| `pm2_5` | float | µg/m³ | PM2.5 particulate matter |
| `pm4_0` | float | µg/m³ | PM4.0 particulate matter |
| `pm10_0` | float | µg/m³ | PM10 particulate matter |
| `voc` | int | index | VOC index (1–500) |
| `nox` | int | index | NOx index (1–500) |
| `luminosity` | float | lux | Luminosity (if available) |
| `sound_inst_dba` | float | dBA | Instantaneous sound level (if available) |
| `sound_avg_dba` | float | dBA | Average sound level (if available) |
| `rssi` | int | dBm | Bluetooth signal strength |

### Example Queries

```flux
// Kitchen air quality (latest)
from(bucket: "building_automation")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "ruuvi"
      and r.sensor_name == "Keittiö")
  |> filter(fn: (r) => r._field == "co2"
      or r._field == "pm2_5"
      or r._field == "voc")
  |> last()

// Outdoor temperature trend (hourly averages)
from(bucket: "building_automation")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "ruuvi"
      and r.sensor_name == "Ulkolämpötila")
  |> filter(fn: (r) => r._field == "temperature")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
```

---

## Measurement: `thermia`

Heat pump data from the Thermia Diplomat 8 ground-source heat pump via the
ThermIQ-ROOM2 MQTT interface. Data is organized into six categories using
the `data_type` tag.

For the complete register map, see [thermiq_register_map.md](thermiq_register_map.md).

### Tags

| Tag | Values |
|-----|--------|
| `data_type` | `temperature`, `status`, `alarm`, `performance`, `runtime`, `setting` |

### Fields — `data_type=temperature`

| Field | Unit | Register | Description |
|-------|------|----------|-------------|
| `outdoor_temp` | °C | d0 | Outdoor temperature |
| `indoor_temp` | °C | d1+d2×0.1 | Indoor temperature (combined) |
| `indoor_target_temp` | °C | d3+d4×0.1 | Indoor target temperature (combined) |
| `supply_temp` | °C | d5 | Supply line temperature |
| `return_temp` | °C | d6 | Return line temperature |
| `hotwater_temp` | °C | d7 | Hot water tank temperature |
| `brine_out_temp` | °C | d8 | Brine circuit outgoing |
| `brine_in_temp` | °C | d9 | Brine circuit incoming |
| `cooling_temp` | °C | d10 | Cooling circuit temperature |
| `supply_shunt_temp` | °C | d11 | Supply line after shunt valve |
| `supply_target_temp` | °C | d14 | Supply line target |
| `supply_target_shunt_temp` | °C | d15 | Supply line target for shunt |
| `pressurepipe_temp` | °C | d23 | Compressor discharge temperature |

### Fields — `data_type=status`

Extracted from bitfields in registers d13, d16, d17:

| Field | Type | Register.Bit | Description |
|-------|------|-------------|-------------|
| `compressor` | int (0/1) | d16.1 | Compressor on/off |
| `brinepump` | int (0/1) | d16.0 | Brine pump on/off |
| `flowlinepump` | int (0/1) | d16.2 | Flow line pump on/off |
| `hotwater_production` | int (0/1) | d16.3 | Hot water production active |
| `aux_heater_3kw` | int (0/1) | d13.0 | 3 kW auxiliary heater |
| `aux_heater_6kw` | int (0/1) | d13.1 | 6 kW auxiliary heater |
| `aux_1` | int (0/1) | d16.7 | Auxiliary 1 |
| `aux_2` | int (0/1) | d16.4 | Auxiliary 2 |
| `shunt_minus` | int (0/1) | d16.5 | Shunt valve closing |
| `shunt_plus` | int (0/1) | d16.6 | Shunt valve opening |
| `active_cooling` | int (0/1) | d17.4 | Active cooling mode |
| `passive_cooling` | int (0/1) | d17.5 | Passive cooling mode |

### Fields — `data_type=alarm`

Extracted from bitfields in registers d19, d20:

| Field | Type | Register.Bit | Description |
|-------|------|-------------|-------------|
| `alarm_highpr_pressostate` | int (0/1) | d19.0 | High pressure alarm |
| `alarm_lowpr_pressostate` | int (0/1) | d19.1 | Low pressure alarm |
| `alarm_motor_breaker` | int (0/1) | d19.2 | Motor circuit breaker |
| `alarm_low_flow_brine` | int (0/1) | d19.3 | Low brine flow |
| `alarm_low_temp_brine` | int (0/1) | d19.4 | Low brine temperature |
| `alarm_outdoor_sensor` | int (0/1) | d20.0 | Outdoor temp sensor fault |
| `alarm_supply_sensor` | int (0/1) | d20.1 | Supply line sensor fault |
| `alarm_return_sensor` | int (0/1) | d20.2 | Return line sensor fault |
| `alarm_hotwater_sensor` | int (0/1) | d20.3 | Hot water sensor fault |
| `alarm_indoor_sensor` | int (0/1) | d20.4 | Indoor sensor fault (always 1 — uses wireless) |
| `alarm_3phase_order` | int (0/1) | d20.5 | Incorrect 3-phase order |
| `alarm_overheating` | int (0/1) | d20.6 | Overheating |

### Fields — `data_type=performance`

| Field | Unit | Register | Description |
|-------|------|----------|-------------|
| `electrical_current` | A | d12 | Electrical current (always 0 on this unit) |
| `demand1` | — | d21 | DEMAND1 signal |
| `demand2` | — | d22 | DEMAND2 signal (128 = neutral) |
| `integral` | °C×min | d25 | Cumulative temperature deficit |
| `defrost` | ×10s | d27 | Defrost timer duration |
| `flowlinepump_speed` | % | d30 | Flow pump speed (0 = fixed-speed) |
| `brinepump_speed` | % | d31 | Brine pump speed (0 = fixed-speed) |

### Fields — `data_type=runtime`

| Field | Unit | Register | Description |
|-------|------|----------|-------------|
| `runtime_compressor` | h | d104 | Compressor total runtime |
| `runtime_3kw` | h | d106 | 3 kW heater total runtime |
| `runtime_6kw` | h | d114 | 6 kW heater total runtime |
| `runtime_hotwater` | h | d108 | Hot water production runtime |
| `runtime_passive_cooling` | h | d110 | Passive cooling runtime |
| `runtime_active_cooling` | h | d112 | Active cooling runtime |

### Fields — `data_type=setting`

| Field | Unit | Register | Description |
|-------|------|----------|-------------|
| `indoor_target_setpoint` | °C | d50 | Indoor target setpoint |
| `mode` | — | d51 | Operating mode (0=Off, 1=Heating, 2=Cooling, 3=Auto) |
| `curve` | — | d52 | Heating curve slope |
| `hotwater_start_temp` | °C | d68 | Hot water heating start temp |
| `hotwater_stop_temp` | °C | d84 | Hot water heating stop temp |
| `integral_limit_a1` | °C×min | d73 | Integral limit for aux step 1 |
| `integral_limit_a2` | °C×min | d79×10 | Integral limit for aux step 2 |

### Example Query

```flux
// Heat pump status overview
from(bucket: "building_automation")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "thermia"
      and r.data_type == "temperature")
  |> last()
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
```

---

## Measurement: `lights`

Light switch on/off status from an HTTP API, polled every 5 minutes.

### Tags

| Tag | Description | Example Values |
|-----|-------------|----------------|
| `light_id` | Switch identifier | `keittio-1`, `mh-1-1`, `kellari-1` |
| `light_name` | Human-readable name from API | `Keittiö katto`, `MH1 katto` |
| `floor` | Floor number | `0` (basement), `1` (ground), `2` (upstairs) |
| `floor_name` | Finnish floor name | `Kellari`, `Alakerta`, `Yläkerta` |
| `switch_type` | `primary` or `secondary` | For dual-function switches |

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `is_on` | int (0/1) | Switch state: 1 = on, 0 = off |

### Floor Classification

Lights are classified into three floors based on their `light_id`:

- **Floor 0 (Kellari)**: `tekninen-tila`, `kellari-wc`, `kellari-eteinen-*`, `kellari-*`
- **Floor 1 (Alakerta)**: `kylpyhuone-*`, `wc-alakerta-*`, `khh-*`, `keittio-*`, `tuulikaappi-*`, `mh-alakerta-*`, `eteinen-*`, `saareke-*`, `autokatos-*`, `ulkovarasto`
- **Floor 2 (Yläkerta)**: `porras-*`, `mh-1-*`, `mh2-*`, `mh3-*`, `kylpyhuone-yk-*`, `aula-yk-*`

### Dual-Function Switches

Some switches have two functions (e.g., ceiling light + accent light). These
produce two data points per poll cycle:
- Primary: `switch_type=primary`, `light_id` as-is
- Secondary: `switch_type=secondary`, `light_id` with `-2` suffix

### Example Query

```flux
// Lights currently on, grouped by floor
from(bucket: "building_automation")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "lights")
  |> last()
  |> filter(fn: (r) => r._value == 1)
  |> group(columns: ["floor_name"])
  |> count()
```
