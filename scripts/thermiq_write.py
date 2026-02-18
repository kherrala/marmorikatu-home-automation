#!/usr/bin/env python3
"""
CLI tool for reading and writing ThermIQ-ROOM2 heat pump registers via MQTT.

Reads current register values by sending a read command and decoding the
response. Writes settings registers (d50-d103) by publishing JSON messages
to the ThermIQ write topic.

Usage:
    python scripts/thermiq_write.py --read
    python scripts/thermiq_write.py --list
    python scripts/thermiq_write.py indoor_requested_t 22
    python scripts/thermiq_write.py d50 22
    python scripts/thermiq_write.py --dry-run indoor_requested_t 22
"""

import argparse
import json
import os
import sys
import threading
import time

import paho.mqtt.client as mqtt

MQTT_BROKER = os.environ.get("MQTT_BROKER", "freenas.kherrala.fi")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_WRITE_TOPIC = os.environ.get("MQTT_WRITE_TOPIC", "ThermIQ/marmorikatu/write")
MQTT_SET_TOPIC = os.environ.get("MQTT_SET_TOPIC", "ThermIQ/marmorikatu/set")
MQTT_READ_TOPIC = os.environ.get("MQTT_READ_TOPIC", "ThermIQ/ThermIQ-room2/read")
MQTT_DATA_TOPIC = os.environ.get("MQTT_DATA_TOPIC", "ThermIQ/marmorikatu/data")

# Writable settings registers (d50-d103)
# Each entry: (decimal_reg, hex_reg, name, unit, min_val, max_val, description)
REGISTERS = [
    (50, 0x32, "indoor_requested_t", "°C", 10, 30, "Indoor target setpoint"),
    (51, 0x33, "main_mode", "", 0, 3, "Mode (0=Off, 1=Heating, 2=Cooling, 3=Auto)"),
    (52, 0x34, "integral1_curve_slope", "", 0, 100, "Heating curve slope"),
    (53, 0x35, "integral1_curve_min", "°C", 10, 50, "Heating curve minimum"),
    (54, 0x36, "integral1_curve_max", "°C", 20, 65, "Heating curve maximum"),
    (55, 0x37, "integral1_curve_p5", "°C", -10, 10, "Curve adjustment at +5°C outdoor"),
    (56, 0x38, "integral1_curve_0", "°C", -10, 10, "Curve adjustment at 0°C outdoor"),
    (57, 0x39, "integral1_curve_n5", "°C", -10, 10, "Curve adjustment at -5°C outdoor"),
    (58, 0x3a, "heating_stop_t", "°C", 5, 30, "Stop heating above this outdoor temp"),
    (59, 0x3b, "reduction_t", "°C", 0, 10, "Temperature reduction"),
    (60, 0x3c, "room_factor", "", 0, 10, "Room factor"),
    (61, 0x3d, "integral2_curve_slope", "", 0, 100, "Curve 2 slope"),
    (62, 0x3e, "integral2_curve_min", "°C", 10, 50, "Curve 2 minimum"),
    (63, 0x3f, "integral2_curve_max", "°C", 20, 65, "Curve 2 maximum"),
    (64, 0x40, "integral2_curve_target", "°C", 10, 30, "Curve 2 target"),
    (65, 0x41, "integral2_curve_actual", "°C", 0, 50, "Curve 2 actual"),
    (66, 0x42, "outdoor_stop_t", "°C", 5, 30, "Outdoor stop temp"),
    (67, 0x43, "pressure_pipe_limit_t", "°C", 100, 150, "Pressurepipe temp limit"),
    (68, 0x44, "hotwater_start_t", "°C", 30, 55, "Hot water start temp"),
    (69, 0x45, "hotwater_runtime", "min", 10, 120, "Hot water operating time"),
    (70, 0x46, "heatpump_runtime", "min", 5, 60, "Heat pump operating time"),
    (71, 0x47, "legionella_interval", "days", 0, 90, "Legionella interval (0=off)"),
    (72, 0x48, "legionella_stop_t", "°C", 55, 70, "Legionella stop temp"),
    (73, 0x49, "integral_limit_a1", "C×min", 50, 500, "Integral limit A1"),
    (74, 0x4a, "integral_hysteresis_a1", "°C", 1, 20, "Hysteresis A1"),
    (75, 0x4b, "returnline_max_t", "°C", 30, 55, "Return line max temp limit"),
    (76, 0x4c, "start_interval_min", "min", 5, 30, "Minimum start interval"),
    (77, 0x4d, "brine_min_t", "°C", -20, 0, "Brine temp minimum limit"),
    (78, 0x4e, "cooling_target_t", "°C", 15, 25, "Cooling target temp"),
    (79, 0x4f, "integral_limit_a2", "×10 C×min", 10, 200, "Integral limit A2 (value×10)"),
    (80, 0x50, "integral_hysteresis_a2", "°C", 1, 50, "Hysteresis A2"),
    (81, 0x51, "elect_boiler_steps_max", "steps", 0, 3, "Max electric boiler steps"),
    (82, 0x52, "current_consumption_max_a", "A", 10, 40, "Max current limit"),
    (83, 0x53, "shunt_time", "s", 30, 180, "Shunt operating time"),
    (84, 0x54, "hotwater_stop_t", "°C", 40, 65, "Hot water stop temp"),
    (87, 0x57, "language", "", 0, 20, "Display language"),
    (93, 0x5d, "returnline_sensor_offset", "°C", -5, 5, "Return line sensor calibration"),
    (96, 0x60, "brine_out_sensor_offset", "°C", -5, 5, "Brine out sensor calibration"),
    (97, 0x61, "heatingsystem_type", "", 0, 3, "Heating system type"),
    (99, 0x63, "internal_logging_t", "min", 10, 120, "Internal logging interval"),
    (100, 0x64, "brine_runout_t", "×10s", 0, 10, "Brine pump run-out duration"),
    (101, 0x65, "brine_run_in_t", "×10s", 0, 10, "Brine pump run-in duration"),
    (102, 0x66, "legionella_run_on", "", 0, 1, "Legionella peak heating enable"),
    (103, 0x67, "legionella_run_length", "h", 0, 5, "Legionella peak heating duration"),
]

# Build lookup dicts
_BY_NAME = {r[2]: r for r in REGISTERS}
_BY_DREG = {f"d{r[0]}": r for r in REGISTERS}
_SETTINGS_BY_DEC = {r[0]: r for r in REGISTERS}

# Parameters set via the /set topic (not register writes)
# (name, value_type, min_val, max_val, description)
PARAMETERS = [
    ("INDR_T", "float", -40.0, 50.0, "Indoor temperature override (°C)"),
    ("EVU", "int", 0, 1, "EVU block (0=off, 1=on)"),
    ("REGFMT", "int", 0, 1, "Register format (0=hex rXX, 1=decimal dDD)"),
]

_PARAMS_BY_NAME = {p[0].lower(): p for p in PARAMETERS}

# Read-mode register map: all known registers grouped by category
# (decimal_reg, name, unit, description)
READ_REGISTERS = {
    "Temperatures": [
        (0, "outdoor_t", "°C", "Outdoor temp"),
        (5, "supply_t", "°C", "Supply line temp"),
        (6, "return_t", "°C", "Return line temp"),
        (7, "hotwater_t", "°C", "Hot water temp"),
        (8, "brine_out_t", "°C", "Brine out temp"),
        (9, "brine_in_t", "°C", "Brine in temp"),
        (10, "cooling_t", "°C", "Cooling temp"),
        (11, "supply_shunt_t", "°C", "Supply line temp, shunt"),
        (14, "supply_target_t", "°C", "Supply line target temp"),
        (15, "supply_shunt_target_t", "°C", "Supply line target temp, shunt"),
        (23, "pressurepipe_t", "°C", "Pressurepipe temp"),
        (24, "hgw_water_t", "°C", "Hot water supply line temp"),
    ],
    "Performance": [
        (12, "current_consumed_a", "A", "Electrical current"),
        (18, "pwm_out_period", "%", "PWM output"),
        (21, "demand1", "", "DEMAND1 signal"),
        (22, "demand2", "", "DEMAND2 signal (128=neutral)"),
        (25, "integral1", "C×min", "Integral A1"),
        (26, "integral1_a_step", "", "Integral A-limit step"),
        (27, "defrost_time", "×10s", "Defrost duration"),
        (28, "time_to_start_min", "min", "Minimum time to start"),
        (30, "supply_pump_speed", "%", "Supply pump speed"),
        (31, "brine_pump_speed", "%", "Brine pump speed"),
    ],
    "Runtime": [
        (104, "compressor_h", "h", "Compressor runtime"),
        (106, "boiler_3kw_h", "h", "3 kW heater runtime"),
        (108, "hotwater_h", "h", "Hot water production runtime"),
        (110, "passive_cooling_h", "h", "Passive cooling runtime"),
        (112, "active_cooling_h", "h", "Active cooling runtime"),
        (114, "boiler_6kw_h", "h", "6 kW heater runtime"),
    ],
}

# Bitfield registers for read display
READ_BITFIELDS = {
    "Component Status (d16)": (16, [
        (0, "brine_pump"), (1, "compressor"), (2, "supply_pump"),
        (3, "hotwater_production"), (4, "aux_2"), (5, "shunt_minus"),
        (6, "shunt_plus"), (7, "aux_1"),
    ]),
    "Aux Heaters (d13)": (13, [
        (0, "3kw_heater"), (1, "6kw_heater"),
    ]),
    "Alarms Pressure/Flow (d19)": (19, [
        (0, "highpressure"), (1, "lowpressure"), (2, "motor_breaker"),
        (3, "brine_flow"), (4, "brine_temperature"),
    ]),
    "Alarms Sensors (d20)": (20, [
        (0, "outdoor_sensor"), (1, "supply_sensor"), (2, "return_sensor"),
        (3, "hotwater_sensor"), (4, "indoor_sensor"), (5, "phase_order"),
        (6, "overheating"),
    ]),
}

# Combined temperature registers (integer + decimal×0.1)
READ_COMBINED_TEMP = [
    (1, 2, "indoor_t", "°C", "Indoor temp"),
    (3, 4, "indoor_target_t", "°C", "Indoor target temp"),
]


def normalize_register_key(key):
    """Convert register key (rXX hex or dDD decimal) to decimal index."""
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


def read_registers(broker, port, read_topic, data_topic, timeout=15):
    """Send a read command and wait for the register response."""
    result = {"payload": None}
    response_event = threading.Event()
    subscribed_event = threading.Event()

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(data_topic)

    def on_subscribe(client, userdata, mid, rc_list, properties=None):
        subscribed_event.set()

    def on_message(client, userdata, msg):
        try:
            result["payload"] = json.loads(msg.payload.decode("utf-8"))
            response_event.set()
        except json.JSONDecodeError:
            pass

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    try:
        client.connect(broker, port, 10)
        client.loop_start()
        # Wait for subscription to be confirmed
        if not subscribed_event.wait(5):
            print("Error: could not subscribe to data topic", file=sys.stderr)
            return None
        # Send read command
        print(f"Sending read command to {read_topic} ...")
        client.publish(read_topic, "")
        print(f"Waiting for response on {data_topic} (timeout {timeout}s) ...")
        # Wait for response
        if not response_event.wait(timeout):
            print(f"Timeout: no response within {timeout}s", file=sys.stderr)
            return None
        return result["payload"]
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None
    finally:
        client.loop_stop()
        client.disconnect()


def display_read_data(payload):
    """Decode and display all register data from a ThermIQ response."""
    # Parse all register values
    regs = {}
    for key, value in payload.items():
        idx = normalize_register_key(key)
        if idx is not None:
            try:
                regs[idx] = int(value)
            except (ValueError, TypeError):
                pass

    # Non-register metadata
    meta_fields = ["Client_Name", "app_info", "reason", "rssi", "INDR_T", "time"]
    meta = {k: payload[k] for k in meta_fields if k in payload}
    if meta:
        print("Device Info")
        print("-" * 60)
        for k, v in meta.items():
            print(f"  {k:<15} {v}")
        print()

    # Combined temperatures
    print("Temperatures")
    print("-" * 60)
    for int_reg, dec_reg, name, unit, desc in READ_COMBINED_TEMP:
        if int_reg in regs:
            value = float(regs[int_reg])
            if dec_reg in regs:
                value += regs[dec_reg] * 0.1
            print(f"  {name:<28} {value:>7.1f} {unit:<6}  {desc}")
    # Use INDR_T if available (higher precision)
    if "INDR_T" in payload:
        print(f"  {'indoor_t (INDR_T)':<28} {float(payload['INDR_T']):>7.1f} {'°C':<6}  Indoor temp (precise)")

    # Simple temperature registers
    for dec, name, unit, desc in READ_REGISTERS["Temperatures"]:
        if dec in regs:
            print(f"  {name:<28} {regs[dec]:>7} {unit:<6}  {desc}")
    print()

    # Bitfields
    for section, (reg_idx, bit_defs) in READ_BITFIELDS.items():
        if reg_idx not in regs:
            continue
        value = regs[reg_idx]
        active = [(name, (value >> bit) & 1) for bit, name in bit_defs]
        on_names = [n for n, v in active if v]
        print(f"{section}")
        print("-" * 60)
        if on_names:
            print(f"  Active: {', '.join(on_names)}")
        else:
            print(f"  All off")
        print()

    # Performance
    print("Performance")
    print("-" * 60)
    for dec, name, unit, desc in READ_REGISTERS["Performance"]:
        if dec in regs:
            print(f"  {name:<28} {regs[dec]:>7} {unit:<6}  {desc}")
    print()

    # Settings — show current values alongside defined ranges
    print("Settings")
    print("-" * 60)
    for reg_entry in REGISTERS:
        dec, hex_addr, name, unit, min_v, max_v, desc = reg_entry
        if dec in regs:
            val = regs[dec]
            unit_str = unit if unit else ""
            range_str = f"[{min_v}..{max_v}]"
            print(f"  {name:<28} {val:>7} {unit_str:<8} {range_str:<14} {desc}")
    print()

    # Runtime
    print("Runtime")
    print("-" * 60)
    for dec, name, unit, desc in READ_REGISTERS["Runtime"]:
        if dec in regs:
            print(f"  {name:<28} {regs[dec]:>7} {unit:<6}  {desc}")
    print()


def list_registers():
    """Print all writable registers and parameters."""
    print(f"{'Reg':<6} {'Hex':<5} {'Name':<30} {'Range':<15} {'Description'}")
    print("-" * 90)
    for dec, hex_addr, name, unit, min_v, max_v, desc in REGISTERS:
        unit_str = f" {unit}" if unit else ""
        range_str = f"{min_v}..{max_v}{unit_str}"
        print(f"d{dec:<5} r{hex_addr:02x}   {name:<30} {range_str:<15} {desc}")
    print()
    print(f"{'Name':<30} {'Type':<8} {'Range':<15} {'Description'} (via /set topic)")
    print("-" * 90)
    for name, vtype, min_v, max_v, desc in PARAMETERS:
        range_str = f"{min_v}..{max_v}"
        print(f"{name:<30} {vtype:<8} {range_str:<15} {desc}")


def resolve_register(identifier):
    """Resolve a register name or dXX number to its register tuple."""
    lower = identifier.lower()
    if lower in _BY_DREG:
        return _BY_DREG[lower]
    if lower in _BY_NAME:
        return _BY_NAME[lower]
    return None


def publish_write(broker, port, topic, hex_addr, value, dry_run=False):
    """Publish a register write command via MQTT."""
    payload = {f"r{hex_addr:02x}": value}
    message = json.dumps(payload)

    if dry_run:
        print(f"[DRY RUN] Would publish to {topic}:")
        print(f"  {message}")
        return True

    print(f"Publishing to {topic}:")
    print(f"  {message}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.connect(broker, port, 10)
        result = client.publish(topic, message)
        result.wait_for_publish(timeout=5)
        print("Published successfully.")
        return True
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False
    finally:
        client.disconnect()


def publish_set(broker, port, topic, param_name, value, dry_run=False):
    """Publish a parameter set command via MQTT."""
    payload = {param_name: value}
    message = json.dumps(payload)

    if dry_run:
        print(f"[DRY RUN] Would publish to {topic}:")
        print(f"  {message}")
        return True

    print(f"Publishing to {topic}:")
    print(f"  {message}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.connect(broker, port, 10)
        result = client.publish(topic, message)
        result.wait_for_publish(timeout=5)
        print("Published successfully.")
        return True
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False
    finally:
        client.disconnect()


def main():
    parser = argparse.ArgumentParser(
        description="Read and write ThermIQ-ROOM2 heat pump settings via MQTT.",
        epilog="Examples:\n"
               "  %(prog)s --read\n"
               "  %(prog)s --read --timeout 30\n"
               "  %(prog)s --list\n"
               "  %(prog)s indoor_requested_t 22\n"
               "  %(prog)s d50 22\n"
               "  %(prog)s --dry-run hotwater_stop_t 55\n"
               "  %(prog)s INDR_T 20.5\n"
               "  %(prog)s EVU 0\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true", help="List all writable registers")
    parser.add_argument("--read", action="store_true", help="Read and display current register values")
    parser.add_argument("--timeout", type=int, default=15, help="Timeout in seconds for --read (default: 15)")
    parser.add_argument("--broker", default=MQTT_BROKER, help=f"MQTT broker (default: {MQTT_BROKER})")
    parser.add_argument("--port", type=int, default=MQTT_PORT, help=f"MQTT port (default: {MQTT_PORT})")
    parser.add_argument("--topic", default=MQTT_WRITE_TOPIC, help=f"MQTT register write topic (default: {MQTT_WRITE_TOPIC})")
    parser.add_argument("--set-topic", default=MQTT_SET_TOPIC, help=f"MQTT parameter set topic (default: {MQTT_SET_TOPIC})")
    parser.add_argument("--read-topic", default=MQTT_READ_TOPIC, help=f"MQTT read command topic (default: {MQTT_READ_TOPIC})")
    parser.add_argument("--data-topic", default=MQTT_DATA_TOPIC, help=f"MQTT data response topic (default: {MQTT_DATA_TOPIC})")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent without publishing")
    parser.add_argument("--confirm", action="store_true", help="Skip interactive confirmation")
    parser.add_argument("register", nargs="?", help="Register name/number (e.g. indoor_requested_t, d50) or parameter (INDR_T, EVU, REGFMT)")
    parser.add_argument("value", nargs="?", help="Value to write")

    args = parser.parse_args()

    if args.list:
        list_registers()
        return

    if args.read:
        payload = read_registers(args.broker, args.port, args.read_topic, args.data_topic, args.timeout)
        if payload is None:
            sys.exit(1)
        display_read_data(payload)
        return

    if not args.register or args.value is None:
        parser.error("register/parameter and value are required (use --list to see options)")

    # Check if it's a parameter (set topic)
    param = _PARAMS_BY_NAME.get(args.register.lower())
    if param:
        pname, vtype, min_v, max_v, desc = param
        try:
            parsed = float(args.value) if vtype == "float" else int(args.value)
        except ValueError:
            print(f"Error: Invalid {vtype} value '{args.value}'", file=sys.stderr)
            sys.exit(1)
        if parsed < min_v or parsed > max_v:
            print(f"Error: Value {parsed} out of range for {pname} ({min_v}..{max_v})", file=sys.stderr)
            sys.exit(1)

        print(f"Parameter: {pname}")
        print(f"Value:     {parsed}")
        print(f"Topic:     {args.set_topic}")
        print(f"Description: {desc}")
        print()

        if not args.dry_run and not args.confirm:
            try:
                answer = input("Confirm write? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                sys.exit(1)
            if answer != "y":
                print("Aborted.")
                sys.exit(1)

        success = publish_set(args.broker, args.port, args.set_topic, pname, parsed, dry_run=args.dry_run)
        sys.exit(0 if success else 1)

    # Otherwise resolve as a register (write topic)
    reg = resolve_register(args.register)
    if reg is None:
        print(f"Error: Unknown register or parameter '{args.register}'", file=sys.stderr)
        print("Use --list to see available options.", file=sys.stderr)
        sys.exit(1)

    dec, hex_addr, name, unit, min_v, max_v, desc = reg

    try:
        int_value = int(args.value)
    except ValueError:
        print(f"Error: Register value must be an integer, got '{args.value}'", file=sys.stderr)
        sys.exit(1)

    if int_value < min_v or int_value > max_v:
        print(f"Error: Value {int_value} out of range for {name} ({min_v}..{max_v})", file=sys.stderr)
        sys.exit(1)

    unit_str = f" {unit}" if unit else ""
    print(f"Register: d{dec} (r{hex_addr:02x}) — {name}")
    print(f"Value:    {int_value}{unit_str}")
    print(f"Description: {desc}")
    print()

    if not args.dry_run and not args.confirm:
        try:
            answer = input("Confirm write? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)
        if answer != "y":
            print("Aborted.")
            sys.exit(1)

    success = publish_write(args.broker, args.port, args.topic, hex_addr, int_value, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
