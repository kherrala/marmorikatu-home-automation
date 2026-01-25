#!/usr/bin/env python3
"""
Import WAGO CSV data into InfluxDB.

Data Model:
- Measurement: hvac (from logfile_dp_*.csv)
- Measurement: rooms (from Temperatures*.csv)

Supports incremental mode for syncing new data only.
"""

import os
import glob
import argparse
from datetime import datetime, timezone, timedelta
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# InfluxDB connection settings
INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")
DATA_DIR = os.environ.get("DATA_DIR", "./data")


def normalize_header(header: str) -> str:
    """Normalize header by replacing special chars for consistent matching."""
    # Remove BOM if present
    header = header.lstrip('\ufeff')
    # Normalize different degree symbols and encodings
    header = header.replace('º', '°')  # Latin-1 masculine ordinal to degree
    header = header.replace('\xba', '°')  # Raw byte
    header = header.replace('�', '°')  # Replacement char
    return header.strip()


# Room sensor mappings - using normalized headers with °
# Renamed: MH Aatu→Seela, MH Onni→Aarni, MH Essi→aikuiset, MH AK→alakerta
# Format: (room_type, field_name, floor_level)
# Floor levels: 2 = upstairs, 1 = ground floor, 0 = basement
ROOM_SENSOR_MAP = {
    # Bedrooms (Makuuhuone = MH)
    "MH Aatu[C°]": ("bedroom", "MH_Seela", 2),
    "MH Onni[C°]": ("bedroom", "MH_Aarni", 2),
    "MH Essi[C°]": ("bedroom", "MH_aikuiset", 2),
    "MH AK[C°]": ("bedroom", "MH_alakerta", 1),

    # Common areas
    "Yk Aula[C°]": ("common", "Ylakerran_aula", 2),
    "Keittiö[C°]": ("common", "Keittio", 1),
    "Eteinen[C°]": ("common", "Eteinen", 1),

    # Basement
    "Kellari[C°]": ("basement", "Kellari", 0),
    "Kellari Eteinen[C°]": ("basement", "Kellari_eteinen", 0),

    # PID control values
    "MH Aatu PID[%]": ("pid", "MH_Seela_PID", 2),
    "MH Onni PID[%]": ("pid", "MH_Aarni_PID", 2),
    "MH Essi PID[%]": ("pid", "MH_aikuiset_PID", 2),
    "MH AK PID[%]": ("pid", "MH_alakerta_PID", 1),
    "Yk Aula PID[%]": ("pid", "Ylakerran_aula_PID", 2),
    "Keittiö PID[%]": ("pid", "Keittio_PID", 1),
    "Eteinen PID[%]": ("pid", "Eteinen_PID", 1),
    "Kellari PID[%]": ("pid", "Kellari_PID", 0),
    "Kellari Eteinen PID[%]": ("pid", "Kellari_eteinen_PID", 0),

    # Energy (no floor - building-wide)
    "Lisälämmitin vuosienergia[Kwh]": ("energy", "Lisalammitin_vuosienergia", None),
    "Maalämpöpumppu vuosienergia[Kwh]": ("energy", "Maalampopumppu_vuosienergia", None),
}

# HVAC sensor mappings
HVAC_SENSOR_MAP = {
    # Voltages
    "U1[V]": ("voltage", "U1_jannite"),
    "U2[V]": ("voltage", "U2_jannite"),
    "U3[V]": ("voltage", "U3_jannite"),

    # Power
    "P Lämpöpumppu[Kw]": ("power", "Lampopumppu_teho"),
    "P Lampopumppu[Kw]": ("power", "Lampopumppu_teho"),
    "P Lisävastus[kw]": ("power", "Lisavastus_teho"),
    "P Lisavastus[kw]": ("power", "Lisavastus_teho"),

    # Energy
    "E Lämpöpumppu[Kwh]": ("energy", "Lampopumppu_energia"),
    "E Lampopumppu[Kwh]": ("energy", "Lampopumppu_energia"),
    "E Lisävastus[Kwh]": ("energy", "Lisavastus_energia"),
    "E Lisavastus[Kwh]": ("energy", "Lisavastus_energia"),

    # IVK temperatures (with various degree symbol encodings)
    "IVK ulkolämpö[c°]": ("ivk_temp", "Ulkolampotila"),
    "IVK ulkolampo[c°]": ("ivk_temp", "Ulkolampotila"),
    "IVK tulo ennen lämmitystä[c°]": ("ivk_temp", "Tuloilma_ennen_lammitysta"),
    "IVK tulo ennen lammitysta[c°]": ("ivk_temp", "Tuloilma_ennen_lammitysta"),
    "IVK positolämpötila[c°]": ("ivk_temp", "Tuloilma_asetusarvo"),
    "IVK positolampotila[c°]": ("ivk_temp", "Tuloilma_asetusarvo"),
    "IVK tulo jälkeen lämmityksen[c°]": ("ivk_temp", "Tuloilma_jalkeen_lammityksen"),
    "IVK tulo jalkeen lammityksen[c°]": ("ivk_temp", "Tuloilma_jalkeen_lammityksen"),
    "IVK Jäteilma[c°]": ("ivk_temp", "Jateilma"),
    "IVK Jateilma[c°]": ("ivk_temp", "Jateilma"),
    "Tuloilma jäähdytyksen jälkeen[c°]": ("ivk_temp", "Tuloilma_jalkeen_jaahdytyksen"),
    "Tuloilma jaahdytyksen jalkeen[c°]": ("ivk_temp", "Tuloilma_jalkeen_jaahdytyksen"),

    # Humidity
    "RH suht kosteus[%]": ("humidity", "Suhteellinen_kosteus"),
    "RH kastepiste[c°]": ("humidity", "Kastepiste"),
    "RH Lämpötila[c°]": ("ivk_temp", "RH_lampotila"),
    "RH Lampotila[c°]": ("ivk_temp", "RH_lampotila"),
    "TH Lämpötila[c°]": ("humidity", "TH_anturi_lampotila"),
    "TH Lampotila[c°]": ("humidity", "TH_anturi_lampotila"),

    # Actuator
    "Toimilaite SP[c°]": ("actuator", "Toimilaite_asetusarvo"),
    "Toimilaite pakotus[c°]": ("actuator", "Toimilaite_pakotus"),
    "Toimilaite ohjaus[c°]": ("actuator", "Toimilaite_ohjaus"),
}


def parse_timestamp(time_str: str) -> datetime:
    """Parse timestamp from CSV."""
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"]:
        try:
            return datetime.strptime(time_str.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Could not parse timestamp: {time_str}")


def parse_float(value: str) -> float | None:
    """Parse float value."""
    if not value or value.strip() == "":
        return None
    try:
        val = float(value.strip().replace(",", "."))
        if abs(val) > 1e10:  # Filter sensor errors
            return None
        return val
    except ValueError:
        return None


def is_valid_temp(value: float) -> bool:
    """Check if temperature is in valid range."""
    return value is not None and -50 < value < 100


def read_csv_latin1(filepath: str) -> tuple[list[str], list[str]]:
    """Read CSV file with Latin-1 encoding."""
    with open(filepath, 'r', encoding='latin-1') as f:
        content = f.read()

    lines = content.strip().split('\n')
    if len(lines) < 2:
        return [], []

    # Normalize headers
    headers = [normalize_header(h) for h in lines[0].split(',')]
    return headers, lines[1:]


def import_room_temps(write_api, filepath: str, batch_size: int = 5000):
    """Import room temperature CSV."""
    filename = os.path.basename(filepath)

    try:
        headers, data_lines = read_csv_latin1(filepath)
    except Exception as e:
        print(f"  Error reading {filename}: {e}")
        return 0, 1

    if not headers or not data_lines:
        return 0, 0

    # Map headers to sensor info (room_type, field_name, floor)
    header_map = {}
    for i, header in enumerate(headers[1:], 1):  # Skip Time
        if header in ROOM_SENSOR_MAP:
            room_type, field_name, floor = ROOM_SENSOR_MAP[header]
            header_map[i] = (room_type, field_name, floor)

    if not header_map:
        print(f"  Warning: No mapped headers found in {filename}")
        print(f"  Headers: {headers[:5]}...")
        return 0, 0

    points = []
    row_count = 0

    for line in data_lines:
        if not line.strip():
            continue

        try:
            values = line.split(',')
            timestamp = parse_timestamp(values[0])

            # Group by room type and floor
            room_points = {}

            for col_idx, (room_type, field_name, floor) in header_map.items():
                if col_idx < len(values):
                    value = parse_float(values[col_idx])
                    if value is not None and (room_type == "pid" or room_type == "energy" or is_valid_temp(value)):
                        # Create unique key for room_type + floor combination
                        point_key = (room_type, floor)
                        if point_key not in room_points:
                            point = Point("rooms").time(timestamp, WritePrecision.S).tag("room_type", room_type)
                            if floor is not None:
                                point = point.tag("floor", str(floor))
                            room_points[point_key] = point
                        room_points[point_key] = room_points[point_key].field(field_name, value)

            for point in room_points.values():
                points.append(point)
                row_count += 1

            if len(points) >= batch_size:
                write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)
                points = []

        except Exception:
            pass

    if points:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)

    return row_count, 0


def import_hvac(write_api, filepath: str, batch_size: int = 5000):
    """Import HVAC logfile CSV."""
    filename = os.path.basename(filepath)

    try:
        headers, data_lines = read_csv_latin1(filepath)
    except Exception as e:
        print(f"  Error reading {filename}: {e}")
        return 0, 1

    if not headers or not data_lines:
        return 0, 0

    # Map headers
    header_map = {}
    for i, header in enumerate(headers[1:], 1):
        if header in HVAC_SENSOR_MAP:
            header_map[i] = HVAC_SENSOR_MAP[header]
        else:
            # Fallback: create field name from header
            clean = header.split('[')[0].strip().replace(' ', '_').replace('ä', 'a').replace('ö', 'o').replace('å', 'a')
            if clean:
                header_map[i] = ("other", clean)

    points = []
    row_count = 0

    for line in data_lines:
        if not line.strip():
            continue

        try:
            values = line.split(',')
            timestamp = parse_timestamp(values[0])

            sensor_points = {}

            for col_idx, (group, field_name) in header_map.items():
                if col_idx < len(values):
                    value = parse_float(values[col_idx])
                    if value is not None:
                        # Validate based on group
                        valid = True
                        if group == "ivk_temp":
                            valid = is_valid_temp(value)
                        elif group == "humidity" and "kosteus" in field_name.lower():
                            valid = 0 <= value <= 100
                        elif group == "power":
                            valid = 0 <= value < 100

                        if valid:
                            if group not in sensor_points:
                                sensor_points[group] = Point("hvac").time(timestamp, WritePrecision.S).tag("sensor_group", group)
                            sensor_points[group] = sensor_points[group].field(field_name, value)

            for point in sensor_points.values():
                points.append(point)
                row_count += 1

            if len(points) >= batch_size:
                write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)
                points = []

        except Exception:
            pass

    if points:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)

    return row_count, 0


def clear_bucket(client):
    """Clear all data from bucket."""
    delete_api = client.delete_api()
    start = datetime(1970, 1, 1, tzinfo=timezone.utc)
    stop = datetime(2030, 1, 1, tzinfo=timezone.utc)

    for measurement in ["hvac", "rooms", "hvac_system", "room_temperatures"]:
        try:
            delete_api.delete(start, stop, f'_measurement="{measurement}"', bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG)
        except Exception:
            pass
    print("Cleared existing data")


def get_last_sync_time() -> datetime | None:
    """Read last sync timestamp from state file."""
    state_file = os.path.join(DATA_DIR, ".last_sync")
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                return datetime.fromisoformat(f.read().strip().replace('Z', '+00:00'))
        except (ValueError, IOError):
            return None
    return None


def get_modified_files(file_list: list[str], since: datetime | None) -> list[str]:
    """Filter files to only those modified since the given timestamp."""
    if since is None:
        return file_list

    modified = []
    for filepath in file_list:
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
            # Add a small buffer (1 minute) to avoid missing files
            if mtime > since.replace(tzinfo=None) - timedelta(minutes=1):
                modified.append(filepath)
        except OSError:
            # If we can't get mtime, include the file to be safe
            modified.append(filepath)
    return modified


def main():
    parser = argparse.ArgumentParser(description="Import WAGO CSV data into InfluxDB")
    parser.add_argument('--incremental', action='store_true',
                        help='Incremental import: skip bucket clearing, only process new files')
    args = parser.parse_args()

    mode = "incremental" if args.incremental else "full"
    print("=" * 60)
    print(f"WAGO CSV Data Importer (v4 - {mode} mode)")
    print("=" * 60)

    client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)

    try:
        health = client.health()
        print(f"InfluxDB status: {health.status}")
    except Exception as e:
        print(f"Error connecting: {e}")
        return

    # Get last sync time for incremental mode
    last_sync = None
    if args.incremental:
        last_sync = get_last_sync_time()
        if last_sync:
            print(f"\nIncremental mode: processing files modified since {last_sync.isoformat()}")
        else:
            print("\nIncremental mode: no previous sync found, processing all files")
    else:
        print("\nClearing existing data...")
        clear_bucket(client)

    write_api = client.write_api(write_options=SYNCHRONOUS)

    # Get all files
    all_room_files = sorted(glob.glob(os.path.join(DATA_DIR, "Temperatures*.csv")))
    all_hvac_files = sorted(glob.glob(os.path.join(DATA_DIR, "logfile_dp_*.csv")))

    # Filter to modified files in incremental mode
    if args.incremental and last_sync:
        room_files = get_modified_files(all_room_files, last_sync)
        hvac_files = get_modified_files(all_hvac_files, last_sync)
    else:
        room_files = all_room_files
        hvac_files = all_hvac_files

    print(f"\nFound {len(room_files)} room temperature files")
    print(f"Found {len(hvac_files)} HVAC logfiles")
    print("-" * 60)

    total = 0

    print("\n--- Room Temperature Files ---")
    for i, fp in enumerate(room_files, 1):
        fn = os.path.basename(fp)
        print(f"[{i}/{len(room_files)}] {fn}...", end=" ", flush=True)
        rows, _ = import_room_temps(write_api, fp)
        total += rows
        print(f"{rows} points")

    print("\n--- HVAC Logfiles ---")
    for i, fp in enumerate(hvac_files, 1):
        fn = os.path.basename(fp)
        print(f"[{i}/{len(hvac_files)}] {fn}...", end=" ", flush=True)
        rows, _ = import_hvac(write_api, fp)
        total += rows
        print(f"{rows} points")

    print("-" * 60)
    print(f"Total: {total:,} data points")
    client.close()


if __name__ == "__main__":
    main()
