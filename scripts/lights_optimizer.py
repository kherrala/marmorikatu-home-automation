#!/usr/bin/env python3
"""
Auto-off lights optimizer.

Periodically inspects the current state of every light in the Marmorikatu
home and turns off those that are demonstrably forgotten on. Rules are
per-category (toilet, bedroom, kitchen, …) — see LIGHT_POLICY / POLICIES
below.

Also runs a couple of simple ON-schedules:
  - Front terrace (Ulkovalo terassi, idx 48): force ON sunset → 22:00.

Occupancy is detected from three signals over rolling windows:
  - Wall switches pressed (`switches` measurement)
  - Lights freshly turned on (positive jump in `lights/is_on`)
  - Keittiö Ruuvi CO₂ (`ruuvi/co2` for sensor_name=Keittiö) rising vs baseline

Sunrise/sunset is computed locally from HOME_LAT/HOME_LON via the `astral`
library — no API dependency.

Decisions are logged to InfluxDB measurement `lights_optimizer` for later
review via Grafana / the MCP `get_lights_optimizer_status` tool.
"""

import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import paho.mqtt.publish as mqtt_publish
from astral import LocationInfo
from astral.sun import sun, elevation as sun_elevation
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

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
SUNRISE_GRACE_MIN = int(os.environ.get("SUNRISE_GRACE_MIN", "60"))
WORKDAY_START_HOUR = int(os.environ.get("WORKDAY_START_HOUR", "9"))
WORKDAY_END_HOUR = int(os.environ.get("WORKDAY_END_HOUR", "16"))
TOILET_TIMEOUT_MIN = int(os.environ.get("TOILET_TIMEOUT_MIN", "30"))
STAIRCASE_TIMEOUT_MIN = int(os.environ.get("STAIRCASE_TIMEOUT_MIN", "30"))
OCCUPANCY_WINDOW_MIN = int(os.environ.get("OCCUPANCY_WINDOW_MIN", "30"))
LONG_ABSENCE_MIN = int(os.environ.get("LONG_ABSENCE_MIN", "120"))
CO2_OCCUPANCY_DELTA_PPM = float(os.environ.get("CO2_OCCUPANCY_DELTA_PPM", "30"))
MANUAL_HOLD_MIN = int(os.environ.get("MANUAL_HOLD_MIN", "15"))
BEDROOM_HOLD_MIN = int(os.environ.get("BEDROOM_HOLD_MIN", "30"))
PORCH_OFF_HOUR = int(os.environ.get("PORCH_OFF_HOUR", os.environ.get("TERRACE_OFF_HOUR", "22")))
# Sun elevation below this (°) → "dark enough indoors" for CO₂-driven auto-on.
# 8° lands roughly 30–60 min either side of horizon depending on latitude/
# season — covers the Finnish dim-evening / dim-morning the user notices.
SUN_DARK_ELEVATION_DEG = float(os.environ.get("SUN_DARK_ELEVATION_DEG", "8"))

# Sauna laude (bench) LED auto-control: track Ruuvi Sauna temperature.
# Hysteresis dead-band (50–55°C) prevents flapping when löyly is poured.
SAUNA_LAUDE_IDX = 4
SAUNA_LAUDE_ON_C = float(os.environ.get("SAUNA_LAUDE_ON_C", "55"))
SAUNA_LAUDE_OFF_C = float(os.environ.get("SAUNA_LAUDE_OFF_C", "50"))

# CO₂-driven auto-on/off for kitchen + living-room ceiling lights.
# - Evening (after sunset): kitchen (40) AND living-room (54).
# - Morning (before sunrise + SUNRISE_GRACE_MIN): kitchen (40) only.
# - Auto-off any time CO₂ has clearly dropped (occupancy gone) or after midnight.
# - If user manually turns off after we auto-on, suppress until next day.
CO2_AUTO_KITCHEN_IDX = 40       # Keittiö kattovalo
CO2_AUTO_LIVINGROOM_IDX = 54    # Olohuone kattovalo
CO2_AUTO_MANAGED = (CO2_AUTO_KITCHEN_IDX, CO2_AUTO_LIVINGROOM_IDX)
# Sliding baseline (last 30→5 min) is too short — when occupancy ramps up
# slowly, both the recent and baseline windows track the rise and the delta
# stays small. Anchor the baseline further back (~2 h ago) so a steady climb
# becomes visible. Defaults below trigger ELEVATED on a single occupant in
# the kitchen / adjacent room within ~15–30 min of arrival.
CO2_AUTO_ON_DELTA_PPM = float(os.environ.get("CO2_AUTO_ON_DELTA_PPM", "20"))
CO2_AUTO_ON_ABSOLUTE_PPM = float(os.environ.get("CO2_AUTO_ON_ABSOLUTE_PPM", "580"))
CO2_AUTO_OFF_DELTA_PPM = float(os.environ.get("CO2_AUTO_OFF_DELTA_PPM", "50"))
CO2_AUTO_OFF_ABSOLUTE_PPM = float(os.environ.get("CO2_AUTO_OFF_ABSOLUTE_PPM", "500"))

DRY_RUN = os.environ.get("DRY_RUN", "0") in ("1", "true", "yes")

# Long-absence-rule exemptions for lights still in policies that respect
# occupancy. Most of the originally-exempted indices are now in the
# "windowless" policy which never auto-offs anyway — but if any future
# light moves back to bedroom/general, this set is the toggle.
ABSENCE_EXEMPT_INDICES: set[int] = set()

# ── Light category map ────────────────────────────────────────────────────────
# Names in comments are from light_labels.LIGHT_LABELS (buttontxt source).
LIGHT_POLICY: dict[int, str] = {
    # Never auto-managed. Includes the windowless basement (no daylight, no
    # occupancy proxy) and the downstairs bedroom that doubles as a daytime
    # home office (kitchen-Ruuvi CO₂ doesn't see her there, so the workday
    # rule was turning lights off mid-Zoom-call).
    # NOTE: light idx 4 (Saunan laude ledi) is NOT here — it has its own
    # temperature-driven block in check_and_control(), see SAUNA_LAUDE_IDX.
    17: "manual_only",  # MH alakerta kattovalo (downstairs bedroom / workspace)
    18: "manual_only",  # MH alakerta ikkuna    (downstairs bedroom / workspace)
    38: "manual_only", 39: "manual_only",
    48: "manual_only",  # Ulkovalo terassi (no schedule — used manually)
    49: "manual_only",  # Kellari etuosa
    50: "manual_only",  # Kellari takaosa
    51: "manual_only",  # Biljardipöytä
    52: "manual_only",  # WC kellari
    59: "manual_only", 60: "manual_only", 61: "manual_only",

    53: "general",     # Kellari varasto — small windows, follows sunrise rule

    # Toilets / bathrooms — frequently forgotten on
    1:  "toilet",      # Kylpyhuone alakerta
    44: "toilet",      # WC alakerta katto
    45: "toilet",      # WC alakerta peili
    29: "toilet",      # Kylpyhuone yläkerta katto
    34: "toilet",      # Kylpyhuone yläkerta peilivalo

    # Bedrooms (upstairs, sleeping use). Aula is NOT a bedroom.
    22: "bedroom", 23: "bedroom",                  # Aatu (upstairs)
    28: "bedroom", 30: "bedroom",                  # Onni (upstairs)
    31: "bedroom", 32: "bedroom", 33: "bedroom",   # Essi (upstairs) — vaatehuone + ikkuna + katto

    # Kitchen — note: idx 40 (Keittiö kattovalo) is CO₂-auto-managed below,
    # NOT in this policy.
    2: "kitchen", 7: "kitchen", 8: "kitchen", 41: "kitchen",

    # Living / dining — note: idx 54 (Olohuone kattovalo) is CO₂-auto-managed
    # below, NOT in this policy. Idx 55 (Olohuone kattovalo 2) is left here.
    5: "livingroom", 19: "livingroom", 20: "livingroom",
    46: "livingroom", 55: "livingroom",

    # Staircase — transient, often forgotten
    25: "staircase",   # Aula rappuset
    42: "staircase",   # Portaikko

    # Common / general
    3: "general", 6: "general", 24: "general", 26: "general",
    35: "general", 36: "general", 37: "general", 43: "general", 56: "general",

    # Schedule-driven
    47: "porch_schedule",  # Sisäänkäynti (front porch) — sunset → PORCH_OFF_HOUR
}


# ── Policy ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Policy:
    auto_off_after_sunrise_min: int | None
    auto_off_when_unoccupied: bool
    auto_off_after_on_duration_min: int | None
    auto_off_after_midnight: bool
    min_hold_after_manual_min: int
    terrace_schedule: bool = False


POLICIES: dict[str, Policy] = {
    "toilet":           Policy(None, False, TOILET_TIMEOUT_MIN,    True,  5),
    "staircase":        Policy(SUNRISE_GRACE_MIN, True, STAIRCASE_TIMEOUT_MIN, True, 5),
    "bedroom":          Policy(None, True,  None,                  True,  BEDROOM_HOLD_MIN),
    "kitchen":          Policy(SUNRISE_GRACE_MIN, True, None,      True,  MANUAL_HOLD_MIN),
    "livingroom":       Policy(SUNRISE_GRACE_MIN, True, None,      True,  MANUAL_HOLD_MIN),
    "general":          Policy(SUNRISE_GRACE_MIN, True, None,      True,  MANUAL_HOLD_MIN),
    "manual_only":      Policy(None, False, None,                  False, 60),
    "porch_schedule":   Policy(None, False, None,                  False, 5, terrace_schedule=True),
}


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

# Per-idx tracking for the CO₂ auto-managed lights:
#   _co2_auto_on_at[idx]        = local-tz time we last auto-on'd this light.
#   _co2_auto_on_confirmed[idx] = True once we've observed is_on=1 after
#                                 our publish — needed to distinguish a
#                                 user dismissal from a silently-failed
#                                 publish (e.g. unresponsive relay).
#   _co2_dismissed_date[idx]    = local date the user dismissed the auto-on,
#                                 so we don't re-enable it the same day.
_co2_auto_on_at: dict[int, datetime] = {}
_co2_auto_on_confirmed: dict[int, bool] = {}
_co2_dismissed_date: dict[int, date] = {}
_CO2_PUBLISH_GRACE_SECONDS = 90.0  # how long to wait for the relay to confirm


def signal_handler(sig, frame):
    global running
    log.info("Shutdown requested")
    running = False


# ── Sun ───────────────────────────────────────────────────────────────────────

def todays_sun(now: datetime) -> tuple[datetime, datetime]:
    s = sun(LOC.observer, date=now.date(), tzinfo=LOCAL_TZ)
    return s["sunrise"], s["sunset"]


# ── InfluxDB queries ──────────────────────────────────────────────────────────

def _query(flux: str) -> list:
    """Run a Flux query and return the records. None on error."""
    try:
        tables = query_api.query(flux, org=INFLUXDB_ORG)
        rows = []
        for table in tables:
            for record in table.records:
                rows.append(record)
        return rows
    except Exception as e:
        log.error("Flux query failed: %s", e)
        return []


def fetch_current_light_states() -> dict[int, tuple[bool, datetime]]:
    """Return {light_id_int: (is_on, last_seen)} for every primary light."""
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "lights" and r._field == "is_on")
  |> filter(fn: (r) => r.switch_type == "primary")
  |> last()
  |> keep(columns: ["_time", "_value", "light_id"])
'''
    out = {}
    for r in _query(flux):
        try:
            idx = int(r.values.get("light_id"))
        except (TypeError, ValueError):
            continue
        out[idx] = (bool(int(r.get_value() or 0)), r.get_time())
    return out


def fetch_last_zero_to_one(idx: int) -> datetime | None:
    """Return the timestamp of the most recent 0→1 transition for one light,
    or None if the light has been continuously on for the whole lookback
    window or hasn't been seen.
    """
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "lights" and r._field == "is_on")
  |> filter(fn: (r) => r.switch_type == "primary" and r.light_id == "{idx}")
  |> sort(columns: ["_time"])
  |> difference(nonNegative: false)
  |> filter(fn: (r) => r._value == 1)
  |> last()
  |> keep(columns: ["_time"])
'''
    rows = _query(flux)
    return rows[0].get_time() if rows else None


def on_duration_min(idx: int, fallback: datetime | None = None) -> float | None:
    """Minutes since the last 0→1 transition, or None if unknown."""
    t = fetch_last_zero_to_one(idx)
    if t is None:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 60.0


# ── Occupancy ─────────────────────────────────────────────────────────────────

def switch_pressed_recently(minutes: int) -> bool:
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r._measurement == "switches" and r._field == "pressed")
  |> filter(fn: (r) => r._value == 1)
  |> count()
'''
    rows = _query(flux)
    return any((r.get_value() or 0) > 0 for r in rows)


def light_turned_on_recently(minutes: int) -> bool:
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r._measurement == "lights" and r._field == "is_on")
  |> sort(columns: ["_time"])
  |> difference(nonNegative: false)
  |> filter(fn: (r) => r._value == 1)
  |> count()
'''
    rows = _query(flux)
    return any((r.get_value() or 0) > 0 for r in rows)


def fetch_sauna_temp_recent() -> float | None:
    """5-minute mean Ruuvi Sauna temperature, or None if no data."""
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Sauna" and r._field == "temperature")
  |> mean()
'''
    rows = _query(flux)
    if not rows:
        return None
    v = rows[0].get_value()
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def co2_signal_class() -> str:
    """Classify the kitchen Ruuvi CO₂ trend.

    Baseline window is 2 h → 1 h ago — far enough back that a slow rise
    in the recent 5 min isn't masked by the baseline drifting up with it.

    Returns:
      "ELEVATED"  — recent 5-min mean is ≥ baseline + CO2_AUTO_ON_DELTA_PPM,
                    OR recent ≥ CO2_AUTO_ON_ABSOLUTE_PPM (someone clearly here)
      "DROPPED"   — recent ≤ baseline − CO2_AUTO_OFF_DELTA_PPM, OR recent has
                    fallen near outdoor (≤ CO2_AUTO_OFF_ABSOLUTE_PPM)
      "BASELINE"  — within the dead-band
      "UNKNOWN"   — sensor data missing
    """
    flux_recent = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Keittiö" and r._field == "co2")
  |> mean()
'''
    flux_baseline = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -2h, stop: -1h)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Keittiö" and r._field == "co2")
  |> mean()
'''
    recent_rows = _query(flux_recent)
    base_rows = _query(flux_baseline)
    if not recent_rows:
        return "UNKNOWN"
    recent = recent_rows[0].get_value()
    base = base_rows[0].get_value() if base_rows else None
    if recent is None:
        return "UNKNOWN"

    # Absolute fallbacks first — they don't need a baseline.
    if recent >= CO2_AUTO_ON_ABSOLUTE_PPM:
        return "ELEVATED"
    if recent <= CO2_AUTO_OFF_ABSOLUTE_PPM:
        return "DROPPED"

    # Delta-based classification (skip if baseline missing, e.g. after restart).
    if base is not None:
        delta = recent - base
        if delta >= CO2_AUTO_ON_DELTA_PPM:
            return "ELEVATED"
        if delta <= -CO2_AUTO_OFF_DELTA_PPM:
            return "DROPPED"
    return "BASELINE"


def co2_recently_elevated(minutes: int) -> bool:
    """Compare the recent (last 5 min) Keittiö CO₂ mean against the baseline
    of the longer rolling window. If the recent mean is ≥ delta ppm above
    the baseline, treat as occupied."""
    short_min = min(5, minutes)
    flux_recent = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{short_min}m)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Keittiö" and r._field == "co2")
  |> mean()
'''
    flux_baseline = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{minutes}m, stop: -{short_min}m)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Keittiö" and r._field == "co2")
  |> mean()
'''
    recent = _query(flux_recent)
    base = _query(flux_baseline)
    if not recent or not base:
        return False
    rv = recent[0].get_value()
    bv = base[0].get_value()
    if rv is None or bv is None:
        return False
    return (rv - bv) >= CO2_OCCUPANCY_DELTA_PPM


def house_occupied() -> bool:
    return (
        switch_pressed_recently(OCCUPANCY_WINDOW_MIN)
        or light_turned_on_recently(OCCUPANCY_WINDOW_MIN)
        or co2_recently_elevated(OCCUPANCY_WINDOW_MIN)
    )


def long_unoccupied() -> bool:
    """True iff none of the three signals fired in the last LONG_ABSENCE_MIN."""
    return not (
        switch_pressed_recently(LONG_ABSENCE_MIN)
        or light_turned_on_recently(LONG_ABSENCE_MIN)
        or co2_recently_elevated(LONG_ABSENCE_MIN)
    )


# ── MQTT publish ──────────────────────────────────────────────────────────────

def publish_state(idx: int, on: bool, reason: str):
    topic = f"{MQTT_TOPIC_PREFIX}/light/{idx}/set"
    payload = "true" if on else "false"
    if DRY_RUN:
        log.info("[DRY RUN] Would publish %s → %s (reason=%s)", topic, payload, reason)
        return True
    try:
        mqtt_publish.single(
            topic=topic,
            payload=payload,
            qos=1,
            retain=False,
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            client_id=f"marmorikatu-lights-optimizer-{idx}",
        )
        log.info("Published %s → %s (reason=%s)", topic, payload, reason)
        return True
    except Exception as e:
        log.error("MQTT publish to %s failed: %s", topic, e)
        return False


# ── Decision logging ──────────────────────────────────────────────────────────

def log_decision(idx: int, decision: str, reason: str, on_dur: float | None = None,
                 category: str = ""):
    name = LIGHT_LABELS.get(idx, (f"light_{idx}", None))[0]
    p = (
        Point("lights_optimizer")
        .tag("light_id", str(idx))
        .tag("light_name", name)
        .tag("category", category)
        .field("decision", decision)
        .field("reason", reason)
        .field("dry_run", 1 if DRY_RUN else 0)
        .time(datetime.now(timezone.utc), WritePrecision.S)
    )
    if on_dur is not None:
        p = p.field("on_duration_min", float(on_dur))
    try:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=p)
    except Exception as e:
        log.error("InfluxDB write failed for light %d: %s", idx, e)


# ── Decision loop ─────────────────────────────────────────────────────────────

def in_after_midnight_window(now: datetime) -> bool:
    """00:30 ≤ now < 07:00 local time. `now` is local-tz-aware."""
    return dtime(0, 30) <= now.time() < dtime(7, 0)


def check_and_control():
    now = datetime.now(LOCAL_TZ)
    weekday = now.weekday() < 5
    sunrise, sunset = todays_sun(now)
    states = fetch_current_light_states()
    occupied = house_occupied()
    long_absent = long_unoccupied() if not occupied else False
    log.info(
        "tick: %s sunrise=%s sunset=%s occupied=%s long_absent=%s lights_seen=%d",
        now.isoformat(timespec="seconds"),
        sunrise.strftime("%H:%M"),
        sunset.strftime("%H:%M"),
        occupied,
        long_absent,
        len(states),
    )

    # --- Front-porch scheduler (idx 47): on sunset → PORCH_OFF_HOUR.
    porch_idx = 47
    porch_state = states.get(porch_idx)
    pol = POLICIES["porch_schedule"]
    target_on = sunset <= now < now.replace(hour=PORCH_OFF_HOUR, minute=0, second=0, microsecond=0)
    if porch_state is None or porch_state[0] != target_on:
        publish_state(porch_idx, target_on, "porch_schedule")
        log_decision(porch_idx, "on" if target_on else "off",
                     "porch_schedule", category="porch_schedule")
    else:
        log_decision(porch_idx, "hold", "porch_already_correct",
                     category="porch_schedule")

    # --- CO₂-driven auto-on/off for kitchen + living-room ceiling lights.
    #     "Dark indoors" is defined as sun elevation < SUN_DARK_ELEVATION_DEG
    #     (default 8°), which kicks in well before astronomical sunset/after
    #     sunrise — matching the user's "getting pretty dark" experience.
    #     Eligible-to-turn-on windows:
    #       - morning (dark + before solar noon): idx 40 only
    #       - evening (dark + after solar noon):  idx 40 + 54
    #     Auto-off any time CO₂ drops or after midnight. Manual dismissal
    #     (light off without us asking) suppresses re-enable until next day.
    co2 = co2_signal_class()
    today = now.date()
    try:
        sun_elev = sun_elevation(LOC.observer, dateandtime=now)
    except Exception:
        sun_elev = 0.0  # conservative: treat as borderline
    is_dark_now = sun_elev < SUN_DARK_ELEVATION_DEG
    is_morning = now.hour < 12
    eligible_for_on: set[int] = set()
    if is_dark_now:
        eligible_for_on.add(CO2_AUTO_KITCHEN_IDX)
        if not is_morning:
            eligible_for_on.add(CO2_AUTO_LIVINGROOM_IDX)

    # Drop stale dismissal entries from previous days
    for idx_d, date_d in list(_co2_dismissed_date.items()):
        if date_d < today:
            del _co2_dismissed_date[idx_d]

    for idx_co2 in CO2_AUTO_MANAGED:
        co2_state = states.get(idx_co2)
        if co2_state is None:
            continue
        currently_on = co2_state[0]

        # Track whether our auto-on actually took effect at the PLC. We
        # need this to distinguish a real user dismissal from a publish
        # that silently failed (relay unresponsive, MQTT lost, etc.).
        auto_on_t = _co2_auto_on_at.get(idx_co2)
        if auto_on_t is not None:
            if currently_on:
                # Relay confirmed our publish.
                _co2_auto_on_confirmed[idx_co2] = True
            elif _co2_auto_on_confirmed.get(idx_co2):
                # Was on, now off → user turned it off after our auto-on.
                _co2_dismissed_date[idx_co2] = today
                _co2_auto_on_at.pop(idx_co2, None)
                _co2_auto_on_confirmed.pop(idx_co2, None)
            elif (now - auto_on_t).total_seconds() > _CO2_PUBLISH_GRACE_SECONDS:
                # Publish never confirmed — relay/PLC isn't responding.
                # Clear the attempt without marking dismissed so we keep
                # retrying on the next eligible tick.
                _co2_auto_on_at.pop(idx_co2, None)

        dismissed_today = _co2_dismissed_date.get(idx_co2) == today

        if currently_on:
            if in_after_midnight_window(now):
                publish_state(idx_co2, False, "co2_auto_after_midnight")
                log_decision(idx_co2, "off", "after_midnight", category="co2_auto")
                _co2_auto_on_at.pop(idx_co2, None)
                _co2_auto_on_confirmed.pop(idx_co2, None)
            elif co2 == "DROPPED":
                publish_state(idx_co2, False, "co2_no_occupancy")
                log_decision(idx_co2, "off", "co2_no_occupancy", category="co2_auto")
                _co2_auto_on_at.pop(idx_co2, None)
                _co2_auto_on_confirmed.pop(idx_co2, None)
            else:
                log_decision(idx_co2, "hold", f"co2_{co2.lower()}", category="co2_auto")
        else:
            if dismissed_today:
                log_decision(idx_co2, "hold", "dismissed_today", category="co2_auto")
            elif idx_co2 not in eligible_for_on:
                log_decision(idx_co2, "hold", "outside_dark_window", category="co2_auto")
            elif co2 == "ELEVATED":
                window = "morning" if is_morning else "evening"
                publish_state(idx_co2, True, f"co2_occupancy_{window}")
                _co2_auto_on_at[idx_co2] = now
                _co2_auto_on_confirmed.pop(idx_co2, None)
                log_decision(idx_co2, "on", f"co2_occupancy_{window}", category="co2_auto")
                # Brief pause so a second publish in the same tick (the
                # other CO₂-managed light) doesn't pile onto the PLC's
                # MQTT command handler before it has finished the first.
                time.sleep(0.3)
            else:
                log_decision(idx_co2, "hold", f"co2_{co2.lower()}", category="co2_auto")

    # --- Sauna laude LED: ON when sauna ≥ SAUNA_LAUDE_ON_C, OFF when sauna
    #     ≤ SAUNA_LAUDE_OFF_C. Hysteresis dead-band keeps it stable as
    #     löyly causes brief drops. Acts regardless of current on/off state.
    laude_state = states.get(SAUNA_LAUDE_IDX)
    sauna_temp = fetch_sauna_temp_recent()
    if laude_state is not None and sauna_temp is not None:
        currently_on = laude_state[0]
        if currently_on and sauna_temp <= SAUNA_LAUDE_OFF_C:
            target_on = False
            reason = f"sauna_cooled_to_{sauna_temp:.1f}C"
        elif not currently_on and sauna_temp >= SAUNA_LAUDE_ON_C:
            target_on = True
            reason = f"sauna_heated_to_{sauna_temp:.1f}C"
        else:
            target_on = currently_on  # within dead-band → hold
            reason = f"hysteresis_hold_{sauna_temp:.1f}C"
        if target_on != currently_on:
            publish_state(SAUNA_LAUDE_IDX, target_on, reason)
            log_decision(SAUNA_LAUDE_IDX, "on" if target_on else "off",
                         reason, category="sauna_laude")
        else:
            log_decision(SAUNA_LAUDE_IDX, "hold", reason, category="sauna_laude")
    elif laude_state is not None:
        log_decision(SAUNA_LAUDE_IDX, "hold", "no_sauna_temp_data",
                     category="sauna_laude")

    # --- Per-light evaluation
    for idx, (is_on, _) in states.items():
        if idx == porch_idx:
            continue  # handled above
        if idx == SAUNA_LAUDE_IDX:
            continue  # handled above
        if idx in CO2_AUTO_MANAGED:
            continue  # handled above
        if not is_on:
            continue

        cat = LIGHT_POLICY.get(idx, "manual_only")
        pol = POLICIES[cat]

        # Manual-on grace window
        on_t = fetch_last_zero_to_one(idx)
        on_dur = (datetime.now(timezone.utc) - on_t).total_seconds() / 60.0 if on_t else None
        if on_dur is not None and on_dur < pol.min_hold_after_manual_min:
            log_decision(idx, "hold", "manual_grace", on_dur, cat)
            continue

        reason = None

        if pol.auto_off_after_sunrise_min is not None and now >= sunrise + timedelta(
                minutes=pol.auto_off_after_sunrise_min):
            reason = "after_sunrise"
        elif pol.auto_off_when_unoccupied and (
                (weekday and WORKDAY_START_HOUR <= now.hour < WORKDAY_END_HOUR and not occupied)
                or (idx not in ABSENCE_EXEMPT_INDICES and long_absent)
        ):
            reason = "house_unoccupied"
        elif pol.auto_off_after_on_duration_min is not None and on_dur is not None \
                and on_dur >= pol.auto_off_after_on_duration_min:
            reason = "duration_exceeded"
        elif pol.auto_off_after_midnight and in_after_midnight_window(now):
            reason = "after_midnight"

        if reason:
            publish_state(idx, False, reason)
            log_decision(idx, "off", reason, on_dur, cat)
        else:
            log_decision(idx, "hold", "no_rule_fired", on_dur, cat)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global influx_client, write_api, query_api

    log.info("=" * 60)
    log.info("Lights Optimizer")
    log.info("=" * 60)
    log.info("HOME=%.4f,%.4f  TZ=%s  DRY_RUN=%s", HOME_LAT, HOME_LON, LOCAL_TZ, DRY_RUN)
    log.info("CHECK_INTERVAL=%ds  sunrise_grace=%dm  occ_window=%dm  long_absence=%dm",
             CHECK_INTERVAL, SUNRISE_GRACE_MIN, OCCUPANCY_WINDOW_MIN, LONG_ABSENCE_MIN)
    log.info("-" * 60)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    influx_client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    try:
        log.info("InfluxDB: %s", influx_client.health().status)
    except Exception as e:
        log.warning("InfluxDB health check: %s", e)
    write_api = influx_client.write_api(write_options=SYNCHRONOUS)
    query_api = influx_client.query_api()

    # Initial sun calc — surface bad coordinates immediately rather than at
    # the first tick.
    sr, ss = todays_sun(datetime.now(LOCAL_TZ))
    log.info("Today's sun: rise=%s set=%s", sr.isoformat(timespec="seconds"),
             ss.isoformat(timespec="seconds"))

    # Run once immediately, then on the configured interval.
    while running:
        try:
            check_and_control()
        except Exception as e:
            log.exception("check_and_control failed: %s", e)

        # Interruptible sleep
        end = time.monotonic() + CHECK_INTERVAL
        while running and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))

    if influx_client:
        influx_client.close()
    log.info("Shutdown complete")


if __name__ == "__main__":
    main()
