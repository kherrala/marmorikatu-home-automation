#!/usr/bin/env python3
"""
Indoor temperature publisher for ThermIQ heat pump.

Reads the mean indoor temperature from InfluxDB (Ruuvi sensor, averaged over
the past AVERAGE_MINUTES minutes) and publishes it to ThermIQ as INDR_T via
MQTT. Runs on a fixed interval instead of reacting to every raw sensor message.

This replaces the per-message forwarding that was previously baked into the
ruuvi_mqtt_subscriber, which caused ThermIQ to receive INDR_T updates at
roughly 1 Hz — far more often than necessary.
"""

import json
import logging
import os
import signal
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient

# ── Configuration ─────────────────────────────────────────────────────────────
MQTT_BROKER    = os.environ.get("MQTT_BROKER",    "freenas.kherrala.fi")
MQTT_PORT      = int(os.environ.get("MQTT_PORT",  "1883"))
MQTT_SET_TOPIC = os.environ.get("MQTT_SET_TOPIC", "ThermIQ/marmorikatu/set")

INFLUXDB_URL    = os.environ.get("INFLUXDB_URL",    "http://localhost:8086")
INFLUXDB_TOKEN  = os.environ.get("INFLUXDB_TOKEN",  "wago-secret-token")
INFLUXDB_ORG    = os.environ.get("INFLUXDB_ORG",    "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

# Ruuvi sensor name used as the indoor reference
INDOOR_SENSOR   = os.environ.get("INDOOR_SENSOR",  "Olohuone")
# Averaging window (minutes) — also controls how much the value can jump
AVERAGE_MINUTES = int(os.environ.get("AVERAGE_MINUTES", "15"))
# How often to check and potentially publish (seconds)
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "300"))
# Minimum change (°C) required to trigger a new publish
MIN_CHANGE      = float(os.environ.get("MIN_CHANGE", "0.1"))
# Safety bounds — don't publish outside this range
INDOOR_MIN      = float(os.environ.get("INDOOR_MIN", "19.0"))
INDOOR_MAX      = float(os.environ.get("INDOOR_MAX", "25.0"))

DRY_RUN = os.environ.get("DRY_RUN", "0") in ("1", "true", "yes")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
running          = True
last_published   = None   # last INDR_T value sent to ThermIQ


def signal_handler(sig, frame):
    global running
    log.info("Shutdown requested")
    running = False


# ── InfluxDB ──────────────────────────────────────────────────────────────────

def fetch_mean_indoor_temp(query_api):
    """Return mean temperature from the configured indoor sensor over the last
    AVERAGE_MINUTES minutes, or None if no data is available."""
    flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{AVERAGE_MINUTES}m)
  |> filter(fn: (r) => r._measurement == "ruuvi"
       and r._field == "temperature"
       and r.sensor_name == "{INDOOR_SENSOR}")
  |> mean()
"""
    try:
        tables = query_api.query(flux, org=INFLUXDB_ORG)
        for table in tables:
            for record in table.records:
                return record.get_value()
    except Exception as e:
        log.error(f"InfluxDB query failed: {e}")
    return None


# ── MQTT ──────────────────────────────────────────────────────────────────────

def publish_indr_t(value):
    """Publish INDR_T to ThermIQ set topic. Returns True on success."""
    rounded = round(value, 1)
    payload = json.dumps({"INDR_T": rounded})
    if DRY_RUN:
        log.info(f"[DRY RUN] Would publish to {MQTT_SET_TOPIC}: {payload}")
        return True
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=10)
        result = client.publish(MQTT_SET_TOPIC, payload)
        result.wait_for_publish(timeout=5)
        client.disconnect()
        log.info(f"Published INDR_T={rounded}°C to {MQTT_SET_TOPIC}")
        return True
    except Exception as e:
        log.error(f"MQTT publish failed: {e}")
        return False


# ── Control loop ──────────────────────────────────────────────────────────────

def check_and_publish(query_api):
    global last_published

    temp = fetch_mean_indoor_temp(query_api)
    if temp is None:
        log.warning(f"No indoor temperature data for '{INDOOR_SENSOR}' in the last {AVERAGE_MINUTES} min")
        return

    log.info(f"Indoor mean ({AVERAGE_MINUTES} min): {temp:.2f}°C  (last sent: "
             f"{f'{last_published:.1f}°C' if last_published is not None else 'none'})")

    if temp < INDOOR_MIN or temp > INDOOR_MAX:
        log.warning(f"Temperature {temp:.2f}°C outside bounds [{INDOOR_MIN}, {INDOOR_MAX}] — skipping")
        return

    if last_published is not None and abs(temp - last_published) < MIN_CHANGE:
        log.info(f"Change {abs(temp - last_published):.2f}°C < threshold {MIN_CHANGE}°C — no publish needed")
        return

    if publish_indr_t(temp):
        last_published = temp


# ── Main ──────────────────────────────────────────────────────────────────────

def sleep_interruptible(seconds):
    end = time.monotonic() + seconds
    while running and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


def main():
    log.info("=" * 60)
    log.info("Indoor Temperature Publisher")
    log.info("=" * 60)
    log.info(f"Sensor:    {INDOOR_SENSOR} (mean over {AVERAGE_MINUTES} min)")
    log.info(f"MQTT:      {MQTT_BROKER}:{MQTT_PORT}  topic={MQTT_SET_TOPIC}")
    log.info(f"Interval:  {CHECK_INTERVAL}s  min_change={MIN_CHANGE}°C")
    log.info(f"Bounds:    {INDOOR_MIN}–{INDOOR_MAX}°C")
    if DRY_RUN:
        log.info("*** DRY RUN MODE — no MQTT commands will be sent ***")
    log.info("-" * 60)

    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    influx_client = InfluxDBClient(
        url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG
    )
    try:
        log.info(f"InfluxDB: {influx_client.health().status}")
    except Exception as e:
        log.warning(f"InfluxDB health check: {e}")

    query_api = influx_client.query_api()

    check_and_publish(query_api)

    while running:
        sleep_interruptible(CHECK_INTERVAL)
        if running:
            check_and_publish(query_api)

    influx_client.close()
    log.info("Shutdown complete")


if __name__ == "__main__":
    main()
