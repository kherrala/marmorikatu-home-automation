# Lights Optimizer (v2)

Comfort-first, provenance-aware auto on/off for the WAGO PLC lighting system.
Implementation: `scripts/lights_optimizer.py`. Decisions log to InfluxDB
measurement `lights_optimizer` (tags `light_id`, `light_name`, `category`;
fields `decision`, `reason`, `manual_locked`, `on_duration_min`, `dry_run`).

## Design principles

1. **Never fight an active user.** A light a human turned on — wall switch,
   mobile app, or voice/MCP — is held. The optimizer only ever turns a light
   *off* on a **high-confidence** cull.
2. **Comfort auto-on** in the dark for the rooms the family lives in.
3. **Energy savings only from high-confidence culls:** daylight waste on
   window/outdoor/decorative lights, whole-house-away, deep-night overnight, and
   duration caps on transient rooms. Lighting is a small share of house energy;
   the optimizer's job is comfort + zero-annoyance waste elimination.

## Command provenance (the core fix)

The PLC, the mobile app, and voice/MCP all drive lights by publishing
`marmorikatu/light/<idx>/set` = `true`/`false`. That payload is consumed by an
out-of-repo WBM Native-MQTT→`Controls[]` BOOL binding that accepts **only** the
bare `true`/`false` (enriching it with a source field was tested live and
**rejected** — see [docs/plc-command-channel.md](plc-command-channel.md); commands also actuate
~12–13 s later). So provenance travels on a **side-channel** topic instead:

- Every software controller also publishes `marmorikatu/light/<idx>/command` =
  `{"on":bool,"src":"optimizer|mobile|mcp|voice","ts":…}` beside its unchanged
  `/set` (`lights_optimizer.publish_command_breadcrumb`,
  `mcp_tools/lights.py`, and the mobile app).
- `plc_mqtt_subscriber` records these as the **`light_command`** measurement
  (tags `light_id`, `light_name`, `source`; field `is_on`).
- The optimizer's `classify_origin(idx, is_on, since)` attributes a
  `lights/is_on` transition to the breadcrumb landing within
  `[since − CMD_CORRELATION_LEAD_S, since + CMD_CORRELATION_LAG_S]`. A
  transition with **no** matching breadcrumb is a **physical wall press**. Result:
  `optimizer` | `human` (mobile/mcp/voice) | `wall`. Both human and wall count as
  a manual action the optimizer must not fight.

## Presence (Core C)

The optimizer consumes a **normalized per-room occupancy** signal — the
`presence` measurement / `presence/<room>` written by the separate Presence
Service project (`memory/presence_architecture.md`): `{room, occupied,
confidence, source}`. It never talks to individual sensors.

Until a room's sensor lands, it degrades gracefully to **interim signals**:

- `living_core` occupancy ← kitchen Ruuvi CO₂ (`co2_signal_class`, below).
- whole-house away ← the legacy **activity heuristic** (`activity_recent` over
  `LONG_ABSENCE_MIN`). BLE advertiser-count (`ble_present_count`) is **opt-in and
  off by default** (`BLE_AWAY_ENABLED`): an always-on, MAC-rotating Samsung
  SmartTag in the basement never lets the count reach zero, so raw BLE is not a
  reliable occupancy signal here.
- darkness ← astronomical sun elevation (`SUN_DARK_ELEVATION_DEG`, 8°).

When the Presence Service publishes `occupied` for a room, the **presence
overlay** activates with no rule change: transit areas (PIR) get motion-on +
short vacancy-off; stay-still rooms (mmWave) hold while occupied and only off
after confirmed vacancy. Gated by `PRESENCE_MIN_CONFIDENCE`.

## Tick

Runs every `CHECK_INTERVAL` (default 60 s):

1. Read every primary light's `is_on` (`fetch_current_light_states`).
2. Compute darkness (sun elevation) and `whole_house_away` (BLE / activity).
3. Run **special blocks**: front porch (idx 47), sauna laude LED (idx 4),
   post-sauna cooldown (idx 1/38/39).
4. `detect_dismissals` — a light we auto-on'd that a human then turned off is
   marked dismissed-until-tomorrow.
5. Per-light **category evaluation** for every other light.

Idempotent: a light is only commanded when its desired state differs from the
observed state, and never reversed within `MIN_DWELL_SECONDS` of our own last
command (`within_min_dwell`) — a hard floor against flapping that sits above the
~13 s PLC latency. Restart-deterministic: `rebuild_state` reconstructs today's
dismissals from the persisted `light_command` + `lights` history on boot.

## Behaviour categories

Every light index maps to exactly one category (`CATEGORY_OF`); each category's
behaviour is a `Cat(auto_on, daylight_off, overnight_off, away_off,
duration_cap_min, manual_hold_min, presence_room, presence_kind)`. Auto-OFF only
fires for the flags set — an unset flag means that cull never happens for the room.

| Category | Lights (idx) | Auto-on | Auto-off criteria |
|---|---|---|---|
| **living** | 8,19,40,54,55 | dark + occupied (CO₂/presence), dismissable | away, or overnight-if-forgotten. **Never** daytime/occupancy off. |
| **window** | 18,20,23,24,30,32,41,46 | — | daylight (sun up), overnight, away |
| **accent** (LED strips) | 2,3,5,6,7 | — | overnight, away |
| **circulation** | 25,26,35,37,42 | presence-gated (deferred) | duration cap (25 min), overnight, away |
| **utility/closet** | 31,36,39*,43,53,56,61 | — | duration cap (30 min), overnight, away |
| **toilet** | 29,34,44,45,52 | — | duration cap (30 min) only. No overnight-kill mid-use. |
| **bedroom** | 22,28,33 | — | overnight, away. **No daylight-off** (nap-safe). |
| **office** | 17 | — | away only (never daytime/overnight — must survive Zoom calls) |
| **theater** | 49,50,51 | — | away only (never off during a movie) |
| **outdoor** | 48,59,60 | — | daylight, overnight. **No occupancy-off** (terrace users read as away indoors). |

`*` idx 39 (Tekninen tila) is categorized `utility` but is also a post-sauna
special light (handled by the sauna block, skipped in the category loop).

### Does the optimizer ever turn things off? Yes — a lot.

Human provenance does **not** globally veto auto-off. The **high-confidence culls
above fire regardless of who switched the light on** — a window light gets a
`daylight_off` even if you flipped it, a forgotten hall light gets `overnight_off`,
a toilet/closet gets its `duration_cap`, and *everything* gets `away_off` when the
house is empty. (In the live test, the optimizer turned off a basement WC a human
had switched on, via `duration_cap`, while leaving the occupied kitchen and living
room alone.)

What comfort-first actually restricts is narrow: the **living / office / theater**
categories simply have *no* daytime or occupancy off-rule, so you're never plunged
into darkness while using a room — but they still go off on `away_off` (and living
on `overnight_off` if forgotten). The one thing provenance truly *prevents* is the
**re-fight**: if the optimizer auto-on'd a light and you turn it off, it is marked
dismissed and won't turn it back on today (`detect_dismissals`). `manual_locked` is
recorded on every decision for observability.

### Overnight cull (gentle)

`in_overnight_window` = `OVERNIGHT_START` (00:30) ≤ now < `OVERNIGHT_END_HOUR`
(06:00). Applies to categories with `overnight_off=True`. A light turned on
*during* the window (on-since ≥ window start — night bathroom, up-late kid) is
**protected** (plus min-dwell), and an occupied room (real presence) is never
culled.

## Special-case blocks

### Front porch — idx 47 (Sisäänkäynti)
The optimizer is the **sole controller** — no other service writes this light.
**No dusk auto-on** (removed by request). It lights the porch while a Unifi
person-detection hold (`light_override.hold_until`, written by the `unifi-webhook`
as a pure *signal* — its `light_request` action, no direct `/set`) is active
(`porch_detection`), and turns it off when the hold expires — but **only if the
optimizer lit it** (`classify_origin == "optimizer"` → `porch_detection_ended`),
so a manual porch-on is never touched. A user turning it off during a detection
is respected (`detection_dismissed`, not re-lit). Daylight-off if left on into
daylight. This replaced the old two-writer setup where the webhook drove the
light directly.

### Sauna laude LED — idx 4 (Saunan laude ledi)
Hysteresis on the Ruuvi `Sauna` temperature: on ≥ `SAUNA_LAUDE_ON_C` (55 °C),
off ≤ `SAUNA_LAUDE_OFF_C` (50 °C), hold in the 50–55 °C dead-band.

### Post-sauna cooldown — idx 1, 38, 39
Once the sauna peaked > `SAUNA_AFTER_PEAK_C` (55 °C) and has been <
`SAUNA_AFTER_OFF_C` (40 °C) for ≥ `SAUNA_AFTER_DELAY_MIN` (30 min), turn these
manual-only lights off — unless one was pressed recently (`MANUAL_HOLD_MIN`
grace), so a fresh shower isn't cut short.

## CO₂ classification (`co2_signal_class`)

Interim living-core occupancy from the kitchen Ruuvi (`sensor_name="Keittiö"`,
`co2`):

```
recent   = mean over last 5 min
baseline = mean over [-2 h, -1 h]  (widens to [-6 h, -1 h] on cold start)

ELEVATED if recent ≥ baseline + CO2_AUTO_ON_DELTA_PPM   (20)  OR recent ≥ CO2_AUTO_ON_ABSOLUTE_PPM  (580)
DROPPED  if recent ≤ baseline − CO2_AUTO_OFF_DELTA_PPM  (100) OR recent ≤ CO2_AUTO_OFF_ABSOLUTE_PPM (450)
BASELINE otherwise    UNKNOWN if recent missing
```

## Decision reason vocabulary

`auto_on_comfort`, `daylight_off`, `overnight_off`, `away_off`, `vacancy_off`,
`duration_cap`, `manual_hold`, `no_off_rule`, `min_dwell_hold`,
`dismissed_today`, `porch_dark_schedule` / `porch_hold` / `porch_already_correct`,
`sauna_heated_to_XC` / `sauna_cooled_to_XC` / `hysteresis_hold_XC` /
`no_sauna_temp_data`, `post_sauna_cooled_Nmin_ago` / `post_sauna_manual_grace`,
`mqtt_publish_failed`. (The `announcer` keys on these reason strings.)

## Tunable environment variables

| Var | Default | Purpose |
|---|---|---|
| `CHECK_INTERVAL` | 60 | Tick period (s) |
| `SUN_DARK_ELEVATION_DEG` | 8 | Darkness threshold (porch + auto-on) |
| `SUNRISE_GRACE_MIN` | 60 | Daylight-off only fires after sunrise+grace |
| `MANUAL_HOLD_MIN` | 90 | Grace after a human-on (living/accent/office) |
| `BEDROOM_HOLD_MIN` | 30 | Bedroom manual grace |
| `SHORT_HOLD_MIN` | 5 | Grace for window/transient categories |
| `TOILET_TIMEOUT_MIN` | 30 | Toilet duration cap |
| `CIRCULATION_TIMEOUT_MIN` | 25 | Hall/stair duration cap |
| `UTILITY_TIMEOUT_MIN` | 30 | Closet/utility duration cap |
| `OVERNIGHT_START_HOUR`/`_MIN` | 0 / 30 | Overnight cull start (local) |
| `OVERNIGHT_END_HOUR` | 6 | Overnight cull end (local) |
| `LONG_ABSENCE_MIN` | 180 | Legacy away lookback (BLE-absent fallback) |
| `AWAY_CONFIRM_MIN` | 15 | BLE away confirmation |
| `BLE_RSSI_INSIDE` | −80 | Min RSSI to count a BLE device as inside |
| `BLE_WINDOW_MIN` | 5 | BLE presence window |
| `MIN_DWELL_SECONDS` | 300 | Never reverse our own command within this |
| `PRESENCE_MIN_CONFIDENCE` | 0.6 | Confidence gate on normalized presence |
| `ROOM_VACANCY_MIN` | 12 | mmWave stay-still vacancy-off |
| `TRANSIT_VACANCY_MIN` | 4 | PIR transit vacancy-off |
| `BATH_VACANCY_MIN` | 15 | Bathroom vacancy-off (still-shower safe) |
| `CO2_AUTO_ON_DELTA_PPM` / `_ABSOLUTE_PPM` | 20 / 580 | ELEVATED thresholds |
| `CO2_AUTO_OFF_DELTA_PPM` / `_ABSOLUTE_PPM` | 100 / 450 | DROPPED thresholds |
| `PORCH_OFF_HOUR` | 23 | Front-porch evening window end (local) |
| `SAUNA_LAUDE_ON_C` / `_OFF_C` | 55 / 50 | Laude LED hysteresis |
| `SAUNA_AFTER_PEAK_C`/`_OFF_C`/`_DELAY_MIN`/`_LOOKBACK_H` | 55 / 40 / 30 / 6 | Post-sauna detection |
| `CMD_CORRELATION_LEAD_S` / `_LAG_S` | 40 / 10 | Command↔transition attribution window |
| `DRY_RUN` | 0 | Log decisions without publishing |

## Rollout

Run `DRY_RUN=1` in shadow first and review the `lights_optimizer` decision log
(Grafana / MCP `get_lights_optimizer_status`) — there should be **no** `*_off`
on living/office/theater during awake hours — before enabling actuation.
