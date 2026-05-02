# Data Collection Pipelines

How data flows from each source system into InfluxDB.

## WAGO PLC MQTT Pipeline

1. The PLC's `pMqttPublish` POU (running every 100 ms cycle) rotates through
   ten logical-group JSON topics under `marmorikatu/...`, publishing them in a
   ~3 s burst followed by a 10 s pause (configurable via `tRoundPeriod`).
2. All topics are published with the retained flag, so a fresh subscriber
   always sees the latest snapshot on connect.
3. The `plc` service subscribes to `marmorikatu/#` on broker
   `freenas.kherrala.fi:1883` and dispatches each topic to a dedicated builder.
4. Builders translate the technical-ID JSON keys to the existing InfluxDB
   schema (same field names that `import_data.py` and `lights_poller.py` used)
   so all dashboards and MCP queries continue to work.

For the publishing protocol — topic layout, payload shapes, the
Controls-index light-id convention, and PLC-side gotchas — see
`../marmorikatu-plc/MQTT.md`.

### Topic → Measurement Mapping

| MQTT topic | InfluxDB measurement | Tags | Notes |
|---|---|---|---|
| `marmorikatu/temperatures` | `rooms` + `hvac` | `room_type`, `floor`; `sensor_group=ivk_temp/cooling` | 9 room PT100 → `rooms`, 3 raw PT100 (`tuloilmakanava`, `jaahdpatteri_1/2`) → `hvac` |
| `marmorikatu/lights` | `lights` | `light_id`, `light_name`, `floor`, `floor_name`, `switch_type=primary` | Keys are bare `Controls[]` indices; labels from `buttontxt.txt` |
| `marmorikatu/switches` | `switches` *(new)* | `switch_id`, `switch_name`, `floor`, `floor_name` | Field `pressed` (0/1); labels from `buttonpos.txt` |
| `marmorikatu/heating` | `rooms` | `room_type=valve`, `floor` | 9 underfloor-heating zone valves (`LL_*`) |
| `marmorikatu/cooling` | `hvac` | `sensor_group=cooling` | 2 cooling pumps |
| `marmorikatu/outlets` | `lights` | `switch_type=outlet`, `floor=""`, `floor_name="Ulko"` | 2 outdoor power outlets |
| `marmorikatu/ventilation` | `hvac` | `sensor_group=ivk_temp/humidity/actuator` | Casa MVHR + Belimo 22DTH + LR24A actuator |
| `marmorikatu/energy/heatpump` | `hvac` | `sensor_group=voltage/current/power/energy`, `meter=heatpump` | OR-WE-517 #1; legacy aliases (`Lampopumppu_teho`, `U1_jannite`, …) also written |
| `marmorikatu/energy/extra` | `hvac` | `sensor_group=voltage/current/power/energy`, `meter=extra` | OR-WE-517 #2; legacy aliases (`Lisavastus_teho`, …) also written |
| `marmorikatu/status` | `plc_publisher` *(new)* | — | `PublishCount`, `ErrorCount`, `ModbusConnected`, … |

### Light-ID Convention

The `marmorikatu/lights` payload uses bare `PersistentVars.Controls[]` indices
as keys (e.g. `"1"`, `"17"`, `"51"`), not `v…` strings. The Controls array has
gaps where outputs are unused:

- `1`–`8` → `Ledi1`–`Ledi8` indicator LEDs
- `17`–`56` → `V9`–`V48` wall-light outputs (`Controls index − 8 = V number`)
- `59`–`61` → `V51`–`V53` outdoor outputs (autokatos, varasto ulkovalo, varasto)

Friendly Finnish names and floor classification are loaded by the subscriber
from a hardcoded table derived from `../marmorikatu-plc/PlcLogic/visu/buttontxt.txt`.

### Cadence and Volume

A round of 10 publishes takes ~3 s of bus time, then idles for 10 s before the
next round — about 4.6 rounds per minute. The subscriber writes ~10 InfluxDB
batches per round (one per topic), each containing 1–50 points depending on
how many fields the topic carries.

### Limitations vs the Old CSV Pipeline

A few CSV columns have no MQTT equivalent yet, because they were PLC-internal
calculations rather than fieldbus signals:

- `Tuloilma_asetusarvo` (supply-air setpoint)
- `Toimilaite_asetusarvo`, `Toimilaite_pakotus`
- Per-room PID heating-demand outputs (`MH_*_PID`, `Keittio_PID`, …)

The dashboards that displayed PID heating demand will show empty data; the
heat-recovery efficiency calculations that referenced `Tuloilma_asetusarvo`
will need to fall back to the post-heating supply temperature. For zone
heating activity, the new `room_type=valve` data under `marmorikatu/heating`
provides a binary alternative.

## Legacy CSV Pipeline (disabled)

The pre-MQTT pipeline (`scripts/import_data.py` + `sync` Docker service) is
preserved in the repo but commented out in `docker-compose.yml`. To run a
historical CSV re-import manually:

```bash
source venv/bin/activate
python scripts/import_data.py              # full re-import (clears existing)
python scripts/import_data.py --incremental # append new lines only
```

## Ruuvi MQTT Pipeline

1. Ruuvi Gateway publishes sensor data to MQTT topic `ruuvi/<gateway_mac>/<sensor_mac>`
2. The `ruuvi` service subscribes and parses JSON payloads
3. Data format 5 (basic): temperature, humidity, pressure, acceleration, voltage
4. Data format 225 (air quality): adds CO2, PM, VOC, NOx
5. Pressure auto-converted from Pa to hPa if value > 10000
6. Each message written immediately to InfluxDB (synchronous writes)
7. Indoor temperature from Olohuone sensor forwarded to ThermIQ via MQTT `set` topic

### Sensor Inventory

| MAC Address | Name | Data Format | Location |
|-------------|------|-------------|----------|
| `D1:86:61:6E:DF:E4` | Sauna | 5 (basic) | Sauna room |
| `D3:1D:6A:1E:7C:4E` | Takka | 5 (basic) | Fireplace area |
| `D7:6C:BC:6D:29:46` | Olohuone | 5 (basic) | Living room |
| `E6:DC:F8:EC:78:3B` | Keittiö | 225 (air quality) | Kitchen |
| `EE:3A:F4:B9:74:E5` | Jääkaappi | 5 (basic) | Inside refrigerator |
| `EF:AA:DF:C0:4F:8C` | Pakastin | 5 (basic) | Inside freezer |
| `F1:19:ED:0F:9A:F6` | Ulkolämpötila | 5 (basic) | Outdoor |

### Indoor Temperature Forwarding

The Ruuvi service forwards the Olohuone sensor's temperature to the ThermIQ
heat pump via MQTT (`ThermIQ/marmorikatu/set` topic, `INDR_T` field).
Temperature values outside 19–25°C are rejected as out of bounds.

## ThermIQ MQTT Pipeline

1. The `thermia` service sends periodic read commands to `ThermIQ/ThermIQ-room2/read`
2. ThermIQ-ROOM2 responds with register dump on `ThermIQ/marmorikatu/data`
3. Registers parsed from decimal (`dDD`) or hex (`rXX`) keys
4. Data split into 6 InfluxDB points per message:
   - `temperature`: simple registers + combined integer/decimal pairs
   - `status`: bitfield extraction from registers d13, d16, d17
   - `alarm`: bitfield extraction from registers d19, d20
   - `performance`: direct register values
   - `runtime`: hour counters
   - `setting`: configuration values (d79 has ×10 multiplier)

### Register Format

The ThermIQ module publishes heat pump registers as a flat JSON object:

```json
{"r00": -5, "r01": 21, "r02": 3, "r05": 35, "r0d": 3, "r10": 7, ...}
```

Both hex (`rXX`) and decimal (`dDD`) key formats are handled. Conversion:
`r0a` = `d10`, `r10` = `d16`, `r32` = `d50`.

See [thermiq_register_map.md](thermiq_register_map.md) for the complete
register map with all field definitions.

### Bitfield Extraction

Status registers are integers where each bit represents a component state:

| Register | Bits Extracted |
|----------|----------------|
| d13 | `aux_heater_3kw`, `aux_heater_6kw` |
| d16 | `brinepump`, `compressor`, `flowlinepump`, `hotwater_production`, `aux_2`, `shunt_minus`, `shunt_plus`, `aux_1` |
| d17 | `active_cooling`, `passive_cooling` |
| d19 | `alarm_highpr_pressostate`, `alarm_lowpr_pressostate`, `alarm_motor_breaker`, `alarm_low_flow_brine`, `alarm_low_temp_brine` |
| d20 | `alarm_outdoor_sensor`, `alarm_supply_sensor`, `alarm_return_sensor`, `alarm_hotwater_sensor`, `alarm_indoor_sensor`, `alarm_3phase_order`, `alarm_overheating` |

### Combined Temperature Registers

Some temperatures use two registers (integer + decimal part):

| Field | Integer Reg | Decimal Reg | Calculation |
|-------|-------------|-------------|-------------|
| `indoor_temp` | d1 | d2 | `d1 + d2 × 0.1` |
| `indoor_target_temp` | d3 | d4 | `d3 + d4 × 0.1` |

## Lights HTTP Pipeline (disabled)

The `lights` service is commented out in `docker-compose.yml`. Light states
now flow via `marmorikatu/lights` and `marmorikatu/outlets`, processed by
the `plc` service, which writes to the same `lights` measurement so existing
dashboards keep working.
