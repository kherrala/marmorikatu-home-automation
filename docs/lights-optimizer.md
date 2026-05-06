# Lights Optimizer

Auto-off and limited auto-on rules for the WAGO PLC lighting system.
Implementation: `scripts/lights_optimizer.py`. Decisions log to InfluxDB
measurement `lights_optimizer` (tags: `light_id`, `light_name`, `category`;
fields: `decision`, `reason`, `on_duration_min`, `dry_run`).

## Tick

Runs every `CHECK_INTERVAL` (default 60 s):

1. Fetch every primary light's current `is_on` state from `lights` measurement.
2. Compute `house_occupied` from three signals over `OCCUPANCY_WINDOW_MIN`
   (default 30 min): wall-switch press, light-on transition, kitchen Ruuvi
   CO₂ rise (`co2_recently_elevated`).
3. Compute today's sunrise / sunset (local astral library).
4. Run **special-case blocks** (front porch, sauna laude LED, CO₂-auto kitchen
   + living room) — each can publish on/off regardless of current state.
5. Run **per-light evaluation loop** for all other primary lights using their
   policy from `LIGHT_POLICY`.

The special-case lights (`porch_idx=47`, `SAUNA_LAUDE_IDX=4`,
`CO2_AUTO_MANAGED={40, 54}`) are explicitly skipped inside the per-light loop.

## Policies (`LIGHT_POLICY`)

```
Policy(auto_off_after_sunrise_min,
       auto_off_when_unoccupied,
       auto_off_after_on_duration_min,
       auto_off_after_midnight,
       min_hold_after_manual_min)
```

| Policy | Behaviour |
|---|---|
| `toilet` | Forced off after `TOILET_TIMEOUT_MIN` since manual on; midnight cleanup. |
| `staircase` | Sunrise grace + occupancy + duration cap + midnight off. |
| `bedroom` | Occupancy off (workday rule); midnight off; longer manual hold (30 min). |
| `kitchen` | Sunrise grace + occupancy + midnight off. |
| `livingroom` | Same as kitchen. |
| `general` | Same as kitchen, used for hallways, outdoor power, etc. |
| `manual_only` | Never auto-managed. Long manual hold (60 min). |
| `porch_schedule` | Hard-coded ON between `sunset` and `PORCH_OFF_HOUR` (default 22:00 local). Front porch (idx 47) only. |

`ABSENCE_EXEMPT_INDICES` is currently empty; it remains as a hook for any
future light moved back into a policy that respects occupancy.

## Special-Case Light Blocks

### Front Porch (idx 47, `porch_schedule`)

`Sisäänkäynti` — front-porch light. ON between `sunset` and `PORCH_OFF_HOUR`
(default 22:00 local), OFF otherwise. Idempotent — only publishes when state
disagrees with target. (Idx 48 `Ulkovalo terassi` is `manual_only` — the
back-yard terrace light is no longer on a schedule.)

### Sauna Laude LED (idx 4)

`Saunan laude ledi` follows the Ruuvi `Sauna` temperature with hysteresis:

- ON when 5-min mean sauna temp ≥ `SAUNA_LAUDE_ON_C` (default **55 °C**).
- OFF when 5-min mean sauna temp ≤ `SAUNA_LAUDE_OFF_C` (default **50 °C**).
- HOLD between 50–55 °C — prevents flapping when löyly causes brief
  temperature dips during active use.

Reason strings: `sauna_heated_to_57.3C`, `sauna_cooled_to_49.8C`,
`hysteresis_hold_52.1C`, `no_sauna_temp_data`.

Hard-coded because `manual_only` policy lets the LED stay on indefinitely
after a session — observed once for ~24 h.

### CO₂-Driven Kitchen + Living Room (idx 40, 54)

Auto-on/off for `Keittiö kattovalo` (40) and `Olohuone kattovalo` (54)
based on the kitchen Ruuvi CO₂ sensor (`sensor_name="Keittiö"`).

#### Dark window

Eligibility for auto-on requires `astral.sun.elevation()` to be below
`SUN_DARK_ELEVATION_DEG` (default **8°**). This fires roughly 30–60 min
either side of horizon crossing depending on latitude/season.

- **Morning** (hour < 12 AND dark): only **idx 40** is eligible.
- **Evening** (hour ≥ 12 AND dark): **both 40 and 54** are eligible.
- Daytime (sun elevation ≥ threshold): no auto-on.

#### CO₂ classification (`co2_signal_class`)

```
recent   = mean over last 5 min   (Keittiö CO₂)
baseline = mean over [-2 h, -1 h] (anchored far enough back that recent
                                   doesn't pull the baseline up with it)

ELEVATED if recent ≥ baseline + CO2_AUTO_ON_DELTA_PPM   (default +20)
       OR recent ≥ CO2_AUTO_ON_ABSOLUTE_PPM              (default 580)
DROPPED  if recent ≤ baseline - CO2_AUTO_OFF_DELTA_PPM   (default 50)
       OR recent ≤ CO2_AUTO_OFF_ABSOLUTE_PPM             (default 500)
BASELINE otherwise
UNKNOWN  if recent missing
```

The 1–2 h baseline window is critical: with a sliding 30-min baseline a
slow occupancy ramp would drift both windows together and the delta would
stay near zero. The absolute-fallback thresholds catch sustained occupancy
without needing a baseline (also handles cold-start after restart when the
2 h-ago window has no data).

#### Auto-on / off / dismissal

- **Auto-on**: dark + eligible + ELEVATED + not dismissed today →
  publish ON. Reason: `co2_occupancy_morning` or `co2_occupancy_evening`.
- **Auto-off**: light is on AND (DROPPED OR `in_after_midnight_window`).
  Reason: `co2_no_occupancy` or `after_midnight`.
- **Dismissal detection**: only fires after the publish has been
  *confirmed* by the relay. Tracked via `_co2_auto_on_confirmed[idx]`,
  set true once `is_on=1` is observed within the
  `_CO2_PUBLISH_GRACE_SECONDS` (default 90 s) window after publish. If
  the relay never confirms within the grace window, the auto-on is
  silently retried — preventing a hardware failure (unresponsive PLC
  output) from being misread as a user dismissal.
- Confirmed-on then off → mark `_co2_dismissed_date[idx] = today`,
  suppress re-enable until next local date.
- A `time.sleep(0.3)` between successive publishes within the same tick
  reduces the chance the PLC's MQTT command handler drops one command
  while still busy with the previous.

## Tunable environment variables

| Var | Default | Purpose |
|---|---|---|
| `CHECK_INTERVAL` | 60 (s) | Tick period |
| `SUNRISE_GRACE_MIN` | 60 | Used by `kitchen`/`livingroom`/`general` policies |
| `WORKDAY_START_HOUR` / `WORKDAY_END_HOUR` | 9 / 16 | Bedroom workday-occupancy rule |
| `OCCUPANCY_WINDOW_MIN` | 30 | `house_occupied` lookback |
| `LONG_ABSENCE_MIN` | 120 | `long_unoccupied` lookback |
| `CO2_OCCUPANCY_DELTA_PPM` | 30 | Old `co2_recently_elevated` threshold |
| `MANUAL_HOLD_MIN` | 15 | Default min-hold-after-manual |
| `BEDROOM_HOLD_MIN` | 30 | Bedroom min-hold-after-manual |
| `PORCH_OFF_HOUR` | 22 | Front-porch schedule end (local). `TERRACE_OFF_HOUR` is accepted as a fallback for backwards compatibility. |
| `SUN_DARK_ELEVATION_DEG` | 8 | Dark threshold for CO₂ auto-on |
| `CO2_AUTO_ON_DELTA_PPM` | 20 | ELEVATED delta threshold |
| `CO2_AUTO_ON_ABSOLUTE_PPM` | 580 | ELEVATED absolute fallback |
| `CO2_AUTO_OFF_DELTA_PPM` | 100 | DROPPED delta threshold (stricter than ON to provide hysteresis) |
| `CO2_AUTO_OFF_ABSOLUTE_PPM` | 450 | DROPPED absolute fallback (close to outdoor ~420) |
| `CO2_AUTO_MIN_ON_SECONDS` | 1200 | Minimum on-time before auto-off can fire (20 min) |
| `SAUNA_LAUDE_ON_C` | 55 | Sauna LED on threshold |
| `SAUNA_LAUDE_OFF_C` | 50 | Sauna LED off threshold |
| `DRY_RUN` | 0 | Set to 1 to log decisions without publishing |
