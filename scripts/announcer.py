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
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import urllib.error
import urllib.request

from influxdb_client import InfluxDBClient

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

# Suppress noisy bursts: don't push more than this many events per tick.
MAX_PER_TICK   = int(os.environ.get("ANNOUNCE_MAX_PER_TICK", "5"))

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


def _push(event: Event) -> None:
    if event.priority > VERBOSITY:
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
    "Jalkilammitin_ylikuume":    ("Jälkilämmittimen ylikuumeneminen havaittu.",             0),
    "Esilammitin_ylikuume":      ("Esilämmittimen ylikuumeneminen havaittu.",               0),
}

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

# Map raw lights_optimizer reason strings → spoken description + priority.
# Many reasons are diagnostic-only ("hold", "manual_only") — only call out the
# user-relevant transitions (auto-off fired, sauna laude on/off, CO2 auto-on/off,
# post-sauna cleanup, porch schedule).
def _format_lights_optimizer(row: dict) -> Event | None:
    decision = (row.get("decision") or "").lower()
    reason   = (row.get("reason") or "").lower()
    category = (row.get("category") or "").lower()
    name     = row.get("light_name") or "Valo"
    on_dur   = row.get("on_duration_min")
    ts       = (row.get("ts") or datetime.now(timezone.utc)).timestamp()

    # Skip "hold" — it's the no-op outcome.
    if decision not in ("on", "off"):
        return None

    # Sauna laude (idx 4) — sensor-driven temperature hysteresis.
    if category == "sauna_laude":
        if decision == "on":
            return Event("Saunan laudevalo syttyi automaattisesti löylyjä varten.",
                         "lights_opt_sauna_on", 1, "lights_opt_sauna_laude", ts)
        else:
            return Event("Saunan laudevalo sammui — sauna on jäähtynyt.",
                         "lights_opt_sauna_off", 1, "lights_opt_sauna_laude", ts)

    # Post-sauna cleanup of bathroom + sauna ceiling (idx 1, 38, 39).
    if category == "sauna_post_session":
        return Event(f"{name} sammutettiin saunavuoron päätteeksi.",
                     "lights_opt_post_sauna", 1,
                     f"lights_opt_post_sauna:{row.get('light_id','')}", ts)

    # CO2-auto kitchen + livingroom ceiling.
    if category == "co2_auto":
        if decision == "on":
            return Event(f"{name} syttyi koholla olevan hiilidioksidipitoisuuden vuoksi.",
                         "lights_opt_co2_on", 1,
                         f"lights_opt_co2:{row.get('light_id','')}", ts)
        else:
            return Event(f"{name} sammui — ilma on raikastunut tai on yöaika.",
                         "lights_opt_co2_off", 2,
                         f"lights_opt_co2:{row.get('light_id','')}", ts)

    # Porch schedule.
    if category == "porch_schedule":
        if decision == "on":
            return Event("Etupihan valo syttyi auringonlaskun mukana.",
                         "lights_opt_porch_on", 2, "lights_opt_porch", ts)
        else:
            return Event("Etupihan valo sammui yön ajaksi.",
                         "lights_opt_porch_off", 2, "lights_opt_porch", ts)

    # Generic auto-off (toilet timeout, bedroom timeout, daylight, unoccupied …).
    if decision == "off":
        suffix = ""
        if on_dur:
            try:
                mins = int(round(float(on_dur)))
                if mins > 0:
                    suffix = f" Se oli päällä {mins} minuuttia."
            except (TypeError, ValueError):
                pass
        msg = f"{name} sammutettiin automaattisesti.{suffix}"
        return Event(msg, "lights_opt_auto_off", 1,
                     f"lights_opt_off:{row.get('light_id','')}", ts)

    return None


# ── State tracking ───────────────────────────────────────────────────────────

class TickState:
    """Latest known values, kept in memory between ticks. Bootstrapped from
    InfluxDB on startup so we don't fire phantom events for the seed values."""

    def __init__(self):
        self.alarm_flags: dict[str, float] = {}
        self.lights_state: dict[int, int] = {}
        self.sauna_state: str = ""
        self.tier: str = ""
        self.co2_class: dict[str, str] = {}
        self.pm25_class: dict[str, str] = {}
        self.last_lights_opt_seen: datetime = datetime.now(timezone.utc)
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
        # PLC liveness — true when we've already announced a heartbeat loss.
        self.plc_lost: bool = False
        # LTO efficiency: tracks how long we've been below the threshold and
        # whether the announcement has already been emitted for this dip.
        self.lto_low_since: datetime | None = None
        self.lto_low_announced: bool = False
        # Push-log cache: kind+key → last push epoch — soft rate-limit so a
        # flapping signal can't blast the kiosk every tick.
        self.last_push_at: "OrderedDict[str, float]" = OrderedDict()

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

def tick(infl: Influx, st: TickState, *, bootstrap: bool = False) -> None:
    pushed = 0
    deferred: list[Event] = []

    def emit(ev: Event, *, min_gap_s: float = 60.0):
        nonlocal pushed
        if not st.cooldown_ok(ev.kind, ev.key, min_gap_s):
            return
        deferred.append(ev)

    # --- HVAC alarm flags (rising edges of any boolean flag we know about).
    flags = infl.latest_alarm_flags()
    for field, (val, ts) in flags.items():
        prev = st.alarm_flags.get(field, 0.0)
        st.alarm_flags[field] = val
        if bootstrap:
            continue
        if val > 0.5 and prev <= 0.5:  # rising edge: alarm on
            spec = ALARM_TEXT_FI.get(field)
            if spec:
                text, prio = spec
                emit(Event(text, f"alarm_on:{field}", prio, f"alarm:{field}",
                           ts.timestamp()), min_gap_s=300)

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

    # --- Light raw on/off (verbose / debug).
    lights = infl.latest_lights()
    for idx, (val, ts) in lights.items():
        prev = st.lights_state.get(idx)
        st.lights_state[idx] = val
        if bootstrap or prev is None or prev == val:
            continue
        if VERBOSITY < 3:
            continue
        name = LIGHT_LABELS.get(idx, (f"Valo {idx}", None))[0]
        if val == 1:
            text = f"{name} syttyi."
            emit(Event(text, "light_on", 3, f"light_on:{idx}", ts.timestamp()),
                 min_gap_s=15)
        else:
            text = f"{name} sammui."
            emit(Event(text, "light_off", 3, f"light_off:{idx}", ts.timestamp()),
                 min_gap_s=15)

    # --- lights_optimizer decisions since last poll.
    rows = infl.lights_optimizer_decisions_since(st.last_lights_opt_seen)
    for row in rows:
        ts = row.get("ts")
        if ts is None:
            continue
        if ts > st.last_lights_opt_seen:
            st.last_lights_opt_seen = ts
        if bootstrap:
            continue
        ev = _format_lights_optimizer(row)
        if ev:
            emit(ev, min_gap_s=120)

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

    while _running:
        try:
            tick(infl, st)
        except Exception as e:
            log.exception("tick failed: %s", e)
        end = time.monotonic() + POLL_INTERVAL
        while _running and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))

    client.close()
    log.info("shutdown complete")


if __name__ == "__main__":
    main()
