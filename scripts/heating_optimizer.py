#!/usr/bin/env python3
"""
Floor heating temperature optimizer.

Optimizes heating costs by adjusting the Thermia heat pump setpoint based on
electricity spot prices. Pre-heats during cheap hours (raising d50) and reduces
demand during expensive hours (via EVU mode which lowers target by reduction_t).

Price data is read from InfluxDB (written by the electricity price poller).
Outdoor temperature is read from InfluxDB (Ruuvi sensor).

Controls:
  - d50 (indoor_requested_t, hex r32): setpoint via MQTT write topic
  - EVU on/off: via MQTT set topic
  - d59 (reduction_t, hex r3b): configured at startup via MQTT write topic

Replaces the simpler evu_controller.py service.
"""

import json
import logging
import os
import signal
import time
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# ── Configuration ─────────────────────────────────────────────────────────────
MQTT_BROKER = os.environ.get("MQTT_BROKER", "freenas.kherrala.fi")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_WRITE_TOPIC = os.environ.get("MQTT_WRITE_TOPIC", "ThermIQ/marmorikatu/write")
MQTT_SET_TOPIC = os.environ.get("MQTT_SET_TOPIC", "ThermIQ/marmorikatu/set")

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

COMFORT_MIN = int(os.environ.get("COMFORT_MIN", "20"))
COMFORT_DEFAULT = int(os.environ.get("COMFORT_DEFAULT", "22"))
COMFORT_MAX = int(os.environ.get("COMFORT_MAX", "23"))
REDUCTION_T = int(os.environ.get("REDUCTION_T", "2"))

PRICE_PERCENTILE_CHEAP = float(os.environ.get("PRICE_PERCENTILE_CHEAP", "25"))
PRICE_PERCENTILE_EXPENSIVE = float(os.environ.get("PRICE_PERCENTILE_EXPENSIVE", "75"))

PRE_HEAT_HOURS = int(os.environ.get("PRE_HEAT_HOURS", "2"))
PRE_HEAT_SLOTS = PRE_HEAT_HOURS * 4  # quarter-hour slots

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))
MIN_HOLD_MINUTES = int(os.environ.get("MIN_HOLD_MINUTES", "15"))

DRY_RUN = os.environ.get("DRY_RUN", "0") in ("1", "true", "yes")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
running = True
current_setpoint = None   # last published setpoint
current_evu = None        # last published EVU state (True/False)
last_change_time = 0.0    # monotonic time of last MQTT change


def signal_handler(sig, frame):
    global running
    log.info("Shutdown requested")
    running = False


# ── MQTT helpers ──────────────────────────────────────────────────────────────

def mqtt_publish(topic, payload_dict):
    """Publish a JSON payload to an MQTT topic. Returns True on success."""
    message = json.dumps(payload_dict)
    if DRY_RUN:
        log.info(f"[DRY RUN] Would publish to {topic}: {message}")
        return True
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=10)
        result = client.publish(topic, message)
        result.wait_for_publish(timeout=5)
        client.disconnect()
        log.info(f"Published to {topic}: {message}")
        return True
    except Exception as e:
        log.error(f"MQTT publish failed: {e}")
        return False


def set_setpoint(value):
    """Set indoor target temperature (d50 / r32)."""
    global current_setpoint, last_change_time
    if current_setpoint == value:
        return
    if mqtt_publish(MQTT_WRITE_TOPIC, {"r32": value}):
        current_setpoint = value
        last_change_time = time.monotonic()
        log.info(f"Setpoint → {value}°C")


def set_evu(enabled):
    """Enable or disable EVU mode."""
    global current_evu, last_change_time
    if current_evu == enabled:
        return
    if mqtt_publish(MQTT_SET_TOPIC, {"EVU": 1 if enabled else 0}):
        current_evu = enabled
        last_change_time = time.monotonic()
        log.info(f"EVU → {'ON' if enabled else 'OFF'}")


def set_reduction_t(value):
    """Set reduction_t register (d59 / r3b) at startup."""
    mqtt_publish(MQTT_WRITE_TOPIC, {"r3b": value})
    log.info(f"reduction_t (d59) → {value}°C")


# ── InfluxDB queries ─────────────────────────────────────────────────────────

def fetch_price_forecast(query_api):
    """
    Fetch electricity prices from now to +36h.
    Returns list of (datetime_utc, price_c_per_kwh) sorted by time.
    """
    flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h, stop: 36h)
  |> filter(fn: (r) => r._measurement == "electricity" and r._field == "price_with_tax")
  |> sort(columns: ["_time"])
"""
    try:
        tables = query_api.query(flux, org=INFLUXDB_ORG)
        prices = []
        for table in tables:
            for record in table.records:
                prices.append((record.get_time(), record.get_value()))
        return prices
    except Exception as e:
        log.error(f"Failed to fetch prices: {e}")
        return []


def fetch_outdoor_temperature(query_api):
    """Fetch latest outdoor temperature from Ruuvi sensor."""
    flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r._field == "temperature")
  |> filter(fn: (r) => r.sensor_name == "Ulkolämpötila" or r.sensor_name == "Ulkolampotila")
  |> last()
"""
    try:
        tables = query_api.query(flux, org=INFLUXDB_ORG)
        for table in tables:
            for record in table.records:
                return record.get_value()
    except Exception as e:
        log.error(f"Failed to fetch outdoor temp: {e}")
    return None


# ── Price classification ─────────────────────────────────────────────────────

CHEAP = "CHEAP"
NORMAL = "NORMAL"
EXPENSIVE = "EXPENSIVE"


def percentile(values, pct):
    """Calculate percentile of a sorted list."""
    if not values:
        return 0
    k = (len(values) - 1) * pct / 100.0
    f = int(k)
    c = f + 1
    if c >= len(values):
        return values[f]
    return values[f] + (k - f) * (values[c] - values[f])


def classify_prices(prices):
    """
    Classify price entries into quarter-hour slots.
    Returns list of (slot_start_utc, tier, price) tuples.
    """
    if not prices:
        return []

    sorted_values = sorted(p for _, p in prices)
    p_cheap = percentile(sorted_values, PRICE_PERCENTILE_CHEAP)
    p_expensive = percentile(sorted_values, PRICE_PERCENTILE_EXPENSIVE)

    log.info(f"Price thresholds: cheap ≤ {p_cheap:.2f}, expensive ≥ {p_expensive:.2f} c/kWh")

    classified = []
    for ts, price in prices:
        if price <= p_cheap:
            tier = CHEAP
        elif price >= p_expensive:
            tier = EXPENSIVE
        else:
            tier = NORMAL
        classified.append((ts, tier, price))

    return classified


def apply_pre_heat(classified):
    """
    Apply pre-heat look-ahead: boost slots before EXPENSIVE blocks to CHEAP
    (which maps to COMFORT_MAX). Returns a new list with modified tiers.
    """
    result = list(classified)
    for i, (ts, tier, price) in enumerate(classified):
        if tier == EXPENSIVE:
            for j in range(max(0, i - PRE_HEAT_SLOTS), i):
                _, orig_tier, _ = result[j]
                if orig_tier != EXPENSIVE:
                    result[j] = (result[j][0], CHEAP, result[j][2])
    return result


def get_current_action(schedule, outdoor_temp):
    """
    Look up the current quarter-hour slot and return (setpoint, evu_enabled).
    Applies cold-weather constraints.
    """
    now_utc = datetime.now(timezone.utc)

    # Find the slot that contains now (price timestamp is the slot start)
    current_tier = NORMAL
    current_price = None
    for i, (ts, tier, price) in enumerate(schedule):
        # Make ts offset-aware UTC if naive
        slot_time = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        # Check if this is the current or most recent past slot
        if slot_time <= now_utc:
            current_tier = tier
            current_price = price
        else:
            break

    log.info(f"Current slot: tier={current_tier}, price={current_price:.2f} c/kWh" if current_price else f"Current slot: tier={current_tier}")

    # Base action from tier
    if current_tier == CHEAP:
        setpoint = COMFORT_MAX
        evu = False
    elif current_tier == EXPENSIVE:
        setpoint = COMFORT_DEFAULT
        evu = True
    else:
        setpoint = COMFORT_DEFAULT
        evu = False

    # Cold-weather constraints on pre-heating
    if outdoor_temp is not None and setpoint > COMFORT_DEFAULT:
        if outdoor_temp < -20:
            log.info(f"Outdoor {outdoor_temp:.1f}°C < -20°C: no pre-heat")
            setpoint = COMFORT_DEFAULT
        elif outdoor_temp < -10:
            cap = COMFORT_DEFAULT + 1
            if setpoint > cap:
                log.info(f"Outdoor {outdoor_temp:.1f}°C < -10°C: cap pre-heat to {cap}°C")
                setpoint = cap

    # Enforce hard floor
    setpoint = max(setpoint, COMFORT_MIN)

    return setpoint, evu


# ── Decision logging ─────────────────────────────────────────────────────────

def log_decision(write_api, setpoint, evu, tier, price, outdoor_temp):
    """Write optimizer decision to InfluxDB for dashboard visualization."""
    point = Point("heating_optimizer") \
        .field("setpoint", setpoint) \
        .field("evu_active", 1 if evu else 0) \
        .field("tier", tier) \
        .field("effective_target", setpoint - REDUCTION_T if evu else setpoint)

    if price is not None:
        point = point.field("price", price)
    if outdoor_temp is not None:
        point = point.field("outdoor_temp", outdoor_temp)

    point = point.time(datetime.now(timezone.utc), WritePrecision.S)

    try:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)
    except Exception as e:
        log.error(f"Failed to log decision: {e}")


# ── Control loop ─────────────────────────────────────────────────────────────

def check_and_control(query_api, write_api):
    """Main control cycle: fetch data, classify, apply actions."""
    # 1. Fetch price forecast
    prices = fetch_price_forecast(query_api)
    if len(prices) < 12:  # less than 3 hours of data
        log.warning(f"Insufficient price data ({len(prices)} points, need ≥12) — using defaults")
        set_setpoint(COMFORT_DEFAULT)
        set_evu(False)
        return

    log.info(f"Fetched {len(prices)} price points")

    # 2. Fetch outdoor temperature
    outdoor_temp = fetch_outdoor_temperature(query_api)
    if outdoor_temp is not None:
        log.info(f"Outdoor temperature: {outdoor_temp:.1f}°C")
    else:
        log.warning("Outdoor temperature unavailable")

    # 3. Classify prices
    classified = classify_prices(prices)

    # 4. Apply pre-heat look-ahead
    schedule = apply_pre_heat(classified)

    # Count tiers for logging
    tier_counts = {CHEAP: 0, NORMAL: 0, EXPENSIVE: 0}
    for _, tier, _ in schedule:
        tier_counts[tier] += 1
    log.info(f"Schedule: {tier_counts[CHEAP]} cheap, {tier_counts[NORMAL]} normal, {tier_counts[EXPENSIVE]} expensive slots")

    # 5. Get current action
    setpoint, evu = get_current_action(schedule, outdoor_temp)

    # 6. Rate limit
    elapsed = time.monotonic() - last_change_time
    if elapsed < MIN_HOLD_MINUTES * 60 and (current_setpoint is not None or current_evu is not None):
        remaining = MIN_HOLD_MINUTES * 60 - elapsed
        if setpoint != current_setpoint or evu != current_evu:
            log.info(f"Rate limited: {remaining:.0f}s remaining before next change")
            # Still log the decision even if rate-limited
            # Find current price for logging
            now_utc = datetime.now(timezone.utc)
            current_price = None
            current_tier = NORMAL
            for ts, tier, price in schedule:
                slot_time = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
                if slot_time <= now_utc:
                    current_price = price
                    current_tier = tier
            log_decision(write_api, current_setpoint or COMFORT_DEFAULT,
                         current_evu or False, current_tier, current_price, outdoor_temp)
            return

    # 7. Apply changes
    effective = setpoint - REDUCTION_T if evu else setpoint
    log.info(f"Action: setpoint={setpoint}°C, EVU={'ON' if evu else 'OFF'}, effective={effective}°C")

    set_setpoint(setpoint)
    set_evu(evu)

    # 8. Log decision
    now_utc = datetime.now(timezone.utc)
    current_price = None
    current_tier = NORMAL
    for ts, tier, price in schedule:
        slot_time = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        if slot_time <= now_utc:
            current_price = price
            current_tier = tier
    log_decision(write_api, setpoint, evu, current_tier, current_price, outdoor_temp)


# ── Main ─────────────────────────────────────────────────────────────────────

def sleep_interruptible(seconds):
    end = time.monotonic() + seconds
    while running and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


def main():
    log.info("=" * 60)
    log.info("Floor Heating Temperature Optimizer")
    log.info("=" * 60)
    log.info(f"MQTT:       {MQTT_BROKER}:{MQTT_PORT}")
    log.info(f"  write:    {MQTT_WRITE_TOPIC}")
    log.info(f"  set:      {MQTT_SET_TOPIC}")
    log.info(f"Setpoints:  min={COMFORT_MIN}, default={COMFORT_DEFAULT}, max={COMFORT_MAX}")
    log.info(f"Reduction:  {REDUCTION_T}°C (effective min = {COMFORT_DEFAULT - REDUCTION_T}°C)")
    log.info(f"Percentiles: cheap ≤ P{PRICE_PERCENTILE_CHEAP:.0f}, expensive ≥ P{PRICE_PERCENTILE_EXPENSIVE:.0f}")
    log.info(f"Pre-heat:   {PRE_HEAT_HOURS}h ({PRE_HEAT_SLOTS} slots)")
    log.info(f"Interval:   {CHECK_INTERVAL}s, hold: {MIN_HOLD_MINUTES}min")
    if DRY_RUN:
        log.info("*** DRY RUN MODE — no MQTT commands will be sent ***")
    log.info("-" * 60)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    influx_client = InfluxDBClient(
        url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG
    )
    try:
        log.info(f"InfluxDB: {influx_client.health().status}")
    except Exception as e:
        log.warning(f"InfluxDB health check: {e}")

    query_api = influx_client.query_api()
    write_api = influx_client.write_api(write_options=SYNCHRONOUS)

    # Configure reduction_t at startup
    set_reduction_t(REDUCTION_T)

    # Run immediately, then every CHECK_INTERVAL
    check_and_control(query_api, write_api)

    while running:
        sleep_interruptible(CHECK_INTERVAL)
        if running:
            check_and_control(query_api, write_api)

    # Restore defaults on shutdown
    log.info("Restoring defaults before exit...")
    set_setpoint(COMFORT_DEFAULT)
    set_evu(False)

    influx_client.close()
    log.info("Shutdown complete")


if __name__ == "__main__":
    main()
