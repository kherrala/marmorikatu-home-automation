"""InfluxDB client and query helper."""

import logging
from influxdb_client import InfluxDBClient
from .config import INFLUXDB_URL, INFLUXDB_TOKEN, INFLUXDB_ORG

log = logging.getLogger("mcp-server")


def get_influx_client():
    return InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)


def execute_flux_query(query: str) -> list[dict]:
    """Execute a Flux query and return results as list of dicts."""
    client = get_influx_client()
    try:
        query_api = client.query_api()
        tables = query_api.query(query, org=INFLUXDB_ORG)

        results = []
        for table in tables:
            for record in table.records:
                row = {}
                for key, value in record.values.items():
                    if key.startswith("_") or key in ("result", "table"):
                        if key in ("_time", "_value", "_field", "_measurement"):
                            row[key] = value.isoformat() if hasattr(value, "isoformat") else value
                        elif key not in ("result", "table", "_start", "_stop"):
                            row[key] = value
                    else:
                        row[key] = value.isoformat() if hasattr(value, "isoformat") else value
                results.append(row)

        return results
    except Exception as e:
        log.error(f"Flux query error: {e}")
        raise
    finally:
        client.close()
