#!/usr/bin/env python3
"""
One-off backfill script for Finnish electricity spot prices.

Downloads the full historical Excel export from porssisahko.net
(https://www.porssisahko.net/api/internal/excel-export) and writes the
data to InfluxDB in the same format as electricity_price_poller.py.

InfluxDB deduplication handles any overlap with data already written
by the live poller — same measurement + tags + timestamp = overwrite.

Usage (run directly on the production server or locally):
  pip install openpyxl influxdb-client requests
  python scripts/backfill_electricity_prices.py

  # Limit to a specific date range (default: everything in the file):
  python scripts/backfill_electricity_prices.py --from 2024-08-01
  python scripts/backfill_electricity_prices.py --from 2024-08-01 --to 2025-02-01
"""

import argparse
import io
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import openpyxl
import requests
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

EXCEL_URL = "https://www.porssisahko.net/api/internal/excel-export"

INFLUXDB_URL    = "http://localhost:8086"
INFLUXDB_TOKEN  = "wago-secret-token"
INFLUXDB_ORG    = "wago"
INFLUXDB_BUCKET = "building_automation"

# Finnish VAT rates on electricity over time
# Finnish electricity VAT was 24% until Aug 31 2024, then raised to 25.5%
FI_VAT_BOUNDARY  = datetime(2024, 9, 1, tzinfo=timezone.utc)
FI_VAT_OLD       = 0.24
FI_VAT_NEW       = 0.255

HELSINKI_TZ = ZoneInfo("Europe/Helsinki")
BATCH_SIZE  = 5000


def download_excel() -> bytes:
    print(f"Downloading {EXCEL_URL} ...")
    resp = requests.get(EXCEL_URL, timeout=120)
    resp.raise_for_status()
    size_mb = len(resp.content) / 1024 / 1024
    print(f"Downloaded {size_mb:.1f} MB")
    return resp.content


def parse_excel(data: bytes, from_date=None, to_date=None):
    """
    Parse the porssisahko.net Excel file.
    Yields (datetime_utc, price_with_tax, price_no_tax) tuples.

    The Excel has two columns:
      - Aika:           timestamp in Europe/Helsinki local time
      - Hinta (snt/kWh): price with VAT in c/kWh
    """
    print("Parsing Excel ...")
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active

    rows = ws.iter_rows(min_row=2, values_only=True)  # skip header row

    skipped_range  = 0
    skipped_errors = 0
    yielded        = 0

    for row in rows:
        aika, hinta = row[0], row[1]

        if aika is None or hinta is None:
            continue

        try:
            # openpyxl returns datetime objects for date cells; strings otherwise
            if isinstance(aika, datetime):
                dt_local = aika.replace(tzinfo=HELSINKI_TZ)
            else:
                dt_local = datetime.fromisoformat(str(aika)).replace(tzinfo=HELSINKI_TZ)

            dt_utc = dt_local.astimezone(timezone.utc)
        except Exception:
            skipped_errors += 1
            continue

        if from_date and dt_utc.date() < from_date:
            skipped_range += 1
            continue
        if to_date and dt_utc.date() > to_date:
            skipped_range += 1
            continue

        try:
            price_with_tax = float(hinta)
        except (TypeError, ValueError):
            skipped_errors += 1
            continue

        vat = FI_VAT_NEW if dt_utc >= FI_VAT_BOUNDARY else FI_VAT_OLD
        price_no_tax = price_with_tax / (1 + vat)

        yielded += 1
        yield dt_utc, price_with_tax, price_no_tax

    wb.close()
    print(f"Parsed: {yielded} rows kept, {skipped_range} outside date range, "
          f"{skipped_errors} parse errors")


def write_to_influxdb(write_api, rows):
    """Write all rows to InfluxDB in batches, returning total point count."""
    total    = 0
    batch    = []
    batches  = 0

    for dt_utc, price_with_tax, price_no_tax in rows:
        point = (
            Point("electricity")
            .tag("source", "porssisahko.net")
            .tag("market", "FI")
            .field("price_with_tax", price_with_tax)
            .field("price_no_tax",   price_no_tax)
            .time(dt_utc, WritePrecision.S)
        )
        batch.append(point)

        if len(batch) >= BATCH_SIZE:
            write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=batch)
            batches += 1
            total   += len(batch)
            print(f"  Written {total} points ...", end="\r")
            batch = []

    if batch:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=batch)
        total += len(batch)

    print(f"  Written {total} points total        ")
    return total


def main():
    parser = argparse.ArgumentParser(description="Backfill electricity prices from porssisahko.net")
    parser.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD",
                        help="Start date (inclusive). Default: all available data")
    parser.add_argument("--to",   dest="to_date",   metavar="YYYY-MM-DD",
                        help="End date (inclusive). Default: all available data")
    parser.add_argument("--influxdb-url",   default=INFLUXDB_URL)
    parser.add_argument("--influxdb-token", default=INFLUXDB_TOKEN)
    parser.add_argument("--influxdb-org",   default=INFLUXDB_ORG)
    parser.add_argument("--influxdb-bucket",default=INFLUXDB_BUCKET)
    args = parser.parse_args()

    from_date = date.fromisoformat(args.from_date) if args.from_date else None
    to_date   = date.fromisoformat(args.to_date)   if args.to_date   else None

    if from_date:
        print(f"Date range: {from_date} → {to_date or 'end of file'}")
    else:
        print("Date range: full file")

    # Download
    data = download_excel()

    # Connect to InfluxDB
    client = InfluxDBClient(
        url=args.influxdb_url, token=args.influxdb_token, org=args.influxdb_org
    )
    try:
        status = client.health().status
        print(f"InfluxDB: {status}")
    except Exception as e:
        print(f"InfluxDB health check failed: {e}", file=sys.stderr)
        sys.exit(1)

    write_api = client.write_api(write_options=SYNCHRONOUS)

    # Parse and write
    rows = parse_excel(data, from_date=from_date, to_date=to_date)
    total = write_to_influxdb(write_api, rows)

    client.close()
    print(f"\nDone. {total} points written to '{args.influxdb_bucket}'.")


if __name__ == "__main__":
    main()
