# Presence Platform — Setup Guide

Local, MQTT-first room-occupancy detection that drives the lights-optimizer. No
Home Assistant. Zigbee sensors → SLZB-06U → Zigbee2MQTT → a **Presence Engine**
that normalizes every sensor into one vendor-neutral per-room model, which the
optimizer already consumes.

```
SONOFF SNZB-03PR2 (PIR)   Aqara FP300 (mmWave)
            │                     │
            └──────► SLZB-06U (Zigbee coordinator, on the network)
                          │
                     Zigbee2MQTT (docker: zigbee2mqtt)      →  zigbee2mqtt/<device>
                          │
                     Presence Engine (docker: presence)     →  presence/<room>  +  `presence` InfluxDB
                          │
                     lights-optimizer  (per-room auto-on / vacancy-off)
```

All services — including the two Zigbee containers (`zigbee2mqtt` + `presence`) —
start by default with `docker compose up -d`. The coordinator IP is baked into the
compose config (`SLZB_HOST` default `192.168.1.218`); set `SLZB_HOST` only if it moves.

---

## Hardware

| Role | Device | Notes |
|---|---|---|
| Coordinator | **SMLIGHT SLZB-06U** (CC2652P) | On Wi-Fi/Ethernet; exposes the radio over TCP `:6638`. Adapter type = `zstack`. |
| Living room | **Aqara FP300** (mmWave + PIR) | True stationary presence. **Must be switched to Zigbee mode before pairing** (ships in Thread mode). |
| Bedrooms / halls / bathrooms / WC | **SONOFF SNZB-03PR2** (PIR) | Battery, fast motion, lux sensor. 6 units. |

---

## One-time setup

### 1. Put the SLZB-06U on the network
Power it (PoE injector or USB-C), join it to Wi-Fi via its own web UI, and note
its **IP address**. In the SLZB web UI set the coordinator **Mode = Zigbee2MQTT**
(raw TCP on `6638`). Confirm `nc -z <slzb-ip> 6638` succeeds from the docker host.

### 2. Start Zigbee2MQTT
On the server (`~/marmorikatu-home-automation`, `git pull` first):
```bash
docker compose up -d zigbee2mqtt
```
The coordinator defaults to `tcp://192.168.1.218:6638`; only if it moves, override
with `SLZB_HOST=<slzb-ip> docker compose up -d zigbee2mqtt` (persist in a `.env`
file: `echo "SLZB_HOST=<slzb-ip>" >> .env`). Watch it come up:
`docker logs -f marmorikatu-zigbee2mqtt` (expect "Coordinator firmware …",
"Zigbee2MQTT started"). The web frontend is at `http://<server>:8080`.

### 3. Pair the sensors
In the frontend → **Permit join (All)** for a few minutes (or per-device).
- **Aqara FP300:** switch it to **Zigbee mode first** (hold per Aqara docs until
  it advertises Zigbee), then pair. If it pairs as Thread it won't appear here.
- **SNZB-03PR2:** long-press until the LED blinks, it joins within seconds.

Give each device a clear **friendly name** in the frontend (e.g.
`snzb_wc_down`, `fp300_living`). Turn Permit join **off** when done.

### 4. Map devices → rooms
Edit `config/presence_rooms.json` (bind-mounted, **hot-reloaded** — no rebuild):
put each device's friendly name under `devices`, pointing at a room key. Rooms
are pre-defined with a sensor `type` and `linger_s` (how long a room stays
"occupied" after the last signal). Example:
```json
"devices": {
  "fp300_living":     "living_room",
  "snzb_hall_down":   "hall_down",
  "snzb_wc_down":     "wc_down",
  "snzb_bath_up":     "bath_up",
  "snzb_bedroom_seela":  "bedroom_seela",
  "snzb_bedroom_aarni":  "bedroom_aarni",
  "snzb_bedroom_adults": "bedroom_adults"
}
```
Room keys **must match** `LIGHT_ROOM` in `scripts/lights_optimizer.py`. Rooms
with no device simply have no presence (the optimizer holds them comfort-first),
so partial coverage is fine.

### 5. Start the Presence Engine
```bash
docker compose up -d presence
```
(Both containers now start with a plain `docker compose up -d` — no profile flag.)

### 6. Verify
```bash
# normalized per-room state on MQTT:
mosquitto_sub -h freenas.kherrala.fi -t 'presence/#' -v
# and in InfluxDB:
influx query 'from(bucket:"building_automation")|>range(start:-10m)|>filter(fn:(r)=>r._measurement=="presence")|>last()'
```
Walk into a room → its `presence/<room>` flips `occupied:true`; leave and after
`linger_s` it flips false. The optimizer picks it up automatically on its next
tick — no restart needed.

---

## Normalized event model

The Presence Engine publishes to `presence/<room>` (retained) and writes the
`presence` InfluxDB measurement (tags `room`, `source`; fields `occupied` 0/1,
`confidence`, `illuminance`, `battery`):
```json
{ "room": "living_room", "occupied": true, "confidence": 0.95,
  "source": "fp300_living", "illuminance": 40, "battery": 100, "ts": 1784... }
```
Consumers **never** see vendor payloads — add a new sensor brand later and only
the engine's normalization changes.

---

## How the optimizer uses it (already wired)

Each light has a physical **room** (`LIGHT_ROOM` in `scripts/lights_optimizer.py`);
its category defines the behaviour. Once a room has presence:

| Room group | With presence installed |
|---|---|
| Living room (54/55/19 → `living_room`, FP300) | auto-on when dark+present; **vacancy-off when the FP300 says empty** (kitchen 8/40 stay on `living_core`/CO₂, so a vacant living room can't kill kitchen lights) |
| Halls / stairs (PIR) | motion + dark → on; off shortly after vacant |
| WC / bathroom (PIR) | motion → on; off after vacant (bath uses a long `linger_s` so a still shower isn't cut) |
| Bedrooms (PIR) | motion + dark → on; off when vacant/overnight *(set the `bedroom` category `auto_on=False` in the code if you don't want ceilings coming on at night)* |
| Office (future FP300) | dark + present → on; only off when away (never mid-work) |
| Theater (future FP300) | **never auto-on** (manual mood); mmWave only prevents wrong auto-off during a movie |

The engine owns the vacancy *timing* (per-room `linger_s`); the optimizer adds
only a small `VACANCY_GRACE_MIN` (90 s) on-time floor to bridge the
switch-on-before-sensor race, plus the global `MIN_DWELL_SECONDS`.

---

## Tuning

- **`config/presence_rooms.json`** — `linger_s` per room (raise for rooms where
  people sit still without a mmWave sensor; the `bath_up` default is 600 s).
  Hot-reloaded.
- **SNZB-03PR2 occupancy timeout / illuminance reporting** — set per-device in
  the Z2M frontend or `zigbee2mqtt/configuration.yaml`.
- **Bedroom auto-on** — enabled by default (the plan's "motion-triggered
  lighting"). If a ceiling light coming on when someone stirs at night is
  unwanted, flip `bedroom` `auto_on` to `False` in `CATS`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| zigbee2mqtt crash-loops | `SLZB_HOST` wrong/unreachable, or coordinator not in Zigbee2MQTT/TCP mode. `nc -z <ip> 6638`. |
| FP300 won't pair | It's still in Thread mode — switch to Zigbee mode first. |
| Device pairs but no `presence/<room>` | friendly name not in `config/presence_rooms.json` `devices`, or room key typo. |
| Room never goes vacant | `linger_s` too high, or a PIR re-triggering; check `zigbee2mqtt/<device>` occupancy in the frontend. |
| Optimizer ignores presence | room key in config ≠ `LIGHT_ROOM`; check the `presence` measurement has that room. |

---

## Future expansion

Add an FP300 to the office/theater; Zigbee contact sensors for doors; fuse
`presence` with Ruuvi BLE, Yale, UniFi, time-of-day and `illuminance` into a
richer occupancy model — all downstream of the same `presence` measurement, with
no optimizer change.
