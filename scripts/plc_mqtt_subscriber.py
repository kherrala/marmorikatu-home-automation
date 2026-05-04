#!/usr/bin/env python3
"""
MQTT subscriber for the Marmorikatu WAGO PLC.

Subscribes to the ten retained `marmorikatu/...` topics published by the
PLC's `pMqttPublish` POU (~13 s round) and writes the data to InfluxDB
using the existing measurement schema so dashboards, MCP tools, and the
heating optimizer continue to work unchanged.

Topic → InfluxDB mapping
------------------------
marmorikatu/temperatures   → rooms (room_type/floor) + hvac (cooling battery)
marmorikatu/lights         → lights (light_id/light_name/floor/floor_name/switch_type)
marmorikatu/switches       → switches (switch_id/switch_name)  [new measurement]
marmorikatu/heating        → rooms (room_type=pid)               [per-room PID %]
marmorikatu/cooling        → hvac (sensor_group=cooling)
marmorikatu/outlets        → lights (switch_type=outlet)
marmorikatu/ventilation    → hvac (sensor_group=ivk_temp/humidity/actuator)
marmorikatu/energy/heatpump → hvac (meter=heatpump, sensor_group=voltage/current/power/energy)
marmorikatu/energy/extra    → hvac (meter=extra,    sensor_group=voltage/current/power/energy)
marmorikatu/status         → plc_publisher          [new measurement]

The full publishing protocol is documented at
../marmorikatu-plc/MQTT.md and ../marmorikatu-plc/README.md.
"""

import json
import os
import signal
import sys
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from light_labels import (
    LIGHT_LABELS, SWITCH_LABELS, FLOOR_NAMES, floor_name as _floor_name,
)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

MQTT_BROKER = os.environ.get("MQTT_BROKER", "freenas.kherrala.fi")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "marmorikatu")

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")


# --------------------------------------------------------------------------
# Lookup tables
# --------------------------------------------------------------------------

# Room temperature mapping: MQTT key → (room_type, field_name, floor).
# Field names match the existing rooms-measurement schema written by
# import_data.py so all current dashboards keep resolving.
ROOM_TEMP_MAP = {
    "yk_aatu": ("bedroom", "MH_Seela", 2),
    "yk_onni": ("bedroom", "MH_Aarni", 2),
    "yk_essi": ("bedroom", "MH_aikuiset", 2),
    "yk_aula": ("common", "Ylakerran_aula", 2),
    "keittio": ("common", "Keittio", 1),
    "mh_ak": ("bedroom", "MH_alakerta", 1),
    "eteinen": ("common", "Eteinen", 1),
    "kellari": ("basement", "Kellari", 0),
    "kellari_eteinen": ("basement", "Kellari_eteinen", 0),
}

# Raw PT100 channels reported under marmorikatu/temperatures that don't fit
# the room schema — they go to hvac with sensor_group=ivk_temp / cooling.
EXTRA_TEMP_MAP = {
    "tuloilmakanava": ("ivk_temp", "Tuloilmakanava"),
    "jaahdpatteri_1": ("cooling", "Jaahpatteri_1"),
    "jaahdpatteri_2": ("cooling", "Jaahpatteri_2"),
}

# Per-room underfloor-heating PID demand (marmorikatu/heating).
# Sample payload: {"essi":0, "aatu":0, "onni":0, "yk_aula":0, "keittio":0,
#                  "mh_ak":0, "eteinen":60, "kellari_eteinen":0, "kellari":100}
# Values are integer 0–100 PID % from the WAGO controller. Field names
# match the historical CSV column names (which reflect previous bedroom
# occupants: aatu→Seela, onni→Aarni, essi→aikuiset).
ROOM_PID_MAP = {
    "kellari_eteinen": ("Kellari_eteinen_PID", 0),
    "kellari":         ("Kellari_PID", 0),
    "keittio":         ("Keittio_PID", 1),
    "essi":            ("MH_aikuiset_PID", 2),
    "onni":            ("MH_Aarni_PID", 2),
    "yk_aula":         ("Ylakerran_aula_PID", 2),
    "aatu":            ("MH_Seela_PID", 2),
    "eteinen":         ("Eteinen_PID", 1),
    "mh_ak":           ("MH_alakerta_PID", 1),
}

# Cooling pumps (marmorikatu/cooling) → hvac/sensor_group=cooling.
COOLING_MAP = {
    "pumppu_jaahdytys": "Pumppu_jaahdytys",
    "jaahdytyspumppu": "Jaahdytyspumppu",
}

# Outdoor power outlets (marmorikatu/outlets) → lights/switch_type=outlet.
# floor=None → outdoor; floor_name="Ulko".
OUTLET_MAP = {
    "ulkopistorasia": "Ulkopistorasia",
    "autokatos_pistorasia": "Autokatos_pistorasia",
}

# Ventilation (marmorikatu/ventilation) → hvac with sensor_group=ivk_temp /
# humidity / actuator. Multiple key candidates per logical field cover
# minor naming differences in the PLC publisher.
#
# Schema: list of (sensor_group, field_name, [candidate_keys...]).
# (sensor_group, target field, candidate keys, scale factor).
# Casa MVHR registers (Outdoor/Supply/Extract/Exhaust/Mode/HeaterCooling) are
# already scaled by the PLC. Belimo 22DTH registers come as raw int×10 and
# need ÷10 to match the existing humidity/dew-point/RH-temp schema.
VENTILATION_FIELDS = [
    ("ivk_temp", "Ulkolampotila",
        ["outdoortemp", "outdoor_temp", "ioutdoortemp", "out_temp"], 1.0),
    ("ivk_temp", "Tuloilma_ennen_lammitysta",
        ["supplytemppreheat", "supply_temp_pre_heat", "supply_pre_heat",
         "isupplytemppreheat", "supply_pre"], 1.0),
    ("ivk_temp", "Tuloilma_jalkeen_lammityksen",
        ["supplytemppostheat", "supply_temp_post_heat", "supply_post_heat",
         "isupplytemppostheat", "supply_post"], 1.0),
    ("ivk_temp", "Poistoilma",
        ["extracttemp", "extract_temp", "iextracttemp"], 1.0),
    ("ivk_temp", "Jateilma",
        ["exhausttemp", "exhaust_temp", "iexhausttemp"], 1.0),
    # Legacy CSV had a separate Tuloilma_asetusarvo (supply-air setpoint),
    # used by the LTO/LVK heat-recovery efficiency Flux query as an exhaust-
    # temp proxy (per docs/heat-recovery-efficiency.md). The WAGO MQTT
    # publisher does not emit a setpoint, so we alias real ExhaustTemp to
    # the legacy field name — that's a more accurate denominator than the
    # original supply-setpoint proxy ever was.
    ("ivk_temp", "Tuloilma_asetusarvo",
        ["exhausttemp", "exhaust_temp", "iexhausttemp"], 1.0),
    ("humidity", "Suhteellinen_kosteus",
        ["relativehumidity", "relative_humidity", "irelativehumidity", "rh"], 0.1),
    ("humidity", "Absoluuttinen_kosteus",
        ["abshumidity", "abs_humidity", "iabshumidity", "absolute_humidity"], 0.1),
    ("humidity", "Entalpia",
        ["enthalpy", "ienthalpy"], 0.1),
    ("humidity", "Kastepiste",
        ["dewpoint", "dew_point", "idewpoint"], 0.1),
    ("humidity", "RH_lampotila",
        ["belimo22dth_temp", "belimo_22dth_temp", "temperature",
         "itemperature", "sensor_temp"], 0.1),
    ("actuator", "Toimilaite_ohjaus",
        ["damperposition", "damper_position", "rel_position",
         "uirelposition", "relposition"], 1.0),
    ("actuator", "IV_tila",
        ["operatingmode", "operating_mode", "ioperatingmode", "mode"], 1.0),
    ("actuator", "IV_lammitys_jaahdytys",
        ["heatercooling", "heater_cooling", "iheatercooling"], 1.0),
]

# Energy-meter field groupings shared by both heatpump and extra meters.
# JSON keys are taken verbatim from the OR-WE-517 register layout.
ENERGY_FIELD_GROUPS = {
    "voltage": ["L1_Voltage", "L2_Voltage", "L3_Voltage", "Grid_Frequency"],
    "current": ["L1_Current", "L2_Current", "L3_Current"],
    "power":   ["Total_Active_Power", "L1_Active_Power",
                "L2_Active_Power", "L3_Active_Power"],
    "energy":  ["Total_Active_Energy", "L1_Total_Active_Energy",
                "L2_Total_Active_Energy", "L3_Total_Active_Energy",
                "Forward_Active_Energy", "Reverse_Active_Energy"],
}

# Legacy-compatible aliases so existing docs / MCP schema / future dashboards
# can keep using the CSV-era names. Written in addition to the verbatim ones.
ENERGY_LEGACY_ALIASES = {
    "heatpump": [
        ("voltage", "L1_Voltage", "U1_jannite"),
        ("voltage", "L2_Voltage", "U2_jannite"),
        ("voltage", "L3_Voltage", "U3_jannite"),
        ("power",   "Total_Active_Power",  "Lampopumppu_teho"),
        ("energy",  "Total_Active_Energy", "Lampopumppu_energia"),
        # Legacy "annual" energy field — semantically the same kWh meter
        # running total. The "vuosi" prefix predates this pipeline.
        ("energy",  "Total_Active_Energy", "Maalampopumppu_vuosienergia"),
    ],
    "extra": [
        ("power",  "Total_Active_Power",  "Lisavastus_teho"),
        ("energy", "Total_Active_Energy", "Lisavastus_energia"),
        ("energy", "Total_Active_Energy", "Lisalammitin_vuosienergia"),
    ],
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def floor_tag(floor):
    """Stringify floor for tag value; outdoor / unclassified → empty string."""
    return str(floor) if floor is not None else ""


floor_name = _floor_name


def to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "on", "yes")
    return False


def lookup_ventilation(payload, candidates):
    """Find the first candidate key (case-insensitive) in payload, or None."""
    lowered = {k.lower(): v for k, v in payload.items()}
    for c in candidates:
        v = lowered.get(c.lower())
        if v is not None:
            return v
    return None


# --------------------------------------------------------------------------
# Per-topic builders
# --------------------------------------------------------------------------

def build_temperatures(payload, ts):
    points = []

    # Group room temps by (room_type, floor) so each group becomes one Point
    # with multiple fields — same shape as import_data.py emits.
    grouped = {}
    for key, value in payload.items():
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        mapping = ROOM_TEMP_MAP.get(key)
        if mapping:
            room_type, field, floor = mapping
            grouped.setdefault((room_type, floor), {})[field] = v

    for (room_type, floor), fields in grouped.items():
        p = Point("rooms").tag("room_type", room_type)
        if floor is not None:
            p = p.tag("floor", str(floor))
        for name, val in fields.items():
            p = p.field(name, val)
        points.append(p.time(ts, WritePrecision.S))

    # Extra raw PT100 channels (supply duct + cooling battery) → hvac
    extra_grouped = {}
    for key, value in payload.items():
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        mapping = EXTRA_TEMP_MAP.get(key)
        if mapping:
            sensor_group, field = mapping
            extra_grouped.setdefault(sensor_group, {})[field] = v

    for sensor_group, fields in extra_grouped.items():
        p = Point("hvac").tag("sensor_group", sensor_group)
        for name, val in fields.items():
            p = p.field(name, val)
        points.append(p.time(ts, WritePrecision.S))

    return points


def build_lights(payload, ts):
    points = []
    for key, value in payload.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        label_floor = LIGHT_LABELS.get(idx)
        if label_floor is None:
            # Unmapped index — these correspond to gaps in the PLC's
            # PersistentVars.Controls[] array (entries with no buttontxt
            # label, e.g. indices 21 and 27). Skip rather than emitting a
            # synthetic `light_<idx>` row that pollutes the dashboards.
            continue
        name, floor = label_floor
        p = (Point("lights")
             .tag("light_id", str(idx))
             .tag("light_name", name)
             .tag("floor", floor_tag(floor))
             .tag("floor_name", floor_name(floor))
             .tag("switch_type", "primary")
             .field("is_on", 1 if to_bool(value) else 0)
             .time(ts, WritePrecision.S))
        points.append(p)
    return points


def build_switches(payload, ts):
    points = []
    for key, value in payload.items():
        clean = key.lower()
        if clean.startswith("in"):
            clean = clean[2:]
        try:
            idx = int(clean)
        except (TypeError, ValueError):
            continue
        label_floor = SWITCH_LABELS.get(idx)
        if label_floor is None:
            name, floor = f"switch_{idx}", None
        else:
            name, floor = label_floor
        p = (Point("switches")
             .tag("switch_id", str(idx))
             .tag("switch_name", name)
             .tag("floor", floor_tag(floor))
             .tag("floor_name", floor_name(floor))
             .field("pressed", 1 if to_bool(value) else 0)
             .time(ts, WritePrecision.S))
        points.append(p)
    return points


def build_heating(payload, ts):
    """Per-room PID demand → rooms/room_type=pid, grouped by floor.

    Writes the WAGO controller's PID-output percent for each underfloor-
    heating zone under the legacy CSV field names (e.g. MH_Seela_PID).
    Values are clamped to [0, 100] in case the publisher sends an out-of-
    range integer.

    The `rooms` measurement's temperature fields are floats too, so the
    room_temperatures dashboard pivots filter out room_type=pid to avoid
    schema-collision noise on the temperature panels.
    """
    grouped: dict[int, dict[str, float]] = {}
    for key, value in payload.items():
        mapping = ROOM_PID_MAP.get(key)
        if not mapping:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        v = max(0.0, min(100.0, v))
        field, floor = mapping
        grouped.setdefault(floor, {})[field] = v

    points = []
    for floor, fields in grouped.items():
        p = Point("rooms").tag("room_type", "pid").tag("floor", str(floor))
        for name, val in fields.items():
            p = p.field(name, val)
        points.append(p.time(ts, WritePrecision.S))
    return points


def build_cooling(payload, ts):
    p = Point("hvac").tag("sensor_group", "cooling")
    found = False
    for key, value in payload.items():
        field = COOLING_MAP.get(key)
        if not field:
            continue
        p = p.field(field, 1 if to_bool(value) else 0)
        found = True
    return [p.time(ts, WritePrecision.S)] if found else []


def build_outlets(payload, ts):
    points = []
    for key, value in payload.items():
        field = OUTLET_MAP.get(key)
        if not field:
            continue
        p = (Point("lights")
             .tag("light_id", key)
             .tag("light_name", field)
             .tag("floor", "")
             .tag("floor_name", "Ulko")
             .tag("switch_type", "outlet")
             .field("is_on", 1 if to_bool(value) else 0)
             .time(ts, WritePrecision.S))
        points.append(p)
    return points


def build_ventilation(payload, ts):
    """Map Casa MVHR + Belimo 22DTH + LR24A actuator readings to hvac fields."""
    grouped = {}
    matched_keys = set()
    for sensor_group, field, candidates, scale in VENTILATION_FIELDS:
        value = lookup_ventilation(payload, candidates)
        if value is None:
            continue
        try:
            v = float(value) * scale
        except (TypeError, ValueError):
            continue
        grouped.setdefault(sensor_group, {})[field] = v
        for c in candidates:
            matched_keys.add(c.lower())

    # Surface unknown keys so the user can extend the candidate lists if the
    # PLC publisher uses different names than we guessed.
    unknown = [k for k in payload.keys() if k.lower() not in matched_keys]
    if unknown:
        print(f"[ventilation] unmapped keys: {unknown}", flush=True)

    points = []
    for sensor_group, fields in grouped.items():
        p = Point("hvac").tag("sensor_group", sensor_group)
        for name, val in fields.items():
            p = p.field(name, val)
        points.append(p.time(ts, WritePrecision.S))
    return points


def build_energy(payload, ts, meter):
    """Build hvac points for one OR-WE-517 meter (heatpump or extra)."""
    points = []

    for sensor_group, fields in ENERGY_FIELD_GROUPS.items():
        p = Point("hvac").tag("sensor_group", sensor_group).tag("meter", meter)
        any_field = False
        for f in fields:
            v = payload.get(f)
            if v is None:
                continue
            try:
                p = p.field(f, float(v))
                any_field = True
            except (TypeError, ValueError):
                pass
        if any_field:
            points.append(p.time(ts, WritePrecision.S))

    # Legacy aliases (Lampopumppu_teho, U1_jannite, etc.) on separate
    # points without the meter tag, so existing CSV-era queries match.
    for sensor_group, src_field, alias_field in ENERGY_LEGACY_ALIASES.get(meter, []):
        v = payload.get(src_field)
        if v is None:
            continue
        try:
            value = float(v)
        except (TypeError, ValueError):
            continue
        p = (Point("hvac")
             .tag("sensor_group", sensor_group)
             .field(alias_field, value)
             .time(ts, WritePrecision.S))
        points.append(p)

    return points


def build_status(payload, ts):
    """Publisher heartbeat — store all six fields in a single point."""
    p = Point("plc_publisher")
    any_field = False
    for key, value in payload.items():
        if isinstance(value, bool):
            p = p.field(key, 1 if value else 0)
            any_field = True
        elif isinstance(value, (int, float)):
            p = p.field(key, float(value))
            any_field = True
    if not any_field:
        return []
    return [p.time(ts, WritePrecision.S)]


# Topic suffix → builder. The connection callback prepends TOPIC_PREFIX.
TOPIC_HANDLERS = {
    "temperatures":   build_temperatures,
    "lights":         build_lights,
    "switches":       build_switches,
    "heating":        build_heating,
    "cooling":        build_cooling,
    "outlets":        build_outlets,
    "ventilation":    build_ventilation,
    "energy/heatpump": lambda p, t: build_energy(p, t, "heatpump"),
    "energy/extra":    lambda p, t: build_energy(p, t, "extra"),
    "status":         build_status,
}


# --------------------------------------------------------------------------
# MQTT plumbing
# --------------------------------------------------------------------------

influx_client = None
write_api = None


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        topic = f"{TOPIC_PREFIX}/#"
        client.subscribe(topic)
        print(f"Connected to {MQTT_BROKER}:{MQTT_PORT}, subscribed to {topic}",
              flush=True)
    else:
        print(f"MQTT connect failed (rc={rc})", flush=True)


def on_disconnect(client, userdata, rc, properties=None, reason_code=None):
    print(f"Disconnected from MQTT broker (rc={rc})", flush=True)


def on_message(client, userdata, msg):
    prefix = f"{TOPIC_PREFIX}/"
    if not msg.topic.startswith(prefix):
        return
    suffix = msg.topic[len(prefix):]
    handler = TOPIC_HANDLERS.get(suffix)
    if handler is None:
        return

    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"[{suffix}] bad payload: {e}", flush=True)
        return

    ts = datetime.now(timezone.utc)
    try:
        points = handler(payload, ts)
    except Exception as e:
        print(f"[{suffix}] handler error: {e}", flush=True)
        return

    if not points:
        return

    try:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)
        print(f"[{suffix}] wrote {len(points)} point(s)", flush=True)
    except Exception as e:
        print(f"[{suffix}] influx write failed: {e}", flush=True)


def signal_handler(sig, frame):
    print("\nShutting down...", flush=True)
    if influx_client:
        influx_client.close()
    sys.exit(0)


def main():
    global influx_client, write_api

    print("=" * 60)
    print("Marmorikatu PLC MQTT Subscriber")
    print("=" * 60)
    print(f"MQTT Broker: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"Topic prefix: {TOPIC_PREFIX}")
    print(f"InfluxDB: {INFLUXDB_URL} bucket={INFLUXDB_BUCKET}")
    print("-" * 60)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    influx_client = InfluxDBClient(
        url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG
    )
    try:
        health = influx_client.health()
        print(f"InfluxDB status: {health.status}", flush=True)
    except Exception as e:
        print(f"Warning: could not verify InfluxDB health: {e}", flush=True)

    write_api = influx_client.write_api(write_options=SYNCHRONOUS)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
