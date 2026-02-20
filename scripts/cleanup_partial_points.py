#!/usr/bin/env python3
"""
Delete partial ThermIQ data points from InfluxDB.

ThermIQ occasionally sends 126-127 registers instead of 128, causing
InfluxDB points with missing fields. This script finds timestamps where
expected fields are missing and deletes those partial points.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from influxdb_client import InfluxDBClient

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

# Which fields must all be present for each data_type
REQUIRED_FIELDS = {
    "temperature": ["supply_temp", "return_temp", "brine_in_temp", "brine_out_temp"],
    "status": ["compressor", "aux_heater_3kw", "aux_heater_6kw"],
    "performance": ["electrical_current", "flowlinepump_speed", "brinepump_speed"],
}


def find_partial_timestamps(query_api, data_type, fields, time_range="-30d"):
    """Find timestamps where not all required fields are present."""
    fields_filter = " or ".join(f'r._field == "{f}"' for f in fields)
    expected_count = len(fields)

    # Query: for each timestamp, count how many of the required fields exist.
    # Timestamps with fewer than expected are partial.
    flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "{data_type}")
  |> filter(fn: (r) => {fields_filter})
  |> group(columns: ["_time"])
  |> count()
  |> group()
  |> filter(fn: (r) => r._value < {expected_count})
  |> keep(columns: ["_time", "_value"])
  |> sort(columns: ["_time"])
"""
    tables = query_api.query(flux, org=INFLUXDB_ORG)
    partial = []
    for table in tables:
        for record in table.records:
            partial.append((record.get_time(), record.get_value()))
    return partial


def delete_timestamps(delete_api, data_type, timestamps, dry_run=True):
    """Delete specific timestamps for a data_type."""
    deleted = 0
    for ts, field_count in timestamps:
        if dry_run:
            print(f"  [DRY RUN] Would delete data_type={data_type} at {ts} ({field_count} fields)")
        else:
            # Delete a 1-second window around the timestamp
            start = ts - timedelta(milliseconds=500)
            stop = ts + timedelta(milliseconds=500)
            delete_api.delete(
                start=start,
                stop=stop,
                predicate=f'_measurement="thermia" AND data_type="{data_type}"',
                bucket=INFLUXDB_BUCKET,
                org=INFLUXDB_ORG,
            )
            print(f"  Deleted data_type={data_type} at {ts} ({field_count} fields)")
        deleted += 1
    return deleted


def main():
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    time_range = "-30d"

    # Parse optional time range argument
    for arg in sys.argv[1:]:
        if arg.startswith("-") and arg not in ("--dry-run", "-n"):
            time_range = arg

    if dry_run:
        print("=== DRY RUN MODE (pass without --dry-run to actually delete) ===\n")

    print(f"InfluxDB: {INFLUXDB_URL}")
    print(f"Bucket: {INFLUXDB_BUCKET}")
    print(f"Time range: {time_range}")
    print()

    client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    query_api = client.query_api()
    delete_api = client.delete_api()

    total = 0
    for data_type, fields in REQUIRED_FIELDS.items():
        print(f"Checking data_type={data_type} (expecting {len(fields)} fields: {', '.join(fields)})...")
        partial = find_partial_timestamps(query_api, data_type, fields, time_range)

        if not partial:
            print(f"  No partial points found.\n")
            continue

        print(f"  Found {len(partial)} partial points.")
        count = delete_timestamps(delete_api, data_type, partial, dry_run=dry_run)
        total += count
        print()

    print(f"Total: {total} partial points {'would be deleted' if dry_run else 'deleted'}.")
    client.close()


if __name__ == "__main__":
    main()
