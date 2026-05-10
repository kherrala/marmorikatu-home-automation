# Kiosk Announcer

Backend service (`announcer`) that mines existing decision/data logs for
state changes worth surfacing, and pushes Finnish-language announcements to
the kiosk over a Server-Sent Events (SSE) channel hosted by `claude-bridge`.
The kiosk speaks them via the existing Piper TTS path **without** requiring
a face-detection greeting, so freezing alarms, sauna-on, expensive-electricity
periods, lights_optimizer auto-offs and air-quality changes are heard live.

## Components

- **`scripts/announcer.py`** — polls InfluxDB every `ANNOUNCE_POLL_INTERVAL`
  seconds (30 s by default), detects edges/transitions, formats a Finnish
  sentence, and POSTs `{text, kind, priority, key, ts}` to
  `BRIDGE_PUSH_URL` (= `http://claude-bridge:3002/announcements/push`).
- **`scripts/claude_bridge.py`** — adds two endpoints:
  - `POST /announcements/push` — internal ingress from the announcer
    service (token-gated by `ANNOUNCE_PUSH_TOKEN` if set).
  - `GET /announcements/stream` — SSE feed for the kiosk. Each connected
    kiosk gets its own queue; a 32-event ring buffer plus `Last-Event-ID`
    handling lets a reconnecting client replay anything it missed.
- **`kiosk/src/announcements/announcer.ts`** — `EventSource` client that
  enqueues incoming events and speaks them when the kiosk is idle. Enforces
  quiet hours and the morning digest replay.

## Event sources

The announcer mines the same measurements the dashboards already rely on —
no new instrumentation required.

| Event class                | Source                                                              | Default priority |
|----------------------------|---------------------------------------------------------------------|------------------|
| HVAC freezing alarm        | `alarm.Alarm_freezing_danger` rising edge                           | 0 (critical)     |
| Other HVAC alarm flags     | `alarm.Alarm_filter_guard`, `Alarm_efficiency`, fan failures, …    | 0–1              |
| Sauna state                | `ruuvi.temperature` for sensor `Sauna` — heating / hot / cooling / off | 1–2          |
| Spot-price tier transition | `heating_optimizer.tier` (CHEAP / NORMAL / EXPENSIVE / PRE_HEAT)    | 1–2              |
| Lights-optimizer decisions | `lights_optimizer` (auto-off, sauna-laude, CO₂ auto, post-sauna, porch) | 1–2          |
| CO₂ class transition       | `ruuvi.co2` per sensor → good / elevated / high / very_high         | 1–2              |
| PM2.5 class transition     | `ruuvi.pm2_5` per sensor → good / elevated / high                   | 1–2              |
| Raw light on/off           | `lights.is_on` rising/falling edge                                  | 3 (debug)        |

### Priority tiers

| Priority | Meaning   | Examples                                                  |
|----------|-----------|-----------------------------------------------------------|
| 0        | Critical  | Freezing alarm, fan-failure, ylikuumeneminen              |
| 1        | Normal    | Sauna ready, expensive period starts, kitchen auto-on     |
| 2        | Verbose   | Tier returned to NORMAL, CO₂ elevated, porch on/off       |
| 3        | Debug     | Every individual light turn-on / turn-off                 |

`ANNOUNCE_VERBOSITY` drops anything with `priority > VERBOSITY` at the source.
Default is **3 (initial rollout — surface everything)**; lower it to **1** or
**2** once the noise level is acceptable. Per-event class additionally has
its own per-key cooldown (60 s – 15 min) inside `announcer.py` so a flapping
sensor can't blast the kiosk every tick.

## Quiet hours and the morning digest

The kiosk side (not the backend) enforces quiet hours — defaults are
**22:00–07:00 local time**. During quiet hours:

- live announcements are **not** spoken,
- they are dropped into a per-day digest, deduplicated by `key`, keeping the
  highest-priority entry.

When the local clock crosses out of quiet hours **and** the kiosk reaches an
idle state (READY / COOLDOWN / DASHBOARD_ONLY, not speaking, not listening,
not processing), the top **3** items from the digest play out as a single
combined utterance prefixed with *"Yön aikana tapahtui:"* (or *"Yön aikana N
tapahtumaa, joista tärkeimmät:"* if the digest had more than 3 entries).

The "played for date" flag persists in `localStorage` (`announcer.digestDate`)
so a page reload doesn't replay the morning digest.

## Configuration (announcer service)

| Env var                         | Default                                            | Notes                                          |
|---------------------------------|----------------------------------------------------|------------------------------------------------|
| `INFLUXDB_URL`                  | `http://influxdb:8086`                             | Inside docker network                          |
| `INFLUXDB_TOKEN/ORG/BUCKET`     | `wago-secret-token` / `wago` / `building_automation` |                                            |
| `BRIDGE_PUSH_URL`               | `http://claude-bridge:3002/announcements/push`     | Where to POST events                           |
| `ANNOUNCE_PUSH_TOKEN`           | *(unset)*                                          | If set, must match on bridge side too          |
| `ANNOUNCE_VERBOSITY`            | `3`                                                | 0=critical only, 3=every light                 |
| `ANNOUNCE_POLL_INTERVAL`        | `30`                                               | Seconds between InfluxDB polls                 |
| `ANNOUNCE_MAX_PER_TICK`         | `5`                                                | Cap on events pushed per poll cycle            |
| `ANNOUNCE_CO2_ELEVATED/HIGH/VERY_HIGH` | `800 / 1100 / 1500` ppm                     | CO₂ class thresholds                           |
| `ANNOUNCE_PM25_ELEVATED/HIGH`   | `12 / 35` µg/m³                                    | PM2.5 class thresholds                         |
| `ANNOUNCE_SAUNA_HEATING_C`      | `45`                                               | Sauna temp → state=heating                     |
| `ANNOUNCE_SAUNA_HOT_C`          | `70`                                               | Sauna temp → state=hot                         |
| `ANNOUNCE_SAUNA_OFF_C`          | `40`                                               | Sauna temp → state=off                         |

### Bridge-side env

| Env var                    | Default | Notes                                                   |
|----------------------------|---------|---------------------------------------------------------|
| `ANNOUNCE_PUSH_TOKEN`      | *(unset)* | If set, `/announcements/push` requires `X-Announce-Token` header |
| `ANNOUNCE_RING_SIZE`       | `32`    | Replay ring-buffer size                                 |
| `ANNOUNCE_KEEPALIVE_SEC`   | `20`    | SSE comment heartbeat (must be < nginx `proxy_read_timeout`) |

### Kiosk-side constants

`kiosk/src/announcements/announcer.ts` — top of file:

- `QUIET_START_HOUR = 22`, `QUIET_END_HOUR = 7`
- `DIGEST_MAX = 3`
- `LIVE_QUEUE_MAX = 12` (oldest non-critical events drop first when full)

## Operations

```bash
# Build + restart just the announcer (after editing announcer.py)
docker compose up --build -d announcer claude-bridge

# Tail announcer + bridge to watch events flow
docker compose logs -f announcer claude-bridge

# Manually push a test announcement (verifies the SSE wiring end-to-end)
docker compose exec claude-bridge curl -s -X POST \
  -H 'Content-Type: application/json' \
  -d '{"text":"Tämä on testikuulutus.","kind":"test","priority":1}' \
  http://localhost:3002/announcements/push

# Watch the SSE stream as the kiosk would
docker compose exec claude-bridge curl -N \
  http://localhost:3002/announcements/stream
```

## Tuning the noise level

1. Run with `ANNOUNCE_VERBOSITY=3` for a few days; note which event classes
   feel noisy or repetitive.
2. To suppress a whole class, drop the priority assigned in `announcer.py`
   (e.g., move CO₂-elevated from priority 2 → 3, then run with verbosity 2).
3. To shorten the announcement burst on price changes, raise the per-event
   cooldown via the `min_gap_s` argument in the relevant `emit(...)` call.
4. To gate by user preference, raise `ANNOUNCE_VERBOSITY` to 1 once the user
   only wants the must-hear set (alarms, sauna, expensive starts, auto-offs).
