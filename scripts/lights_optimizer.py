#!/usr/bin/env python3
"""
Lights optimizer v2 — comfort-first, provenance-aware.

Design goals (see docs/lights-optimizer.md and the v2 spec):
  * NEVER fight an active user. A light a human turned on (wall switch, mobile
    app, or voice/MCP) is held; the optimizer only ever turns OFF lights that
    are demonstrably forgotten.
  * Comfort auto-ON in the dark for the rooms the family lives in.
  * Energy savings only from HIGH-confidence culls: daylight waste on
    window/outdoor/decorative lights, whole-house-away, deep-night overnight,
    and duration caps on transient rooms.

Provenance (the core fix for v1's "flapping"):
  Every software controller (this optimizer, the mobile app, MCP/voice) also
  publishes a breadcrumb to `marmorikatu/light/<idx>/command`
  {"on":bool,"src":...} beside its `/set` command. `plc_mqtt_subscriber`
  records these as the `light_command` measurement. A `lights/is_on`
  transition with NO matching breadcrumb is inferred to be a physical wall
  press. So the optimizer can tell WHO last set a light and never auto-offs a
  human's light during awake hours. (The PLC `/set` accepts only bare
  `true`/`false` — enriching that payload was tested and rejected; see
  docs/plc-command-channel.md. Commands actuate ~12–13 s later, so all
  confirm/min-dwell windows sit well above that.)

Presence (Core C): the optimizer consumes a NORMALIZED per-room occupancy
  signal (`presence` measurement / `presence/<room>` — written by the separate
  Presence Service project). Until that lands, it degrades to interim signals:
  kitchen-CO₂ for the open-plan living core, astronomical darkness, and
  BLE-identity "anyone home" (`ble` measurement) for whole-house-away, with a
  legacy activity fallback.

Special blocks (ported from v1, they work well): front porch (idx 47,
  sun-elevation schedule + Unifi hold), sauna laude LED (idx 4, temperature
  hysteresis), post-sauna cooldown (idx 1/38/39).
"""

import json
import logging
import math
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
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
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("MAX_CONSECUTIVE_FAILURES", "5"))

# Darkness threshold (astronomical sun elevation, °). Shared by porch + auto-on.
SUN_DARK_ELEVATION_DEG = float(os.environ.get("SUN_DARK_ELEVATION_DEG", "8"))
# Daylight-off only fires between sunrise+grace and sunset (real daylight hours).
SUNRISE_GRACE_MIN = int(os.environ.get("SUNRISE_GRACE_MIN", "60"))

# Manual-grace windows (minutes) — after a human turns a light on, the "soft"
# rules (duration cap) are suppressed for at least this long.
MANUAL_HOLD_MIN = int(os.environ.get("MANUAL_HOLD_MIN", "90"))
BEDROOM_HOLD_MIN = int(os.environ.get("BEDROOM_HOLD_MIN", "30"))
SHORT_HOLD_MIN = int(os.environ.get("SHORT_HOLD_MIN", "5"))

# Duration caps for transient categories (minutes since on).
TOILET_TIMEOUT_MIN = int(os.environ.get("TOILET_TIMEOUT_MIN", "30"))
CIRCULATION_TIMEOUT_MIN = int(os.environ.get("CIRCULATION_TIMEOUT_MIN", "25"))
UTILITY_TIMEOUT_MIN = int(os.environ.get("UTILITY_TIMEOUT_MIN", "30"))

# Overnight "gentle" cull window (local). A light turned on DURING the window
# (night bathroom / up-late kid) is protected by min-dwell + on_since.
OVERNIGHT_START_HOUR = int(os.environ.get("OVERNIGHT_START_HOUR", "0"))
OVERNIGHT_START_MIN = int(os.environ.get("OVERNIGHT_START_MIN", "30"))
OVERNIGHT_END_HOUR = int(os.environ.get("OVERNIGHT_END_HOUR", "6"))

# Whole-house-away confirmation.
LONG_ABSENCE_MIN = int(os.environ.get("LONG_ABSENCE_MIN", "180"))
AWAY_CONFIRM_MIN = int(os.environ.get("AWAY_CONFIRM_MIN", "15"))
BLE_RSSI_INSIDE = float(os.environ.get("BLE_RSSI_INSIDE", "-80"))
BLE_WINDOW_MIN = int(os.environ.get("BLE_WINDOW_MIN", "5"))
# BLE-based away detection is OPT-IN and OFF by default. Raw advertiser-count
# presence is unreliable here: an always-on, MAC-rotating Samsung SmartTag (the
# basement bike) never lets the count reach zero, so away would never fire; and
# carried keychain tags stay quiet near their owner's phone. Leave off and use
# the activity fallback until real occupancy comes from the Presence Service.
BLE_AWAY_ENABLED = os.environ.get("BLE_AWAY_ENABLED", "0") in ("1", "true", "yes")

# Idempotent reconciler: never reverse a light within MIN_DWELL_SECONDS of our
# own last command (hard floor against flapping; sits above the ~13 s PLC latency).
MIN_DWELL_SECONDS = float(os.environ.get("MIN_DWELL_SECONDS", "300"))

# Presence-Service contract (Core C). Consumed once the Presence Engine writes a
# `presence` measurement for a room; until then presence_for_room() returns None
# and the room keeps its interim (comfort-first) behaviour.
PRESENCE_MIN_CONFIDENCE = float(os.environ.get("PRESENCE_MIN_CONFIDENCE", "0.6"))
# Per-room vacancy TIMING lives in the Presence Engine (its per-room linger_s),
# so the optimizer just needs a small on-time floor before a vacancy-off — it
# bridges the race where a light is switched on a beat before the sensor reports
# presence, so we don't instantly turn it back off.
VACANCY_GRACE_MIN = float(os.environ.get("VACANCY_GRACE_MIN", "1.5"))

# CO₂ (interim living-core occupancy). Ported from v1.
CO2_AUTO_ON_DELTA_PPM = float(os.environ.get("CO2_AUTO_ON_DELTA_PPM", "20"))
CO2_AUTO_ON_ABSOLUTE_PPM = float(os.environ.get("CO2_AUTO_ON_ABSOLUTE_PPM", "580"))
CO2_AUTO_OFF_DELTA_PPM = float(os.environ.get("CO2_AUTO_OFF_DELTA_PPM", "100"))
CO2_AUTO_OFF_ABSOLUTE_PPM = float(os.environ.get("CO2_AUTO_OFF_ABSOLUTE_PPM", "450"))

# Front porch schedule (idx 47).
PORCH_OFF_HOUR = int(os.environ.get("PORCH_OFF_HOUR", os.environ.get("TERRACE_OFF_HOUR", "23")))

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
    """Behaviour of a light category. Auto-OFF only fires for the flags set
    here (comfort-first: absent flag ⇒ that cull never happens for the room)."""
    auto_on: bool                 # comfort auto-on when dark + occupied
    daylight_off: bool            # off when sun clearly up
    overnight_off: bool           # off in the deep-night window if forgotten
    away_off: bool                # off when the whole house is away
    duration_cap_min: int | None  # transient duration cap (minutes)
    manual_hold_min: int          # grace after a human-on before soft rules
    presence_room: str | None     # normalized Presence-Service room key
    presence_kind: str | None     # "mmwave" (hold-while-still) | "motion" (transit)


CATS: dict[str, Cat] = {
    #                 auto_on daylt  overn  away   cap                     hold             room            kind
    "living":     Cat(True,  False, True,  True,  None,                    MANUAL_HOLD_MIN, "living_core",  "mmwave"),
    # Full room light switched on deliberately (e.g. Olohuone LED next to the
    # auto-on kattovalo): NEVER auto-on, but still vacancy/overnight/away-off.
    "secondary":  Cat(False, False, True,  True,  None,                    MANUAL_HOLD_MIN, None,           "mmwave"),
    "window":     Cat(False, True,  True,  True,  None,                    SHORT_HOLD_MIN,  None,           None),
    "accent":     Cat(False, False, True,  True,  None,                    MANUAL_HOLD_MIN, None,           None),
    # Transit + toilet + bedroom auto-ON on motion when dark (needs a real PIR
    # in that room — no-op until the Presence Engine publishes it).
    "circulation":Cat(True,  False, True,  True,  CIRCULATION_TIMEOUT_MIN, SHORT_HOLD_MIN,  "hall",         "motion"),
    "utility":    Cat(False, False, True,  True,  UTILITY_TIMEOUT_MIN,     SHORT_HOLD_MIN,  None,           "motion"),
    "toilet":     Cat(True,  False, False, True,  TOILET_TIMEOUT_MIN,      SHORT_HOLD_MIN,  None,           "motion"),
    "bedroom":    Cat(True,  False, True,  True,  None,                    BEDROOM_HOLD_MIN,None,           "motion"),
    "office":     Cat(True,  False, False, True,  None,                    MANUAL_HOLD_MIN, "office",       "mmwave"),
    # Theater: mmWave prevents wrong auto-OFF during a movie, but NEVER auto-on
    # (you set the mood manually) — motion mid-film must not relight it.
    "theater":    Cat(False, False, False, True,  None,                    MANUAL_HOLD_MIN, "theater",      "mmwave"),
    "outdoor":    Cat(False, True,  True,  False, None,                    SHORT_HOLD_MIN,  None,           None),
}

# Per-light PHYSICAL room → matches a room in config/presence_rooms.json. A light
# uses this room's presence for auto-on/vacancy-off; lights not listed fall back
# to their category's presence_room. Kitchen (8, 40) intentionally stays on
# `living_core` (CO₂, no vacancy sensor) so a living-room FP300 going vacant
# can't turn off the kitchen. Populate as sensors are installed; a room with no
# sensor simply yields no presence (comfort-first hold), so this is safe now.
LIGHT_ROOM: dict[int, str] = {
    54: "living_room", 19: "living_room",                      # living-room proper (FP300)
    5: "living_room",                                          # Olohuone LED, full room light (FP300)
    17: "office",                                              # office (future FP300)
    49: "theater", 50: "theater", 51: "theater",              # basement theater
    35: "hall_down", 37: "hall_down", 42: "hall_down",        # downstairs entry/stairs (PIR)
    25: "hall_up", 26: "hall_up", 3: "hall_up",               # upstairs hall/stairs (PIR); 3 = YK aula LED
    44: "wc_down", 45: "wc_down", 52: "wc_basement",          # WCs (PIR)
    29: "bath_up", 34: "bath_up",                             # upstairs bathroom (PIR)
    22: "bedroom_seela", 28: "bedroom_aarni", 33: "bedroom_adults",  # bedrooms (PIR)
}

# Light index → category. Every index in LIGHT_LABELS is covered. Special-block
# lights (porch 47, laude 4, post-sauna 1/38/39) are handled outside the loop.
CATEGORY_OF: dict[int, str] = {
    # LIVING — open-plan kitchen / dining / living core. 55 (Olohuone kattovalo 2)
    # is NOT physically connected — excluded via DISCONNECTED_IDX below.
    8: "living", 19: "living", 40: "living", 54: "living",
    # SECONDARY — full room light, manual-on only (no auto-on), still auto-off.
    # 5 = Olohuone LED: user wants only the kattovalo (54/55) to auto-on.
    5: "secondary",
    # WINDOW — decorative window lights, pointless in daylight
    18: "window", 20: "window", 23: "window", 24: "window",
    30: "window", 32: "window", 41: "window", 46: "window",
    # ACCENT — kitchen cabinet LED strips only: 2 = mood (above cupboards),
    # 7 = task (under-cabinet). Full-room LEDs (3/5/6) are NOT accent — see below.
    2: "accent", 7: "accent",
    # CIRCULATION — halls, entry, staircases (transient). 3 = YK aula LED, a full
    # upstairs-hall light.
    3: "circulation", 25: "circulation", 26: "circulation", 35: "circulation", 37: "circulation", 42: "circulation",
    # UTILITY / CLOSET — windowless, forgotten-prone. 6 = KHH (utility room) LED,
    # a full room light.
    6: "utility", 31: "utility", 36: "utility", 43: "utility", 53: "utility", 56: "utility", 61: "utility",
    # TOILET — WCs + mirror lights
    29: "toilet", 34: "toilet", 44: "toilet", 45: "toilet", 52: "toilet",
    # BEDROOM (sleep) — ceilings/wardrobes upstairs (no daylight-off, nap-safe)
    22: "bedroom", 28: "bedroom", 33: "bedroom",
    # OFFICE — downstairs bedroom / workspace (never off during work)
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

# Per-light runtime state (rebuilt from InfluxDB on boot → restart-deterministic):
#   _dismissed_date[idx]  = local date a human turned off our auto-on (suppress
#                           re-auto-on until the next local day).
#   _last_publish_ts[idx] = monotonic-ish epoch of our last command (min-dwell).
_dismissed_date: dict[int, date] = {}
_last_publish_ts: dict[int, float] = {}
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


def fetch_last_transition(idx: int) -> tuple[bool | None, datetime | None]:
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
    return out


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


def co2_signal_class() -> str:
    return _memoize("co2", _co2_signal_class_uncached)


def _co2_signal_class_uncached() -> str:
    """Kitchen Ruuvi CO₂ trend for the living core: ELEVATED / DROPPED /
    BASELINE / UNKNOWN. Baseline anchored 2 h→1 h back so a slow occupancy ramp
    stays visible; absolute fallbacks catch sustained occupancy with no
    baseline (cold start)."""
    def _mean(rng: str) -> float | None:
        rows = _query(f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range({rng})
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Keittiö" and r._field == "co2")
  |> mean()
''')
        if not rows:
            return None
        v = rows[0].get_value()
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    recent = _mean("start: -5m")
    if recent is None:
        return "UNKNOWN"
    base = _mean("start: -2h, stop: -1h")
    if base is None:
        base = _mean("start: -6h, stop: -1h")  # cold-start widen
    if recent >= CO2_AUTO_ON_ABSOLUTE_PPM:
        return "ELEVATED"
    if recent <= CO2_AUTO_OFF_ABSOLUTE_PPM:
        return "DROPPED"
    if base is not None:
        delta = recent - base
        if delta >= CO2_AUTO_ON_DELTA_PPM:
            return "ELEVATED"
        if delta <= -CO2_AUTO_OFF_DELTA_PPM:
            return "DROPPED"
    return "BASELINE"


def living_core_occupied() -> bool | None:
    """Interim living-core occupancy: normalized presence if available, else
    kitchen CO₂. None if no signal at all."""
    p = presence_for_room("living_core")
    if p is not None:
        return p
    c = co2_signal_class()
    if c == "ELEVATED":
        return True
    if c == "DROPPED":
        return False
    return None  # BASELINE / UNKNOWN → no strong signal


def ble_present_count() -> int | None:
    """Distinct strong-RSSI BLE MACs seen in the last BLE_WINDOW_MIN — a
    whole-house 'anyone home' proxy. None if the `ble` measurement has no data
    (subsystem not deployed yet) → caller falls back to activity heuristic."""
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{BLE_WINDOW_MIN}m)
  |> filter(fn: (r) => r._measurement == "ble" and r._field == "rssi")
  |> filter(fn: (r) => r._value >= {BLE_RSSI_INSIDE})
  |> group(columns: ["mac"])
  |> last()
'''
    rows = _query(flux)
    if not rows:
        # Distinguish "no ble measurement at all" from "measured, nobody home".
        any_ble = _query(f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "ble")
  |> limit(n: 1)
''')
        return 0 if any_ble else None
    macs = {r.values.get("mac") for r in rows}
    macs.discard(None)
    return len(macs)


def activity_recent(minutes: int) -> bool:
    """Legacy fallback: any wall-switch press or light-on transition in window."""
    presses = _query(f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r._measurement == "switches" and r._field == "pressed" and r._value == 1)
  |> count()
''')
    if any((r.get_value() or 0) > 0 for r in presses):
        return True
    ons = _query(f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r._measurement == "lights" and r._field == "is_on")
  |> sort(columns: ["_time"])
  |> difference(nonNegative: false)
  |> filter(fn: (r) => r._value == 1)
  |> count()
''')
    return any((r.get_value() or 0) > 0 for r in ons)


def whole_house_away() -> bool:
    """High-confidence 'nobody home'. BLE advertiser-count is opt-in
    (BLE_AWAY_ENABLED) because an always-on basement SmartTag never lets the
    count reach zero; by default use the legacy activity heuristic."""
    if BLE_AWAY_ENABLED:
        n = ble_present_count()
        if n is not None:
            return n == 0
    return not activity_recent(LONG_ABSENCE_MIN)


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
def log_decision(idx: int, decision: str, reason: str, category: str = "",
                 manual_locked: bool = False, on_dur: float | None = None):
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


# ── Per-light category evaluation ─────────────────────────────────────────────
def evaluate_light(idx: int, is_on: bool, now: datetime, sunrise: datetime,
                   sunset: datetime, is_dark: bool, away: bool):
    """Decide + act on one categorized light. Comfort-first: auto-OFF only on
    high-confidence culls; a human's light is held during awake hours."""
    cat_name = CATEGORY_OF.get(idx, "utility")
    cat = CATS[cat_name]

    # Min-dwell: never reverse our own very recent command.
    if within_min_dwell(idx):
        log_decision(idx, "hold", "min_dwell_hold", cat_name)
        return

    today = now.date()

    # REAL per-room presence from the Presence Engine ONLY (None until a sensor
    # exists for this light's room). Deliberately does NOT include kitchen-CO₂:
    # CO₂ lags and reads "dropped" when people sit still, so it may only ever
    # turn a light ON (comfort), never OFF — using it for vacancy-off was the v1
    # bug that turned off the occupied kitchen/living room. The room is the
    # light's physical room (LIGHT_ROOM) or its category default.
    room = LIGHT_ROOM.get(idx) or cat.presence_room
    presence = presence_for_room(room)

    # ---- OFF (light currently on) ----
    if is_on:
        _, since = fetch_last_transition(idx)
        on_dur_min = ((datetime.now(timezone.utc) - since).total_seconds() / 60.0
                      if since is not None else float("inf"))
        human_on = classify_origin(idx, is_on, since) in ("human", "wall")
        # 1) Whole-house away — highest-confidence cull, overrides manual.
        if cat.away_off and away:
            _act_off(idx, "away_off", cat_name, human_on, on_dur_min)
            return
        # 2) Daylight waste (window / accent-opt / outdoor) — overrides manual;
        #    these serve no purpose once the sun is clearly up.
        if cat.daylight_off and in_daylight(now, sunrise, sunset):
            _act_off(idx, "daylight_off", cat_name, human_on, on_dur_min)
            return
        # 3) Presence vacancy-off — ONLY when a REAL presence signal says empty
        #    (mmWave/PIR via the Presence Engine, which already applied the
        #    room's linger). Never fires on CO₂/no-data. Small on-time floor
        #    bridges the switch-on-before-sensor race.
        if cat.presence_kind and presence is False and on_dur_min >= VACANCY_GRACE_MIN:
            _act_off(idx, "vacancy_off", cat_name, human_on, on_dur_min)
            return
        # 4) Overnight cull — forgotten lights only. A light turned on DURING
        #    the window (on_since ≥ window start) is protected. A room with real
        #    presence (occupied) is never culled.
        if cat.overnight_off and in_overnight_window(now):
            turned_on_in_window = since is not None and since.astimezone(LOCAL_TZ) >= overnight_start_dt(now)
            if not turned_on_in_window and presence is not True:
                _act_off(idx, "overnight_off", cat_name, human_on, on_dur_min)
                return
        # 5) Duration cap (transient categories) — after the manual grace. Real
        #    presence (occupied) vetoes the cap.
        if cat.duration_cap_min is not None and on_dur_min >= max(cat.duration_cap_min, cat.manual_hold_min):
            if presence is not True:
                _act_off(idx, "duration_cap", cat_name, human_on, on_dur_min)
                return
        # Otherwise: HOLD. This is the comfort-first default — living spaces,
        # a human's light, anything without a high-confidence off reason.
        reason = "manual_hold" if human_on else "no_off_rule"
        log_decision(idx, "hold", reason, cat_name, human_on, on_dur_min)
        return

    # ---- ON (light currently off) → comfort auto-on ----
    if not cat.auto_on:
        return  # category never auto-ons (no log spam for the many off lights)
    if not is_dark:
        return
    if _dismissed_date.get(idx) == today:
        log_decision(idx, "hold", "dismissed_today", cat_name)
        return
    # Auto-ON occupancy: real presence if available, else the CO₂ interim signal
    # for the living category (kitchen/dining/living). CO₂ is allowed to turn
    # lights ON (it never turns off), so living lights keep comfort auto-on even
    # before an FP300 is installed.
    occ_for_on = presence
    if occ_for_on is None and cat_name == "living":
        occ_for_on = living_core_occupied()
    if occ_for_on is True:
        if publish_state(idx, True, "auto_on_comfort"):
            log_decision(idx, "on", "auto_on_comfort", cat_name)
            time.sleep(0.3)  # pace successive publishes for the PLC
        else:
            log_decision(idx, "hold", "mqtt_publish_failed", cat_name)


def _act_off(idx: int, reason: str, cat_name: str, human_on: bool, on_dur: float):
    if publish_state(idx, False, reason):
        log_decision(idx, "off", reason, cat_name, human_on, on_dur)
    else:
        log_decision(idx, "hold", "mqtt_publish_failed", cat_name, human_on, on_dur)


def detect_dismissals(now: datetime, states: dict[int, bool]):
    """For auto-on-capable lights that are OFF: if we auto-on'd them earlier and
    a human turned them off, suppress re-auto-on until the next local day."""
    today = now.date()
    for idx, cat_name in CATEGORY_OF.items():
        if not CATS[cat_name].auto_on:
            continue
        if states.get(idx):
            continue  # still on
        if _dismissed_date.get(idx) == today:
            continue
        # Was our last command an ON, and the light is now off by a human?
        cmds = fetch_recent_commands(idx)
        if not cmds:
            continue
        last_target, last_src, _ = cmds[-1]
        _, since = fetch_last_transition(idx)
        off_origin = classify_origin(idx, False, since)
        if last_src == "optimizer" and last_target is True and off_origin in ("human", "wall"):
            _dismissed_date[idx] = today
            log.info("light %d dismissed by %s — suppress auto-on until tomorrow", idx, off_origin)


# ── Tick ──────────────────────────────────────────────────────────────────────
def check_and_control():
    _memo.clear()
    now = datetime.now(LOCAL_TZ)
    sunrise, sunset = todays_sun(now)
    elev = sun_elev(now)
    is_dark = elev < SUN_DARK_ELEVATION_DEG
    states = fetch_current_light_states()
    away = whole_house_away()

    # Drop stale dismissals from previous days.
    today = now.date()
    for i, d in list(_dismissed_date.items()):
        if d < today:
            del _dismissed_date[i]

    log.info("tick: %s elev=%.1f dark=%s away=%s lights=%d",
             now.isoformat(timespec="seconds"), elev, is_dark, away, len(states))

    # Special blocks first.
    run_porch(now, states, sunrise, sunset)
    run_sauna_laude(states)
    run_post_sauna(now, states)

    # Dismissal detection before auto-on so a same-tick dismissal suppresses.
    detect_dismissals(now, states)

    # Category loop.
    for idx, is_on in states.items():
        if idx in SPECIAL_IDX or idx in DISCONNECTED_IDX or idx not in CATEGORY_OF:
            continue
        evaluate_light(idx, is_on, now, sunrise, sunset, is_dark, away)


# ── Boot: rebuild dismissals from the persisted command/state log ─────────────
def rebuild_state():
    """Restart-determinism: reconstruct today's dismissals from InfluxDB so a
    restart doesn't re-enable a light the user dismissed earlier today."""
    now = datetime.now(LOCAL_TZ)
    states = fetch_current_light_states()
    for idx, cat_name in CATEGORY_OF.items():
        if not CATS[cat_name].auto_on or states.get(idx):
            continue
        cmds = fetch_recent_commands(idx, lookback_min=18 * 60)
        if not cmds:
            continue
        last_target, last_src, last_t = cmds[-1]
        if last_src == "optimizer" and last_target is True:
            _, since = fetch_last_transition(idx)
            if since is not None and classify_origin(idx, False, since) in ("human", "wall"):
                if since.astimezone(LOCAL_TZ).date() == now.date():
                    _dismissed_date[idx] = now.date()
    if _dismissed_date:
        log.info("rebuilt dismissals: %s", {k: str(v) for k, v in _dismissed_date.items()})


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global influx_client, write_api, query_api
    log.info("=" * 60)
    log.info("Lights Optimizer v2 (comfort-first, provenance-aware)")
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
    try:
        rebuild_state()
    except Exception as e:
        log.warning("state rebuild failed (continuing): %s", e)

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
