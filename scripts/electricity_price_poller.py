#!/usr/bin/env python3
"""
Finnish electricity spot price poller.

Polls spot-hinta.fi API for Nord Pool day-ahead market prices
and stores them in InfluxDB.

Prices are published once daily around 14:15 EET. The script fetches
on startup, then waits until 14:15 EET and polls every 10 minutes
until tomorrow's prices are available. Once fetched, it sleeps until
the next day's 14:15.
"""

import os
import signal
import time
from datetime import datetime, timedelta, timezone
import requests
from dateutil.parser import isoparse
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# Configuration from environment
SPOT_HINTA_API_URL = os.environ.get("SPOT_HINTA_API_URL", "https://api.spot-hinta.fi/TodayAndDayForward")
RETRY_INTERVAL = int(os.environ.get("RETRY_INTERVAL", "600"))  # 10 minutes

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

EET = timezone(timedelta(hours=2))
PUBLISH_HOUR = 14
PUBLISH_MINUTE = 15

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


def has_tomorrow_prices(data):
    """Check if the response contains prices for tomorrow."""
    now_eet = datetime.now(EET)
    tomorrow_date = (now_eet + timedelta(days=1)).date()
    for entry in data:
        try:
            dt = isoparse(entry["DateTime"])
            if dt.astimezone(EET).date() == tomorrow_date:
                return True
        except (KeyError, ValueError):
            pass
    return False


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
    """Fetch prices and store to InfluxDB. Returns True if tomorrow's prices were included."""
    data = fetch_prices()
    if not data:
        print("No price data received")
        return False

    tomorrow_available = has_tomorrow_prices(data)

    points = process_prices(data)
    if points:
        write_to_influxdb(points)
        prices = [entry.get("PriceWithTax", 0) * 100.0 for entry in data]
        print(f"Price range: {min(prices):.1f} - {max(prices):.1f} c/kWh ({len(data)} entries)")
        if tomorrow_available:
            print("Tomorrow's prices are available")
        else:
            print("Tomorrow's prices not yet available")

    return tomorrow_available


def seconds_until_next_publish():
    """Calculate seconds until the next 14:15 EET."""
    now = datetime.now(EET)
    target = now.replace(hour=PUBLISH_HOUR, minute=PUBLISH_MINUTE, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    delta = (target - now).total_seconds()
    return delta


def sleep_interruptible(seconds):
    """Sleep in 1-second increments so signal handlers can interrupt."""
    end = time.monotonic() + seconds
    while running and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


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
    print(f"Retry Interval: {RETRY_INTERVAL} seconds")
    print(f"InfluxDB: {INFLUXDB_URL}")
    print(f"Bucket: {INFLUXDB_BUCKET}")
    print("-" * 60)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

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

    # Initial fetch
    print("Starting initial fetch...")
    has_tomorrow = poll_and_store()

    while running:
        if has_tomorrow:
            # Tomorrow's prices already fetched — sleep until next publish window
            wait = seconds_until_next_publish()
            print(f"Next check at {PUBLISH_HOUR}:{PUBLISH_MINUTE:02d} EET "
                  f"(sleeping {wait / 3600:.1f} hours)")
            sleep_interruptible(wait)
            if not running:
                break
            has_tomorrow = poll_and_store()
        else:
            # Tomorrow's prices not yet available — retry every RETRY_INTERVAL
            now_eet = datetime.now(EET)
            publish_time = now_eet.replace(hour=PUBLISH_HOUR, minute=PUBLISH_MINUTE,
                                           second=0, microsecond=0)
            if now_eet < publish_time:
                # Before publish window — sleep until then
                wait = (publish_time - now_eet).total_seconds()
                print(f"Before publish window, sleeping until "
                      f"{PUBLISH_HOUR}:{PUBLISH_MINUTE:02d} EET "
                      f"({wait / 3600:.1f} hours)")
                sleep_interruptible(wait)
                if not running:
                    break
                has_tomorrow = poll_and_store()
            else:
                # In publish window — retry every RETRY_INTERVAL
                print(f"Tomorrow's prices not yet available, "
                      f"retrying in {RETRY_INTERVAL} seconds...")
                sleep_interruptible(RETRY_INTERVAL)
                if not running:
                    break
                has_tomorrow = poll_and_store()

    if influx_client:
        influx_client.close()
    print("Shutdown complete")


if __name__ == "__main__":
    main()
