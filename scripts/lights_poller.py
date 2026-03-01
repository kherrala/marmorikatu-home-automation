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


# Global state
influx_client = None
write_api = None
running = True


def get_floor_name(floor: int | None) -> str:
    """Get human-readable floor name."""
    names = {0: "Kellari", 1: "Alakerta", 2: "Yläkerta"}
    return names.get(floor, "Muu")


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


def parse_polled_at(polled_at_str: str | None) -> datetime:
    """Parse polledAt ISO8601 timestamp from API, fall back to current time."""
    if polled_at_str:
        try:
            return datetime.fromisoformat(polled_at_str.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def process_light(light: dict) -> list:
    """Process a single light and return InfluxDB points."""
    points = []
    light_id = light.get("id", "unknown")
    name = light.get("name", light_id)
    floor = light.get("floor")  # int or None (outdoor/unclassified lights)
    floor_tag = str(floor) if floor is not None else ""
    timestamp = parse_polled_at(light.get("polledAt"))

    # Primary light status
    is_on = light.get("isOn")
    if is_on is not None:
        point = Point("lights") \
            .tag("light_id", light_id) \
            .tag("light_name", name) \
            .tag("floor", floor_tag) \
            .tag("floor_name", get_floor_name(floor)) \
            .tag("switch_type", "primary") \
            .field("is_on", 1 if is_on else 0) \
            .time(timestamp, WritePrecision.S)
        points.append(point)

    # Secondary light status (for dual-function lights)
    if light.get("hasDualFunction") and light.get("isOn2") is not None:
        point = Point("lights") \
            .tag("light_id", f"{light_id}-2") \
            .tag("light_name", f"{name} (2)") \
            .tag("floor", floor_tag) \
            .tag("floor_name", get_floor_name(floor)) \
            .tag("switch_type", "secondary") \
            .field("is_on", 1 if light["isOn2"] else 0) \
            .time(timestamp, WritePrecision.S)
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
