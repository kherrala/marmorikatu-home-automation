# Room lighting controller

The `lights-optimizer` service controls WAGO PLC lights using room occupancy
and brightness. Implementation: `scripts/lights_optimizer.py`; room policy:
[lighting-policy-by-room.md](lighting-policy-by-room.md).

## One decision policy for sensor rooms

`decide_room_light()` is a pure decision function with these rules:

1. **Occupied and dim:** turn on the room's permitted automatic lights.
2. **Confirmed vacant:** turn off the room's lights, after any manual-ON grace.
3. **Bright daylight:** turn off optimizer-lit lights in rooms with a calibrated
   brightness threshold, even while occupied. A manual ON prevents this shutoff.
4. **Manual OFF:** suppress automatic relighting until confirmed vacancy ends
   that occupancy session.
5. **Unknown occupancy:** do not auto-on or infer vacancy. Missing data never
   enables a duration, overnight, or away-based shutoff. An independently valid
   daylight threshold can still turn off an optimizer-lit light.
6. **Manual ON:** keep the light ON for at least 10 minutes, even if a PIR reports
   no movement. After that, confirmed vacancy may switch it OFF. Continued
   occupancy keeps it ON; manual ON still protects against daylight shutoff.

There is no CO₂, BLE, switch-activity, or whole-house-away heuristic. Sensor rooms
have no competing overnight schedules or maximum visit durations. Rooms mapped
in `LIGHT_ROOM` follow this policy even before their sensor is installed: missing
presence holds their state, subject only to an explicitly configured daylight rule.

### Occupancy and vacancy timing

The separate [Presence Engine](presence-setup.md) combines sensor inputs and owns
vacancy timing. The optimizer reads its normalized `presence.occupied` and
`confidence`, not individual Zigbee device payloads. Missing readings within the
10-minute query window or confidence below `PRESENCE_MIN_CONFIDENCE` give unknown.

Kitchen and living room share one occupancy zone, including Ruokailu:

- Either the kitchen PIR or living-room FP300 occupied → occupied.
- Both confidently vacant → vacant.
- Otherwise → unknown.

The downstairs hall PIR is not part of this zone. Each physical room still uses
its own illuminance reading. The living FP300 combines radar presence and PIR
motion; either positive holds occupancy. Both must stay clear for 300 seconds
before vacancy. Its 7200-second linger is a dead-sensor failsafe, not a departure
countdown.

PIR vacancy grace starts after the device's explicit false: halls 90 seconds,
KHH/kitchen 180 seconds, downstairs WC/upstairs bathroom 900 seconds. The sensor's detection
duration is additional. A new positive cancels the pending vacancy immediately.
These values live in `config/presence_rooms.json`. The 15-minute WC/bathroom
grace allows someone to sit still; after departure, lights may stay ON for that
period. The basement has no presence sensors: manual ON, scheduled forgotten-light OFF only.

### Brightness and command timing

Automatic ON requires astronomical darkness (sun elevation below 8°) **or** a
room illuminance mean below its threshold. Windowless WC lights 44/45 bypass
the brightness gate. Lux is averaged over four minutes:

| Physical room | ON below | Occupied daylight OFF above |
|---|---|---|
| Olohuone / Ruokailu | 80 lux | 200 lux, optimizer-lit ceiling/dining lights only |
| KHH | 40 lux | Disabled |
| Other sensor rooms, including kitchen | 40 lux | Disabled |

The wide ON/OFF gap prevents immediate relighting after daylight shutoff. The
OFF gate also requires sun elevation at least 8°, sunrise + 60 minutes to have
passed, and sunset not yet reached. Lamp brightness cannot trigger a night-time
shutoff. Rooms whose lamps dominate their own sensor do not get daylight OFF
until separately calibrated.

KHH's former 12-lux threshold blocked morning arrivals measured at 17–18 lux.
Only LED 6 auto-ons; ceiling 56 remains manual-on.

Two short timing guards cover PLC and sensor races:

- `MIN_DWELL_SECONDS=30`: do not issue another normal room command while our last
  command is still within the PLC actuation/state-broadcast round trip.
- `VACANCY_GRACE_MIN=1.5`: do not switch off a sensor-room light in its first 90
  seconds, while a wall press and presence reporting catch up. This floor also
  applies to daylight shutoff. It is not a second vacancy countdown.

## Manual actions and provenance

The PLC accepts only bare `true`/`false` at `marmorikatu/light/<idx>/set`.
Software controllers also publish a breadcrumb at
`marmorikatu/light/<idx>/command` containing `{"on":bool,"src":…,"ts":…}`.
The PLC subscriber records these as `light_command`. See
[plc-command-channel.md](plc-command-channel.md).

`classify_origin()` matches a light transition with a command within
`[transition − 40 seconds, transition + 10 seconds]`. Optimizer commands are
automatic; mobile/MCP/voice commands are human. An unmatched transition is
inferred to be a wall switch action.

A fresh human OFF on an automatic light creates a dismissal. Each OFF edge is
processed once; the old OFF state while an ON command is in flight cannot create
a new dismissal. Confirmed room/zone vacancy clears it. There is no timeout that
re-enables a manually darkened occupied room, and unknown occupancy cannot clear
it. Dismissals are in memory and reset on service restart.

Manual ON protects sensor rooms against all automatic shutoff for the first
`ROOM_MANUAL_HOLD_MIN` (10 minutes), measured from the observed ON transition.
This works for any wall/app/voice ON, including immediately correcting an
automatic OFF; no frustration detector or extra history state is needed. The
transition timestamp can be recovered after a restart. After the minimum,
confirmed vacancy may turn it OFF; measured-brightness shutoff remains blocked.
The explicit sensorless-light policies below are separate.
`manual_locked` in the decision log records manual provenance, not an absolute
veto of every OFF rule.

## Sensorless lights and special controls

Category settings are just `auto_on`, `daylight_off`, `overnight_off`, and
`duration_cap_min`. The latter three apply only to lights **without** a
`LIGHT_ROOM` mapping. Sensorless lights never auto-on from guessed occupancy.

| Lights | Automatic OFF policy |
|---|---|
| Window lights 18/20/23/24/30/32/41/46 | Daylight or forgotten overnight |
| Kitchen cabinet strips 2/7 | Forgotten overnight |
| Upstairs aula LED 3 | Forgotten overnight |
| Basement front/rear/store 49/50/53 | Forgotten lights OFF from 20:00; manual ON afterward protected |
| Basement billiard/WC 51/52 | Forgotten lights OFF from 00:30; manual ON afterward protected |
| Portaikko 42 | 25-minute duration cap or forgotten overnight |
| Closets/stores 31/36/43/61 | 30-minute duration cap or forgotten overnight |
| Terrace/carport/storage exterior 48/59/60 | Daylight or forgotten overnight |

Except for basement front/rear/store 49/50/53, “forgotten overnight” means ON
before 00:30 and still ON during 00:30–06:00.
A light switched on during that window is protected from the overnight rule;
its separate duration cap, if configured, still applies. Daylight means
sunrise + 60 minutes through sunset. These policies remain deliberate exceptions
for outputs without room sensors, not fallbacks when a sensor stops reporting.

The basement is a separate, sensorless workspace used for long workdays into
late evening. Front/rear/store outputs 49/50/53 use the `basement` category:
manual ON and a 20:00–06:00 forgotten-light OFF window. Billiard table 51 and
basement WC 52 use `secondary` with the later 00:30–06:00 window. A light
switched ON after its own cutoff is held for the rest of that night, including
after midnight for the 20:00 group. The next night can switch it OFF if still
forgotten. There are no presence mappings, daylight shutoffs, duration caps or
future-sensor placeholders. Upstairs activity cannot control basement lighting.

Special controls remain separate:

- **Front porch 47:** a Unifi person-detection `light_override` hold requests ON.
  When the hold ends, only an optimizer-lit porch is switched off; a manual ON
  remains on at night. A manual OFF during detection is respected. A manual
  porch light left on into daylight is switched off. The webhook applies the
  detection request's darkness condition; there is no dusk or off-hour schedule.
- **Sauna laude LED 4:** ON at sauna temperature ≥55°C, OFF at ≤50°C, hold between.
- **Post-sauna 1/38/39:** OFF after a sauna peak ≥55°C followed by at least 30
  minutes below 40°C, with 90 minutes of grace after a recent switch-on.
- **Disconnected output 55:** never commanded or logged.

## Tick and diagnostics

Each tick reads primary light states, updates transition history, reads sun and
presence, clears ended dismissals, runs special controls, detects fresh manual
OFF edges, and evaluates the normal lights. Expensive shared queries are memoized
within the tick. Compose uses a one-second interval; actual work and command
pacing can extend it.

Decisions are written to `lights_optimizer`: tags `light_id`, `light_name`,
`category`; fields `decision`, `reason`, `manual_locked`, `on_duration_min` when
ON, and `dry_run`. Unchanged HOLD decisions are deduplicated, with a 30-minute
heartbeat; commands always log. Useful room reasons:

- `auto_on_comfort`, `vacancy_off`, `bright_enough` explain actions.
- `not_dark`, `room_vacant`, `presence_unknown`, `dismissed_session`, `manual_only`
  explain why an OFF light stays OFF.
- `manual_hold`, `no_off_rule`, `min_dwell_hold` explain other holds.
- `daylight_off`, `overnight_off`, `duration_cap` belong to sensorless policies.
- Porch/sauna reasons remain unchanged; `mqtt_publish_failed` records failure.

## Tuning

Connection settings are in Compose. Main behavior settings:

| Variable | Code default | Purpose |
|---|---|---|
| `CHECK_INTERVAL` | 60 s (Compose: 1 s) | Delay between ticks |
| `SUN_DARK_ELEVATION_DEG` | 8° | Astronomical darkness |
| `SUNRISE_GRACE_MIN` | 60 min | Delay before daylight OFF is allowed |
| `DARK_LUX_THRESHOLD` | 40 lux | Default measured ON threshold |
| `ILLUMINANCE_WINDOW_MIN` | 4 min | Lux averaging window |
| `PRESENCE_MIN_CONFIDENCE` | 0.6 | Normalized presence confidence gate |
| `MIN_DWELL_SECONDS` | 30 s | Command round-trip guard |
| `VACANCY_GRACE_MIN` | 1.5 min | Initial ON floor for sensor rooms |
| `WINDOWLESS_LIGHTS` | 44,45 | Lights that bypass the ON brightness gate |
| `CIRCULATION_TIMEOUT_MIN` / `UTILITY_TIMEOUT_MIN` | 25 / 30 min | Sensorless duration caps |
| `OVERNIGHT_START_HOUR` / `OVERNIGHT_START_MIN` / `OVERNIGHT_END_HOUR` | 0 / 30 / 6 | Other sensorless overnight window; shared end hour |
| `BASEMENT_OFF_HOUR` | 20 | Front/rear/store cutoff; billiard/WC retain the 00:30 default |
| `ROOM_MANUAL_HOLD_MIN` | 10 min | Minimum ON time after a manual ON in sensor rooms |
| `MANUAL_HOLD_MIN` | 90 min | Post-sauna switch-on grace only |
| `SAUNA_LAUDE_ON_C` / `SAUNA_LAUDE_OFF_C` | 55 / 50°C | Laude hysteresis |
| `SAUNA_AFTER_PEAK_C` / `SAUNA_AFTER_OFF_C` / `SAUNA_AFTER_DELAY_MIN` / `SAUNA_AFTER_LOOKBACK_H` | 55°C / 40°C / 30 min / 6 h | Post-sauna detection |
| `CMD_CORRELATION_LEAD_S` / `CMD_CORRELATION_LAG_S` | 40 / 10 s | Command attribution window |
| `DISMISSAL_FRESH_S` | 120 s | Freshness limit for registering an OFF edge |
| `DRY_RUN` | 0 | Suppress MQTT actuation; Compose reads `LIGHTS_DRY_RUN` |

Per-room lux overrides are `ROOM_DARK_LUX` and `ROOM_BRIGHT_LUX` in code.
Vacancy timers belong in the Presence Engine's room configuration.

Before deployment, run the test suite and a `DRY_RUN=1` shadow tick against live
observations. Check unknown-presence holds, manual OFF sessions, occupied-daylight
shutoff, and next-visit auto-on. Restarting clears in-memory dismissals.
