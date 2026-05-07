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
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# ── Configuration ─────────────────────────────────────────────────────────────
MQTT_BROKER    = os.environ.get("MQTT_BROKER",    "freenas.kherrala.fi")
MQTT_PORT      = int(os.environ.get("MQTT_PORT",  "1883"))
MQTT_SET_TOPIC = os.environ.get("MQTT_SET_TOPIC", "ThermIQ/marmorikatu/set")

INFLUXDB_URL    = os.environ.get("INFLUXDB_URL",    "http://localhost:8086")
INFLUXDB_TOKEN  = os.environ.get("INFLUXDB_TOKEN",  "wago-secret-token")
INFLUXDB_ORG    = os.environ.get("INFLUXDB_ORG",    "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

# Sensors that contribute to the published indoor temperature. The published
# value is the MEDIAN of the per-sensor means over AVERAGE_MINUTES — robust
# against an outlier room (e.g. one warm room with sun, or a sauna left
# warm) and far more representative than a single sensor. Two lists, comma-
# separated:
#
#   INDOOR_RUUVI_SENSORS — Ruuvi sensor_name values (measurement="ruuvi",
#                          field="temperature"). Names with non-ASCII chars
#                          (e.g. "Keittiö") are fine — the query matches
#                          exactly.
#   INDOOR_ROOM_FIELDS   — WAGO `rooms` measurement field names (any
#                          room_type), e.g. MH_Seela, Ylakerran_aula.
#
# Defaults exclude basement (intentionally cooler) and sauna (transient).
# Backwards compatibility: if neither is set but the legacy single-sensor
# INDOOR_SENSOR env var is, fall back to that one sensor.
# Hard blacklist — sauna readings must never influence the published indoor
# temperature regardless of any env var override (sauna swings 23–80 °C and
# would push the median into nonsense the moment the sauna is on).
_SENSOR_BLACKLIST = {"sauna", "sauna ruuvi"}


def _split_csv_no_blacklist(value: str) -> list[str]:
    return [
        s for s in (s.strip() for s in value.split(","))
        if s and s.lower() not in _SENSOR_BLACKLIST
    ]


INDOOR_RUUVI_SENSORS = _split_csv_no_blacklist(os.environ.get(
    "INDOOR_RUUVI_SENSORS",
    "Olohuone,Keittiö,Takka",
))
INDOOR_ROOM_FIELDS = _split_csv_no_blacklist(os.environ.get(
    "INDOOR_ROOM_FIELDS",
    "MH_Seela,MH_Aarni,MH_aikuiset,MH_alakerta,Ylakerran_aula,Keittio,Eteinen",
))
_legacy_single = os.environ.get("INDOOR_SENSOR")
if _legacy_single and not (os.environ.get("INDOOR_RUUVI_SENSORS")
                           or os.environ.get("INDOOR_ROOM_FIELDS")):
    INDOOR_RUUVI_SENSORS = _split_csv_no_blacklist(_legacy_single)
    INDOOR_ROOM_FIELDS = []
# Averaging window (minutes) — per-sensor mean window, also bounds jumps
AVERAGE_MINUTES = int(os.environ.get("AVERAGE_MINUTES", "15"))

# Tier-aware bias: instead of writing the Thermia setpoint / EVU / reduction
# registers each price-tier transition (which would wear the heat pump's
# internal flash), we bias the published INDR_T so the Thermia naturally
# computes less supply temp during EXPENSIVE and more during CHEAP. INDR_T
# is treated as a sensor input by ThermIQ — no flash write.
TIER_BIAS = {
    "CHEAP":     float(os.environ.get("TIER_BIAS_CHEAP_C",     "-0.5")),
    "PRE_HEAT":  float(os.environ.get("TIER_BIAS_PRE_HEAT_C",  "-0.5")),
    "NORMAL":    float(os.environ.get("TIER_BIAS_NORMAL_C",    "0.0")),
    "EXPENSIVE": float(os.environ.get("TIER_BIAS_EXPENSIVE_C", "2.0")),
}

# Demand-aware counter-bias: when the per-room PID controllers (WAGO) are
# calling for max heat, the rooms genuinely need warming and the
# tier-based suppression should NOT win. We add a negative bias scaled
# by the mean of all per-room PID demand (0–100%). At full demand
# (mean_pid = 100), demand_bias = -DEMAND_BIAS_MAX_C, which cancels the
# EXPENSIVE tier suppression and adds extra push during NORMAL/CHEAP.
#
#   final_bias = tier_bias + demand_bias
#                tier_bias   ∈ {-0.5, 0, +2.0} (per tier)
#                demand_bias = -(mean_pid / 100) * DEMAND_BIAS_MAX_C  ≤ 0
#
# Set DEMAND_BIAS_MAX_C=0 to disable and revert to pure tier-bias.
DEMAND_BIAS_MAX_C = float(os.environ.get("DEMAND_BIAS_MAX_C", "2.0"))
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

def _per_sensor_means(query_api):
    """Return [(label, mean_temp)] for every configured sensor that has data.

    Ruuvi sensors are queried by sensor_name; WAGO room fields by _field. Any
    sensor missing data over AVERAGE_MINUTES is silently skipped.
    """
    out = []
    if INDOOR_RUUVI_SENSORS:
        flux_filter = " or ".join(f'r.sensor_name == "{s}"' for s in INDOOR_RUUVI_SENSORS)
        flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{AVERAGE_MINUTES}m)
  |> filter(fn: (r) => r._measurement == "ruuvi"
       and r._field == "temperature"
       and ({flux_filter}))
  |> group(columns: ["sensor_name"])
  |> mean()
"""
        try:
            for table in query_api.query(flux, org=INFLUXDB_ORG):
                for record in table.records:
                    v = record.get_value()
                    if v is None:
                        continue
                    out.append((f"ruuvi:{record.values.get('sensor_name', '?')}", float(v)))
        except Exception as e:
            log.error(f"Ruuvi query failed: {e}")

    if INDOOR_ROOM_FIELDS:
        flux_filter = " or ".join(f'r._field == "{f}"' for f in INDOOR_ROOM_FIELDS)
        flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{AVERAGE_MINUTES}m)
  |> filter(fn: (r) => r._measurement == "rooms" and ({flux_filter}))
  |> group(columns: ["_field"])
  |> mean()
"""
        try:
            for table in query_api.query(flux, org=INFLUXDB_ORG):
                for record in table.records:
                    v = record.get_value()
                    if v is None:
                        continue
                    out.append((f"rooms:{record.values.get('_field', '?')}", float(v)))
        except Exception as e:
            log.error(f"Rooms query failed: {e}")

    return out


def fetch_median_indoor_temp(query_api):
    """Return (median, [(label, value)]) of available indoor sensors.

    Sensors are queried per-sensor and combined via median in Python (more
    robust against an outlier room than mean). If no sensor reports data,
    returns (None, []).
    """
    samples = _per_sensor_means(query_api)
    if not samples:
        return None, []
    values = sorted(v for _, v in samples)
    n = len(values)
    median = values[n // 2] if n % 2 == 1 else (values[n // 2 - 1] + values[n // 2]) / 2.0
    return median, samples


def fetch_mean_pid_demand(query_api):
    """Mean of all per-room PID demand percentages (room_type=pid) over the
    last AVERAGE_MINUTES, returned as a 0–100 float. None if no data.

    These are produced by the WAGO PLC's per-room PID controllers and
    represent how much heating each underfloor circuit is calling for.
    100% across many rooms = building genuinely under-heated."""
    flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{AVERAGE_MINUTES}m)
  |> filter(fn: (r) => r._measurement == "rooms" and r.room_type == "pid")
  |> mean()
  |> group()
  |> mean()
"""
    try:
        for table in query_api.query(flux, org=INFLUXDB_ORG):
            for record in table.records:
                v = record.get_value()
                if v is not None:
                    return float(v)
    except Exception as e:
        log.warning(f"Could not fetch PID demand: {e}")
    return None


def fetch_current_tier(query_api):
    """Return the latest heating-optimizer tier (CHEAP/NORMAL/EXPENSIVE/PRE_HEAT)
    or 'NORMAL' if not available."""
    flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "heating_optimizer" and r._field == "tier")
  |> last()
"""
    try:
        for table in query_api.query(flux, org=INFLUXDB_ORG):
            for record in table.records:
                v = record.get_value()
                if isinstance(v, str) and v:
                    return v
    except Exception as e:
        log.warning(f"Could not fetch tier: {e}")
    return "NORMAL"


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

def write_telemetry(write_api, *, median_temp, tier, tier_bias,
                    demand_bias, total_bias, biased_temp, mean_pid,
                    sensor_count, last_sent):
    """Persist publisher state so dashboards can chart the actual control
    mechanism (bias, sensor median, sent INDR_T)."""
    p = (
        Point("indoor_publisher")
        .field("sensor_median", float(median_temp))
        .field("sent_indr_t", float(biased_temp))
        .field("tier_bias", float(tier_bias))
        .field("demand_bias", float(demand_bias))
        .field("total_bias", float(total_bias))
        .field("sensor_count", int(sensor_count))
        .field("tier", str(tier))
    )
    if mean_pid is not None:
        p = p.field("mean_pid_demand", float(mean_pid))
    if last_sent is not None:
        p = p.field("last_published", float(last_sent))
    try:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=p)
    except Exception as e:
        log.warning(f"InfluxDB write failed: {e}")


def check_and_publish(query_api, write_api):
    global last_published

    median_temp, samples = fetch_median_indoor_temp(query_api)
    if median_temp is None:
        log.warning(f"No indoor sensor data in the last {AVERAGE_MINUTES} min — skipping")
        return

    tier = fetch_current_tier(query_api)
    tier_bias = TIER_BIAS.get(tier, TIER_BIAS["NORMAL"])

    mean_pid = fetch_mean_pid_demand(query_api)
    if mean_pid is not None and DEMAND_BIAS_MAX_C > 0:
        demand_bias = -(max(0.0, min(100.0, mean_pid)) / 100.0) * DEMAND_BIAS_MAX_C
    else:
        demand_bias = 0.0

    bias = tier_bias + demand_bias
    biased_temp = median_temp + bias

    sample_str = ", ".join(f"{lbl}={v:.1f}" for lbl, v in samples)
    pid_str = f"{mean_pid:.0f}%" if mean_pid is not None else "—"
    log.info(
        f"Indoor median ({AVERAGE_MINUTES} min, n={len(samples)}): "
        f"{median_temp:.2f}°C  tier={tier}  PID={pid_str}  "
        f"bias=tier{tier_bias:+.1f}+demand{demand_bias:+.1f}={bias:+.2f}°C  "
        f"→ INDR_T={biased_temp:.2f}°C  "
        f"(last sent: {f'{last_published:.1f}°C' if last_published is not None else 'none'})"
    )
    log.info(f"  per-sensor: {sample_str}")

    write_telemetry(
        write_api,
        median_temp=median_temp,
        tier=tier,
        tier_bias=tier_bias,
        demand_bias=demand_bias,
        total_bias=bias,
        biased_temp=biased_temp,
        mean_pid=mean_pid,
        sensor_count=len(samples),
        last_sent=last_published,
    )

    if biased_temp < INDOOR_MIN or biased_temp > INDOOR_MAX:
        log.warning(f"Biased temp {biased_temp:.2f}°C outside bounds [{INDOOR_MIN}, {INDOOR_MAX}] — skipping")
        return

    if last_published is not None and abs(biased_temp - last_published) < MIN_CHANGE:
        log.info(f"Change {abs(biased_temp - last_published):.2f}°C < threshold {MIN_CHANGE}°C — no publish needed")
        return

    if publish_indr_t(biased_temp):
        last_published = biased_temp


# ── Main ──────────────────────────────────────────────────────────────────────

def sleep_interruptible(seconds):
    end = time.monotonic() + seconds
    while running and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


def main():
    log.info("=" * 60)
    log.info("Indoor Temperature Publisher")
    log.info("=" * 60)
    log.info(f"Ruuvi:     {INDOOR_RUUVI_SENSORS or '(none)'}")
    log.info(f"Rooms:     {INDOOR_ROOM_FIELDS or '(none)'}")
    log.info(f"Aggregate: median over {AVERAGE_MINUTES} min")
    log.info(f"Tier bias: {TIER_BIAS}")
    log.info(f"Demand bias: max -{DEMAND_BIAS_MAX_C}°C at 100% PID demand")
    log.info(f"MQTT:      {MQTT_BROKER}:{MQTT_PORT}  topic={MQTT_SET_TOPIC}")
    log.info(f"Interval:  {CHECK_INTERVAL}s  min_change={MIN_CHANGE}°C")
    log.info(f"Bounds:    {INDOOR_MIN}–{INDOOR_MAX}°C  (applied to biased value)")
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
    write_api = influx_client.write_api(write_options=SYNCHRONOUS)

    check_and_publish(query_api, write_api)

    while running:
        sleep_interruptible(CHECK_INTERVAL)
        if running:
            check_and_publish(query_api, write_api)

    write_api.close()
    influx_client.close()
    log.info("Shutdown complete")


if __name__ == "__main__":
    main()
