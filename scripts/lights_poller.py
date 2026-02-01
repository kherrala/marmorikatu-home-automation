#!/usr/bin/env python3
"""
Light switch status poller.

Polls the lights API and stores status in InfluxDB.
Each light switch status is stored with floor classification.
"""

import os
import json
import signal
import sys
import time
from datetime import datetime, timezone
import requests
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# Configuration from environment
LIGHTS_API_URL = os.environ.get("LIGHTS_API_URL", "http://localhost:8080/api/lights")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))  # 5 minutes default

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

# Floor classification based on light ID patterns
# Floor 0 = Basement, Floor 1 = Ground floor, Floor 2 = Upstairs
FLOOR_MAPPING = {
    # Basement (kellari)
    "tekninen-tila": 0,
    "kellari-wc": 0,
    "kellari-eteinen-1": 0,
    "kellari-eteinen-2": 0,
    "kellari-1": 0,
    "kellari-2": 0,
    # Ground floor (alakerta)
    "kylpyhuone-1": 1,
    "kylpyhuone-2": 1,
    "wc-alakerta-1": 1,
    "wc-alakerta-2": 1,
    "khh-1": 1,
    "khh-2": 1,
    "khh-vaatehuone": 1,
    "keittio-1": 1,
    "keittio-2": 1,
    "tuulikaappi-1": 1,
    "tuulikaappi-2": 1,
    "tuulikaappi-vaatehuone": 1,
    "mh-alakerta-1": 1,
    "mh-alakerta-2": 1,
    "eteinen-1": 1,
    "eteinen-2": 1,
    "saareke-1": 1,
    "saareke-2": 1,
    "saareke-3": 1,
    "saareke-4": 1,
    "saareke-5": 1,
    "saareke-6": 1,
    "saareke-7": 1,
    "saareke-8": 1,
    "autokatos-1": 1,
    "autokatos-2": 1,
    "ulkovarasto": 1,
    # Upstairs (yläkerta)
    "porras-ak-1": 2,
    "porras-ak-2": 2,
    "mh-1-1": 2,
    "mh-1-2": 2,
    "mh-1-vaatehuone": 2,
    "kylpyhuone-yk-1": 2,
    "kylpyhuone-yk-2": 2,
    "porras-yk-1": 2,
    "porras-yk-2": 2,
    "aula-yk-1": 2,
    "aula-yk-2": 2,
    "mh2-1": 2,
    "mh2-2": 2,
    "mh3-1": 2,
    "mh3-2": 2,
}

# Global state
influx_client = None
write_api = None
running = True


def get_floor(light_id: str) -> int:
    """Get floor number for a light ID. Returns 1 (ground) if unknown."""
    return FLOOR_MAPPING.get(light_id, 1)


def get_floor_name(floor: int) -> str:
    """Get human-readable floor name."""
    names = {0: "Kellari", 1: "Alakerta", 2: "Yläkerta"}
    return names.get(floor, "Tuntematon")


def poll_lights() -> list:
    """Fetch light status from API."""
    try:
        response = requests.get(LIGHTS_API_URL, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("lights", [])
    except requests.RequestException as e:
        print(f"Error fetching lights API: {e}")
        return []


def process_light(light: dict) -> list:
    """Process a single light and return InfluxDB points."""
    points = []
    light_id = light.get("id", "unknown")
    name = light.get("name", light_id)
    floor = get_floor(light_id)

    # Primary switch status
    is_on = light.get("isOn")
    if is_on is not None:
        first_press = light.get("firstPress", name)
        point = Point("lights") \
            .tag("light_id", light_id) \
            .tag("light_name", first_press) \
            .tag("floor", str(floor)) \
            .tag("floor_name", get_floor_name(floor)) \
            .tag("switch_type", "primary") \
            .field("is_on", 1 if is_on else 0) \
            .time(datetime.now(timezone.utc), WritePrecision.S)
        points.append(point)

    # Secondary switch status (for dual-function switches)
    if light.get("hasDualFunction") and light.get("isOn2") is not None:
        second_press = light.get("secondPress", f"{name} (2)")
        point = Point("lights") \
            .tag("light_id", f"{light_id}-2") \
            .tag("light_name", second_press) \
            .tag("floor", str(floor)) \
            .tag("floor_name", get_floor_name(floor)) \
            .tag("switch_type", "secondary") \
            .field("is_on", 1 if light["isOn2"] else 0) \
            .time(datetime.now(timezone.utc), WritePrecision.S)
        points.append(point)

    return points


def write_to_influxdb(points: list):
    """Write points to InfluxDB."""
    global write_api
    try:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)
        print(f"Wrote {len(points)} light status points to InfluxDB")
    except Exception as e:
        print(f"Error writing to InfluxDB: {e}")


def poll_and_store():
    """Poll lights API and store to InfluxDB."""
    lights = poll_lights()
    if not lights:
        print("No lights data received")
        return

    points = []
    for light in lights:
        points.extend(process_light(light))

    if points:
        write_to_influxdb(points)

    # Calculate and log summary
    on_count = sum(1 for p in points if p._fields.get("is_on") == 1)
    print(f"Light status: {on_count}/{len(points)} lights on")


def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    global running
    print("\nShutting down...")
    running = False


def main():
    global influx_client, write_api, running

    print("=" * 60)
    print("Light Switch Status Poller")
    print("=" * 60)
    print(f"Lights API: {LIGHTS_API_URL}")
    print(f"Poll Interval: {POLL_INTERVAL} seconds")
    print(f"InfluxDB: {INFLUXDB_URL}")
    print(f"Bucket: {INFLUXDB_BUCKET}")
    print("-" * 60)

    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Connect to InfluxDB
    print("Connecting to InfluxDB...")
    influx_client = InfluxDBClient(
        url=INFLUXDB_URL,
        token=INFLUXDB_TOKEN,
        org=INFLUXDB_ORG
    )

    try:
        health = influx_client.health()
        print(f"InfluxDB status: {health.status}")
    except Exception as e:
        print(f"Warning: Could not verify InfluxDB health: {e}")

    write_api = influx_client.write_api(write_options=SYNCHRONOUS)

    # Initial poll
    print("Starting initial poll...")
    poll_and_store()

    # Main polling loop
    print(f"Entering polling loop (every {POLL_INTERVAL} seconds)...")
    while running:
        time.sleep(POLL_INTERVAL)
        if running:
            poll_and_store()

    # Cleanup
    if influx_client:
        influx_client.close()
    print("Shutdown complete")


if __name__ == "__main__":
    main()
