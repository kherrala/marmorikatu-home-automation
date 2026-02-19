# Data Collection Pipelines

How data flows from each source system into InfluxDB.

## WAGO CSV Sync Pipeline

1. The `sync` service connects to the WAGO PLC at `192.168.1.10` via SSH
2. SCP copies CSV files from `/media/sd/CSV_Files/` to the local `./data/` directory
3. The import script (`scripts/import_data.py`) runs in incremental mode:
   - Reads CSV files with Latin-1 encoding
   - Normalizes headers (handles BOM, degree symbols)
   - Maps CSV columns to InfluxDB fields via `HVAC_SENSOR_MAP` and `ROOM_SENSOR_MAP`
   - Groups fields by sensor group or room type into single InfluxDB points
   - Tracks per-file line counts in `.import_state.json` to avoid re-importing
   - Batch writes (5000 points per batch)
4. Two CSV file patterns:
   - `logfile_dp_*.csv` → `hvac` measurement (6 sensor groups)
   - `Temperatures*.csv` → `rooms` measurement (5 room types)

### CSV Column Mapping

HVAC columns are mapped via `HVAC_SENSOR_MAP` in `scripts/import_data.py`:

| CSV Column | Sensor Group | InfluxDB Field |
|------------|-------------|----------------|
| `IVK ulkolämpö[c°]` | `ivk_temp` | `Ulkolampotila` |
| `IVK tulo ennen lämmitystä[c°]` | `ivk_temp` | `Tuloilma_ennen_lammitysta` |
| `IVK positolämpötila[c°]` | `ivk_temp` | `Tuloilma_asetusarvo` |
| `IVK tulo jälkeen lämmityksen[c°]` | `ivk_temp` | `Tuloilma_jalkeen_lammityksen` |
| `IVK Jäteilma[c°]` | `ivk_temp` | `Jateilma` |
| `RH suht kosteus[%]` | `humidity` | `Suhteellinen_kosteus` |
| `P Lämpöpumppu[Kw]` | `power` | `Lampopumppu_teho` |
| `E Lämpöpumppu[Kwh]` | `energy` | `Lampopumppu_energia` |
| `U1[V]` | `voltage` | `U1_jannite` |
| ... | | *(see import_data.py for full mapping)* |

Room columns are mapped via `ROOM_SENSOR_MAP`, producing `(room_type, field_name, floor)` tuples.

### Validation Rules

- Temperature fields: -50°C to 100°C
- Humidity: 0–100%
- Power: 0–100 kW
- Absolute values > 1×10¹⁰ discarded as sensor errors

### Incremental Import State

The file `.import_state.json` in the data directory tracks how many lines have
been processed per CSV file:

```json
{
  "files": {
    "logfile_dp_2026.csv": {"lines": 4320, "type": "hvac"},
    "Temperatures2026.csv": {"lines": 8760, "type": "room"}
  }
}
```

On subsequent runs with `--incremental`, only lines beyond the recorded count
are processed.

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

## Lights HTTP Pipeline

1. The `lights` service polls `http://host.docker.internal:8080/api/lights` every 5 minutes
2. Response JSON contains switch status for all light switches
3. Each switch classified by floor (0/1/2) based on `light_id` mapping
4. Dual-function switches produce two data points (primary + secondary)
5. Written to InfluxDB `lights` measurement with floor and name tags

### Floor Classification

| Floor | Name | Example Light IDs |
|-------|------|-------------------|
| 0 | Kellari (basement) | `tekninen-tila`, `kellari-wc`, `kellari-1` |
| 1 | Alakerta (ground) | `keittio-1`, `eteinen-1`, `mh-alakerta-1`, `saareke-1` |
| 2 | Yläkerta (upstairs) | `mh-1-1`, `aula-yk-1`, `porras-yk-1` |

### Dual-Function Switches

Some switches control two functions (e.g., ceiling light + accent light).
The API reports both states via `isOn` and `isOn2`. These produce two InfluxDB
points per poll:

- Primary: `switch_type=primary`, `light_id` as-is, name from `firstPress`
- Secondary: `switch_type=secondary`, `light_id` with `-2` suffix, name from `secondPress`
