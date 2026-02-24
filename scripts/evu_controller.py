#!/usr/bin/env python3
"""
ThermIQ EVU mode controller.

Enables the EVU block on the heat pump when electricity prices are high for the
next 6 hours but expected to fall in the 3 hours after that, preventing the
compressor from running during expensive peak periods while ensuring prices
will recover before committing to an extended block.

EVU is enabled ({"EVU":1}) when ALL conditions are met:
  - Price data is fully available for the next PEAK_HOURS hours
  - Average price_with_tax for the next PEAK_HOURS hours > PRICE_THRESHOLD c/kWh
  - Average price for the PEAK_HOURS+1 .. PEAK_HOURS+DROP_HOURS window < PRICE_THRESHOLD
    (i.e. prices are expected to drop after the peak — prevents blocking during a
    prolonged high-price period where there is no cheaper window ahead)

EVU is disabled ({"EVU":0}) when any condition is no longer met.

Checks are run every CHECK_INTERVAL seconds (default 900 = 15 minutes).
The EVU state is sent on every state change; on startup the current state is
always published to synchronise the device.
"""

import json
import logging
import os
import signal
import time
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient

# ── Configuration ─────────────────────────────────────────────────────────────
MQTT_BROKER   = os.environ.get("MQTT_BROKER",   "freenas.kherrala.fi")
MQTT_PORT     = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_SET_TOPIC = os.environ.get("MQTT_SET_TOPIC", "ThermIQ/marmorikatu/set")

INFLUXDB_URL    = os.environ.get("INFLUXDB_URL",    "http://localhost:8086")
INFLUXDB_TOKEN  = os.environ.get("INFLUXDB_TOKEN",  "wago-secret-token")
INFLUXDB_ORG    = os.environ.get("INFLUXDB_ORG",    "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

PRICE_THRESHOLD = float(os.environ.get("PRICE_THRESHOLD", "15.0"))  # c/kWh
PEAK_HOURS      = int(os.environ.get("PEAK_HOURS",  "6"))   # high-price window length
DROP_HOURS      = int(os.environ.get("DROP_HOURS",  "3"))   # low-price window length after peak
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "900"))  # seconds between checks

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
running   = True
evu_state = None  # None = unknown (force publish on first check)


def signal_handler(sig, frame):
    global running
    log.info("Shutdown requested")
    running = False


# ── InfluxDB helpers ──────────────────────────────────────────────────────────

def query_price_window(query_api, start_h: int, duration_h: int):
    """
    Query electricity prices for a window of `duration_h` complete hours
    starting `start_h` hours ahead of the current hour boundary.

    Returns (avg_price_c_per_kwh, num_points) or (None, 0) on error.
    """
    now_utc    = datetime.now(timezone.utc)
    hour_start = now_utc.replace(minute=0, second=0, microsecond=0)

    window_start = hour_start + timedelta(hours=start_h)
    window_stop  = window_start + timedelta(hours=duration_h)

    start_str = window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    stop_str  = window_stop.strftime("%Y-%m-%dT%H:%M:%SZ")

    flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {start_str}, stop: {stop_str})
  |> filter(fn: (r) => r._measurement == "electricity" and r._field == "price_with_tax")
  |> group()
  |> reduce(
      fn: (r, accumulator) => ({{
          sum:   accumulator.sum + r._value,
          count: accumulator.count + 1.0
      }}),
      identity: {{sum: 0.0, count: 0.0}}
  )
"""
    try:
        tables = query_api.query(flux, org=INFLUXDB_ORG)
        for table in tables:
            for record in table.records:
                count = record.values.get("count", 0.0)
                total = record.values.get("sum",   0.0)
                if count > 0:
                    return total / count, int(count)
    except Exception as e:
        log.error(f"InfluxDB query error: {e}")

    return None, 0


# ── MQTT helper ───────────────────────────────────────────────────────────────

def publish_evu(enabled: bool) -> bool:
    """Publish EVU=1 or EVU=0 to the ThermIQ set topic. Returns True on success."""
    payload = json.dumps({"EVU": 1 if enabled else 0})
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=10)
        result = client.publish(MQTT_SET_TOPIC, payload)
        result.wait_for_publish(timeout=5)
        client.disconnect()
        log.info(f"EVU → {'ON' if enabled else 'OFF'}  (published {payload} to {MQTT_SET_TOPIC})")
        return True
    except Exception as e:
        log.error(f"MQTT publish failed: {e}")
        return False


# ── Control logic ─────────────────────────────────────────────────────────────

def check_and_control(query_api):
    """Evaluate conditions and publish EVU state if it changed."""
    global evu_state

    # Query next PEAK_HOURS hours (starting from the next complete hour)
    avg_peak, count_peak = query_price_window(query_api, start_h=1, duration_h=PEAK_HOURS)

    if avg_peak is None or count_peak < PEAK_HOURS:
        log.warning(
            f"Price data unavailable or incomplete for next {PEAK_HOURS}h "
            f"({count_peak}/{PEAK_HOURS} points found) — EVU control skipped"
        )
        # Do not disable an already-active EVU here: if we lost data mid-peak
        # it is safer to leave the current state unchanged rather than releasing
        # the block prematurely.
        return

    # Query the following DROP_HOURS hours (hours PEAK_HOURS+1 .. PEAK_HOURS+DROP_HOURS)
    avg_drop, count_drop = query_price_window(
        query_api, start_h=1 + PEAK_HOURS, duration_h=DROP_HOURS
    )

    log.info(
        f"Prices: next {PEAK_HOURS}h avg={avg_peak:.2f} c/kWh ({count_peak} pts)  |  "
        f"hours {PEAK_HOURS+1}-{PEAK_HOURS+DROP_HOURS}: "
        f"{'N/A' if avg_drop is None else f'{avg_drop:.2f} c/kWh ({count_drop} pts)'}  |  "
        f"threshold={PRICE_THRESHOLD} c/kWh"
    )

    should_enable = (
        avg_peak > PRICE_THRESHOLD
        and avg_drop is not None
        and count_drop >= DROP_HOURS          # full drop window must have data
        and avg_drop < PRICE_THRESHOLD
    )

    if evu_state is None or should_enable != evu_state:
        if publish_evu(should_enable):
            evu_state = should_enable
    else:
        log.info(f"EVU unchanged ({'ON' if evu_state else 'OFF'})")


# ── Main loop ─────────────────────────────────────────────────────────────────

def sleep_interruptible(seconds: float):
    end = time.monotonic() + seconds
    while running and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


def main():
    log.info("=" * 60)
    log.info("ThermIQ EVU Controller")
    log.info("=" * 60)
    log.info(f"MQTT:      {MQTT_BROKER}:{MQTT_PORT}  topic={MQTT_SET_TOPIC}")
    log.info(f"Threshold: {PRICE_THRESHOLD} c/kWh")
    log.info(f"Windows:   peak={PEAK_HOURS}h, drop={DROP_HOURS}h")
    log.info(f"Interval:  {CHECK_INTERVAL}s")
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

    # Run immediately on startup, then every CHECK_INTERVAL
    check_and_control(query_api)

    while running:
        sleep_interruptible(CHECK_INTERVAL)
        if running:
            check_and_control(query_api)

    # Disable EVU cleanly on shutdown so the heat pump resumes normal operation
    if evu_state:
        log.info("Disabling EVU before exit...")
        publish_evu(False)

    influx_client.close()
    log.info("Shutdown complete")


if __name__ == "__main__":
    main()
