#!/usr/bin/env python3
"""
Presence Engine — normalizes Zigbee occupancy sensors into per-room presence.

Consumes raw Zigbee2MQTT device events (Aqara FP300 mmWave, SONOFF SNZB-03PR2
PIR) and produces a single vendor-neutral occupancy model per room:

    { "room": "living_room", "occupied": true, "confidence": 0.95,
      "source": "aqara_fp300", "illuminance": 40, "battery": 96, "ts": ... }

Published two ways so any consumer can use it:
  * MQTT topic  presence/<room>   (retained JSON)
  * InfluxDB    measurement `presence`  (tags room, source; fields occupied,
    confidence, illuminance, battery)

The lights-optimizer consumes exactly this (its `presence_for_room()` reads the
`presence` measurement) — so adding a sensor lights up that room's automation
with no optimizer change.

Room state ownership: the engine owns the occupancy *timing*. A room goes
occupied on the first positive signal and stays occupied until `linger_s` after
the last positive signal, then flips to vacant. mmWave rooms use a short linger
(the sensor already reports stillness); PIR rooms use a longer one so the light
doesn't drop between re-triggers. Consumers therefore see a clean, debounced
occupied/vacant — they don't need their own timers.

Config: config/presence_rooms.json (hot-reloaded on mtime change) maps each
Zigbee2MQTT `friendly_name` to a room, and each room to a sensor type + linger.
See docs/presence-setup.md.
"""
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from health import touch_health

MQTT_BROKER = os.environ.get("MQTT_BROKER", "freenas.kherrala.fi")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
Z2M_BASE = os.environ.get("Z2M_BASE_TOPIC", "zigbee2mqtt")
PRESENCE_TOPIC_PREFIX = os.environ.get("PRESENCE_TOPIC_PREFIX", "presence")

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

CONFIG_FILE = os.environ.get("PRESENCE_ROOMS_FILE", "/app/config/presence_rooms.json")
TICK_S = float(os.environ.get("PRESENCE_TICK_S", "5"))
# Re-publish/re-write each room at least this often (liveness + InfluxDB freshness).
HEARTBEAT_S = float(os.environ.get("PRESENCE_HEARTBEAT_S", "60"))

# Default per-type behaviour when a room omits it.
TYPE_DEFAULTS = {
    "mmwave": {"linger_s": 30, "confidence": 0.95},
    "pir":    {"linger_s": 120, "confidence": 0.85},
}

influx_client = None
write_api = None

# Loaded config
_devices: dict[str, str] = {}      # friendly_name -> room
_rooms: dict[str, dict] = {}       # room -> {type, linger_s, confidence}
_config_mtime = 0.0

# Runtime room state
#   room -> {occupied, last_positive, illuminance, battery, source, last_emit}
_state: dict[str, dict] = {}


def _log(msg):
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}", flush=True)


def load_config(force=False):
    """(Re)load config/presence_rooms.json on first call or mtime change."""
    global _devices, _rooms, _config_mtime
    try:
        mtime = os.path.getmtime(CONFIG_FILE)
    except OSError:
        if force:
            _log(f"WARNING: config {CONFIG_FILE} not found — no rooms mapped yet")
        return
    if not force and mtime == _config_mtime:
        return
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _log(f"ERROR reading config: {e}")
        return
    devices = cfg.get("devices", {}) or {}
    rooms = {}
    for room, rc in (cfg.get("rooms", {}) or {}).items():
        rc = dict(rc or {})
        defaults = TYPE_DEFAULTS.get(rc.get("type", "pir"), TYPE_DEFAULTS["pir"])
        rc.setdefault("linger_s", defaults["linger_s"])
        rc.setdefault("confidence", defaults["confidence"])
        rooms[room] = rc
    _devices, _rooms, _config_mtime = devices, rooms, mtime
    _log(f"config loaded: {len(_devices)} devices → {len(_rooms)} rooms "
         f"({', '.join(sorted(_rooms)) or 'none'})")


def _positive(payload: dict) -> bool | None:
    """Is this a positive occupancy/motion signal? None if the message carries
    no occupancy field (e.g. a battery-only report)."""
    for key in ("occupancy", "presence"):
        if key in payload:
            v = payload[key]
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.lower() in ("true", "occupied", "presence", "detected", "1")
            if isinstance(v, (int, float)):
                return v > 0
    return None


def _num(payload: dict, *keys):
    for k in keys:
        if k in payload and isinstance(payload[k], (int, float)):
            return payload[k]
    return None


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        topic = f"{Z2M_BASE}/#"
        client.subscribe(topic)
        _log(f"connected {MQTT_BROKER}:{MQTT_PORT}, subscribed {topic}")
    else:
        _log(f"MQTT connect failed rc={rc}")


def on_message(client, userdata, msg):
    touch_health()
    # zigbee2mqtt/<friendly_name>  (skip bridge/availability sub-topics)
    parts = msg.topic.split("/")
    if len(parts) != 2 or parts[0] != Z2M_BASE:
        return
    friendly = parts[1]
    room = _devices.get(friendly)
    if room is None:
        return  # device not mapped to a room
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return

    st = _state.setdefault(room, {"occupied": False, "last_positive": 0.0,
                                  "illuminance": None, "battery": None,
                                  "source": friendly, "last_emit": 0.0})
    st["source"] = friendly
    lux = _num(payload, "illuminance_lux", "illuminance")
    if lux is not None:
        st["illuminance"] = lux
    batt = _num(payload, "battery")
    if batt is not None:
        st["battery"] = batt

    pos = _positive(payload)
    if pos is True:
        st["last_positive"] = time.time()
        if not st["occupied"]:
            st["occupied"] = True
            emit_room(room)  # rising edge — publish immediately
    elif pos is False:
        # A device that explicitly reports vacant just stops refreshing the
        # linger; the tick loop flips the room vacant once linger_s elapses.
        pass


def emit_room(room: str):
    st = _state.get(room)
    rc = _rooms.get(room)
    if st is None or rc is None:
        return
    now = time.time()
    st["last_emit"] = now
    payload = {
        "room": room,
        "occupied": bool(st["occupied"]),
        "confidence": rc["confidence"] if st["occupied"] else 0.0,
        "source": st["source"],
        "ts": int(now),
    }
    if st["illuminance"] is not None:
        payload["illuminance"] = st["illuminance"]
    if st["battery"] is not None:
        payload["battery"] = st["battery"]
    try:
        client.publish(f"{PRESENCE_TOPIC_PREFIX}/{room}", json.dumps(payload),
                       qos=1, retain=True)
    except Exception as e:
        _log(f"publish presence/{room} failed: {e}")

    p = (Point("presence")
         .tag("room", room)
         .tag("source", st["source"])
         .field("occupied", 1 if st["occupied"] else 0)
         .field("confidence", float(payload["confidence"]))
         .time(datetime.now(timezone.utc), WritePrecision.S))
    if st["illuminance"] is not None:
        p = p.field("illuminance", float(st["illuminance"]))
    if st["battery"] is not None:
        p = p.field("battery", float(st["battery"]))
    try:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=p)
    except Exception as e:
        _log(f"influx write presence/{room} failed: {e}")


# Module-level client so emit_room can reach it.
client: mqtt.Client | None = None
running = True


def signal_handler(sig, frame):
    global running
    running = False


def main():
    global influx_client, write_api, client
    _log("Marmorikatu Presence Engine")
    _log(f"Z2M topic: {Z2M_BASE}/#  → presence/<room> + `presence` measurement")
    load_config(force=True)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    influx_client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    try:
        _log(f"InfluxDB: {influx_client.health().status}")
    except Exception as e:
        _log(f"InfluxDB health check: {e}")
    write_api = influx_client.write_api(write_options=SYNCHRONOUS)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()

    while running:
        load_config()  # hot-reload on mtime change
        now = time.time()
        for room, rc in _rooms.items():
            st = _state.setdefault(room, {"occupied": False, "last_positive": 0.0,
                                          "illuminance": None, "battery": None,
                                          "source": "", "last_emit": 0.0})
            # Expire occupancy after the room's linger.
            if st["occupied"] and (now - st["last_positive"]) > rc["linger_s"]:
                st["occupied"] = False
                emit_room(room)
            elif (now - st["last_emit"]) > HEARTBEAT_S:
                emit_room(room)  # periodic refresh (InfluxDB freshness + liveness)
        touch_health()
        end = now + TICK_S
        while running and time.time() < end:
            time.sleep(min(0.5, end - time.time()))

    client.loop_stop()
    if influx_client:
        influx_client.close()
    _log("Shutdown complete")


if __name__ == "__main__":
    main()
