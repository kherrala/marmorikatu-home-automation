#!/usr/bin/env python3
"""
Finnish electricity spot price poller.

Polls spot-hinta.fi API for Nord Pool day-ahead market prices
and stores them in InfluxDB.
"""

import os
import signal
import sys
import time
import requests
from dateutil.parser import isoparse
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# Configuration from environment
SPOT_HINTA_API_URL = os.environ.get("SPOT_HINTA_API_URL", "https://api.spot-hinta.fi/TodayAndDayForward")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "900"))  # 15 minutes default

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

# Global state
influx_client = None
write_api = None
running = True


def fetch_prices():
    """Fetch electricity prices from spot-hinta.fi API."""
    try:
        response = requests.get(SPOT_HINTA_API_URL, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error fetching spot prices: {e}")
        return []


def process_prices(data):
    """Convert API response to InfluxDB points."""
    points = []
    for entry in data:
        try:
            dt = isoparse(entry["DateTime"])
            dt_utc = dt.astimezone(tz=None).replace(tzinfo=None)

            point = Point("electricity") \
                .tag("source", "spot-hinta.fi") \
                .tag("market", "FI") \
                .field("price_no_tax", float(entry["PriceNoTax"]) * 100.0) \
                .field("price_with_tax", float(entry["PriceWithTax"]) * 100.0) \
                .time(dt_utc, WritePrecision.S)
            points.append(point)
        except (KeyError, ValueError) as e:
            print(f"Skipping entry: {e}")
    return points


def write_to_influxdb(points):
    """Write points to InfluxDB."""
    try:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)
        print(f"Wrote {len(points)} electricity price points to InfluxDB")
    except Exception as e:
        print(f"Error writing to InfluxDB: {e}")


def poll_and_store():
    """Fetch prices and store to InfluxDB."""
    data = fetch_prices()
    if not data:
        print("No price data received")
        return

    points = process_prices(data)
    if points:
        write_to_influxdb(points)

        # Log price range summary
        prices = [entry.get("PriceWithTax", 0) for entry in data]
        print(f"Price range: {min(prices):.2f} - {max(prices):.2f} c/kWh ({len(data)} entries)")


def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    global running
    print("\nShutting down...")
    running = False


def main():
    global influx_client, write_api, running

    print("=" * 60)
    print("Electricity Spot Price Poller")
    print("=" * 60)
    print(f"API: {SPOT_HINTA_API_URL}")
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
