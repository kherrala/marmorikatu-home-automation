#!/usr/bin/env python3
"""Room lighting controller.

Sensor rooms: occupied + dim -> on; confirmed vacancy -> off. Optional measured
bright-day shutoff applies only to optimizer-lit lights. Unknown occupancy holds
existing lights. Manual OFF suppresses auto-on until that occupancy session ends.

The Presence Engine owns sensor fusion and vacancy timers. Sensorless lights use
explicit daylight/overnight/timeout policies. Porch detection and sauna control
are separate. InfluxDB supplies observations/history and records decisions; MQTT
commands remain bare true/false with a separate provenance breadcrumb.
"""

import json
import logging
import math
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import paho.mqtt.publish as mqtt_publish
from astral import LocationInfo
from astral.sun import sunrise as sun_rise, sunset as sun_set, elevation as sun_elevation
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from health import touch_health
from light_labels import LIGHT_LABELS

# ── Configuration ─────────────────────────────────────────────────────────────
MQTT_BROKER = os.environ.get("MQTT_BROKER", "freenas.kherrala.fi")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "marmorikatu")

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

LOCAL_TZ = ZoneInfo(os.environ.get("LOCAL_TZ", "Europe/Helsinki"))
HOME_LAT = float(os.environ.get("HOME_LAT") or os.environ.get("WEATHER_LAT") or "61.4978")
HOME_LON = float(os.environ.get("HOME_LON") or os.environ.get("WEATHER_LON") or "23.7610")

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))
TICK_LOG_S = float(os.environ.get("TICK_LOG_S", "60"))   # throttle the per-tick log line
_last_tick_log = 0.0
_last_tick_state: bool | None = None
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("MAX_CONSECUTIVE_FAILURES", "5"))

# Darkness threshold (astronomical sun elevation, °). Shared by porch + auto-on.
SUN_DARK_ELEVATION_DEG = float(os.environ.get("SUN_DARK_ELEVATION_DEG", "8"))
# Daylight-off only fires between sunrise+grace and sunset (real daylight hours).
SUNRISE_GRACE_MIN = int(os.environ.get("SUNRISE_GRACE_MIN", "60"))

# Manual grace for the post-sauna block only.
MANUAL_HOLD_MIN = int(os.environ.get("MANUAL_HOLD_MIN", "90"))
# Timeouts only apply to sensorless stairs and utility/closet lights.
CIRCULATION_TIMEOUT_MIN = int(os.environ.get("CIRCULATION_TIMEOUT_MIN", "25"))
UTILITY_TIMEOUT_MIN = int(os.environ.get("UTILITY_TIMEOUT_MIN", "30"))

# Overnight "gentle" cull window (local). A light turned on DURING the window
# (night bathroom / up-late kid) is protected by min-dwell + on_since.
OVERNIGHT_START_HOUR = int(os.environ.get("OVERNIGHT_START_HOUR", "0"))
OVERNIGHT_START_MIN = int(os.environ.get("OVERNIGHT_START_MIN", "30"))
OVERNIGHT_END_HOUR = int(os.environ.get("OVERNIGHT_END_HOUR", "6"))

# Cover command -> PLC actuation -> broadcast (~26 s), without a long re-entry lockout.
MIN_DWELL_SECONDS = float(os.environ.get("MIN_DWELL_SECONDS", "30"))

# The Presence Engine owns detection fusion, debounce and confidence.
PRESENCE_MIN_CONFIDENCE = float(os.environ.get("PRESENCE_MIN_CONFIDENCE", "0.6"))
DARK_LUX_THRESHOLD = float(os.environ.get("DARK_LUX_THRESHOLD", "40"))
ILLUMINANCE_WINDOW_MIN = float(os.environ.get("ILLUMINANCE_WINDOW_MIN", "4"))
# FP300 and SONOFF sensor scales differ. KHH's morning ambient is 17–18 lux.
ROOM_DARK_LUX = {"living_room": 80, "khh": 40}
# Only calibrated rooms get daylight shutoff: the threshold must exceed the
# lamps' own contribution, with a wide gap from the dark-on threshold.
ROOM_BRIGHT_LUX = {"living_room": 200}
# Per-room vacancy TIMING lives in the Presence Engine (its per-room linger_s),
# so the optimizer just needs a small on-time floor before a vacancy-off — it
# bridges the race where a light is switched on a beat before the sensor reports
# presence, so we don't instantly turn it back off.
VACANCY_GRACE_MIN = float(os.environ.get("VACANCY_GRACE_MIN", "1.5"))

# Sauna laude LED (idx 4) hysteresis.
SAUNA_LAUDE_IDX = 4
SAUNA_LAUDE_ON_C = float(os.environ.get("SAUNA_LAUDE_ON_C", "55"))
SAUNA_LAUDE_OFF_C = float(os.environ.get("SAUNA_LAUDE_OFF_C", "50"))

# Post-sauna cooldown auto-off for bathroom + sauna ceiling lights.
SAUNA_AFTER_LIGHTS = (1, 38, 39)
SAUNA_AFTER_PEAK_C = float(os.environ.get("SAUNA_AFTER_PEAK_C", "55"))
SAUNA_AFTER_OFF_C = float(os.environ.get("SAUNA_AFTER_OFF_C", "40"))
SAUNA_AFTER_DELAY_MIN = int(os.environ.get("SAUNA_AFTER_DELAY_MIN", "30"))
SAUNA_AFTER_LOOKBACK_H = int(os.environ.get("SAUNA_AFTER_LOOKBACK_H", "6"))

DRY_RUN = os.environ.get("DRY_RUN", "0") in ("1", "true", "yes")

# Correlation tolerance: a /set actuates ~12–13 s after the command breadcrumb,
# and state broadcasts every ~13 s, so a transition is attributed to a command
# whose breadcrumb landed within [transition − LEAD, transition + LAG].
CMD_CORRELATION_LEAD_S = float(os.environ.get("CMD_CORRELATION_LEAD_S", "40"))
CMD_CORRELATION_LAG_S = float(os.environ.get("CMD_CORRELATION_LAG_S", "10"))


# ── Behaviour categories ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class Cat:
    """Auto-on permission; the remaining rules apply ONLY without a room sensor."""
    auto_on: bool = False
    daylight_off: bool = False
    overnight_off: bool = False
    duration_cap_min: int | None = None


CATS: dict[str, Cat] = {
    "living":      Cat(auto_on=True),
    "secondary":   Cat(overnight_off=True),
    "window":      Cat(daylight_off=True, overnight_off=True),
    "accent":      Cat(overnight_off=True),
    "circulation": Cat(auto_on=True, overnight_off=True, duration_cap_min=CIRCULATION_TIMEOUT_MIN),
    "utility":     Cat(overnight_off=True, duration_cap_min=UTILITY_TIMEOUT_MIN),
    "workroom":    Cat(auto_on=True),
    "toilet":      Cat(auto_on=True),
    "bedroom":     Cat(auto_on=True),
    "office":      Cat(auto_on=True),
    "theater":     Cat(),
    "outdoor":     Cat(daylight_off=True, overnight_off=True),
}

# Physical sensor room per light. A mapped room with missing/unreliable data
# stays unknown: it never falls through to guessed-away or duration-based culls.
LIGHT_ROOM: dict[int, str] = {
    54: "living_room", 19: "living_room",                      # living-room proper (FP300)
    8: "kitchen", 40: "kitchen",                              # kitchen ceilings — dedicated snzb_kitchen PIR.
    # Kitchen and living room share occupancy, but keep their own lux thresholds.
    5: "living_room",                                          # Olohuone LED, full room light (FP300)
    17: "office",                                              # office (future FP300)
    49: "theater", 50: "theater", 51: "theater",              # basement theater
    35: "hall_down", 37: "hall_down",                         # eteinen + tuulikaappi (PIR). Portaikko 42 excluded — the hall_down sensor is nowhere near it
    25: "hall_up", 26: "hall_up",                             # upstairs hall kattovalo (26) + stairs (25), PIR.
    # 3 "Yläkerta aula LED" intentionally omitted — manual-on, no sensor link.
    44: "wc_down", 45: "wc_down", 52: "wc_basement",          # WCs (PIR)
    29: "bath_up", 34: "bath_up",                             # upstairs bathroom (PIR)
    6: "khh", 56: "khh",                                     # KHH LED (6) + ceiling (56), one indoor PIR.
    # NOTE: 61 "Varasto" is the detached AUTOKATOS (carport) storage — a separate
    # outbuilding with NO sensor. It is deliberately NOT mapped here: linking it to
    # the indoor KHH PIR lit the carport store whenever someone entered the utility
    # room. It's a manual-on utility light (see CATEGORY_OF).
    22: "bedroom_seela", 28: "bedroom_aarni", 33: "bedroom_adults",  # bedrooms (PIR)
}

# Windowless WCs auto-on on occupancy at any hour, regardless of brightness.
WINDOWLESS_LIGHTS = set(
    int(x) for x in os.environ.get("WINDOWLESS_LIGHTS", "44,45,52").split(",") if x.strip()
)

# Light index → category. Every index in LIGHT_LABELS is covered. Special-block
# lights (porch 47, laude 4, post-sauna 1/38/39) are handled outside the loop.
CATEGORY_OF: dict[int, str] = {
    # LIVING — open-plan kitchen / dining / living core. 55 (Olohuone kattovalo 2)
    # is NOT physically connected — excluded via DISCONNECTED_IDX below.
    8: "living", 19: "living", 40: "living", 54: "living",
    # SECONDARY — full room light, manual-on only (no auto-on), still auto-off.
    # 5 = Olohuone LED: user wants only the kattovalo (54/55) to auto-on.
    # 3 = Yläkerta aula LED: manual-on, NOT sensor-driven — user wants only the
    # aula kattovalo (26) to auto-on from the upstairs-hall PIR, not the LED too.
    3: "secondary", 5: "secondary",
    # 53 = Kellari varasto: sensorless storeroom used for long spells. Was 'utility'
    # (30-min duration cap) which kept cutting off work sessions — secondary drops
    # the cap but keeps the overnight forgotten-light cull.
    53: "secondary",
    # WINDOW — decorative window lights, pointless in daylight
    18: "window", 20: "window", 23: "window", 24: "window",
    30: "window", 32: "window", 41: "window", 46: "window",
    # ACCENT — kitchen cabinet LED strips only: 2 = mood (above cupboards),
    # 7 = task (under-cabinet). Full-room LEDs (3/5/6) are NOT accent — see below.
    2: "accent", 7: "accent",
    # CIRCULATION — halls, entry, staircases (transient). 26 = aula kattovalo,
    # 25 = aula stairs. The aula LED (3) is deliberately NOT here (secondary above).
    25: "circulation", 26: "circulation", 35: "circulation", 37: "circulation", 42: "circulation",
    # UTILITY / CLOSET — windowless, forgotten-prone, manual-on (no room sensor).
    # 43 = KHH wardrobe (closet, stays manual).
    # 61 "Varasto" = detached autokatos (carport) storage, no sensor → manual-on.
    31: "utility", 36: "utility", 43: "utility", 61: "utility",
    # WORKROOM — motion auto-on via the snzb_khh indoor PIR (LIGHT_ROOM=khh). Only
    # the KHH LED (6) auto-ons; the KHH ceiling (56) is secondary (manual-on,
    # auto-off only) — user wants just the LED automatic. The autokatos varasto (61)
    # is NOT here — it's a detached outbuilding, manual-on utility (see above).
    6: "workroom",
    56: "secondary",
    # TOILET — WCs + mirror lights
    29: "toilet", 34: "toilet", 44: "toilet", 45: "toilet", 52: "toilet",
    # BEDROOM (sleep) — ceilings/wardrobes upstairs (no daylight-off, nap-safe)
    22: "bedroom", 28: "bedroom", 33: "bedroom",
    # OFFICE — downstairs bedroom / workspace
    17: "office",
    # THEATER — windowless basement leisure/work (never off during use)
    49: "theater", 50: "theater", 51: "theater",
    # OUTDOOR — terrace / carport / storage exterior (porch 47 = special block)
    48: "outdoor", 59: "outdoor", 60: "outdoor",
}

# Lights handled by dedicated blocks, skipped in the category loop.
PORCH_IDX = 47
SPECIAL_IDX = {PORCH_IDX, SAUNA_LAUDE_IDX, *SAUNA_AFTER_LIGHTS}

# PLC outputs with no physical light wired — never evaluate, command, or log them
# (auto-switching + announcing a phantom light is pure noise). 55 = Olohuone
# kattovalo 2 (the second olohuone ceiling output is unconnected).
DISCONNECTED_IDX = {55}


# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("lights_optimizer")


# ── State ─────────────────────────────────────────────────────────────────────
running = True
LOC = LocationInfo("Tampere", "Finland", "Europe/Helsinki", HOME_LAT, HOME_LON)
influx_client: InfluxDBClient | None = None
write_api = None
query_api = None

# Manual OFF lasts until confirmed departure, not until an arbitrary timer.
# These sessions are in-memory and start fresh after a service restart.
_dismissed: set[int] = set()
_last_publish_ts: dict[int, float] = {}
DISMISSAL_FRESH_S = float(os.environ.get("DISMISSAL_FRESH_S", "120"))
# Process each observed OFF edge once, including after a dismissal is cleared.
_dismissal_seen: dict[int, datetime] = {}
# Per-tick memoization of expensive shared queries (cleared each tick).
_memo: dict = {}


def _memoize(key, fn):
    if key not in _memo:
        _memo[key] = fn()
    return _memo[key]


def signal_handler(sig, frame):
    global running
    log.info("Shutdown requested")
    running = False


# ── Sun ───────────────────────────────────────────────────────────────────────
def todays_sun(now: datetime) -> tuple[datetime, datetime]:
    """Sunrise/sunset with midsummer polar-day fallbacks (never uses civil
    twilight, which raises at this latitude around midsummer)."""
    d = now.date()
    try:
        sr = sun_rise(LOC.observer, date=d, tzinfo=LOCAL_TZ)
    except ValueError:
        sr = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        ss = sun_set(LOC.observer, date=d, tzinfo=LOCAL_TZ)
    except ValueError:
        ss = now.replace(hour=23, minute=59, second=0, microsecond=0)
    return sr, ss


def sun_elev(now: datetime) -> float:
    """Instantaneous sun elevation (°). Fail-safe to bright daylight on error so
    a sensor/astral fault never pins lights on."""
    try:
        return sun_elevation(LOC.observer, dateandtime=now)
    except Exception:
        return 90.0


# ── InfluxDB helpers ──────────────────────────────────────────────────────────
def _query(flux: str) -> list:
    try:
        rows = []
        for table in query_api.query(flux, org=INFLUXDB_ORG):
            for record in table.records:
                rows.append(record)
        return rows
    except Exception as e:
        log.error("Flux query failed: %s", e)
        return []


def fetch_current_light_states() -> dict[int, bool]:
    """{light_id: is_on} for every primary light (last value over 10 min)."""
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "lights" and r._field == "is_on")
  |> filter(fn: (r) => r.switch_type == "primary")
  |> last()
  |> keep(columns: ["_value", "light_id"])
'''
    out: dict[int, bool] = {}
    for r in _query(flux):
        try:
            out[int(r.values.get("light_id"))] = bool(int(r.get_value() or 0))
        except (TypeError, ValueError):
            continue
    return out


# Last-transition cache: idx -> (is_on, since). At 1s ticks a 24h is_on query per
# light every tick (49/tick) would hammer InfluxDB. Instead we bootstrap each
# light once from history, then maintain the cache incrementally from the
# per-tick state snapshot (already one query) — a state that differs from the
# cache means an edge just happened (since = now).
_transition_cache: dict[int, tuple] = {}


def update_transition_cache(states: dict[int, bool]) -> None:
    """Refresh the last-transition cache from this tick's light-state snapshot.
    First sight of a light bootstraps its `since` from the full 24h history;
    thereafter a changed state stamps `since = now`, an unchanged one is kept."""
    now = datetime.now(timezone.utc)
    for idx, is_on in states.items():
        on = bool(is_on)
        cached = _transition_cache.get(idx)
        if cached is None:
            v, since = _fetch_last_transition_uncached(idx)
            # Trust the fetched edge only if it agrees with the current state.
            _transition_cache[idx] = (on, since if (v == on and since is not None) else now)
        elif cached[0] != on:
            _transition_cache[idx] = (on, now)      # edge this tick
        # else: unchanged → keep the cached since


def fetch_last_transition(idx: int) -> tuple[bool | None, datetime | None]:
    """(current_is_on, time_of_last_change). Served from the cache maintained by
    update_transition_cache(); falls back to a direct history query on a miss
    (e.g. a special-block light not in the tick snapshot)."""
    cached = _transition_cache.get(idx)
    if cached is not None:
        return cached
    v, since = _fetch_last_transition_uncached(idx)
    _transition_cache[idx] = (bool(v) if v is not None else False, since)
    return _transition_cache[idx]


def _fetch_last_transition_uncached(idx: int) -> tuple[bool | None, datetime | None]:
    """(current_is_on, time_of_last_change) for one light over 24 h. If the
    light held one state the whole window, time is the window start (treated as
    'on since long ago'). Uses difference() to find the last edge."""
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "lights" and r._field == "is_on")
  |> filter(fn: (r) => r.switch_type == "primary" and r.light_id == "{idx}")
  |> sort(columns: ["_time"])
'''
    rows = _query(flux)
    if not rows:
        return None, None
    last_val = None
    last_change = None
    prev = None
    for r in rows:
        try:
            v = bool(int(r.get_value() or 0))
        except (TypeError, ValueError):
            continue
        t = r.get_time()
        if prev is None or v != prev:
            last_change = t
        prev = v
        last_val = v
    return last_val, last_change


def fetch_recent_commands(idx: int, lookback_min: int = 180) -> list[tuple[bool, str, datetime]]:
    """Return [(target_on, source, time)] breadcrumbs for one light, sorted by
    time, from the `light_command` measurement written by plc_mqtt_subscriber."""
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{lookback_min}m)
  |> filter(fn: (r) => r._measurement == "light_command" and r._field == "is_on")
  |> filter(fn: (r) => r.light_id == "{idx}")
  |> sort(columns: ["_time"])
  |> keep(columns: ["_time", "_value", "source"])
'''
    out: list[tuple[bool, str, datetime]] = []
    for r in _query(flux):
        t = r.get_time()
        try:
            target = bool(int(r.get_value() or 0))
        except (TypeError, ValueError):
            continue
        src = r.values.get("source") or "unknown"
        if t is not None:
            out.append((target, str(src), t))
    return sorted(out, key=lambda cmd: cmd[2])


def classify_origin(idx: int, is_on: bool, since: datetime | None) -> str:
    """Who caused the CURRENT state of this light?

    Returns "optimizer" | "human" (mobile/mcp/voice) | "wall" | "unknown".
    A transition is attributed to a command breadcrumb whose timestamp falls
    within [since − LEAD, since + LAG] (commands actuate ~12 s later). If a
    matching breadcrumb exists, its source decides; if none does, the change
    came from a physical wall switch. Both mobile/mcp and wall count as a human
    action (the optimizer must not fight either)."""
    if since is None:
        return "unknown"
    lo = since - timedelta(seconds=CMD_CORRELATION_LEAD_S)
    hi = since + timedelta(seconds=CMD_CORRELATION_LAG_S)
    best_src = None
    for target, src, t in fetch_recent_commands(idx):
        if target == is_on and lo <= t <= hi:
            best_src = src  # latest matching wins (list is time-sorted)
    if best_src is None:
        return "wall"
    return "optimizer" if best_src == "optimizer" else "human"


# ── Occupancy / presence ──────────────────────────────────────────────────────
def presence_for_room(room: str | None) -> bool | None:
    """Normalized per-room occupancy from the Presence Service's `presence`
    measurement (occupied field, room tag). Returns True/False if a fresh,
    confident reading exists, else None (room falls back to interim behaviour).
    Activates automatically once the Presence Service starts writing."""
    if not room:
        return None
    return _memoize(("presence", room), lambda: _presence_for_room_uncached(room))


def room_illuminance(room: str | None) -> float | None:
    """Latest measured illuminance (lux) for a room from the `presence`
    measurement, or None if no sensor there reports it."""
    if not room:
        return None
    return _memoize(("lux", room), lambda: _room_illuminance_uncached(room))


def _room_illuminance_uncached(room: str) -> float | None:
    # MEAN over a short window, not the instantaneous last() — indoor lux is very
    # noisy on a partly-cloudy day (seen swinging 150↔620 minute-to-minute), and
    # driving auto-on/off off a single sample would chatter the light on every
    # passing cloud. The mean is the stable ambient level.
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{int(ILLUMINANCE_WINDOW_MIN)}m)
  |> filter(fn: (r) => r._measurement == "presence" and r.room == "{room}")
  |> filter(fn: (r) => r._field == "illuminance")
  |> mean()
'''
    rows = _query(flux)
    if not rows:
        return None
    try:
        return float(rows[0].values.get("_value"))
    except (TypeError, ValueError):
        return None


def _presence_for_room_uncached(room: str) -> bool | None:
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "presence" and r.room == "{room}")
  |> filter(fn: (r) => r._field == "occupied" or r._field == "confidence")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
    rows = _query(flux)
    if not rows:
        return None
    r = rows[0]
    occ = r.values.get("occupied")
    conf = r.values.get("confidence")
    if occ is None:
        return None
    try:
        if conf is not None and float(conf) < PRESENCE_MIN_CONFIDENCE:
            return None
    except (TypeError, ValueError):
        pass
    return bool(occ)


# The kitchen and living room are ONE open-plan space, lit by both the kitchen
# ceilings (8,40) and the living ceilings (19,54). A single sensor never covers all
# of it — the kitchen PIR misses the sofa, the living FP300 misses the counter, and
# either can drop off the mesh — so driving each half off its own sensor culled the
# whole room's lights whenever one half read vacant (people on the sofa, kitchen PIR
# quiet → lights off). Treat the two as one occupancy zone.
OPEN_PLAN_ROOMS = ("kitchen", "living_room")


def living_core_presence() -> bool | None:
    """Real occupancy across the open-plan zone. Any occupied sensor holds the
    zone; vacancy requires BOTH sensors to confidently report empty. An unavailable
    sensor cannot prove its half empty. CO₂ is excluded:
    residual CO₂ crossing a threshold must never relight an empty room at night.
    """
    readings = [presence_for_room(r) for r in OPEN_PLAN_ROOMS]
    if any(p is True for p in readings):
        return True
    if all(p is False for p in readings):
        return False
    return None


# ── Sauna ─────────────────────────────────────────────────────────────────────
def fetch_sauna_temp_recent() -> float | None:
    rows = _query(f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Sauna" and r._field == "temperature")
  |> mean()
''')
    if not rows:
        return None
    v = rows[0].get_value()
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def sauna_session_ended_minutes_ago() -> float | None:
    """Minutes since the sauna dropped below SAUNA_AFTER_OFF_C, if it peaked
    above SAUNA_AFTER_PEAK_C in the lookback window; else None."""
    rows = _query(f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{SAUNA_AFTER_LOOKBACK_H}h)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Sauna" and r._field == "temperature")
  |> sort(columns: ["_time"])
''')
    samples: list[tuple[datetime, float]] = []
    for r in rows:
        try:
            t, v = r.get_time(), r.get_value()
            if t is not None and v is not None:
                samples.append((t, float(v)))
        except (TypeError, ValueError):
            continue
    if not samples:
        return None
    if max(v for _, v in samples) < SAUNA_AFTER_PEAK_C:
        return None
    if samples[-1][1] >= SAUNA_AFTER_OFF_C:
        return None
    drop_time = None
    for t, v in samples:
        if v < SAUNA_AFTER_OFF_C and drop_time is None:
            drop_time = t
        elif v >= SAUNA_AFTER_OFF_C:
            drop_time = None
    if drop_time is None:
        return None
    return (datetime.now(timezone.utc) - drop_time).total_seconds() / 60.0


def light_override_until(light_id: int) -> float:
    """Latest light_override.hold_until epoch (Unifi porch pulse), or 0.0."""
    rows = _query(f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "light_override"
        and r._field == "hold_until" and r.light_id == "{light_id}")
  |> last()
''')
    if not rows:
        return 0.0
    try:
        return float(rows[0].get_value() or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ── MQTT publish ──────────────────────────────────────────────────────────────
def publish_command_breadcrumb(idx: int, on: bool, src: str = "optimizer"):
    """Provenance breadcrumb on the side-channel /command topic (never /set)."""
    topic = f"{MQTT_TOPIC_PREFIX}/light/{idx}/command"
    payload = json.dumps({"on": bool(on), "src": src, "ts": int(time.time())})
    try:
        mqtt_publish.single(
            topic=topic, payload=payload, qos=1, retain=False,
            hostname=MQTT_BROKER, port=MQTT_PORT,
            client_id=f"marmorikatu-lights-optimizer-cmd-{idx}",
        )
    except Exception as e:
        log.warning("command breadcrumb publish to %s failed: %s", topic, e)


def publish_state(idx: int, on: bool, reason: str) -> bool:
    topic = f"{MQTT_TOPIC_PREFIX}/light/{idx}/set"
    payload = "true" if on else "false"
    if DRY_RUN:
        log.info("[DRY RUN] Would publish %s → %s (reason=%s)", topic, payload, reason)
        _last_publish_ts[idx] = time.time()
        return True
    try:
        mqtt_publish.single(
            topic=topic, payload=payload, qos=1, retain=False,
            hostname=MQTT_BROKER, port=MQTT_PORT,
            client_id=f"marmorikatu-lights-optimizer-{idx}",
        )
        log.info("Published %s → %s (reason=%s)", topic, payload, reason)
        publish_command_breadcrumb(idx, on, "optimizer")
        _last_publish_ts[idx] = time.time()
        return True
    except Exception as e:
        log.error("MQTT publish to %s failed: %s", topic, e)
        return False


def within_min_dwell(idx: int) -> bool:
    """True if we commanded this light within MIN_DWELL_SECONDS (don't reverse)."""
    ts = _last_publish_ts.get(idx)
    return ts is not None and (time.time() - ts) < MIN_DWELL_SECONDS


# ── Decision logging ──────────────────────────────────────────────────────────
# Decision-log dedup: at 1s ticks we would otherwise write a lights_optimizer
# point per light every second. Write only when a light's decision actually
# changes, on any actuation (on/off is always a real event), or a slow freshness
# heartbeat — never the same "hold" second after second.
_last_decision: dict[int, tuple] = {}
_last_decision_ts: dict[int, float] = {}
DECISION_HEARTBEAT_S = float(os.environ.get("DECISION_HEARTBEAT_S", "1800"))  # 30 min


def log_decision(idx: int, decision: str, reason: str, category: str = "",
                 manual_locked: bool = False, on_dur: float | None = None):
    key = (decision, reason, category, 1 if manual_locked else 0)
    now = time.time()
    # Skip a repeated non-actuation decision (e.g. hold) unless it changed or the
    # heartbeat is due. Actuations (on/off) always write — they're the events.
    if (decision not in ("on", "off")
            and _last_decision.get(idx) == key
            and (now - _last_decision_ts.get(idx, 0.0)) < DECISION_HEARTBEAT_S):
        return
    _last_decision[idx] = key
    _last_decision_ts[idx] = now
    name = LIGHT_LABELS.get(idx, (f"light_{idx}", None))[0]
    p = (
        Point("lights_optimizer")
        .tag("light_id", str(idx))
        .tag("light_name", name)
        .tag("category", category)
        .field("decision", decision)
        .field("reason", reason)
        .field("manual_locked", 1 if manual_locked else 0)
        .field("dry_run", 1 if DRY_RUN else 0)
        .time(datetime.now(timezone.utc), WritePrecision.S)
    )
    if on_dur is not None and math.isfinite(on_dur):
        p = p.field("on_duration_min", float(on_dur))
    try:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=p)
    except Exception as e:
        log.error("InfluxDB write failed for light %d: %s", idx, e)


# ── Windows ───────────────────────────────────────────────────────────────────
def in_overnight_window(now: datetime) -> bool:
    start = dtime(OVERNIGHT_START_HOUR, OVERNIGHT_START_MIN)
    end = dtime(OVERNIGHT_END_HOUR, 0)
    return start <= now.time() < end


def overnight_start_dt(now: datetime) -> datetime:
    """The datetime at which tonight's overnight window began (for on_since)."""
    today_start = now.replace(hour=OVERNIGHT_START_HOUR, minute=OVERNIGHT_START_MIN,
                              second=0, microsecond=0)
    return today_start


def in_daylight(now: datetime, sunrise: datetime, sunset: datetime) -> bool:
    return sunrise + timedelta(minutes=SUNRISE_GRACE_MIN) <= now < sunset


# ── Porch (idx 47) ────────────────────────────────────────────────────────────
def run_porch(now: datetime, states: dict[int, bool], sunrise: datetime, sunset: datetime):
    """Front porch (idx 47). The optimizer is the SOLE controller — no other
    service writes this light. Behaviour:
      * NO dusk auto-on (removed by request).
      * While a Unifi person-detection hold (`light_override`, written by the
        webhook as a pure signal) is active → light the porch.
      * When the hold expires → turn it off, but ONLY if WE lit it (command
        provenance) — a porch the user switched on manually is never touched.
      * A user turning it off during a detection is respected (not re-lit).
      * Daylight-off if it's been left on into daylight.
    """
    state = states.get(PORCH_IDX)
    if state is None:
        return
    hold_active = light_override_until(PORCH_IDX) > now.timestamp()

    if hold_active:
        if state:
            log_decision(PORCH_IDX, "hold", "porch_detection", "outdoor")
            return
        # Porch off during a detection hold: light it — unless the user just
        # turned it off (respect the dismissal, don't fight them).
        _, since = fetch_last_transition(PORCH_IDX)
        if classify_origin(PORCH_IDX, False, since) in ("human", "wall"):
            log_decision(PORCH_IDX, "hold", "detection_dismissed", "outdoor")
        elif publish_state(PORCH_IDX, True, "porch_detection"):
            log_decision(PORCH_IDX, "on", "porch_detection", "outdoor")
        else:
            log_decision(PORCH_IDX, "hold", "mqtt_publish_failed", "outdoor")
        return

    # No active hold.
    if not state:
        log_decision(PORCH_IDX, "hold", "no_rule_fired", "outdoor")
        return
    # Porch is on with no hold: turn off if WE lit it (detection over), else
    # only daylight-off — never touch a manual on at night.
    _, since = fetch_last_transition(PORCH_IDX)
    if classify_origin(PORCH_IDX, True, since) == "optimizer":
        reason = "porch_detection_ended"
    elif in_daylight(now, sunrise, sunset):
        reason = "daylight_off"
    else:
        log_decision(PORCH_IDX, "hold", "manual", "outdoor")
        return
    if publish_state(PORCH_IDX, False, reason):
        log_decision(PORCH_IDX, "off", reason, "outdoor")
    else:
        log_decision(PORCH_IDX, "hold", "mqtt_publish_failed", "outdoor")


def run_sauna_laude(states: dict[int, bool]):
    state = states.get(SAUNA_LAUDE_IDX)
    if state is None:
        return
    temp = fetch_sauna_temp_recent()
    if temp is None:
        log_decision(SAUNA_LAUDE_IDX, "hold", "no_sauna_temp_data", "bath")
        return
    if state and temp <= SAUNA_LAUDE_OFF_C:
        target, reason = False, f"sauna_cooled_to_{temp:.1f}C"
    elif not state and temp >= SAUNA_LAUDE_ON_C:
        target, reason = True, f"sauna_heated_to_{temp:.1f}C"
    else:
        log_decision(SAUNA_LAUDE_IDX, "hold", f"hysteresis_hold_{temp:.1f}C", "bath")
        return
    if publish_state(SAUNA_LAUDE_IDX, target, reason):
        log_decision(SAUNA_LAUDE_IDX, "on" if target else "off", reason, "bath")
    else:
        log_decision(SAUNA_LAUDE_IDX, "hold", "mqtt_publish_failed", "bath")


def run_post_sauna(now: datetime, states: dict[int, bool]):
    ended = sauna_session_ended_minutes_ago()
    if ended is None or ended < SAUNA_AFTER_DELAY_MIN:
        return
    for idx in SAUNA_AFTER_LIGHTS:
        if not states.get(idx):
            continue
        # Don't cut a fresh shower/bath short — respect a recent manual on.
        is_on, since = fetch_last_transition(idx)
        if since is not None:
            on_dur = (datetime.now(timezone.utc) - since).total_seconds() / 60.0
            if on_dur < MANUAL_HOLD_MIN:
                log_decision(idx, "hold", "post_sauna_manual_grace", "bath", on_dur=on_dur)
                continue
        reason = f"post_sauna_cooled_{ended:.0f}min_ago"
        if publish_state(idx, False, reason):
            log_decision(idx, "off", reason, "bath")
        else:
            log_decision(idx, "hold", "mqtt_publish_failed", "bath")


# ── Room decisions ───────────────────────────────────────────────────────────
def decide_room_light(*, is_on: bool, occupied: bool | None, auto_on: bool,
                      dim: bool, bright: bool, manual_on: bool, dismissed: bool,
                      on_minutes: float) -> tuple[str, str]:
    """Pure room policy. No clocks, history queries, or overlapping schedules.

    `occupied` is already debounced by the Presence Engine. `bright` means a
    calibrated daylight threshold, not merely the lamp illuminating its sensor.
    """
    if is_on:
        if on_minutes >= VACANCY_GRACE_MIN:
            if occupied is False:
                return "off", "vacancy_off"
            if bright and not manual_on:
                return "off", "bright_enough"
        return "hold", "manual_hold" if manual_on else "no_off_rule"
    if not auto_on:
        return "hold", "manual_only"
    if dismissed:
        return "hold", "dismissed_session"
    if occupied is not True:
        return "hold", "presence_unknown" if occupied is None else "room_vacant"
    if not dim:
        return "hold", "not_dark"
    return "on", "auto_on_comfort"


def presence_for_light(idx: int) -> bool | None:
    """Use the same zone for decisions and manual-OFF session clearing."""
    room = LIGHT_ROOM.get(idx)
    return living_core_presence() if room in OPEN_PLAN_ROOMS else presence_for_room(room)


def evaluate_light(idx: int, is_on: bool, now: datetime, sunrise: datetime,
                   sunset: datetime, is_dark: bool):
    cat_name = CATEGORY_OF[idx]
    cat = CATS[cat_name]
    if within_min_dwell(idx):
        log_decision(idx, "hold", "min_dwell_hold", cat_name)
        return

    since = fetch_last_transition(idx)[1] if is_on else None
    on_minutes = ((now - since).total_seconds() / 60.0 if since else float("inf"))
    manual_on = is_on and classify_origin(idx, True, since) in ("human", "wall")
    room = LIGHT_ROOM.get(idx)
    decision, reason = "hold", "manual_hold" if manual_on else "no_off_rule"
    if room:
        lux = room_illuminance(room)
        dim = idx in WINDOWLESS_LIGHTS or is_dark or (
            lux is not None and lux < ROOM_DARK_LUX.get(room, DARK_LUX_THRESHOLD))
        bright = (cat.auto_on and not is_dark and in_daylight(now, sunrise, sunset)
                  and room in ROOM_BRIGHT_LUX and lux is not None
                  and lux > ROOM_BRIGHT_LUX[room])
        decision, reason = decide_room_light(
            is_on=is_on, occupied=presence_for_light(idx), auto_on=cat.auto_on,
            dim=dim, bright=bright, manual_on=manual_on,
            dismissed=idx in _dismissed, on_minutes=on_minutes)
    elif is_on:
        # Sensorless lights keep only their explicit, predictable schedules.
        if cat.daylight_off and in_daylight(now, sunrise, sunset):
            decision, reason = "off", "daylight_off"
        elif (cat.overnight_off and in_overnight_window(now)
              and (since is None or since.astimezone(LOCAL_TZ) < overnight_start_dt(now))):
            decision, reason = "off", "overnight_off"
        elif cat.duration_cap_min is not None and on_minutes >= cat.duration_cap_min:
            decision, reason = "off", "duration_cap"
    else:
        return  # No sensor -> no automatic arrival trigger.

    if decision in ("on", "off"):
        if publish_state(idx, decision == "on", reason):
            if decision == "on":
                time.sleep(0.3)  # pace successive PLC commands
        else:
            decision, reason = "hold", "mqtt_publish_failed"
    log_decision(idx, decision, reason, cat_name, manual_on,
                 on_minutes if is_on else None)


def maintain_dismissals():
    """Confirmed vacancy ends a manual-OFF session; unknown occupancy does not."""
    for idx in list(_dismissed):
        if presence_for_light(idx) is False:
            _dismissed.remove(idx)
            log.info("light %d dismissal cleared (room vacant)", idx)


def detect_dismissals(states: dict[int, bool]):
    """Register a SESSION dismissal when the user turns OFF an auto-on-capable
    light, so we stop fighting them. Edge-triggered on the FRESH off transition
    — an old off still sitting as the last transition (e.g. the user walking back
    into a room they darkened hours ago) must NOT re-arm suppression. Cleared by
    maintain_dismissals when the room goes vacant."""
    for idx, cat_name in CATEGORY_OF.items():
        if not CATS[cat_name].auto_on or idx not in LIGHT_ROOM:
            continue
        if states.get(idx) is not False:
            continue  # still on, or no current observation
        if idx in _dismissed:
            continue
        _, since = fetch_last_transition(idx)
        if since is None:
            continue
        if _dismissal_seen.get(idx) == since:
            continue
        _dismissal_seen[idx] = since
        # Only a JUST-happened off is a dismissal — this is what makes it
        # session-scoped rather than a stale-history day-lock.
        if (datetime.now(timezone.utc) - since).total_seconds() > DISMISSAL_FRESH_S:
            continue
        # While our ON command is in flight, the last observed OFF predates it.
        # That old state is not the user rejecting the new command.
        if since.timestamp() <= _last_publish_ts.get(idx, 0.0):
            continue
        off_origin = classify_origin(idx, False, since)
        if off_origin in ("human", "wall"):
            _dismissed.add(idx)
            log.info("light %d dismissed by %s — suppress auto-on until the room is next vacant",
                     idx, off_origin)


# ── Tick ──────────────────────────────────────────────────────────────────────
def check_and_control():
    _memo.clear()
    now = datetime.now(LOCAL_TZ)
    sunrise, sunset = todays_sun(now)
    elev = sun_elev(now)
    is_dark = elev < SUN_DARK_ELEVATION_DEG
    states = fetch_current_light_states()
    update_transition_cache(states)   # maintain last-transition cache (1 query/tick, not 49)

    # End manual-OFF sessions only on confirmed departure.
    maintain_dismissals()

    # Throttle the tick summary — at 1s ticks it would be a line per second.
    # Log at most every TICK_LOG_S, or immediately when darkness changes.
    global _last_tick_log, _last_tick_state
    tnow = time.monotonic()
    if (tnow - _last_tick_log) > TICK_LOG_S or _last_tick_state != is_dark:
        log.info("tick: %s elev=%.1f dark=%s lights=%d",
                 now.isoformat(timespec="seconds"), elev, is_dark, len(states))
        _last_tick_log = tnow
        _last_tick_state = is_dark

    # Special blocks first.
    run_porch(now, states, sunrise, sunset)
    run_sauna_laude(states)
    run_post_sauna(now, states)

    # Dismissal detection before auto-on so a same-tick dismissal suppresses.
    detect_dismissals(states)

    # Category loop.
    for idx, is_on in states.items():
        if idx in SPECIAL_IDX or idx in DISCONNECTED_IDX or idx not in CATEGORY_OF:
            continue
        evaluate_light(idx, is_on, now, sunrise, sunset, is_dark)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global influx_client, write_api, query_api
    log.info("=" * 60)
    log.info("Room lighting controller (presence + brightness)")
    log.info("HOME=%.4f,%.4f TZ=%s DRY_RUN=%s CHECK_INTERVAL=%ds",
             HOME_LAT, HOME_LON, LOCAL_TZ, DRY_RUN, CHECK_INTERVAL)
    log.info("=" * 60)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    influx_client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    try:
        log.info("InfluxDB: %s", influx_client.health().status)
    except Exception as e:
        log.warning("InfluxDB health check: %s", e)
    write_api = influx_client.write_api(write_options=SYNCHRONOUS)
    query_api = influx_client.query_api()

    sr, ss = todays_sun(now := datetime.now(LOCAL_TZ))
    log.info("Today's sun: rise=%s set=%s", sr.strftime("%H:%M"), ss.strftime("%H:%M"))

    consecutive_failures = 0
    while running:
        try:
            check_and_control()
            consecutive_failures = 0
            touch_health()
        except Exception as e:
            consecutive_failures += 1
            log.exception("check_and_control failed (%d/%d): %s",
                          consecutive_failures, MAX_CONSECUTIVE_FAILURES, e)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.critical("%d consecutive failures — exiting for restart", consecutive_failures)
                if influx_client:
                    influx_client.close()
                sys.exit(1)

        end = time.monotonic() + CHECK_INTERVAL
        while running and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))

    if influx_client:
        influx_client.close()
    log.info("Shutdown complete")


if __name__ == "__main__":
    main()
