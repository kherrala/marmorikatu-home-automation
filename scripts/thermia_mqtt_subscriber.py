#!/usr/bin/env python3
"""
MQTT subscriber for ThermIQ-ROOM2 heat pump data.

Subscribes to ThermIQ MQTT topic and stores Thermia heat pump measurements
in InfluxDB. Handles both hex (rXX) and decimal (dDD) register formats,
extracts bitfields, and combines multi-register values.
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
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "ThermIQ/ThermIQ-room2")
READ_INTERVAL = int(os.environ.get("READ_INTERVAL", "30"))

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

# Register map: decimal register index -> field name
# Temperature registers (simple integer °C unless noted)
TEMP_REGISTERS = {
    0: "outdoor_temp",         # r00
    5: "supply_temp",          # r05
    6: "return_temp",          # r06
    7: "hotwater_temp",        # r07
    8: "brine_out_temp",       # r08
    9: "brine_in_temp",        # r09
    10: "cooling_temp",        # r0a
    11: "supply_shunt_temp",   # r0b
    14: "supply_target_temp",  # r0e
    15: "supply_target_shunt_temp",  # r0f
    23: "pressurepipe_temp",   # r17
    24: "hotwater_supply_temp",  # r18
}

# Combined temperature registers (integer + decimal part)
# r01 + r02*0.1 = indoor_temp, r03 + r04*0.1 = indoor_target_temp
COMBINED_TEMP = {
    "indoor_temp": (1, 2),          # (integer_reg, decimal_reg)
    "indoor_target_temp": (3, 4),
}

# Performance registers
PERF_REGISTERS = {
    12: "electrical_current",    # r0c (A)
    30: "flowlinepump_speed",    # r1e (%)
    31: "brinepump_speed",       # r1f (%)
    25: "integral",              # r19 (C*min)
    27: "defrost",               # r1b (*10s)
}

# Runtime registers (hours)
RUNTIME_REGISTERS = {
    104: "runtime_compressor",       # r68
    106: "runtime_3kw",              # r6a
    108: "runtime_hotwater",         # r6c
    110: "runtime_passive_cooling",  # r6e
    112: "runtime_active_cooling",   # r70
    114: "runtime_6kw",              # r72
}

# Setting registers (read/write)
SETTING_REGISTERS = {
    50: "indoor_target_setpoint",  # r32
    51: "mode",                    # r33
    52: "curve",                   # r34
    68: "hotwater_start_temp",     # r44
    84: "hotwater_stop_temp",      # r54
}

# Bitfield definitions: register -> [(bit, field_name), ...]
STATUS_BITFIELDS = {
    13: [  # r0d
        (0, "aux_heater_3kw"),
        (1, "aux_heater_6kw"),
    ],
    16: [  # r10
        (0, "brinepump"),
        (1, "compressor"),
        (2, "flowlinepump"),
        (3, "hotwater_production"),
        (4, "aux_2"),
        (5, "shunt_minus"),
        (6, "shunt_plus"),
        (7, "aux_1"),
    ],
    17: [  # r11
        (4, "active_cooling"),
        (5, "passive_cooling"),
    ],
}

ALARM_BITFIELDS = {
    19: [  # r13
        (0, "alarm_highpr_pressostate"),
        (1, "alarm_lowpr_pressostate"),
        (2, "alarm_motor_breaker"),
        (3, "alarm_low_flow_brine"),
        (4, "alarm_low_temp_brine"),
    ],
    20: [  # r14
        (0, "alarm_outdoor_sensor"),
        (1, "alarm_supply_sensor"),
        (2, "alarm_return_sensor"),
        (3, "alarm_hotwater_sensor"),
        (4, "alarm_indoor_sensor"),
        (5, "alarm_3phase_order"),
        (6, "alarm_overheating"),
    ],
}

# Global InfluxDB client
influx_client = None
write_api = None


def normalize_register_key(key):
    """Convert register key to decimal index.

    Handles both hex (rXX) and decimal (dDD) formats.
    Returns None for non-register keys.
    """
    if key.startswith("r") and len(key) >= 2:
        try:
            return int(key[1:], 16)
        except ValueError:
            return None
    elif key.startswith("d") and len(key) >= 2:
        try:
            return int(key[1:])
        except ValueError:
            return None
    return None


def parse_registers(payload):
    """Parse MQTT payload into a dict of {decimal_index: int_value}."""
    registers = {}
    for key, value in payload.items():
        idx = normalize_register_key(key)
        if idx is not None:
            try:
                registers[idx] = int(value)
            except (ValueError, TypeError):
                pass
    return registers


def extract_bits(value, bit_definitions):
    """Extract individual bits from a bitfield value."""
    fields = {}
    for bit, name in bit_definitions:
        fields[name] = (value >> bit) & 1
    return fields


def build_points(registers, timestamp):
    """Build InfluxDB points from parsed registers."""
    points = []

    # Temperature point
    temp_fields = {}
    for reg_idx, field_name in TEMP_REGISTERS.items():
        if reg_idx in registers:
            temp_fields[field_name] = float(registers[reg_idx])

    # Combined temperatures (integer + 0.1 decimal)
    for field_name, (int_reg, dec_reg) in COMBINED_TEMP.items():
        if int_reg in registers:
            value = float(registers[int_reg])
            if dec_reg in registers:
                value += registers[dec_reg] * 0.1
            temp_fields[field_name] = value

    if temp_fields:
        point = Point("thermia").tag("data_type", "temperature")
        for name, value in temp_fields.items():
            point = point.field(name, value)
        point = point.time(timestamp, WritePrecision.S)
        points.append(point)

    # Status point (bitfields)
    status_fields = {}
    for reg_idx, bit_defs in STATUS_BITFIELDS.items():
        if reg_idx in registers:
            status_fields.update(extract_bits(registers[reg_idx], bit_defs))

    if status_fields:
        point = Point("thermia").tag("data_type", "status")
        for name, value in status_fields.items():
            point = point.field(name, int(value))
        point = point.time(timestamp, WritePrecision.S)
        points.append(point)

    # Alarm point (bitfields)
    alarm_fields = {}
    for reg_idx, bit_defs in ALARM_BITFIELDS.items():
        if reg_idx in registers:
            alarm_fields.update(extract_bits(registers[reg_idx], bit_defs))

    if alarm_fields:
        point = Point("thermia").tag("data_type", "alarm")
        for name, value in alarm_fields.items():
            point = point.field(name, int(value))
        point = point.time(timestamp, WritePrecision.S)
        points.append(point)

    # Performance point
    perf_fields = {}
    for reg_idx, field_name in PERF_REGISTERS.items():
        if reg_idx in registers:
            perf_fields[field_name] = float(registers[reg_idx])

    if perf_fields:
        point = Point("thermia").tag("data_type", "performance")
        for name, value in perf_fields.items():
            point = point.field(name, value)
        point = point.time(timestamp, WritePrecision.S)
        points.append(point)

    # Runtime point
    runtime_fields = {}
    for reg_idx, field_name in RUNTIME_REGISTERS.items():
        if reg_idx in registers:
            runtime_fields[field_name] = float(registers[reg_idx])

    if runtime_fields:
        point = Point("thermia").tag("data_type", "runtime")
        for name, value in runtime_fields.items():
            point = point.field(name, value)
        point = point.time(timestamp, WritePrecision.S)
        points.append(point)

    # Setting point
    setting_fields = {}
    for reg_idx, field_name in SETTING_REGISTERS.items():
        if reg_idx in registers:
            setting_fields[field_name] = float(registers[reg_idx])

    if setting_fields:
        point = Point("thermia").tag("data_type", "setting")
        for name, value in setting_fields.items():
            point = point.field(name, value)
        point = point.time(timestamp, WritePrecision.S)
        points.append(point)

    return points


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
        registers = parse_registers(payload)

        if not registers:
            return

        timestamp = datetime.now(timezone.utc)
        points = build_points(registers, timestamp)

        if points:
            write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)
            print(f"Wrote {len(points)} points ({len(registers)} registers)")

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
    print("ThermIQ-ROOM2 Heat Pump MQTT Subscriber")
    print("=" * 60)
    print(f"MQTT Broker: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"MQTT Topic: {MQTT_TOPIC}")
    print(f"Read interval: {READ_INTERVAL}s")
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

    # Setup MQTT client
    print("Connecting to MQTT broker...")
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

    # Connect and start background network loop
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()

    # Periodically send read command to request register data
    read_topic = f"{MQTT_TOPIC}/read"
    print(f"Will send read command to {read_topic} every {READ_INTERVAL}s")

    try:
        while True:
            mqtt_client.publish(read_topic, "")
            time.sleep(READ_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        if influx_client:
            influx_client.close()


if __name__ == "__main__":
    main()
