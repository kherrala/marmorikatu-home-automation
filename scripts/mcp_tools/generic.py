"""Generic data access tools: schema, query, latest, statistics, time range."""

import json
from datetime import datetime, timezone

from mcp.types import Tool, TextContent

from .config import INFLUXDB_BUCKET
from .schema import SCHEMA
from .influxdb import execute_flux_query

TOOLS = [
    Tool(
        name="describe_schema",
        description="""Get the complete data schema for the building automation system.

Returns detailed information about all measurements (hvac, rooms, ruuvi, thermia),
their fields, units, descriptions, and available tags.

Use this tool first to understand what data is available before querying.""",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),
    Tool(
        name="list_measurements",
        description="List all available measurements in the database.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),
    Tool(
        name="describe_measurement",
        description="Get detailed information about a specific measurement including fields, tags, and time range.",
        inputSchema={
            "type": "object",
            "properties": {
                "measurement": {
                    "type": "string",
                    "description": "Measurement name: 'hvac', 'rooms', 'ruuvi', or 'thermia'"
                }
            },
            "required": ["measurement"]
        }
    ),
    Tool(
        name="query_data",
        description="""Execute a Flux query against the InfluxDB database.

The database uses:
- Bucket: 'building_automation'
- Measurements: 'hvac', 'rooms', 'ruuvi', 'thermia'

Example query to get last 24h of outdoor temperature:
```flux
from(bucket: "building_automation")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
```

Always use aggregateWindow for time series to reduce data volume.""",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Flux query to execute"
                }
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="get_latest",
        description="""Get the most recent values for specified fields from a measurement.

Examples:
- measurement='hvac', fields=['Ulkolampotila', 'Tuloilma_ennen_lammitysta']
- measurement='ruuvi', fields=['temperature', 'humidity'], sensor_name='Keittiö'
- measurement='rooms', fields=['Keittio', 'Eteinen']
- measurement='thermia', fields=['outdoor_temp', 'supply_temp', 'compressor']""",
        inputSchema={
            "type": "object",
            "properties": {
                "measurement": {
                    "type": "string",
                    "description": "Measurement name: 'hvac', 'rooms', 'ruuvi', or 'thermia'"
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of field names to retrieve"
                },
                "sensor_name": {
                    "type": "string",
                    "description": "For ruuvi: filter by sensor name (e.g., 'Keittiö', 'Ulkolämpötila')"
                }
            },
            "required": ["measurement", "fields"]
        }
    ),
    Tool(
        name="get_statistics",
        description="""Get statistics (min, max, mean, count) for a field over a time range.

Time range examples: '-1h', '-24h', '-7d', '-30d'""",
        inputSchema={
            "type": "object",
            "properties": {
                "measurement": {
                    "type": "string",
                    "description": "Measurement name"
                },
                "field": {
                    "type": "string",
                    "description": "Field name"
                },
                "time_range": {
                    "type": "string",
                    "description": "Time range (e.g., '-24h', '-7d')",
                    "default": "-24h"
                },
                "sensor_name": {
                    "type": "string",
                    "description": "For ruuvi: filter by sensor name"
                }
            },
            "required": ["measurement", "field"]
        }
    ),
    Tool(
        name="get_time_range",
        description="Get the time range of available data for a measurement.",
        inputSchema={
            "type": "object",
            "properties": {
                "measurement": {
                    "type": "string",
                    "description": "Measurement name"
                }
            },
            "required": ["measurement"]
        }
    ),
]


async def handle_describe_schema(arguments):
    return [TextContent(type="text", text=json.dumps(SCHEMA, indent=2, ensure_ascii=False))]


async def handle_list_measurements(arguments):
    measurements = list(SCHEMA["measurements"].keys())
    return [TextContent(type="text", text=json.dumps({
        "measurements": measurements,
        "descriptions": {m: SCHEMA["measurements"][m]["description"] for m in measurements}
    }, indent=2, ensure_ascii=False))]


async def handle_describe_measurement(arguments):
    measurement = arguments.get("measurement")
    if measurement not in SCHEMA["measurements"]:
        return [TextContent(type="text", text=f"Unknown measurement: {measurement}. Available: hvac, rooms, ruuvi")]

    query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> first()
  |> keep(columns: ["_time"])
'''
    try:
        first_result = execute_flux_query(query)
        first_time = first_result[0]["_time"] if first_result else "unknown"
    except:
        first_time = "unknown"

    result = {
        **SCHEMA["measurements"][measurement],
        "first_data": first_time,
        "last_data": datetime.now(timezone.utc).isoformat()
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


async def handle_query_data(arguments):
    query = arguments.get("query")
    try:
        results = execute_flux_query(query)
        return [TextContent(type="text", text=json.dumps({
            "count": len(results),
            "data": results[:100],
            "truncated": len(results) > 100
        }, indent=2, ensure_ascii=False, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=f"Query error: {str(e)}")]


async def handle_get_latest(arguments):
    measurement = arguments.get("measurement")
    fields = arguments.get("fields", [])
    sensor_name = arguments.get("sensor_name")

    field_filter = " or ".join([f'r._field == "{f}"' for f in fields])
    sensor_filter = f' and r.sensor_name == "{sensor_name}"' if sensor_name else ""

    query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "{measurement}"{sensor_filter})
  |> filter(fn: (r) => {field_filter})
  |> last()
'''
    try:
        results = execute_flux_query(query)
        formatted = {}
        for r in results:
            field = r.get("_field", "unknown")
            formatted[field] = {
                "value": r.get("_value"),
                "time": r.get("_time"),
                "unit": SCHEMA["measurements"].get(measurement, {}).get("fields", {}).get(field, {}).get("unit", "")
            }
        return [TextContent(type="text", text=json.dumps(formatted, indent=2, ensure_ascii=False, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def handle_get_statistics(arguments):
    measurement = arguments.get("measurement")
    field = arguments.get("field")
    time_range = arguments.get("time_range", "-24h")
    sensor_name = arguments.get("sensor_name")

    sensor_filter = f' and r.sensor_name == "{sensor_name}"' if sensor_name else ""

    try:
        results = {}
        for stat in ["min", "max", "mean", "count"]:
            q = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "{measurement}"{sensor_filter})
  |> filter(fn: (r) => r._field == "{field}")
  |> {stat}()
'''
            r = execute_flux_query(q)
            results[stat] = r[0].get("_value") if r else None

        unit = SCHEMA["measurements"].get(measurement, {}).get("fields", {}).get(field, {}).get("unit", "")
        results["unit"] = unit
        results["field"] = field
        results["time_range"] = time_range

        return [TextContent(type="text", text=json.dumps(results, indent=2, ensure_ascii=False, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def handle_get_time_range(arguments):
    measurement = arguments.get("measurement")

    try:
        q_first = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> first()
'''
        first_result = execute_flux_query(q_first)

        q_last = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> last()
'''
        last_result = execute_flux_query(q_last)

        return [TextContent(type="text", text=json.dumps({
            "measurement": measurement,
            "first_record": first_result[0]["_time"] if first_result else None,
            "last_record": last_result[0]["_time"] if last_result else None
        }, indent=2, ensure_ascii=False, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


HANDLERS = {
    "describe_schema": handle_describe_schema,
    "list_measurements": handle_list_measurements,
    "describe_measurement": handle_describe_measurement,
    "query_data": handle_query_data,
    "get_latest": handle_get_latest,
    "get_statistics": handle_get_statistics,
    "get_time_range": handle_get_time_range,
}
