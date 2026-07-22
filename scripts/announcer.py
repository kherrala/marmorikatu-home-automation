#!/usr/bin/env python3
"""
Kiosk announcer service.

Polls InfluxDB for state changes that the user might want to hear about and
pushes Finnish-language announcement events to claude-bridge's
/announcements/push endpoint. The bridge fans out to every connected kiosk
via SSE; the kiosk speaks the text via the existing Piper TTS path even
when the camera/face-detection greeting hasn't been triggered.

Event sources mined here:
  - HVAC freezing alarm (alarm.Alarm_freezing_danger rising edge)
  - HVAC alarm-flag rising edges (filter guard, IR sensor, fan failures, …)
  - Sauna state transitions (off → heating → hot → cooling)
  - lights_optimizer decisions (auto-off triggers, sauna-laude on/off,
    CO2-driven kitchen/livingroom on/off, post-sauna cleanup)
  - heating_optimizer tier transitions (CHEAP / NORMAL / EXPENSIVE / PRE_HEAT)
  - Raw light on/off (verbose / debug only)
  - Air-quality class transitions (CO2 ppm and PM2.5 µg/m³ from Ruuvi format 225)

Each event is classified into a priority tier:
  0 = critical (always announced; leak / freezing / sensor faults)
  1 = normal   (sauna on, expensive starting, auto-off, CO2 high, …)
  2 = verbose  (tier transitions to CHEAP/NORMAL, CO2 elevated, …)
  3 = debug    (every individual light on/off)

The service drops anything with priority > ANNOUNCE_VERBOSITY (default 1).
The kiosk is responsible for quiet hours & the overnight-digest replay.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import urllib.error
import urllib.parse
import urllib.request

from influxdb_client import InfluxDBClient

from health import touch_health
from light_labels import LIGHT_LABELS

# ── Configuration ────────────────────────────────────────────────────────────

INFLUXDB_URL    = os.environ.get("INFLUXDB_URL",    "http://localhost:8086")
INFLUXDB_TOKEN  = os.environ.get("INFLUXDB_TOKEN",  "wago-secret-token")
INFLUXDB_ORG    = os.environ.get("INFLUXDB_ORG",    "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

BRIDGE_PUSH_URL = os.environ.get(
    "BRIDGE_PUSH_URL", "http://claude-bridge:3002/announcements/push"
)
PUSH_TOKEN     = os.environ.get("ANNOUNCE_PUSH_TOKEN", "")
PUSH_TIMEOUT_S = float(os.environ.get("ANNOUNCE_PUSH_TIMEOUT", "5"))

# 0=critical only, 1=normal, 2=verbose, 3=debug-every-light.
# Default 3 for initial rollout — surface everything we know how to detect.
VERBOSITY      = int(os.environ.get("ANNOUNCE_VERBOSITY", "3"))
POLL_INTERVAL  = int(os.environ.get("ANNOUNCE_POLL_INTERVAL", "30"))
# Suppress the raw "syttyi/sammui" echo of a light the optimizer just actuated
# (which already got a richer "...automaattisesti" announcement) — matched by
# direction, within this window of the optimizer decision (covers PLC latency).
RAW_ECHO_SUPPRESS_S = float(os.environ.get("RAW_ECHO_SUPPRESS_S", "60"))
# Liveness: exit non-zero after this many consecutive failed ticks so the
# container crash-loops visibly instead of looping forever. See scripts/health.py.
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("MAX_CONSECUTIVE_FAILURES", "5"))

# Suppress noisy bursts: don't push more than this many events per tick.
MAX_PER_TICK   = int(os.environ.get("ANNOUNCE_MAX_PER_TICK", "5"))

# ── Periodic news headlines ──────────────────────────────────────────────────
# Speak the top headlines once per clock-hour during the day. Independent of
# ANNOUNCE_VERBOSITY (pushed with force=True) so news isn't silenced when the
# rest of the announcements are turned down. Quiet hours on the kiosk still
# apply, but the 07–20 window is inside waking hours anyway.
LOCAL_TZ            = ZoneInfo(os.environ.get("LOCAL_TZ", "Europe/Helsinki"))
NEWS_ENABLED        = os.environ.get("NEWS_ANNOUNCE_ENABLED", "1") == "1"
NEWS_API_URL        = os.environ.get("NEWS_API_URL", "http://news:3021/api/news")
NEWS_START_HOUR     = int(os.environ.get("NEWS_ANNOUNCE_START_HOUR", "7"))   # inclusive
NEWS_END_HOUR       = int(os.environ.get("NEWS_ANNOUNCE_END_HOUR", "20"))    # exclusive
NEWS_NATIONAL_N     = int(os.environ.get("NEWS_ANNOUNCE_NATIONAL_COUNT", "3"))
NEWS_REGIONAL_N     = int(os.environ.get("NEWS_ANNOUNCE_REGIONAL_COUNT", "1"))
NEWS_NATIONAL_SRC   = os.environ.get("NEWS_ANNOUNCE_NATIONAL_SOURCE", "Uutiset")
NEWS_REGIONAL_SRC   = os.environ.get("NEWS_ANNOUNCE_REGIONAL_SOURCE", "Pirkanmaa")
NEWS_FETCH_TIMEOUT  = float(os.environ.get("NEWS_ANNOUNCE_FETCH_TIMEOUT", "8"))

# Air-quality classification thresholds (ppm CO2, µg/m³ PM2.5).
CO2_ELEVATED   = float(os.environ.get("ANNOUNCE_CO2_ELEVATED", "800"))
CO2_HIGH       = float(os.environ.get("ANNOUNCE_CO2_HIGH",     "1100"))
CO2_VERY_HIGH  = float(os.environ.get("ANNOUNCE_CO2_VERY_HIGH", "1500"))
PM25_ELEVATED  = float(os.environ.get("ANNOUNCE_PM25_ELEVATED", "12"))
PM25_HIGH      = float(os.environ.get("ANNOUNCE_PM25_HIGH",     "35"))

# Sauna state thresholds (°C, Ruuvi sensor "Sauna").
SAUNA_HEATING_C = float(os.environ.get("ANNOUNCE_SAUNA_HEATING_C", "45"))
SAUNA_HOT_C     = float(os.environ.get("ANNOUNCE_SAUNA_HOT_C",     "70"))
SAUNA_OFF_C     = float(os.environ.get("ANNOUNCE_SAUNA_OFF_C",     "40"))

# Wasted-electricity warning: once the sauna has been continuously in
# heating/hot state for SAUNA_WASTE_AFTER_MIN minutes (default 2 h), nag
# every SAUNA_WASTE_REPEAT_MIN minutes (default 15 min) until the heater
# is turned off (state leaves heating/hot). Marked priority 0 so the
# kiosk plays it through quiet hours — leaving the kiuas on overnight is
# exactly the case this warning exists for.
SAUNA_WASTE_AFTER_MIN  = int(os.environ.get("ANNOUNCE_SAUNA_WASTE_AFTER_MIN",  "120"))
SAUNA_WASTE_REPEAT_MIN = int(os.environ.get("ANNOUNCE_SAUNA_WASTE_REPEAT_MIN", "15"))

# Indoor temperature thresholds (°C). Hysteresis prevents flapping.
INDOOR_TEMP_LOW_C  = float(os.environ.get("ANNOUNCE_INDOOR_LOW_C",  "18.0"))
INDOOR_TEMP_HIGH_C = float(os.environ.get("ANNOUNCE_INDOOR_HIGH_C", "26.0"))
INDOOR_TEMP_HYST_C = float(os.environ.get("ANNOUNCE_INDOOR_HYST_C", "0.5"))

# Outdoor temperature class boundaries (°C). Crossings fire announcements.
OUTDOOR_FREEZE_C = float(os.environ.get("ANNOUNCE_OUTDOOR_FREEZE_C", "-5"))
OUTDOOR_DEEP_C   = float(os.environ.get("ANNOUNCE_OUTDOOR_DEEP_C",   "-15"))
OUTDOOR_THAW_C   = float(os.environ.get("ANNOUNCE_OUTDOOR_THAW_C",   "5"))

# Per-floor "too hot" warning: the upstairs and downstairs each have their own
# cooling HVAC, so warn per floor (its hottest room) and point at that floor's
# cooler — the basement has none and runs cool, so it's excluded (see ROOM_FLOOR
# / COOLED_FLOORS). Hysteresis prevents flapping around the threshold.
FLOOR_HOT_C      = float(os.environ.get("ANNOUNCE_FLOOR_HOT_C", "25.0"))
FLOOR_HOT_HYST_C = float(os.environ.get("ANNOUNCE_FLOOR_HOT_HYST_C", "0.5"))

# Weather warnings (helle / myrsky) are deduced by the weather service, which is
# the single source of the verdict (the threshold lives there, shared with the
# kiosk weather card). The announcer just polls the forecast endpoint and speaks
# the warnings it carries — see _weather_warnings.
WEATHER_API_URL   = os.environ.get("WEATHER_API_URL", "http://weather:3020/api/weather")
WEATHER_TIMEOUT_S = float(os.environ.get("ANNOUNCE_WEATHER_TIMEOUT", "6"))
WEATHER_CHECK_S   = int(os.environ.get("ANNOUNCE_WEATHER_CHECK_S", "600"))

# PLC heartbeat: alarm if no plc_publisher write seen in N seconds.
PLC_HEARTBEAT_LOSS_S = int(os.environ.get("ANNOUNCE_PLC_HEARTBEAT_LOSS_S", "180"))

# LTO heat-recovery efficiency: warn when sustained below LOW_RATIO for
# LOW_DURATION_MIN. The sensible-LTO formula needs a meaningful
# (Poistoilma − Ulkolampotila) gap; we skip when |gap| < MIN_DELTA_C.
LTO_LOW_RATIO        = float(os.environ.get("ANNOUNCE_LTO_LOW_RATIO",        "0.60"))
LTO_LOW_DURATION_MIN = int(os.environ.get("ANNOUNCE_LTO_LOW_DURATION_MIN", "15"))
LTO_MIN_DELTA_C      = float(os.environ.get("ANNOUNCE_LTO_MIN_DELTA_C",     "5"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("announcer")

# ── Helpers ──────────────────────────────────────────────────────────────────

@dataclass
class Event:
    text: str
    kind: str
    priority: int     # 0..3 — 0=critical
    key: str          # dedup key used on the kiosk
    ts: float         # event source-time (epoch seconds)


def _fetch_headlines(source: str, limit: int) -> list[dict]:
    """GET the top `limit` headlines for one source from the news service.
    Returns [] on any error — news is best-effort, never fatal to the tick."""
    url = f"{NEWS_API_URL}?source={urllib.parse.quote(source)}&limit={limit}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=NEWS_FETCH_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning("news fetch (%s) failed: %s", source, e)
        return []


def _push(event: Event, *, force: bool = False) -> None:
    if not force and event.priority > VERBOSITY:
        return
    payload = json.dumps({
        "text":     event.text,
        "kind":     event.kind,
        "priority": event.priority,
        "key":      event.key,
        "ts":       event.ts,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if PUSH_TOKEN:
        headers["X-Announce-Token"] = PUSH_TOKEN
    req = urllib.request.Request(BRIDGE_PUSH_URL, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=PUSH_TIMEOUT_S) as resp:
            log.info("pushed %s [p%d]: %s", event.kind, event.priority, event.text)
    except urllib.error.URLError as e:
        log.warning("push %s failed: %s", event.kind, e)
    except Exception as e:
        log.warning("push %s failed: %s", event.kind, e)


def _co2_class(ppm: float) -> str:
    if ppm >= CO2_VERY_HIGH: return "very_high"
    if ppm >= CO2_HIGH:      return "high"
    if ppm >= CO2_ELEVATED:  return "elevated"
    return "good"


def _pm25_class(ug: float) -> str:
    if ug >= PM25_HIGH:     return "high"
    if ug >= PM25_ELEVATED: return "elevated"
    return "good"


def _sauna_state(temp_c: float, prev: str) -> str:
    """Hysteresis state machine for sauna heating sessions."""
    if temp_c >= SAUNA_HOT_C:
        return "hot"
    if temp_c >= SAUNA_HEATING_C:
        # heating up, OR cooling down through this band — keep prior direction
        if prev in ("hot", "cooling"):
            return "cooling"
        return "heating"
    if temp_c < SAUNA_OFF_C:
        return "off"
    # in the 40–45 band: stay in whatever side we came from to avoid flapping
    return prev or "off"


# ── InfluxDB queries ─────────────────────────────────────────────────────────

class Influx:
    def __init__(self, client: InfluxDBClient):
        self.q = client.query_api()

    def _query(self, flux: str) -> list:
        try:
            return self.q.query(flux, org=INFLUXDB_ORG)
        except Exception as e:
            log.warning("flux query failed: %s", e)
            return []

    def latest_alarm_flags(self) -> dict[str, tuple[float, datetime]]:
        """Return {field_name: (value, time)} for every Casa MVHR alarm flag.

        The plc subscriber writes alarm flags into the `hvac` measurement
        with `sensor_group="alarm"` (alongside the other ventilation sensor
        groups), not into a standalone `alarm` measurement.
        """
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -10m)\n'
            f'  |> filter(fn: (r) => r._measurement == "hvac" and r.sensor_group == "alarm")\n'
            f'  |> last()\n'
        )
        out: dict[str, tuple[float, datetime]] = {}
        for table in self._query(flux):
            for rec in table.records:
                field = rec.get_field()
                val   = rec.get_value()
                ts    = rec.get_time()
                if field is None or val is None or ts is None:
                    continue
                try:
                    out[field] = (float(val), ts)
                except (TypeError, ValueError):
                    pass
        return out

    def latest_lights(self) -> dict[int, tuple[int, datetime]]:
        """Return {light_id_int: (is_on, time)} for every light.

        The lights measurement uses the `light_id` tag (set by the plc
        subscriber); there is no `id` tag.
        """
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -10m)\n'
            f'  |> filter(fn: (r) => r._measurement == "lights" and r._field == "is_on")\n'
            f'  |> last()\n'
        )
        out: dict[int, tuple[int, datetime]] = {}
        for table in self._query(flux):
            for rec in table.records:
                tag = rec.values.get("light_id")
                ts  = rec.get_time()
                if tag is None or ts is None:
                    continue
                try:
                    out[int(tag)] = (1 if float(rec.get_value()) > 0.5 else 0, ts)
                except (TypeError, ValueError):
                    pass
        return out

    def latest_sauna_temp(self) -> tuple[float | None, datetime | None]:
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -15m)\n'
            f'  |> filter(fn: (r) => r._measurement == "ruuvi" and r._field == "temperature" '
            f'and r.sensor_name == "Sauna")\n'
            f'  |> last()\n'
        )
        for table in self._query(flux):
            for rec in table.records:
                try:
                    return float(rec.get_value()), rec.get_time()
                except (TypeError, ValueError):
                    pass
        return None, None

    def latest_air_quality(self) -> dict[str, dict]:
        """Return {sensor_name: {"co2": ppm?, "pm25": ug?, "ts": time}}."""
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -15m)\n'
            f'  |> filter(fn: (r) => r._measurement == "ruuvi" '
            f'and (r._field == "co2" or r._field == "pm2_5"))\n'
            f'  |> last()\n'
        )
        out: dict[str, dict] = {}
        for table in self._query(flux):
            for rec in table.records:
                name  = rec.values.get("sensor_name")
                field = rec.get_field()
                val   = rec.get_value()
                ts    = rec.get_time()
                if not name or val is None or ts is None:
                    continue
                slot = out.setdefault(name, {"ts": ts})
                if field == "co2":
                    try:
                        slot["co2"] = float(val)
                    except (TypeError, ValueError):
                        pass
                elif field == "pm2_5":
                    try:
                        slot["pm25"] = float(val)
                    except (TypeError, ValueError):
                        pass
                if ts > slot["ts"]:
                    slot["ts"] = ts
        return out

    def latest_heating_tier(self) -> tuple[str | None, float | None, datetime | None]:
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -2h)\n'
            f'  |> filter(fn: (r) => r._measurement == "heating_optimizer" '
            f'and (r._field == "tier" or r._field == "price"))\n'
            f'  |> last()\n'
        )
        tier: str | None = None
        price: float | None = None
        ts: datetime | None = None
        for table in self._query(flux):
            for rec in table.records:
                f, v, t = rec.get_field(), rec.get_value(), rec.get_time()
                if t is not None and (ts is None or t > ts):
                    ts = t
                if f == "tier" and isinstance(v, str):
                    tier = v
                elif f == "price" and v is not None:
                    try:
                        price = float(v)
                    except (TypeError, ValueError):
                        pass
        return tier, price, ts

    def latest_thermia_aux(self) -> dict[str, tuple[float, datetime]]:
        """Latest aux-heater states from Thermia. Boolean fields (1/0)."""
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -10m)\n'
            f'  |> filter(fn: (r) => r._measurement == "thermia" '
            f'and (r._field == "aux_heater_3kw" or r._field == "aux_heater_6kw"))\n'
            f'  |> last()\n'
        )
        out: dict[str, tuple[float, datetime]] = {}
        for table in self._query(flux):
            for rec in table.records:
                f, v, t = rec.get_field(), rec.get_value(), rec.get_time()
                if f and v is not None and t is not None:
                    try:
                        out[f] = (float(v), t)
                    except (TypeError, ValueError):
                        pass
        return out

    def latest_thermia_alarms(self) -> dict[str, tuple[float, datetime]]:
        """Latest Thermia fault flags (thermia, data_type=alarm, alarm_* fields).
        Range-limited so a dead ThermIQ feed (>10 min) yields nothing rather than
        replaying a stale fault. alarm_indoor_sensor is dropped (constant 1)."""
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -10m)\n'
            f'  |> filter(fn: (r) => r._measurement == "thermia" '
            f'and r._field =~ /^alarm_/ and r._field != "alarm_indoor_sensor")\n'
            f'  |> last()\n'
        )
        out: dict[str, tuple[float, datetime]] = {}
        for table in self._query(flux):
            for rec in table.records:
                f, v, t = rec.get_field(), rec.get_value(), rec.get_time()
                if f and v is not None and t is not None:
                    try:
                        out[f] = (float(v), t)
                    except (TypeError, ValueError):
                        pass
        return out

    def latest_ruuvi_env(self) -> dict[str, dict]:
        """Per-sensor latest temperature + voltage (+ freshest time) for the
        freezer/fridge/battery/offline checks. Wide range so a stalled tag still
        returns its last-seen time (enables offline detection)."""
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -2h)\n'
            f'  |> filter(fn: (r) => r._measurement == "ruuvi" '
            f'and (r._field == "temperature" or r._field == "voltage"))\n'
            f'  |> last()\n'
        )
        out: dict[str, dict] = {}
        for table in self._query(flux):
            for rec in table.records:
                name = rec.values.get("sensor_name")
                field, val, ts = rec.get_field(), rec.get_value(), rec.get_time()
                if not name or val is None or ts is None:
                    continue
                slot = out.setdefault(name, {})
                try:
                    slot[field] = float(val)
                except (TypeError, ValueError):
                    continue
                if slot.get("ts") is None or ts > slot["ts"]:
                    slot["ts"] = ts
        return out

    def latest_iv_mode(self) -> tuple[float | None, datetime | None]:
        """Latest Casa MVHR OperatingMode (hvac.IV_tila)."""
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -15m)\n'
            f'  |> filter(fn: (r) => r._measurement == "hvac" and r._field == "IV_tila")\n'
            f'  |> last()\n'
        )
        for table in self._query(flux):
            for rec in table.records:
                try:
                    return float(rec.get_value()), rec.get_time()
                except (TypeError, ValueError):
                    pass
        return None, None

    def latest_outdoor_temp(self) -> tuple[float | None, datetime | None]:
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -15m)\n'
            f'  |> filter(fn: (r) => r._measurement == "hvac" and r._field == "Ulkolampotila")\n'
            f'  |> last()\n'
        )
        for table in self._query(flux):
            for rec in table.records:
                try:
                    return float(rec.get_value()), rec.get_time()
                except (TypeError, ValueError):
                    pass
        return None, None

    def latest_room_temps(self) -> dict[str, tuple[float, datetime]]:
        """Selected indoor room temperatures from the rooms measurement."""
        keys = list(ROOM_LABELS_FI.keys())
        pred = " or ".join([f'r._field == "{k}"' for k in keys])
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -10m)\n'
            f'  |> filter(fn: (r) => r._measurement == "rooms" and ({pred}))\n'
            f'  |> last()\n'
        )
        out: dict[str, tuple[float, datetime]] = {}
        for table in self._query(flux):
            for rec in table.records:
                f, v, t = rec.get_field(), rec.get_value(), rec.get_time()
                if f and v is not None and t is not None:
                    try:
                        out[f] = (float(v), t)
                    except (TypeError, ValueError):
                        pass
        return out

    def latest_plc_heartbeat(self) -> datetime | None:
        """Latest write time of any plc_publisher field — liveness signal."""
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -1h)\n'
            f'  |> filter(fn: (r) => r._measurement == "plc_publisher")\n'
            f'  |> last()\n'
        )
        latest: datetime | None = None
        for table in self._query(flux):
            for rec in table.records:
                t = rec.get_time()
                if t is not None and (latest is None or t > latest):
                    latest = t
        return latest

    def latest_lto_efficiency(self) -> float | None:
        """Sensible LTO efficiency = (Tuloilma_ennen − Ulko) / (Poisto − Ulko)."""
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: -10m)\n'
            f'  |> filter(fn: (r) => r._measurement == "hvac" '
            f'and (r._field == "Tuloilma_ennen_lammitysta" '
            f'or r._field == "Poistoilma" or r._field == "Ulkolampotila"))\n'
            f'  |> last()\n'
        )
        vals: dict[str, float] = {}
        for table in self._query(flux):
            for rec in table.records:
                f, v = rec.get_field(), rec.get_value()
                if f and v is not None:
                    try:
                        vals[f] = float(v)
                    except (TypeError, ValueError):
                        pass
        tu = vals.get("Tuloilma_ennen_lammitysta")
        po = vals.get("Poistoilma")
        ul = vals.get("Ulkolampotila")
        if tu is None or po is None or ul is None:
            return None
        denom = po - ul
        if abs(denom) < LTO_MIN_DELTA_C:
            return None  # gap too small for the formula to be meaningful
        return (tu - ul) / denom

    def lights_optimizer_decisions_since(self, since: datetime) -> list[dict]:
        """Return all lights_optimizer rows after `since`, deduplicated per
        (light_id, decision) keeping the most recent."""
        # Pivot so decision/reason/category come back as columns on one row.
        ts_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        flux = (
            f'from(bucket: "{INFLUXDB_BUCKET}")\n'
            f'  |> range(start: {ts_iso})\n'
            f'  |> filter(fn: (r) => r._measurement == "lights_optimizer")\n'
            f'  |> filter(fn: (r) => r._field == "decision" or r._field == "reason" '
            f'or r._field == "dry_run" or r._field == "on_duration_min")\n'
            f'  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n'
        )
        rows: list[dict] = []
        for table in self._query(flux):
            for rec in table.records:
                rows.append({
                    "ts":        rec.get_time(),
                    "light_id":  rec.values.get("light_id"),
                    "light_name": rec.values.get("light_name"),
                    "category":  rec.values.get("category"),
                    "decision":  rec.values.get("decision"),
                    "reason":    rec.values.get("reason"),
                    "on_duration_min": rec.values.get("on_duration_min"),
                    "dry_run":   rec.values.get("dry_run"),
                })
        rows.sort(key=lambda r: r["ts"] or datetime.min.replace(tzinfo=timezone.utc))
        return rows


# ── Friendly text formatting ─────────────────────────────────────────────────

ALARM_TEXT_FI = {
    "Alarm_freezing_danger":     ("Ilmanvaihdon jäätymisvaroitus aktivoitunut.",            0),
    "Alarm_filter_guard":        ("Ilmanvaihdon suodattimet kaipaavat huoltoa.",            1),
    "Alarm_efficiency":          ("Ilmanvaihdon hyötysuhde on heikentynyt.",                1),
    "Alarm_temp_deviation":      ("Ilmanvaihdon lämpötilapoikkeama havaittu.",              1),
    "Alarm_IR_sensor":           ("Ilmanvaihdon IR-tunnistin hälyttää.",                    1),
    "Alarm_overheat_after":      ("Jälkilämmittimen ylikuumeneminen.",                      0),
    "Alarm_fan_failure_supply":  ("Tuloilmapuhaltimen vika.",                               0),
    "Alarm_fan_failure_extract": ("Poistoilmapuhaltimen vika.",                             0),
    "Alarm_temp_sensor":         ("Ilmanvaihdon lämpötila-anturin vika.",                   1),
    "Alarm_service_reminder":    ("Ilmanvaihto kaipaa määräaikaishuoltoa.",                 1),
    "Jalkilammitin_ylikuume":    ("Jälkilämmittimen ylikuumeneminen havaittu.",             0),
    "Esilammitin_ylikuume":      ("Esilämmittimen ylikuumeneminen havaittu.",               0),
}

# How often a still-active CRITICAL (priority 0) alarm re-announces. Warn/info
# alarms speak once on the rising edge; criticals repeat until the condition clears.
ALARM_REPEAT_S = float(os.environ.get("ALARM_REPEAT_S", "300"))

# Thermia heat-pump faults — written to `thermia` (data_type=alarm) as boolean
# alarm_* fields. d19 hard faults + phase-order + overheating are critical (0,
# repeat); sensor faults are warn (1, once). alarm_indoor_sensor is omitted on
# purpose — it is a constant 1 (wireless indoor unit), not a real fault.
THERMIA_ALARM_TEXT_FI = {
    "alarm_highpr_pressostate": ("Maalämpöpumpun korkeapainehälytys.",                    0),
    "alarm_lowpr_pressostate":  ("Maalämpöpumpun matalapainehälytys.",                    0),
    "alarm_motor_breaker":      ("Maalämpöpumpun kompressorin moottorisuoja on lauennut.", 0),
    "alarm_low_flow_brine":     ("Maalämpöpumpun liuospiirin virtaus on liian matala.",   0),
    "alarm_low_temp_brine":     ("Maalämpöpumpun liuoslämpötila on liian matala.",        0),
    "alarm_3phase_order":       ("Maalämpöpumpun vaihejärjestys on väärin.",              0),
    "alarm_overheating":        ("Maalämpöpumpun ylikuumeneminen.",                       0),
    "alarm_outdoor_sensor":     ("Maalämpöpumpun ulkoanturin vika.",                      1),
    "alarm_supply_sensor":      ("Maalämpöpumpun menoveden anturin vika.",                1),
    "alarm_return_sensor":      ("Maalämpöpumpun paluuveden anturin vika.",               1),
    "alarm_hotwater_sensor":    ("Maalämpöpumpun käyttöveden anturin vika.",              1),
}

# Ruuvi environment alert thresholds (mobile-app parity).
FREEZER_SENSOR  = os.environ.get("RUUVI_FREEZER_NAME", "Pakastin")
FRIDGE_SENSOR   = os.environ.get("RUUVI_FRIDGE_NAME", "Jääkaappi")
FREEZER_WARM_C  = float(os.environ.get("FREEZER_WARM_C", "-15"))
FRIDGE_WARM_C   = float(os.environ.get("FRIDGE_WARM_C", "8"))
RUUVI_OFFLINE_S = float(os.environ.get("RUUVI_OFFLINE_S", "1800"))  # 30 min

# Ventilation humidity-boost ("power") mode. Casa MVHR OperatingMode (IV_tila)
# switches to IV_BOOST_MODE when it boosts on humidity; it can cycle often, so
# each direction is rate-limited to IV_BOOST_GAP.
IV_BOOST_MODE = int(os.environ.get("IV_BOOST_MODE", "2"))
IV_BOOST_GAP  = float(os.environ.get("IV_BOOST_GAP", "300"))


def _iv_boost_transition(prev, cur, boost_mode=IV_BOOST_MODE):
    """Return 'on'/'off'/None for an IV_tila change that crosses the boost mode."""
    if prev is None or cur is None or prev == cur:
        return None
    was = int(round(prev)) == boost_mode
    now = int(round(cur)) == boost_mode
    if now and not was:
        return "on"
    if was and not now:
        return "off"
    return None


def _alarm_should_emit(prio: int, active: bool, prev_active: bool) -> bool:
    """Critical (priority 0) alarms re-emit whenever active — the emit cooldown
    (ALARM_REPEAT_S) paces them to every ~5 min until they clear. Warn/info
    alarms speak once, on the rising edge (inactive → active)."""
    if not active:
        return False
    if prio == 0:
        return True
    return not prev_active


def _battery_low(voltage, temp) -> bool:
    """Temperature-compensated Ruuvi low-battery test — a CR2477 coin cell sags
    in the cold, so the threshold drops with temperature (Ruuvi's guidance)."""
    if voltage is None:
        return False
    t = 20.0 if temp is None else temp
    if t < -20.0:
        thr = 2.0
    elif t < 0.0:
        thr = 2.3
    elif t < 20.0:
        thr = 2.4
    else:
        thr = 2.5
    return voltage < thr


def _emit_alarm_flags(flags: dict, text_map: dict, prev_state: dict,
                      emit_fn, bootstrap: bool) -> None:
    """Shared processor for boolean alarm-flag dicts ({field: (val, ts)}):
    rising-edge for warn/info, repeat-while-active for critical (priority 0)."""
    for field, (val, ts) in flags.items():
        prev = prev_state.get(field, 0.0)
        prev_state[field] = val
        if bootstrap:
            continue
        spec = text_map.get(field)
        if not spec:
            continue
        text, prio = spec
        if _alarm_should_emit(prio, val > 0.5, prev > 0.5):
            emit_fn(Event(text, f"alarm_on:{field}", prio, f"alarm:{field}",
                          ts.timestamp()), min_gap_s=ALARM_REPEAT_S)

TIER_TEXT_FI = {
    "EXPENSIVE": ("Sähkön kallis tunti alkaa nyt.",            1, "tier"),
    "PRE_HEAT":  ("Lämpöpumppu esilämmittää nyt halvalla.",    2, "tier"),
    "CHEAP":     ("Sähkön halpa tunti on alkanut.",            2, "tier"),
    "NORMAL":    ("Sähkön hinta on normaalitasolla.",          2, "tier"),
}

CO2_RANK = {"good": 0, "elevated": 1, "high": 2, "very_high": 3}

# Texts are direction-aware: "{room} {trend}, hiilidioksidipitoisuus {level}."
# trend is filled in based on whether the new class is worse or better than
# the previous one ("ilma tunkkanee" vs "ilma raikastuu"). Keeping a single
# template per (level, direction) cell avoids combinatorial bloat.
CO2_LEVEL_FI = {
    "good":      "raikas",
    "elevated":  "hieman koholla",
    "high":      "korkealla",
    "very_high": "erittäin korkea",
}
CO2_PRIORITY = {"elevated": 2, "high": 1, "very_high": 1, "good": 2}

PM25_RANK = {"good": 0, "elevated": 1, "high": 2}
PM25_LEVEL_FI = {
    "good":      "normaali",
    "elevated":  "koholla",
    "high":      "korkealla",
}
PM25_PRIORITY = {"elevated": 2, "high": 1, "good": 2}


def _co2_message(sensor: str, prev: str, cls: str) -> str:
    """Direction-aware CO2 announcement."""
    level = CO2_LEVEL_FI[cls]
    if CO2_RANK[cls] > CO2_RANK[prev]:
        if cls == "good":
            return f"{sensor} on raikastunut."  # unreachable but defensive
        trend = "tunkkanee"
    else:
        if cls == "good":
            return f"{sensor} on raikastunut."
        trend = "raikastuu, mutta on edelleen tunkkainen"
    return f"{sensor} {trend}, hiilidioksidipitoisuus on {level}."


def _pm25_message(sensor: str, prev: str, cls: str) -> str:
    level = PM25_LEVEL_FI[cls]
    if PM25_RANK[cls] > PM25_RANK[prev]:
        return f"{sensor} pienhiukkaspitoisuus nousee, on {level}."
    if cls == "good":
        return f"{sensor} pienhiukkaspitoisuus on palannut normaaliksi."
    return f"{sensor} pienhiukkaspitoisuus laskee, on edelleen {level}."

SAUNA_TEXT_FI = {
    "heating":  ("Sauna on lämpiämässä.",                  1),
    "hot":      ("Sauna on lämmin ja valmis löylyihin.",   1),
    "cooling":  ("Saunan lämmitys on lopetettu, sauna jäähtyy.", 1),
    "off":      ("Sauna on jäähtynyt.",                    2),
}

# Spoken-form labels for the indoor temperature sensors we monitor.
# Skips Kellari + Kellari_eteinen — basement runs intentionally cooler.
#
# The PLC schema still exposes legacy field names from the previous owners'
# children (Aatu, Onni, Essi). The actual rooms now belong to other people
# — keep the legacy keys mapped to today's labels so if the PLC ever
# publishes them again the announcer says the right thing:
#   MH2 = Aatu (old) → Aarni's room  (new MH_Aarni field)
#   MH3 = Onni (old) → Seela's room  (new MH_Seela field)
#   MH1 = Essi (old) → master bedroom (new MH_aikuiset field)
ROOM_LABELS_FI = {
    "MH_Aarni":     "Aarnin huone",
    "MH_Seela":     "Seelan huone",
    "MH_aikuiset":  "Aikuisten makuuhuone",
    "MH_alakerta":  "Alakerran makuuhuone",
    "Aatu":         "Aarnin huone",
    "Onni":         "Seelan huone",
    "Essi":         "Aikuisten makuuhuone",
    "Eteinen":      "Eteinen",
    "Olohuone":     "Olohuone",
    "Keittio":      "Keittiö",
    "Ylakerran_aula": "Yläkerran aula",
}

# Which floor each monitored room sensor sits on, for the per-floor heat warning.
# Mirrors the mobile Rooms model. Basement sensors are omitted — no cooler there.
# Legacy keys (Aatu/Onni/Essi) map to the upstairs bedrooms they used to be.
ROOM_FLOOR = {
    "MH_Aarni": "ylakerta", "MH_Seela": "ylakerta", "MH_aikuiset": "ylakerta",
    "Aatu": "ylakerta", "Onni": "ylakerta", "Essi": "ylakerta",
    "Ylakerran_aula": "ylakerta",
    "MH_alakerta": "alakerta", "Eteinen": "alakerta",
    "Olohuone": "alakerta", "Keittio": "alakerta",
}

# Each cooled floor → (spoken floor name, spoken cooler name). The basement is
# deliberately absent: it has no cooling HVAC and runs cool on its own.
COOLED_FLOORS = {
    "ylakerta": ("Yläkerta", "yläkerran jäähdytys"),
    "alakerta": ("Alakerta", "alakerran jäähdytys"),
}

# Map raw lights_optimizer decisions → spoken description + priority.
# v2 keys on the (distinct) `reason` string rather than `category`, so it is
# robust to category renames. Many reasons are diagnostic-only ("hold",
# "min_dwell_hold", "no_off_rule") — only user-relevant transitions are spoken.
def _format_lights_optimizer(row: dict) -> Event | None:
    decision = (row.get("decision") or "").lower()
    reason   = (row.get("reason") or "").lower()
    name     = row.get("light_name") or "Valo"
    on_dur   = row.get("on_duration_min")
    light_id = row.get("light_id", "")
    ts       = (row.get("ts") or datetime.now(timezone.utc)).timestamp()

    # Skip "hold" — it's the no-op outcome.
    if decision not in ("on", "off"):
        return None

    # Sauna laude (idx 4) — sensor-driven temperature hysteresis.
    if reason.startswith("sauna_heated"):
        return Event("Saunan laudevalo syttyi automaattisesti löylyjä varten.",
                     "lights_opt_sauna_on", 1, "lights_opt_sauna_laude", ts)
    if reason.startswith("sauna_cooled"):
        return Event("Saunan laudevalo sammui — sauna on jäähtynyt.",
                     "lights_opt_sauna_off", 1, "lights_opt_sauna_laude", ts)

    # Post-sauna cleanup of bathroom + sauna ceiling (idx 1, 38, 39).
    if reason.startswith("post_sauna"):
        return Event(f"{name} sammutettiin saunavuoron päätteeksi.",
                     "lights_opt_post_sauna", 1,
                     f"lights_opt_post_sauna:{light_id}", ts)

    # Front porch — detection-driven (Unifi person detection).
    if reason.startswith("porch"):
        if decision == "on":
            return Event("Etupihan valo syttyi — etupihalla havaittiin liikettä.",
                         "lights_opt_porch_on", 2, "lights_opt_porch", ts)
        return Event("Etupihan valo sammui.",
                     "lights_opt_porch_off", 2, "lights_opt_porch", ts)

    # Comfort auto-on (living core, dark + occupied).
    if decision == "on" and reason == "auto_on_comfort":
        return Event(f"{name} syttyi automaattisesti hämärän aikaan.",
                     "lights_opt_auto_on", 1, f"lights_opt_on:{light_id}", ts)

    # Auto-off variants — tailored message per high-confidence cull reason.
    if decision == "off":
        OFF_MESSAGES = {
            "daylight_off":  f"{name} sammutettiin — ulkona on valoisaa.",
            "overnight_off": f"{name} sammutettiin yöksi.",
            "away_off":      f"{name} sammutettiin, koska kotona ei ole ketään.",
            "vacancy_off":   f"{name} sammutettiin — huone on tyhjä.",
            "duration_cap":  f"{name} sammutettiin automaattisesti.",
        }
        base = OFF_MESSAGES.get(reason, f"{name} sammutettiin automaattisesti.")
        suffix = ""
        if on_dur:
            try:
                mins = int(round(float(on_dur)))
                if mins > 0:
                    suffix = f" Se oli päällä {mins} minuuttia."
            except (TypeError, ValueError):
                pass
        prio = 2 if reason in ("daylight_off", "overnight_off") else 1
        return Event(base + suffix, "lights_opt_auto_off", prio,
                     f"lights_opt_off:{light_id}", ts)

    return None


def _join_fi(names: list[str]) -> str:
    """Finnish list join: ['A']→'A', ['A','B']→'A ja B', ['A','B','C']→'A, B ja C'."""
    names = [n for n in names if n]
    if not names:
        return "Valot"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " ja " + names[-1]


def _group_key(row: dict):
    """Group key for merging simultaneous multi-light optimizer events into one
    announcement. None ⇒ don't group (single-light special cases: sauna laude,
    porch). Groupable reasons share (decision, reason-family)."""
    decision = (row.get("decision") or "").lower()
    reason   = (row.get("reason") or "").lower()
    if decision not in ("on", "off"):
        return None
    if reason.startswith(("sauna_heated", "sauna_cooled", "porch")):
        return None
    if reason.startswith("post_sauna"):
        return ("off", "post_sauna")
    if decision == "on" and reason == "auto_on_comfort":
        return ("on", "auto_on_comfort")
    if decision == "off":
        return ("off", reason)
    return None


def _format_lights_group(rows: list[dict]) -> Event | None:
    """One announcement for ≥2 lights that changed together for the same reason.
    e.g. 'Kylpyhuone alakerta, Sauna siivousvalo ja Tekninen tila sammutettiin
    saunavuoron päätteeksi.' Passive 'sammutettiin' is number-agnostic; auto-on
    uses the plural 'syttyivät'."""
    rows = sorted(rows, key=lambda r: int(r.get("light_id") or 0))
    decision = (rows[0].get("decision") or "").lower()
    reason   = (rows[0].get("reason") or "").lower()
    names = _join_fi([r.get("light_name") or "Valo" for r in rows])
    ids = "-".join(str(r.get("light_id") or "") for r in rows)
    ts = max((r.get("ts") or datetime.now(timezone.utc)) for r in rows).timestamp()

    if decision == "on":  # auto_on_comfort
        return Event(f"{names} syttyivät automaattisesti hämärän aikaan.",
                     "lights_opt_auto_on", 1, f"lights_opt_on_grp:{ids}", ts)
    if reason.startswith("post_sauna"):
        return Event(f"{names} sammutettiin saunavuoron päätteeksi.",
                     "lights_opt_post_sauna", 1, f"lights_opt_post_sauna_grp:{ids}", ts)
    OFF = {
        "daylight_off":  f"{names} sammutettiin — ulkona on valoisaa.",
        "overnight_off": f"{names} sammutettiin yöksi.",
        "away_off":      f"{names} sammutettiin, koska kotona ei ole ketään.",
        "vacancy_off":   f"{names} sammutettiin — huone on tyhjä.",
        "duration_cap":  f"{names} sammutettiin automaattisesti.",
    }
    text = OFF.get(reason, f"{names} sammutettiin automaattisesti.")
    prio = 2 if reason in ("daylight_off", "overnight_off") else 1
    return Event(text, "lights_opt_auto_off", prio, f"lights_opt_off_grp:{ids}", ts)


# ── State tracking ───────────────────────────────────────────────────────────

class TickState:
    """Latest known values, kept in memory between ticks. Bootstrapped from
    InfluxDB on startup so we don't fire phantom events for the seed values."""

    def __init__(self):
        self.alarm_flags: dict[str, float] = {}
        self.thermia_alarms: dict[str, float] = {}
        # per Ruuvi sensor_name → {"fridge_warm","battery_low","offline"} bools
        # for rising-edge tracking of the warn-tier environment alerts.
        self.ruuvi_env_state: dict[str, dict] = {}
        self.iv_mode: float | None = None   # last Casa MVHR OperatingMode
        self.lights_state: dict[int, int] = {}
        self.sauna_state: str = ""
        self.tier: str = ""
        self.co2_class: dict[str, str] = {}
        self.pm25_class: dict[str, str] = {}
        self.last_lights_opt_seen: datetime = datetime.now(timezone.utc)
        # idx → (target_on, decision_epoch): the optimizer's most recent
        # actuation per light, so the raw on/off block can drop its echo.
        self.optimizer_acted: dict[int, tuple[bool, float]] = {}
        # Sauna-left-on tracking: when the heater is currently active
        # (state in {heating, hot}), session_start is the moment it most
        # recently left {off, cooling}. Cleared once the heater goes off.
        self.sauna_session_start: datetime | None = None
        self.sauna_warning_last:  datetime | None = None
        # Thermia auxiliary-heater state (aux_heater_3kw / aux_heater_6kw).
        self.aux_heater_state: dict[str, float] = {}
        # Outdoor temperature class (warm / cold / freeze / deep).
        self.outdoor_class: str = ""
        # Indoor room temperature class per sensor (low / normal / high).
        self.room_temp_class: dict[str, str] = {}
        # Per-floor heat class (hot / normal) for the cooled floors.
        self.floor_heat_class: dict[str, str] = {}
        # Deduced weather-warning classes (helle / myrsky) → "on" / "off".
        self.weather_class: dict[str, str] = {}
        # Monotonic time of the last forecast check, to throttle the fetch.
        self.weather_checked: float = 0.0
        # PLC liveness — true when we've already announced a heartbeat loss.
        self.plc_lost: bool = False
        # LTO efficiency: tracks how long we've been below the threshold and
        # whether the announcement has already been emitted for this dip.
        self.lto_low_since: datetime | None = None
        self.lto_low_announced: bool = False
        # Push-log cache: kind+key → last push epoch — soft rate-limit so a
        # flapping signal can't blast the kiosk every tick.
        self.last_push_at: "OrderedDict[str, float]" = OrderedDict()
        # Periodic news: "YYYY-MM-DD-HH" (local) of the last hour we read the
        # headlines, so we fire exactly once per clock-hour in the window.
        self.last_news_hour: str = ""

    def cooldown_ok(self, kind: str, key: str, min_gap_s: float) -> bool:
        ck = f"{kind}:{key}"
        now = time.time()
        last = self.last_push_at.get(ck, 0.0)
        if now - last < min_gap_s:
            return False
        self.last_push_at[ck] = now
        if len(self.last_push_at) > 256:
            self.last_push_at.popitem(last=False)
        return True


# ── Tick: detect transitions and push events ─────────────────────────────────

def _fetch_forecast() -> dict | None:
    """Latest Open-Meteo forecast via the weather service; None on any failure."""
    try:
        req = urllib.request.Request(
            WEATHER_API_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=WEATHER_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning("weather forecast fetch failed: %s", e)
        return None


def _weather_warnings(fc, st, emit, bootstrap) -> None:
    """Announce transitions of the weather service's deduced warnings.

    The weather service is the single source of the helle/myrsky verdict (so the
    threshold lives in one place, shared with the kiosk weather card); here we
    only track which are active and speak the edges. On bootstrap we seed the
    state without announcing so a reload doesn't re-declare a standing warning.
    """
    active = {w.get("kind"): w for w in (fc.get("warnings") or []) if w.get("kind")}
    for kind, off_text in (("helle", "Helle on hellittämässä."),
                           ("myrsky", "Myrsky on ohi.")):
        prev = st.weather_class.get(kind, "off")
        new = "on" if kind in active else "off"
        st.weather_class[kind] = new
        if bootstrap or new == prev:
            continue
        if new == "on":
            w = active[kind]
            text = f"{w.get('title', 'Säävaroitus')}. {w.get('detail', '')}".strip()
        else:
            text = off_text
        emit(Event(text, f"weather_{kind}_{new}", 1, f"weather:{kind}",
                   time.time()), min_gap_s=3600)


def tick(infl: Influx, st: TickState, *, bootstrap: bool = False) -> None:
    pushed = 0
    deferred: list[Event] = []

    def emit(ev: Event, *, min_gap_s: float = 60.0):
        nonlocal pushed
        if not st.cooldown_ok(ev.kind, ev.key, min_gap_s):
            return
        deferred.append(ev)

    # --- HVAC (MVHR) + Thermia heat-pump alarm flags. Critical (priority 0)
    # alarms repeat every ALARM_REPEAT_S while active; warn/info speak once on
    # the rising edge. Same processor for both flag sources.
    _emit_alarm_flags(infl.latest_alarm_flags(), ALARM_TEXT_FI,
                      st.alarm_flags, emit, bootstrap)
    _emit_alarm_flags(infl.latest_thermia_alarms(), THERMIA_ALARM_TEXT_FI,
                      st.thermia_alarms, emit, bootstrap)

    # --- Ruuvi environment alerts: freezer/fridge warm, low battery, offline.
    # A stale tag (no reading > RUUVI_OFFLINE_S) can't be trusted for its value
    # alarms, so 'offline' supersedes them. Freezer-warm is critical (repeats);
    # the rest are warn (rising edge). Bootstrap seeds state without announcing.
    now_epoch = datetime.now(timezone.utc).timestamp()
    for name, d in infl.latest_ruuvi_env().items():
        ts = d.get("ts")
        if ts is None:
            continue
        temp, volt = d.get("temperature"), d.get("voltage")
        offline = (now_epoch - ts.timestamp()) > RUUVI_OFFLINE_S
        prev = st.ruuvi_env_state.setdefault(name, {})

        if not bootstrap and offline and not prev.get("offline"):
            emit(Event(f"{name}: anturi ei vastaa.", "ruuvi_offline", 1,
                       f"ruuvi_offline:{name}", ts.timestamp()), min_gap_s=ALARM_REPEAT_S)
        prev["offline"] = offline
        if offline:
            continue  # stale data — skip value/battery checks

        if name == FREEZER_SENSOR and temp is not None and temp > FREEZER_WARM_C:
            if not bootstrap:  # critical: repeat while warm
                emit(Event(f"Pakastin on lämmennyt, lämpötila {round(temp)} astetta.",
                           "ruuvi_freezer_warm", 0, "ruuvi_freezer_warm",
                           ts.timestamp()), min_gap_s=ALARM_REPEAT_S)

        if name == FRIDGE_SENSOR and temp is not None:
            warm = temp > FRIDGE_WARM_C
            if not bootstrap and warm and not prev.get("fridge_warm"):
                emit(Event(f"Jääkaappi on lämmennyt, lämpötila {round(temp)} astetta.",
                           "ruuvi_fridge_warm", 1, "ruuvi_fridge_warm",
                           ts.timestamp()), min_gap_s=ALARM_REPEAT_S)
            prev["fridge_warm"] = warm

        low = _battery_low(volt, temp)
        if not bootstrap and low and not prev.get("battery_low"):
            emit(Event(f"Vaihda paristo: {name}, jännite {volt:.1f} volttia.",
                       "ruuvi_battery_low", 1, f"ruuvi_battery_low:{name}",
                       ts.timestamp()), min_gap_s=ALARM_REPEAT_S)
        prev["battery_low"] = low

    # --- Ventilation humidity-boost (power) mode enter/leave. Rate-limited per
    # direction so the MVHR's frequent humidity cycling doesn't spam.
    iv, iv_ts = infl.latest_iv_mode()
    if iv is not None:
        trans = _iv_boost_transition(st.iv_mode, iv)
        st.iv_mode = iv
        if not bootstrap and trans:
            ts_ep = (iv_ts or datetime.now(timezone.utc)).timestamp()
            if trans == "on":
                emit(Event("Kosteustehostus.", "iv_boost_on", 1, "iv_boost", ts_ep),
                     min_gap_s=IV_BOOST_GAP)
            else:
                emit(Event("Perustila.", "iv_boost_off", 1, "iv_boost", ts_ep),
                     min_gap_s=IV_BOOST_GAP)

    # --- Sauna state machine.
    sauna_t, sauna_ts = infl.latest_sauna_temp()
    if sauna_t is not None and sauna_ts is not None:
        new_state = _sauna_state(sauna_t, st.sauna_state)
        if not bootstrap and new_state and new_state != st.sauna_state and st.sauna_state:
            spec = SAUNA_TEXT_FI.get(new_state)
            if spec:
                text, prio = spec
                emit(Event(text, f"sauna_{new_state}", prio, "sauna_state",
                           sauna_ts.timestamp()), min_gap_s=300)
        st.sauna_state = new_state or st.sauna_state

        # Sauna-left-on warning: heater is "on" while state is heating or hot.
        # On entry to that range, start the session clock; on exit, clear it
        # (and the per-warning rate-limit so the next session starts fresh).
        # Bootstrap uses the current sample's timestamp as a conservative
        # session start — if the heater was on before the announcer started,
        # we under-count by however long it had already been on. The warning
        # will still fire 2 h after restart at worst, and the next state
        # transition resyncs cleanly.
        now_utc = datetime.now(timezone.utc)
        if st.sauna_state in ("heating", "hot"):
            if st.sauna_session_start is None:
                st.sauna_session_start = sauna_ts
            elapsed_min = (now_utc - st.sauna_session_start).total_seconds() / 60.0
            if elapsed_min >= SAUNA_WASTE_AFTER_MIN:
                last = st.sauna_warning_last
                due = (last is None) or \
                      ((now_utc - last).total_seconds() / 60.0 >= SAUNA_WASTE_REPEAT_MIN)
                if not bootstrap and due:
                    h, m = divmod(int(elapsed_min), 60)
                    if h > 0 and m > 0:
                        dur = f"{h} tuntia {m} minuuttia"
                    elif h > 0:
                        dur = f"{h} tuntia"
                    else:
                        dur = f"{m} minuuttia"
                    text = (f"Sauna on ollut päällä jo {dur}. "
                            f"Käytkö löylyissä, vai voisiko kiukaan sammuttaa?")
                    # min_gap_s=0 — we already gated on SAUNA_WASTE_REPEAT_MIN.
                    emit(Event(text, "sauna_left_on", 0, "sauna_left_on",
                               sauna_ts.timestamp()), min_gap_s=0)
                    st.sauna_warning_last = now_utc
        else:
            st.sauna_session_start = None
            st.sauna_warning_last  = None

    # --- Heating-tier transitions.
    tier, price, tier_ts = infl.latest_heating_tier()
    if tier is not None and tier_ts is not None:
        if not bootstrap and tier != st.tier and st.tier:
            spec = TIER_TEXT_FI.get(tier)
            if spec:
                text, prio, _ = spec
                if price is not None:
                    text = f"{text} Hinta nyt {price:.1f} senttiä kilowattitunnilta."
                emit(Event(text, f"heating_tier_{tier.lower()}", prio, "heating_tier",
                           tier_ts.timestamp()), min_gap_s=600)
        st.tier = tier

    # --- Air quality (CO2 + PM2.5 per Ruuvi sensor). Emit on every class
    # transition — both worsening AND improving — so the user knows when the
    # air has cleared, not just when it got bad.
    aq = infl.latest_air_quality()
    for sensor, slot in aq.items():
        ts = slot["ts"].timestamp()
        if "co2" in slot:
            cls = _co2_class(slot["co2"])
            prev = st.co2_class.get(sensor, "")
            if not bootstrap and cls != prev and prev:
                prio = CO2_PRIORITY.get(cls, 2)
                text = _co2_message(sensor, prev, cls)
                emit(Event(text, f"co2_{cls}", prio, f"co2:{sensor}", ts),
                     min_gap_s=900)
            st.co2_class[sensor] = cls
        if "pm25" in slot:
            cls = _pm25_class(slot["pm25"])
            prev = st.pm25_class.get(sensor, "")
            if not bootstrap and cls != prev and prev:
                prio = PM25_PRIORITY.get(cls, 2)
                text = _pm25_message(sensor, prev, cls)
                emit(Event(text, f"pm25_{cls}", prio, f"pm25:{sensor}", ts),
                     min_gap_s=900)
            st.pm25_class[sensor] = cls

    # --- Thermia auxiliary heater (aux_heater_3kw / aux_heater_6kw).
    # Aux heaters are the most expensive way to make heat. Announce every
    # rising/falling edge with a 15 min cooldown so cycling during a single
    # cold spell doesn't spam.
    aux = infl.latest_thermia_aux()
    for field, (val, ts) in aux.items():
        prev = st.aux_heater_state.get(field)
        st.aux_heater_state[field] = val
        if bootstrap or prev is None:
            continue
        prev_on = prev > 0.5
        cur_on  = val  > 0.5
        if cur_on == prev_on:
            continue
        size = "kolmen kilowatin" if "3kw" in field else "kuuden kilowatin"
        if cur_on:
            text = f"Lämpöpumpun {size} sähkövastus käynnistyi."
        else:
            text = f"Lämpöpumpun {size} sähkövastus sammui."
        emit(Event(text, "aux_heater_on" if cur_on else "aux_heater_off",
                   1, f"aux:{field}", ts.timestamp()), min_gap_s=900)

    # --- Outdoor temperature crossings.
    ot, ot_ts = infl.latest_outdoor_temp()
    if ot is not None and ot_ts is not None:
        if ot < OUTDOOR_DEEP_C:
            cls = "deep"
        elif ot < OUTDOOR_FREEZE_C:
            cls = "freeze"
        elif ot < OUTDOOR_THAW_C:
            cls = "cold"
        else:
            cls = "warm"
        prev = st.outdoor_class
        st.outdoor_class = cls
        if not bootstrap and prev and prev != cls:
            text_map = {
                ("warm", "cold"):    f"Ulkolämpötila on laskenut, ulkona {ot:.1f} astetta.",
                ("cold", "warm"):    f"Ulkona on lämmennyt, lämpötila {ot:.1f} astetta.",
                ("cold", "freeze"):  f"Pakkasta on tullut, ulkona {ot:.1f} astetta.",
                ("freeze", "cold"):  f"Pakkanen on hellittänyt, ulkona {ot:.1f} astetta.",
                ("freeze", "deep"):  f"Kova pakkanen, ulkona {ot:.1f} astetta.",
                ("deep", "freeze"):  f"Kovat pakkaset hellittävät, ulkona {ot:.1f} astetta.",
                ("warm", "freeze"):  f"Pakkasta on tullut, ulkona {ot:.1f} astetta.",
                ("freeze", "warm"):  f"Suoja-aikaa, ulkona {ot:.1f} astetta.",
                ("cold", "deep"):    f"Kovat pakkaset, ulkona {ot:.1f} astetta.",
                ("deep", "cold"):    f"Pakkanen on hellittänyt, ulkona {ot:.1f} astetta.",
                ("deep", "warm"):    f"Suoja-aikaa, ulkona {ot:.1f} astetta.",
                ("warm", "deep"):    f"Kovat pakkaset, ulkona {ot:.1f} astetta.",
            }
            text = text_map.get((prev, cls), f"Ulkolämpötila on nyt {ot:.1f} astetta.")
            prio = 0 if cls == "deep" else (1 if cls == "freeze" else 2)
            emit(Event(text, f"outdoor_{cls}", prio, "outdoor_temp",
                       ot_ts.timestamp()), min_gap_s=1800)

    # --- Indoor room temperatures out of range (with hysteresis).
    rooms_temps = infl.latest_room_temps()
    for room, (temp, ts) in rooms_temps.items():
        prev = st.room_temp_class.get(room, "")
        # Hysteresis: while flagged "low", require temp > LOW + HYST to clear.
        # While flagged "high", require temp < HIGH − HYST to clear.
        if prev == "low":
            if temp < INDOOR_TEMP_LOW_C + INDOOR_TEMP_HYST_C:
                cls = "low"
            elif temp > INDOOR_TEMP_HIGH_C:
                cls = "high"
            else:
                cls = "normal"
        elif prev == "high":
            if temp > INDOOR_TEMP_HIGH_C - INDOOR_TEMP_HYST_C:
                cls = "high"
            elif temp < INDOOR_TEMP_LOW_C:
                cls = "low"
            else:
                cls = "normal"
        else:
            if temp < INDOOR_TEMP_LOW_C:
                cls = "low"
            elif temp > INDOOR_TEMP_HIGH_C:
                cls = "high"
            else:
                cls = "normal"
        st.room_temp_class[room] = cls
        if bootstrap or cls == prev or not prev:
            continue
        # Heat is announced per floor now (below), tied to that floor's cooler, so
        # don't also name a single hot room. The cold path stays per room — one
        # chilly room is worth calling out and isn't a floor-cooler concern.
        if cls == "high" or prev == "high":
            continue
        label = ROOM_LABELS_FI.get(room, room)
        if cls == "low":
            text = (f"{label} on viilentynyt alle {INDOOR_TEMP_LOW_C:.0f} asteen, "
                    f"nyt {temp:.1f} astetta.")
        elif cls == "high":
            text = (f"{label} on lämmennyt yli {INDOOR_TEMP_HIGH_C:.0f} asteen, "
                    f"nyt {temp:.1f} astetta.")
        else:
            if prev == "low":
                text = f"{label} on lämmennyt taas, nyt {temp:.1f} astetta."
            else:
                text = f"{label} on viilentynyt taas, nyt {temp:.1f} astetta."
        emit(Event(text, f"room_temp_{cls}", 1, f"room_temp:{room}",
                   ts.timestamp()), min_gap_s=1800)

    # --- Per-floor heat: warn per cooled floor (its hottest room) and point at
    # that floor's cooler. Only floors with cooling HVAC — basement excluded.
    for floor_key, (floor_label, cooler_label) in COOLED_FLOORS.items():
        floor_readings = [(t, ts) for f, (t, ts) in rooms_temps.items()
                          if ROOM_FLOOR.get(f) == floor_key]
        if not floor_readings:
            continue
        hottest, hot_ts = max(floor_readings, key=lambda x: x[0])
        prev = st.floor_heat_class.get(floor_key, "")
        # Hysteresis: once "hot", stay hot until it drops a clear margin below.
        hot = (hottest >= FLOOR_HOT_C - FLOOR_HOT_HYST_C) if prev == "hot" \
            else (hottest >= FLOOR_HOT_C)
        cls = "hot" if hot else "normal"
        st.floor_heat_class[floor_key] = cls
        if bootstrap or cls == prev or not prev:
            continue
        if cls == "hot":
            text = (f"{floor_label} on lämmennyt yli {FLOOR_HOT_C:.0f} asteen, "
                    f"nyt {hottest:.1f} astetta — laita {cooler_label} päälle.")
        else:
            text = f"{floor_label} on viilentynyt taas, nyt {hottest:.1f} astetta."
        emit(Event(text, f"floor_heat_{cls}", 1, f"floor_heat:{floor_key}",
                   hot_ts.timestamp()), min_gap_s=1800)

    # --- Deduced weather warnings (heat / storm) from the forecast, throttled so
    # we don't hit the weather service every tick. On bootstrap it only seeds the
    # baseline class (no announcement) so a reload doesn't re-declare a warning.
    now_mono = time.monotonic()
    if now_mono - st.weather_checked >= WEATHER_CHECK_S:
        st.weather_checked = now_mono
        fc = _fetch_forecast()
        if fc:
            _weather_warnings(fc, st, emit, bootstrap)

    # --- PLC heartbeat — alarm if no fresh plc_publisher write.
    hb_ts = infl.latest_plc_heartbeat()
    now_utc = datetime.now(timezone.utc)
    if hb_ts is not None:
        age_s = (now_utc - hb_ts).total_seconds()
        lost  = age_s > PLC_HEARTBEAT_LOSS_S
        if not bootstrap and lost != st.plc_lost:
            if lost:
                mins = int(age_s / 60)
                text = (f"PLC-yhteys on katkennut, viimeinen mittaus "
                        f"{mins} minuuttia sitten.")
                emit(Event(text, "plc_heartbeat_lost", 0, "plc_heartbeat",
                           now_utc.timestamp()), min_gap_s=600)
            else:
                text = "PLC-yhteys on palautunut, mittaukset jatkuvat."
                emit(Event(text, "plc_heartbeat_recovered", 1, "plc_heartbeat",
                           now_utc.timestamp()), min_gap_s=600)
        st.plc_lost = lost

    # --- Heat-recovery (LTO) efficiency degradation.
    eta = infl.latest_lto_efficiency()
    if eta is not None:
        if eta < LTO_LOW_RATIO:
            if st.lto_low_since is None:
                st.lto_low_since = now_utc
            elapsed_min = (now_utc - st.lto_low_since).total_seconds() / 60.0
            if (not bootstrap
                    and elapsed_min >= LTO_LOW_DURATION_MIN
                    and not st.lto_low_announced):
                text = (f"Ilmanvaihdon lämmöntalteenoton hyötysuhde on heikentynyt "
                        f"{int(eta * 100)} prosenttiin. "
                        f"Suodattimet voivat kaivata huoltoa.")
                emit(Event(text, "lto_low", 1, "lto_efficiency",
                           now_utc.timestamp()), min_gap_s=3600)
                st.lto_low_announced = True
        else:
            if st.lto_low_announced and not bootstrap:
                text = (f"Ilmanvaihdon hyötysuhde on palautunut, "
                        f"nyt {int(eta * 100)} prosenttia.")
                emit(Event(text, "lto_recovered", 2, "lto_efficiency",
                           now_utc.timestamp()), min_gap_s=600)
            st.lto_low_since = None
            st.lto_low_announced = False

    # --- lights_optimizer decisions since last poll. Processed BEFORE the raw
    # on/off block so we can suppress the redundant raw "syttyi/sammui" echo of
    # any light the optimizer just actuated. The optimizer re-publishes the same
    # command across ticks while the PLC catches up, so we dedup:
    #   (a) collapse repeat (light_id, reason) rows within a poll;
    #   (b) 600 s cross-poll cooldown so a slow catch-up can't slip a duplicate.
    rows = infl.lights_optimizer_decisions_since(st.last_lights_opt_seen)
    seen_in_tick: set[str] = set()
    grouped: dict[tuple, list[dict]] = {}   # (decision, reason) → rows fired together
    singles: list[dict] = []                # ungroupable (porch, sauna laude)
    for row in rows:
        ts = row.get("ts")
        if ts is None:
            continue
        if ts > st.last_lights_opt_seen:
            st.last_lights_opt_seen = ts
        # Record every actuation (even during bootstrap) so the raw block below
        # can drop its echo, direction-matched.
        decision = (row.get("decision") or "").lower()
        if decision in ("on", "off"):
            try:
                st.optimizer_acted[int(row.get("light_id"))] = (decision == "on", ts.timestamp())
            except (TypeError, ValueError):
                pass
        if bootstrap:
            continue
        tick_key = f"{row.get('light_id', '')}:{row.get('reason', '')}"
        if tick_key in seen_in_tick:
            continue
        seen_in_tick.add(tick_key)
        gk = _group_key(row)
        if gk is None:
            singles.append(row)
        else:
            grouped.setdefault(gk, []).append(row)

    for row in singles:
        ev = _format_lights_optimizer(row)
        if ev:
            emit(ev, min_gap_s=600)
    # Merge multi-light simultaneous events (e.g. post-sauna 1/38/39, or several
    # living lights vacancy-off) into a single announcement; size-1 groups keep
    # the normal per-light phrasing.
    for grp in grouped.values():
        ev = _format_lights_optimizer(grp[0]) if len(grp) == 1 else _format_lights_group(grp)
        if ev:
            emit(ev, min_gap_s=600)

    # --- Light raw on/off (verbose / debug). Only announce UNEXPLAINED flips
    # (a human/wall press) — skip the echo of a change the optimizer just made,
    # matched by direction within RAW_ECHO_SUPPRESS_S of its decision.
    lights = infl.latest_lights()
    for idx, (val, ts) in lights.items():
        prev = st.lights_state.get(idx)
        st.lights_state[idx] = val
        if bootstrap or prev is None or prev == val:
            continue
        if VERBOSITY < 3:
            continue
        acted = st.optimizer_acted.get(idx)
        if (acted is not None and acted[0] == (val == 1)
                and abs(ts.timestamp() - acted[1]) <= RAW_ECHO_SUPPRESS_S):
            continue  # optimizer already announced this change
        name = LIGHT_LABELS.get(idx, (f"Valo {idx}", None))[0]
        if val == 1:
            text = f"{name} syttyi."
            emit(Event(text, "light_on", 3, f"light_on:{idx}", ts.timestamp()),
                 min_gap_s=15)
        else:
            text = f"{name} sammui."
            emit(Event(text, "light_off", 3, f"light_off:{idx}", ts.timestamp()),
                 min_gap_s=15)
    # Keep optimizer_acted bounded — drop entries older than 5 min.
    _cutoff = datetime.now(timezone.utc).timestamp() - 300
    for _i in [k for k, (_o, t) in st.optimizer_acted.items() if t < _cutoff]:
        del st.optimizer_acted[_i]

    # --- Periodic news headlines (once per clock-hour in the daytime window).
    #     Pushed with force=True so it bypasses ANNOUNCE_VERBOSITY and the
    #     per-tick burst cap — it's a deliberate, rate-limited feature, not
    #     ambient noise.
    if NEWS_ENABLED and not bootstrap:
        now_local = datetime.now(LOCAL_TZ)
        hour_key = now_local.strftime("%Y-%m-%d-%H")
        if (NEWS_START_HOUR <= now_local.hour < NEWS_END_HOUR
                and hour_key != st.last_news_hour):
            st.last_news_hour = hour_key  # one attempt per hour, success or not
            national = _fetch_headlines(NEWS_NATIONAL_SRC, NEWS_NATIONAL_N)
            regional = _fetch_headlines(NEWS_REGIONAL_SRC, NEWS_REGIONAL_N)
            if national or regional:
                parts = [h["title"].strip().rstrip(".") + "."
                         for h in national if h.get("title")]
                text = "Uutiskatsaus. " + " ".join(parts)
                reg_titles = [h["title"].strip().rstrip(".") + "."
                              for h in regional if h.get("title")]
                if reg_titles:
                    text += " Pirkanmaalta: " + " ".join(reg_titles)
                _push(Event(text, "news_headlines", 2, f"news:{hour_key}",
                            now_local.timestamp()), force=True)
            else:
                log.info("news: no headlines available for %s", hour_key)

    # Cap per-tick burst — keep highest-priority items, drop the rest.
    if not deferred:
        return
    deferred.sort(key=lambda e: (e.priority, -e.ts))
    keep = deferred[:MAX_PER_TICK]
    if len(deferred) > MAX_PER_TICK:
        log.info("dropped %d lower-priority events to stay under MAX_PER_TICK=%d",
                 len(deferred) - MAX_PER_TICK, MAX_PER_TICK)
    for ev in keep:
        _push(ev)


# ── Main loop ────────────────────────────────────────────────────────────────

_running = True

def _stop(signum, _frame):
    global _running
    log.info("received %s, stopping", signal.Signals(signum).name)
    _running = False


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT,  _stop)

    log.info("verbosity=%d poll=%ds bridge=%s", VERBOSITY, POLL_INTERVAL, BRIDGE_PUSH_URL)
    client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    try:
        log.info("influxdb: %s", client.health().status)
    except Exception as e:
        log.warning("influxdb health check: %s", e)

    infl = Influx(client)
    st = TickState()

    # Bootstrap: read once without pushing so we don't blast on startup.
    log.info("bootstrapping current state...")
    try:
        tick(infl, st, bootstrap=True)
    except Exception as e:
        log.exception("bootstrap failed: %s", e)
    log.info("bootstrap done — sauna=%s tier=%s lights=%d alarms=%d",
             st.sauna_state, st.tier, len(st.lights_state), len(st.alarm_flags))

    consecutive_failures = 0
    while _running:
        try:
            tick(infl, st)
            consecutive_failures = 0
            touch_health()
        except Exception as e:
            consecutive_failures += 1
            log.exception("tick failed (%d/%d consecutive): %s",
                          consecutive_failures, MAX_CONSECUTIVE_FAILURES, e)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.critical("%d consecutive failures — exiting non-zero for restart/visibility",
                             consecutive_failures)
                sys.exit(1)
        end = time.monotonic() + POLL_INTERVAL
        while _running and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))

    client.close()
    log.info("shutdown complete")


if __name__ == "__main__":
    main()
