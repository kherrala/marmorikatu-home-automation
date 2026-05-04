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
from astral.sun import sun
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
TERRACE_OFF_HOUR = int(os.environ.get("TERRACE_OFF_HOUR", "22"))

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
    4:  "manual_only",
    17: "manual_only",  # MH alakerta kattovalo (downstairs bedroom / workspace)
    18: "manual_only",  # MH alakerta ikkuna    (downstairs bedroom / workspace)
    38: "manual_only", 39: "manual_only",
    47: "manual_only",
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

    # Kitchen
    2: "kitchen", 7: "kitchen", 8: "kitchen", 40: "kitchen", 41: "kitchen",

    # Living / dining
    5: "livingroom", 19: "livingroom", 20: "livingroom",
    46: "livingroom", 54: "livingroom", 55: "livingroom",

    # Staircase — transient, often forgotten
    25: "staircase",   # Aula rappuset
    42: "staircase",   # Portaikko

    # Common / general
    3: "general", 6: "general", 24: "general", 26: "general",
    35: "general", 36: "general", 37: "general", 43: "general", 56: "general",

    # Schedule-driven
    48: "terrace_schedule",  # Ulkovalo terassi
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
    "terrace_schedule": Policy(None, False, None,                  False, 5, terrace_schedule=True),
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

    # --- Terrace scheduler runs first; cares about state regardless of on/off
    terrace_idx = 48
    terr_state = states.get(terrace_idx)
    pol = POLICIES["terrace_schedule"]
    target_on = sunset <= now < now.replace(hour=TERRACE_OFF_HOUR, minute=0, second=0, microsecond=0)
    if terr_state is None or terr_state[0] != target_on:
        publish_state(terrace_idx, target_on, "terrace_schedule")
        log_decision(terrace_idx, "on" if target_on else "off",
                     "terrace_schedule", category="terrace_schedule")
    else:
        log_decision(terrace_idx, "hold", "terrace_already_correct",
                     category="terrace_schedule")

    # --- Per-light evaluation
    for idx, (is_on, _) in states.items():
        if idx == terrace_idx:
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
