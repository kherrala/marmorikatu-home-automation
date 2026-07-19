#!/usr/bin/env python3
"""
BLE identity subscriber — whole-house "who / anyone is home".

The Ruuvi Gateway can retransmit every BLE advertisement it hears over MQTT as
`ruuvi/<gw_mac>/<device_mac>` with the envelope:

    {"gw_mac":"CC:F1:A2:8E:F8:8A","rssi":-71,"aoa":[],"gwts":...,"ts":...,
     "data":"02011A1BFF7500...","coords":""}

(`data` is the raw BLE advertisement hex.) This subscriber records each device
sighting into the `ble` measurement (tag `mac`, optional `device_class`/`name`;
field `rssi`) so the lights-optimizer can count strong-RSSI phone-class
advertisers → whole-house-away. See docs/lights-optimizer.md (Core F).

NOTE: modern phones AND Samsung SmartTag / Apple AirTag finders use randomized
private MACs that rotate ~every 15 min, so this gives at best AGGREGATE presence
(how many strong advertisers are around), NOT stable per-person identity.

CAVEAT for this house: raw advertiser-count is NOT a reliable "anyone home"
signal — an always-on Samsung SmartTag 2 in the basement (on the bike, separated
from its owner) broadcasts rotating finder beacons 24/7, so the count never
reaches zero. The optimizer therefore keeps BLE-away OFF by default
(BLE_AWAY_ENABLED). This measurement is still useful raw data for a future,
smarter presence model; real occupancy is the Zigbee Presence Service's job.

The gateway must be configured to forward BLE advertisements (raw mode) to this
broker/topic. This subscriber branches on payload shape (`data`+`gw_mac`
present) so it ignores the gateway's `gw_status` heartbeat and any decoded
Ruuvi-tag JSON that shares the topic tree — it never interferes with
`ruuvi_mqtt_subscriber`.
"""
import json
import os
import signal
import sys
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from health import touch_health

MQTT_BROKER = os.environ.get("MQTT_BROKER", "freenas.kherrala.fi")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
# Same topic tree the gateway publishes on; we filter by payload shape.
BLE_MQTT_TOPIC = os.environ.get("BLE_MQTT_TOPIC", "ruuvi/#")

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

# Optional friendly names for known static-MAC devices (JSON MAC→name).
try:
    BLE_DEVICE_NAMES = json.loads(os.environ.get("BLE_DEVICE_NAMES", "") or "{}")
    if not isinstance(BLE_DEVICE_NAMES, dict):
        BLE_DEVICE_NAMES = {}
except json.JSONDecodeError:
    BLE_DEVICE_NAMES = {}

# Company IDs (little-endian in AD 0xFF) and 16-bit service UUIDs that identify
# common phone/wearable classes. Enough to bucket advertisers; not exhaustive.
_COMPANY_CLASS = {0x0075: "samsung", 0x004C: "apple", 0x00E0: "google",
                  0x0499: "ruuvi", 0x0006: "microsoft"}
_SERVICE_CLASS = {0xFD5A: "samsung", 0xFCF1: "google", 0xFE2C: "google",
                  0xFEAA: "google", 0x180F: "generic"}

influx_client = None
write_api = None


def _classify(data_hex: str) -> str:
    """Best-effort device class from the raw advertisement AD structures."""
    try:
        b = bytes.fromhex(data_hex)
    except ValueError:
        return "other"
    i = 0
    while i < len(b):
        length = b[i]
        if length == 0 or i + length >= len(b) + 1:
            break
        ad_type = b[i + 1] if i + 1 < len(b) else 0
        val = b[i + 2:i + 1 + length]
        if ad_type == 0xFF and len(val) >= 2:            # manufacturer specific
            company = val[0] | (val[1] << 8)
            if company in _COMPANY_CLASS:
                return _COMPANY_CLASS[company]
        elif ad_type in (0x02, 0x03) and len(val) >= 2:  # 16-bit service UUIDs
            uuid = val[0] | (val[1] << 8)
            if uuid in _SERVICE_CLASS:
                return _SERVICE_CLASS[uuid]
        elif ad_type == 0x16 and len(val) >= 2:          # 16-bit service data
            uuid = val[0] | (val[1] << 8)
            if uuid in _SERVICE_CLASS:
                return _SERVICE_CLASS[uuid]
        i += length + 1
    return "other"


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        client.subscribe(BLE_MQTT_TOPIC)
        print(f"Connected to {MQTT_BROKER}:{MQTT_PORT}, subscribed to {BLE_MQTT_TOPIC}",
              flush=True)
    else:
        print(f"MQTT connect failed (rc={rc})", flush=True)


def on_disconnect(client, userdata, rc, properties=None, reason_code=None):
    print(f"Disconnected from MQTT broker (rc={rc})", flush=True)


def on_message(client, userdata, msg):
    # Any received message (even the gw_status heartbeat) proves we're connected
    # and the feed is alive — BLE arrives in bursts, so tie liveness to receipt,
    # not to writes, or the container falsely goes unhealthy during quiet spells.
    touch_health()
    try:
        d = json.loads(msg.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    # Only raw BLE advertisement envelopes — ignore gw_status and decoded JSON.
    if not isinstance(d, dict) or "data" not in d or "gw_mac" not in d:
        return
    mac = msg.topic.split("/")[-1].upper()
    rssi = d.get("rssi")
    if rssi is None:
        return
    try:
        rssi = int(rssi)
    except (TypeError, ValueError):
        return

    p = (
        Point("ble")
        .tag("mac", mac)
        .tag("device_class", _classify(d.get("data", "")))
        .field("rssi", rssi)
        .time(datetime.now(timezone.utc), WritePrecision.S)
    )
    name = BLE_DEVICE_NAMES.get(mac)
    if name:
        p = p.tag("name", name)
    try:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=[p])
        touch_health()
    except Exception as e:
        print(f"[ble] influx write failed: {e}", flush=True)


def signal_handler(sig, frame):
    print("\nShutting down...", flush=True)
    if influx_client:
        influx_client.close()
    sys.exit(0)


def main():
    global influx_client, write_api
    print("=" * 60)
    print("Marmorikatu BLE Identity Subscriber")
    print(f"MQTT: {MQTT_BROKER}:{MQTT_PORT} topic={BLE_MQTT_TOPIC}")
    print(f"InfluxDB: {INFLUXDB_URL} bucket={INFLUXDB_BUCKET}")
    print("=" * 60)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    influx_client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    try:
        print(f"InfluxDB status: {influx_client.health().status}", flush=True)
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
