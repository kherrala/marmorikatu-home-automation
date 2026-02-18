#!/usr/bin/env python3
"""
MQTT subscriber for Ruuvi sensor data.

Subscribes to Ruuvi gateway MQTT topics and stores measurements in InfluxDB.
Supports multiple data formats:
- dataFormat 5: Basic Ruuvi tag (temperature, humidity, pressure, acceleration, voltage)
- dataFormat 225: Advanced sensor (PM, CO2, VOC, NOx)
"""

import os
import json
import signal
import sys
import time
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# Configuration from environment
MQTT_BROKER = os.environ.get("MQTT_BROKER", "freenas.kherrala.fi")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "ruuvi/CC:F1:A2:8E:F8:8A/#")

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

# Sensor name mappings (MAC -> friendly name)
# Can be overridden via RUUVI_SENSOR_NAMES env var as JSON
DEFAULT_SENSOR_NAMES = {
    "D1:86:61:6E:DF:E4": "Sauna",
    "D3:1D:6A:1E:7C:4E": "Takka",
    "D7:6C:BC:6D:29:46": "Olohuone",
    "E6:DC:F8:EC:78:3B": "Keittiö",
    "EE:3A:F4:B9:74:E5": "Jääkaappi",
    "EF:AA:DF:C0:4F:8C": "Pakastin",
    "F1:19:ED:0F:9A:F6": "Ulkolämpötila",
}

# Load sensor names from environment or use defaults
try:
    SENSOR_NAMES = json.loads(os.environ.get("RUUVI_SENSOR_NAMES", "{}"))
    if not SENSOR_NAMES:
        SENSOR_NAMES = DEFAULT_SENSOR_NAMES
except json.JSONDecodeError:
    SENSOR_NAMES = DEFAULT_SENSOR_NAMES

# ThermIQ indoor temperature forwarding
THERMIQ_WRITE_TOPIC = os.environ.get("THERMIQ_WRITE_TOPIC", "ThermIQ/marmorikatu/write")
THERMIQ_INDOOR_SENSOR = os.environ.get("THERMIQ_INDOOR_SENSOR", "Olohuone")
THERMIQ_INDOOR_MIN = 19.0
THERMIQ_INDOOR_MAX = 25.0
THERMIQ_WRITE_INTERVAL = 600  # 10 minutes

# Global state
influx_client = None
write_api = None
mqtt_client_ref = None
last_thermiq_write = 0.0


def get_sensor_name(sensor_id: str) -> str:
    """Get friendly name for sensor, or use MAC if not mapped."""
    return SENSOR_NAMES.get(sensor_id, sensor_id)


def process_basic_ruuvi(data: dict, sensor_id: str, sensor_name: str) -> Point:
    """Process dataFormat 5 - basic Ruuvi tag."""
    point = Point("ruuvi") \
        .tag("sensor_id", sensor_id) \
        .tag("sensor_name", sensor_name) \
        .tag("data_format", "5") \
        .tag("sensor_type", "basic")

    # Temperature (always present)
    if data.get("temperature") is not None:
        point = point.field("temperature", float(data["temperature"]))

    # Humidity
    if data.get("humidity") is not None:
        point = point.field("humidity", float(data["humidity"]))

    # Pressure (convert to hPa if in Pa)
    if data.get("pressure") is not None:
        pressure = float(data["pressure"])
        if pressure > 10000:  # Likely in Pa, convert to hPa
            pressure = pressure / 100
        point = point.field("pressure", pressure)

    # Acceleration
    if data.get("accelX") is not None:
        point = point.field("accel_x", float(data["accelX"]))
    if data.get("accelY") is not None:
        point = point.field("accel_y", float(data["accelY"]))
    if data.get("accelZ") is not None:
        point = point.field("accel_z", float(data["accelZ"]))

    # Battery voltage
    if data.get("voltage") is not None:
        point = point.field("voltage", float(data["voltage"]))

    # TX power
    if data.get("txPower") is not None:
        point = point.field("tx_power", int(data["txPower"]))

    # Movement counter
    if data.get("movementCounter") is not None:
        point = point.field("movement_counter", int(data["movementCounter"]))

    # RSSI (signal strength)
    if data.get("rssi") is not None:
        point = point.field("rssi", int(data["rssi"]))

    return point


def process_advanced_ruuvi(data: dict, sensor_id: str, sensor_name: str) -> Point:
    """Process dataFormat 225 - advanced Ruuvi sensor with air quality."""
    point = Point("ruuvi") \
        .tag("sensor_id", sensor_id) \
        .tag("sensor_name", sensor_name) \
        .tag("data_format", "225") \
        .tag("sensor_type", "air_quality")

    # Temperature
    if data.get("temperature") is not None:
        point = point.field("temperature", float(data["temperature"]))

    # Humidity
    if data.get("humidity") is not None:
        point = point.field("humidity", float(data["humidity"]))

    # Pressure
    if data.get("pressure") is not None:
        pressure = float(data["pressure"])
        if pressure > 10000:
            pressure = pressure / 100
        point = point.field("pressure", pressure)

    # Particulate matter (PM)
    if data.get("PM1.0") is not None:
        point = point.field("pm1_0", float(data["PM1.0"]))
    if data.get("PM2.5") is not None:
        point = point.field("pm2_5", float(data["PM2.5"]))
    if data.get("PM4.0") is not None:
        point = point.field("pm4_0", float(data["PM4.0"]))
    if data.get("PM10.0") is not None:
        point = point.field("pm10_0", float(data["PM10.0"]))

    # Air quality
    if data.get("CO2") is not None:
        point = point.field("co2", int(data["CO2"]))
    if data.get("VOC") is not None:
        point = point.field("voc", int(data["VOC"]))
    if data.get("NOx") is not None:
        point = point.field("nox", int(data["NOx"]))

    # Optional sensors
    if data.get("luminosity") is not None:
        point = point.field("luminosity", float(data["luminosity"]))
    if data.get("sound_inst_dba") is not None:
        point = point.field("sound_inst_dba", float(data["sound_inst_dba"]))
    if data.get("sound_avg_dba") is not None:
        point = point.field("sound_avg_dba", float(data["sound_avg_dba"]))

    # RSSI
    if data.get("rssi") is not None:
        point = point.field("rssi", int(data["rssi"]))

    return point


def forward_indoor_temp_to_thermiq(client, temperature):
    """Forward indoor temperature to ThermIQ heat pump via MQTT write."""
    global last_thermiq_write

    now = time.monotonic()
    if now - last_thermiq_write < THERMIQ_WRITE_INTERVAL:
        return

    if temperature < THERMIQ_INDOOR_MIN or temperature > THERMIQ_INDOOR_MAX:
        print(f"ThermIQ: indoor temp {temperature}°C outside bounds "
              f"({THERMIQ_INDOOR_MIN}-{THERMIQ_INDOOR_MAX}°C), skipping")
        return

    value = f"{temperature:.1f}"
    payload = json.dumps({"INDR_T": value})
    client.publish(THERMIQ_WRITE_TOPIC, payload)
    last_thermiq_write = now
    print(f"ThermIQ: set INDR_T to {value}°C")


def on_connect(client, userdata, flags, rc, properties=None):
    """Callback when connected to MQTT broker."""
    if rc == 0:
        print(f"Connected to MQTT broker {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe(MQTT_TOPIC)
        print(f"Subscribed to topic: {MQTT_TOPIC}")
    else:
        print(f"Failed to connect to MQTT broker, return code: {rc}")


def on_disconnect(client, userdata, rc, properties=None, reason_code=None):
    """Callback when disconnected from MQTT broker."""
    print(f"Disconnected from MQTT broker (rc={rc})")


def on_message(client, userdata, msg):
    """Callback when message received from MQTT."""
    global write_api

    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        sensor_id = payload.get("id", "unknown")
        sensor_name = get_sensor_name(sensor_id)
        data_format = payload.get("dataFormat")

        # Get timestamp from message or use current time
        ts = payload.get("ts")
        if ts:
            timestamp = datetime.fromtimestamp(ts, tz=timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        # Process based on data format
        if data_format == 5:
            point = process_basic_ruuvi(payload, sensor_id, sensor_name)
        elif data_format == 225:
            point = process_advanced_ruuvi(payload, sensor_id, sensor_name)
        else:
            # Unknown format, try basic processing
            print(f"Unknown data format {data_format} for sensor {sensor_id}")
            point = process_basic_ruuvi(payload, sensor_id, sensor_name)

        # Set timestamp
        point = point.time(timestamp, WritePrecision.S)

        # Write to InfluxDB
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)

        # Forward indoor temperature to ThermIQ
        if sensor_name == THERMIQ_INDOOR_SENSOR and payload.get("temperature") is not None:
            forward_indoor_temp_to_thermiq(client, float(payload["temperature"]))

    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON: {e}")
    except Exception as e:
        print(f"Error processing message: {e}")


def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    print("\nShutting down...")
    if influx_client:
        influx_client.close()
    sys.exit(0)


def main():
    global influx_client, write_api

    print("=" * 60)
    print("Ruuvi MQTT Subscriber")
    print("=" * 60)
    print(f"MQTT Broker: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"MQTT Topic: {MQTT_TOPIC}")
    print(f"InfluxDB: {INFLUXDB_URL}")
    print(f"Bucket: {INFLUXDB_BUCKET}")
    print(f"Sensor mappings: {len(SENSOR_NAMES)} configured")
    for mac, name in SENSOR_NAMES.items():
        print(f"  {mac} -> {name}")
    print(f"ThermIQ forwarding: {THERMIQ_INDOOR_SENSOR} -> {THERMIQ_WRITE_TOPIC}")
    print(f"  Bounds: {THERMIQ_INDOOR_MIN}-{THERMIQ_INDOOR_MAX}°C, interval: {THERMIQ_WRITE_INTERVAL}s")
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

    # Setup MQTT client
    print("Connecting to MQTT broker...")
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

    # Connect and start loop
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_forever()


if __name__ == "__main__":
    main()
