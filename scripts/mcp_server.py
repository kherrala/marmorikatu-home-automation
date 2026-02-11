#!/usr/bin/env python3
"""
MCP Server for Building Automation Data Analytics.

Provides tools for Claude Desktop to query and analyze measurement data
from InfluxDB (HVAC, room temperatures, Ruuvi sensors).

Runs as an SSE (Server-Sent Events) server for URL-based MCP integration.
"""

import os
import json
from datetime import datetime, timezone
from typing import Any
import asyncio
import uvicorn

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent, Resource
from influxdb_client import InfluxDBClient
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse

# Configuration
INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

# Schema documentation
SCHEMA = {
    "measurements": {
        "hvac": {
            "description": "HVAC system data from WAGO controller (logged every 2 hours)",
            "fields": {
                "Ulkolampotila": {"unit": "°C", "description": "Outdoor temperature"},
                "Tuloilma_ennen_lammitysta": {"unit": "°C", "description": "Supply air after heat recovery, before heating coil"},
                "Tuloilma_jalkeen_lammityksen": {"unit": "°C", "description": "Supply air after heating coil"},
                "Tuloilma_jalkeen_jaahdytyksen": {"unit": "°C", "description": "Supply air after cooling (summer)"},
                "Tuloilma_asetusarvo": {"unit": "°C", "description": "Supply air setpoint (target temperature)"},
                "Jateilma": {"unit": "°C", "description": "Exhaust air after heat recovery unit"},
                "Suhteellinen_kosteus": {"unit": "%", "description": "Relative humidity (exhaust side)"},
                "Kastepiste": {"unit": "°C", "description": "Dew point temperature"},
                "RH_lampotila": {"unit": "°C", "description": "RH sensor temperature"},
                "Lampopumppu_teho": {"unit": "kW", "description": "Heat pump power consumption"},
                "Lisavastus_teho": {"unit": "kW", "description": "Auxiliary heater power consumption"},
                "Lampopumppu_energia": {"unit": "kWh", "description": "Heat pump cumulative energy"},
                "Lisavastus_energia": {"unit": "kWh", "description": "Auxiliary heater cumulative energy"},
                "U1_jannite": {"unit": "V", "description": "Phase 1 voltage"},
                "U2_jannite": {"unit": "V", "description": "Phase 2 voltage"},
                "U3_jannite": {"unit": "V", "description": "Phase 3 voltage"},
                "Toimilaite_ohjaus": {"unit": "%", "description": "Heating valve actuator position"},
                "Toimilaite_asetusarvo": {"unit": "%", "description": "Heating valve setpoint"},
                "Toimilaite_pakotus": {"unit": "-", "description": "Heating valve override status"},
            },
            "tags": {
                "sensor_group": ["ivk_temp", "humidity", "power", "energy", "voltage", "actuator"]
            }
        },
        "rooms": {
            "description": "Room temperature data from WAGO controller (logged hourly)",
            "fields": {
                "MH_Seela": {"unit": "°C", "description": "Bedroom - Seela"},
                "MH_Aarni": {"unit": "°C", "description": "Bedroom - Aarni"},
                "MH_aikuiset": {"unit": "°C", "description": "Bedroom - Adults"},
                "MH_alakerta": {"unit": "°C", "description": "Bedroom - Downstairs guest room"},
                "Ylakerran_aula": {"unit": "°C", "description": "Upstairs hallway"},
                "Keittio": {"unit": "°C", "description": "Kitchen"},
                "Eteinen": {"unit": "°C", "description": "Entrance hall"},
                "Kellari": {"unit": "°C", "description": "Basement main area"},
                "Kellari_eteinen": {"unit": "°C", "description": "Basement entrance"},
                "MH_Seela_PID": {"unit": "%", "description": "Seela room heating demand (0-100%)"},
                "MH_Aarni_PID": {"unit": "%", "description": "Aarni room heating demand (0-100%)"},
                "MH_aikuiset_PID": {"unit": "%", "description": "Adults room heating demand (0-100%)"},
                "MH_alakerta_PID": {"unit": "%", "description": "Downstairs room heating demand (0-100%)"},
                "Ylakerran_aula_PID": {"unit": "%", "description": "Upstairs hallway heating demand (0-100%)"},
                "Keittio_PID": {"unit": "%", "description": "Kitchen heating demand (0-100%)"},
                "Eteinen_PID": {"unit": "%", "description": "Entrance heating demand (0-100%)"},
                "Kellari_PID": {"unit": "%", "description": "Basement heating demand (0-100%)"},
                "Kellari_eteinen_PID": {"unit": "%", "description": "Basement entrance heating demand (0-100%)"},
            },
            "tags": {
                "room_type": ["bedroom", "common", "basement", "pid", "energy"]
            }
        },
        "ruuvi": {
            "description": "Ruuvi Bluetooth sensor data via MQTT (near real-time)",
            "fields": {
                "temperature": {"unit": "°C", "description": "Temperature"},
                "humidity": {"unit": "%", "description": "Relative humidity"},
                "pressure": {"unit": "hPa", "description": "Atmospheric pressure"},
                "voltage": {"unit": "V", "description": "Battery voltage"},
                "rssi": {"unit": "dBm", "description": "Bluetooth signal strength"},
                "accel_x": {"unit": "g", "description": "X-axis acceleration"},
                "accel_y": {"unit": "g", "description": "Y-axis acceleration"},
                "accel_z": {"unit": "g", "description": "Z-axis acceleration"},
                "movement_counter": {"unit": "-", "description": "Movement detection counter"},
                "co2": {"unit": "ppm", "description": "CO2 concentration (air quality sensors only)"},
                "pm1_0": {"unit": "µg/m³", "description": "PM1.0 particulate matter"},
                "pm2_5": {"unit": "µg/m³", "description": "PM2.5 particulate matter"},
                "pm4_0": {"unit": "µg/m³", "description": "PM4.0 particulate matter"},
                "pm10_0": {"unit": "µg/m³", "description": "PM10 particulate matter"},
                "voc": {"unit": "index", "description": "VOC index (1-500)"},
                "nox": {"unit": "index", "description": "NOx index (1-500)"},
            },
            "tags": {
                "sensor_name": ["Sauna", "Takka", "Olohuone", "Keittiö", "Jääkaappi", "Pakastin", "Ulkolämpötila"],
                "sensor_type": ["basic", "air_quality"],
                "data_format": ["5", "225"]
            },
            "sensors": {
                "Sauna": {"type": "basic", "location": "Sauna room"},
                "Takka": {"type": "basic", "location": "Fireplace area"},
                "Olohuone": {"type": "basic", "location": "Living room"},
                "Keittiö": {"type": "air_quality", "location": "Kitchen", "features": ["CO2", "PM", "VOC", "NOx"]},
                "Jääkaappi": {"type": "basic", "location": "Inside refrigerator"},
                "Pakastin": {"type": "basic", "location": "Inside freezer"},
                "Ulkolämpötila": {"type": "basic", "location": "Outdoor"},
            }
        },
        "thermia": {
            "description": "Thermia ground-source heat pump data via ThermIQ-ROOM2 MQTT (near real-time)",
            "fields": {
                "outdoor_temp": {"unit": "°C", "description": "Outdoor temperature (heat pump sensor)"},
                "indoor_temp": {"unit": "°C", "description": "Indoor temperature (combined integer + decimal)"},
                "indoor_target_temp": {"unit": "°C", "description": "Indoor target temperature"},
                "supply_temp": {"unit": "°C", "description": "Supply line temperature"},
                "return_temp": {"unit": "°C", "description": "Return line temperature"},
                "hotwater_temp": {"unit": "°C", "description": "Hot water tank temperature"},
                "brine_out_temp": {"unit": "°C", "description": "Brine circuit outgoing temperature"},
                "brine_in_temp": {"unit": "°C", "description": "Brine circuit incoming temperature"},
                "cooling_temp": {"unit": "°C", "description": "Cooling circuit temperature"},
                "supply_shunt_temp": {"unit": "°C", "description": "Supply line temperature after shunt valve"},
                "pressurepipe_temp": {"unit": "°C", "description": "Pressure pipe temperature"},
                "hotwater_supply_temp": {"unit": "°C", "description": "Hot water supply line temperature"},
                "supply_target_temp": {"unit": "°C", "description": "Supply line target temperature"},
                "supply_target_shunt_temp": {"unit": "°C", "description": "Supply line target temperature for shunt"},
                "compressor": {"unit": "bool", "description": "Compressor on/off"},
                "brinepump": {"unit": "bool", "description": "Brine pump on/off"},
                "flowlinepump": {"unit": "bool", "description": "Flow line pump on/off"},
                "hotwater_production": {"unit": "bool", "description": "Hot water production active"},
                "aux_heater_3kw": {"unit": "bool", "description": "3 kW auxiliary heater on/off"},
                "aux_heater_6kw": {"unit": "bool", "description": "6 kW auxiliary heater on/off"},
                "electrical_current": {"unit": "A", "description": "Electrical current draw"},
                "flowlinepump_speed": {"unit": "%", "description": "Flow line pump speed"},
                "brinepump_speed": {"unit": "%", "description": "Brine pump speed"},
                "integral": {"unit": "°C*min", "description": "Heating integral value"},
                "defrost": {"unit": "*10s", "description": "Defrost timer"},
                "runtime_compressor": {"unit": "h", "description": "Compressor total runtime"},
                "runtime_3kw": {"unit": "h", "description": "3 kW heater total runtime"},
                "runtime_6kw": {"unit": "h", "description": "6 kW heater total runtime"},
                "runtime_hotwater": {"unit": "h", "description": "Hot water production total runtime"},
                "runtime_passive_cooling": {"unit": "h", "description": "Passive cooling total runtime"},
                "runtime_active_cooling": {"unit": "h", "description": "Active cooling total runtime"},
                "indoor_target_setpoint": {"unit": "°C", "description": "Indoor target setpoint (writable)"},
                "mode": {"unit": "#", "description": "Operating mode"},
                "curve": {"unit": "-", "description": "Heating curve setting"},
                "hotwater_start_temp": {"unit": "°C", "description": "Hot water heating start temperature"},
                "hotwater_stop_temp": {"unit": "°C", "description": "Hot water heating stop temperature"},
            },
            "tags": {
                "data_type": ["temperature", "status", "alarm", "performance", "runtime", "setting"]
            }
        }
    },
    "calculations": {
        "heat_recovery_efficiency": {
            "formula": "η = (T_supply - T_outdoor) / (T_setpoint - T_outdoor) × 100%",
            "description": "Heat recovery unit efficiency based on sensible heat",
            "fields_used": ["Tuloilma_ennen_lammitysta", "Ulkolampotila", "Tuloilma_asetusarvo"],
            "typical_range": "50-80%"
        },
        "recovered_heat_power": {
            "formula": "Q = 0.1387 kW/K × (T_supply - T_outdoor)",
            "description": "Heat power recovered by HRU (based on 414 m³/h airflow)",
            "fields_used": ["Tuloilma_ennen_lammitysta", "Ulkolampotila"],
            "typical_range": "0-4 kW"
        },
        "freezing_probability": {
            "formula": "Weighted composite: 50% temp risk (-5 to -25°C) + 35% humidity risk (15-30%) + 15% exhaust temp risk (0-5°C)",
            "description": "Heat exchanger freezing probability",
            "fields_used": ["Ulkolampotila", "Suhteellinen_kosteus", "Jateilma"],
            "typical_range": "0-95%"
        }
    }
}


def get_influx_client():
    """Create InfluxDB client."""
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
                row = {"_time": record.get_time().isoformat() if record.get_time() else None}
                for key, value in record.values.items():
                    if not key.startswith("_") or key in ["_value", "_field", "_measurement"]:
                        row[key] = value
                results.append(row)
        return results
    finally:
        client.close()


# Create MCP server
app = Server("building-automation-analytics")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
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
        Tool(
            name="get_heat_recovery_efficiency",
            description="""Calculate heat recovery unit (LTO) efficiency over a time range.

Returns efficiency values based on the formula:
η = (T_supply_after_HRU - T_outdoor) / (T_setpoint - T_outdoor) × 100%

Typical good efficiency is 50-80%.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_range": {
                        "type": "string",
                        "description": "Time range (e.g., '-24h', '-7d')",
                        "default": "-24h"
                    },
                    "aggregation": {
                        "type": "string",
                        "description": "Aggregation window (e.g., '1h', '2h', '1d')",
                        "default": "2h"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_energy_consumption",
            description="""Get energy consumption summary for heat pump and auxiliary heater.

Returns cumulative energy readings and consumption over the time range.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_range": {
                        "type": "string",
                        "description": "Time range (e.g., '-24h', '-7d', '-30d')",
                        "default": "-7d"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_room_temperatures",
            description="""Get current room temperatures and heating demand (PID values).

Returns temperature and PID% for all monitored rooms.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_pid": {
                        "type": "boolean",
                        "description": "Include PID heating demand values",
                        "default": True
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_air_quality",
            description="""Get air quality data from the Keittiö (kitchen) Ruuvi sensor.

Returns CO2, PM2.5, VOC, NOx levels with health guideline thresholds.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_range": {
                        "type": "string",
                        "description": "Time range",
                        "default": "-24h"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_freezing_probability",
            description="""Calculate heat exchanger (LTO) freezing probability.

Returns a composite risk score (0-95%) based on:
- Outdoor temperature (50% weight): risk increases from -5°C to -25°C
- Exhaust humidity (35% weight): risk increases from 15% to 30% RH
- Exhaust air temperature (15% weight): risk increases from 5°C to 0°C

If exhaust air is below 0°C, probability is forced to 95%.
Uses latest sensor values from the HVAC measurement.""",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="compare_indoor_outdoor",
            description="""Compare indoor and outdoor temperatures.

Shows the temperature difference and trends for indoor comfort analysis.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_range": {
                        "type": "string",
                        "description": "Time range",
                        "default": "-24h"
                    },
                    "indoor_source": {
                        "type": "string",
                        "description": "Indoor temp source: 'rooms' (Keittio) or 'ruuvi' (Olohuone)",
                        "default": "ruuvi"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_thermia_status",
            description="""Get current Thermia ground-source heat pump status.

Returns all temperatures, component on/off states, active alarms, pump speeds,
and runtime counters from the ThermIQ-ROOM2 module.""",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_thermia_temperatures",
            description="""Get Thermia heat pump temperature time series.

Returns temperature trends for supply, return, brine, hot water, outdoor,
and indoor temperatures over the specified time range.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_range": {
                        "type": "string",
                        "description": "Time range (e.g., '-24h', '-7d')",
                        "default": "-24h"
                    },
                    "aggregation": {
                        "type": "string",
                        "description": "Aggregation window (e.g., '5m', '1h')",
                        "default": "5m"
                    }
                },
                "required": []
            }
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""

    if name == "describe_schema":
        return [TextContent(type="text", text=json.dumps(SCHEMA, indent=2, ensure_ascii=False))]

    elif name == "list_measurements":
        measurements = list(SCHEMA["measurements"].keys())
        return [TextContent(type="text", text=json.dumps({
            "measurements": measurements,
            "descriptions": {m: SCHEMA["measurements"][m]["description"] for m in measurements}
        }, indent=2, ensure_ascii=False))]

    elif name == "describe_measurement":
        measurement = arguments.get("measurement")
        if measurement not in SCHEMA["measurements"]:
            return [TextContent(type="text", text=f"Unknown measurement: {measurement}. Available: hvac, rooms, ruuvi")]

        # Get time range from database
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

    elif name == "query_data":
        query = arguments.get("query")
        try:
            results = execute_flux_query(query)
            return [TextContent(type="text", text=json.dumps({
                "count": len(results),
                "data": results[:100],  # Limit to 100 rows
                "truncated": len(results) > 100
            }, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Query error: {str(e)}")]

    elif name == "get_latest":
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
            # Format results nicely
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

    elif name == "get_statistics":
        measurement = arguments.get("measurement")
        field = arguments.get("field")
        time_range = arguments.get("time_range", "-24h")
        sensor_name = arguments.get("sensor_name")

        sensor_filter = f' and r.sensor_name == "{sensor_name}"' if sensor_name else ""

        query = f'''
data = from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "{measurement}"{sensor_filter})
  |> filter(fn: (r) => r._field == "{field}")

min_val = data |> min() |> findRecord(fn: (key) => true, idx: 0)
max_val = data |> max() |> findRecord(fn: (key) => true, idx: 0)
mean_val = data |> mean() |> findRecord(fn: (key) => true, idx: 0)
count_val = data |> count() |> findRecord(fn: (key) => true, idx: 0)

array.from(rows: [{{
  min: min_val._value,
  max: max_val._value,
  mean: mean_val._value,
  count: count_val._value
}}])
'''
        # Simpler approach - multiple queries
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

    elif name == "get_time_range":
        measurement = arguments.get("measurement")

        try:
            # Get first record
            q_first = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> first()
'''
            first_result = execute_flux_query(q_first)

            # Get last record
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

    elif name == "get_heat_recovery_efficiency":
        time_range = arguments.get("time_range", "-24h")
        aggregation = arguments.get("aggregation", "2h")

        query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila" or r._field == "Tuloilma_ennen_lammitysta" or r._field == "Tuloilma_asetusarvo")
  |> aggregateWindow(every: {aggregation}, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.Ulkolampotila and exists r.Tuloilma_ennen_lammitysta and exists r.Tuloilma_asetusarvo)
  |> filter(fn: (r) => r.Tuloilma_asetusarvo != r.Ulkolampotila)
  |> map(fn: (r) => ({{
    _time: r._time,
    outdoor_temp: r.Ulkolampotila,
    supply_after_hru: r.Tuloilma_ennen_lammitysta,
    setpoint: r.Tuloilma_asetusarvo,
    efficiency: (r.Tuloilma_ennen_lammitysta - r.Ulkolampotila) / (r.Tuloilma_asetusarvo - r.Ulkolampotila) * 100.0,
    recovered_power_kw: 0.1387 * (r.Tuloilma_ennen_lammitysta - r.Ulkolampotila)
  }}))
  |> filter(fn: (r) => r.efficiency > 0.0 and r.efficiency <= 100.0)
'''
        try:
            results = execute_flux_query(query)

            # Calculate summary statistics
            if results:
                efficiencies = [r["efficiency"] for r in results if r.get("efficiency")]
                powers = [r["recovered_power_kw"] for r in results if r.get("recovered_power_kw")]

                summary = {
                    "time_range": time_range,
                    "data_points": len(results),
                    "efficiency": {
                        "mean": sum(efficiencies) / len(efficiencies) if efficiencies else None,
                        "min": min(efficiencies) if efficiencies else None,
                        "max": max(efficiencies) if efficiencies else None,
                        "unit": "%"
                    },
                    "recovered_power": {
                        "mean": sum(powers) / len(powers) if powers else None,
                        "min": min(powers) if powers else None,
                        "max": max(powers) if powers else None,
                        "unit": "kW"
                    },
                    "recent_values": results[-5:] if len(results) > 5 else results
                }
            else:
                summary = {"error": "No data available for the specified time range"}

            return [TextContent(type="text", text=json.dumps(summary, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_freezing_probability":
        query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila" or r._field == "Suhteellinen_kosteus" or r._field == "Jateilma")
  |> last()
'''
        try:
            results = execute_flux_query(query)
            values = {r["_field"]: r["_value"] for r in results}

            outdoor = values.get("Ulkolampotila")
            humidity = values.get("Suhteellinen_kosteus")
            exhaust = values.get("Jateilma")

            if outdoor is None or humidity is None or exhaust is None:
                missing = [k for k, v in {"Ulkolampotila": outdoor, "Suhteellinen_kosteus": humidity, "Jateilma": exhaust}.items() if v is None]
                return [TextContent(type="text", text=json.dumps({
                    "error": "Missing sensor data",
                    "missing_fields": missing,
                    "hint": "WAGO data is logged every ~2 hours. Try a wider time range or check if the controller is online."
                }, indent=2, ensure_ascii=False))]

            # Temperature risk: linear from -5°C (0%) to -25°C (100%), weight 50%
            temp_raw = (-5.0 - outdoor) / 20.0
            temp_risk = max(0.0, min(1.0, temp_raw))
            temp_score = temp_risk * 50.0

            # Humidity risk: linear from 15% (0%) to 30% (100%), weight 35%
            hum_raw = (humidity - 15.0) / 15.0
            hum_risk = max(0.0, min(1.0, hum_raw))
            hum_score = hum_risk * 35.0

            # Exhaust temp risk: linear from 5°C (0%) to 0°C (100%), weight 15%
            exh_raw = (5.0 - exhaust) / 5.0
            exh_risk = max(0.0, min(1.0, exh_raw))
            exh_score = exh_risk * 15.0

            total = temp_score + hum_score + exh_score

            # Exhaust below 0°C forces 95%
            if exhaust < 0.0:
                probability = 95.0
            elif total > 95.0:
                probability = 95.0
            else:
                probability = round(total, 1)

            # Risk level classification
            if probability < 25:
                risk_level = "low"
            elif probability < 50:
                risk_level = "moderate"
            elif probability < 75:
                risk_level = "high"
            else:
                risk_level = "critical"

            result = {
                "probability": probability,
                "risk_level": risk_level,
                "components": {
                    "temperature": {
                        "value": outdoor,
                        "unit": "°C",
                        "score": round(temp_score, 1),
                        "max_score": 50,
                        "risk_range": "-5°C (0%) to -25°C (100%)"
                    },
                    "humidity": {
                        "value": humidity,
                        "unit": "%",
                        "score": round(hum_score, 1),
                        "max_score": 35,
                        "risk_range": "15% (0%) to 30% (100%)"
                    },
                    "exhaust_temp": {
                        "value": exhaust,
                        "unit": "°C",
                        "score": round(exh_score, 1),
                        "max_score": 15,
                        "risk_range": "5°C (0%) to 0°C (100%)"
                    }
                },
                "thresholds": {
                    "low": "< 25%",
                    "moderate": "25-50%",
                    "high": "50-75%",
                    "critical": ">= 75%",
                    "forced_95": "Exhaust air below 0°C"
                }
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_energy_consumption":
        time_range = arguments.get("time_range", "-7d")

        query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Lampopumppu_energia" or r._field == "Lisavastus_energia")
  |> aggregateWindow(every: {time_range.replace("-", "")}, fn: spread, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
        try:
            # Get consumption (difference between first and last)
            q_first = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Lampopumppu_energia" or r._field == "Lisavastus_energia")
  |> first()
'''
            q_last = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Lampopumppu_energia" or r._field == "Lisavastus_energia")
  |> last()
'''
            first_results = execute_flux_query(q_first)
            last_results = execute_flux_query(q_last)

            first_vals = {r["_field"]: r["_value"] for r in first_results}
            last_vals = {r["_field"]: r["_value"] for r in last_results}

            hp_consumption = (last_vals.get("Lampopumppu_energia", 0) or 0) - (first_vals.get("Lampopumppu_energia", 0) or 0)
            aux_consumption = (last_vals.get("Lisavastus_energia", 0) or 0) - (first_vals.get("Lisavastus_energia", 0) or 0)

            result = {
                "time_range": time_range,
                "heat_pump": {
                    "start_reading_kwh": first_vals.get("Lampopumppu_energia"),
                    "end_reading_kwh": last_vals.get("Lampopumppu_energia"),
                    "consumption_kwh": hp_consumption
                },
                "auxiliary_heater": {
                    "start_reading_kwh": first_vals.get("Lisavastus_energia"),
                    "end_reading_kwh": last_vals.get("Lisavastus_energia"),
                    "consumption_kwh": aux_consumption
                },
                "total_consumption_kwh": hp_consumption + aux_consumption
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_room_temperatures":
        include_pid = arguments.get("include_pid", True)

        temp_fields = ["MH_Seela", "MH_Aarni", "MH_aikuiset", "MH_alakerta",
                       "Ylakerran_aula", "Keittio", "Eteinen", "Kellari", "Kellari_eteinen"]

        if include_pid:
            pid_fields = [f + "_PID" for f in temp_fields]
            all_fields = temp_fields + pid_fields
        else:
            all_fields = temp_fields

        field_filter = " or ".join([f'r._field == "{f}"' for f in all_fields])

        query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "rooms")
  |> filter(fn: (r) => {field_filter})
  |> last()
'''
        try:
            results = execute_flux_query(query)

            # Organize by room
            rooms = {}
            for r in results:
                field = r.get("_field", "")
                value = r.get("_value")
                time = r.get("_time")

                if field.endswith("_PID"):
                    room = field.replace("_PID", "")
                    if room not in rooms:
                        rooms[room] = {}
                    rooms[room]["heating_demand_%"] = value
                else:
                    if field not in rooms:
                        rooms[field] = {}
                    rooms[field]["temperature_°C"] = value
                    rooms[field]["last_update"] = time

            # Add descriptions
            for room in rooms:
                desc = SCHEMA["measurements"]["rooms"]["fields"].get(room, {}).get("description", "")
                rooms[room]["description"] = desc

            return [TextContent(type="text", text=json.dumps(rooms, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_air_quality":
        time_range = arguments.get("time_range", "-24h")

        query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Keittiö")
  |> filter(fn: (r) => r._field == "co2" or r._field == "pm2_5" or r._field == "voc" or r._field == "nox" or r._field == "temperature" or r._field == "humidity")
  |> last()
'''
        try:
            results = execute_flux_query(query)

            data = {}
            for r in results:
                field = r.get("_field")
                value = r.get("_value")

                if field == "co2":
                    status = "good" if value < 800 else ("moderate" if value < 1200 else "poor")
                    data["co2"] = {"value": value, "unit": "ppm", "status": status, "thresholds": "good<800, moderate<1200, poor>=1200"}
                elif field == "pm2_5":
                    status = "good" if value < 10 else ("moderate" if value < 25 else "poor")
                    data["pm2_5"] = {"value": value, "unit": "µg/m³", "status": status, "thresholds": "good<10, moderate<25, poor>=25"}
                elif field == "voc":
                    status = "good" if value < 150 else ("moderate" if value < 250 else "poor")
                    data["voc"] = {"value": value, "unit": "index", "status": status, "thresholds": "good<150, moderate<250, poor>=250"}
                elif field == "nox":
                    status = "good" if value < 20 else ("moderate" if value < 150 else "poor")
                    data["nox"] = {"value": value, "unit": "index", "status": status, "thresholds": "good<20, moderate<150, poor>=150"}
                elif field == "temperature":
                    data["temperature"] = {"value": value, "unit": "°C"}
                elif field == "humidity":
                    data["humidity"] = {"value": value, "unit": "%"}

            data["location"] = "Kitchen (Keittiö)"
            data["sensor_type"] = "Ruuvi air quality sensor"

            return [TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "compare_indoor_outdoor":
        time_range = arguments.get("time_range", "-24h")
        indoor_source = arguments.get("indoor_source", "ruuvi")

        if indoor_source == "ruuvi":
            indoor_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Olohuone")
  |> filter(fn: (r) => r._field == "temperature")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
'''
        else:
            indoor_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "rooms")
  |> filter(fn: (r) => r._field == "Keittio")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
'''

        outdoor_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
'''

        try:
            indoor_results = execute_flux_query(indoor_query)
            outdoor_results = execute_flux_query(outdoor_query)

            indoor_temps = [r["_value"] for r in indoor_results if r.get("_value") is not None]
            outdoor_temps = [r["_value"] for r in outdoor_results if r.get("_value") is not None]

            result = {
                "time_range": time_range,
                "indoor_source": indoor_source,
                "indoor": {
                    "mean": sum(indoor_temps) / len(indoor_temps) if indoor_temps else None,
                    "min": min(indoor_temps) if indoor_temps else None,
                    "max": max(indoor_temps) if indoor_temps else None,
                    "current": indoor_temps[-1] if indoor_temps else None,
                    "unit": "°C"
                },
                "outdoor": {
                    "mean": sum(outdoor_temps) / len(outdoor_temps) if outdoor_temps else None,
                    "min": min(outdoor_temps) if outdoor_temps else None,
                    "max": max(outdoor_temps) if outdoor_temps else None,
                    "current": outdoor_temps[-1] if outdoor_temps else None,
                    "unit": "°C"
                },
                "temperature_difference": {
                    "current": (indoor_temps[-1] - outdoor_temps[-1]) if indoor_temps and outdoor_temps else None,
                    "mean": (sum(indoor_temps) / len(indoor_temps) - sum(outdoor_temps) / len(outdoor_temps)) if indoor_temps and outdoor_temps else None,
                    "unit": "°C"
                }
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_thermia_status":
        try:
            # Query all data types
            results_by_type = {}
            for data_type in ["temperature", "status", "alarm", "performance", "runtime", "setting"]:
                query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "{data_type}")
  |> last()
'''
                results = execute_flux_query(query)
                type_data = {}
                for r in results:
                    field = r.get("_field", "unknown")
                    value = r.get("_value")
                    unit = SCHEMA["measurements"].get("thermia", {}).get("fields", {}).get(field, {}).get("unit", "")
                    type_data[field] = {"value": value, "unit": unit}
                if type_data:
                    results_by_type[data_type] = type_data

            # Check for active alarms
            alarms = results_by_type.get("alarm", {})
            active_alarms = [name for name, info in alarms.items() if info.get("value") == 1]

            result = {
                "temperatures": results_by_type.get("temperature", {}),
                "component_status": results_by_type.get("status", {}),
                "alarms": {
                    "active": active_alarms,
                    "all": results_by_type.get("alarm", {})
                },
                "performance": results_by_type.get("performance", {}),
                "runtime_hours": results_by_type.get("runtime", {}),
                "settings": results_by_type.get("setting", {})
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_thermia_temperatures":
        time_range = arguments.get("time_range", "-24h")
        aggregation = arguments.get("aggregation", "5m")

        query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "temperature")
  |> filter(fn: (r) => r._field == "outdoor_temp" or r._field == "indoor_temp" or r._field == "supply_temp" or r._field == "return_temp" or r._field == "hotwater_temp" or r._field == "brine_in_temp" or r._field == "brine_out_temp")
  |> aggregateWindow(every: {aggregation}, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
        try:
            results = execute_flux_query(query)
            return [TextContent(type="text", text=json.dumps({
                "time_range": time_range,
                "aggregation": aggregation,
                "count": len(results),
                "data": results[:100],
                "truncated": len(results) > 100
            }, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


def create_starlette_app():
    """Create Starlette app with SSE transport for MCP."""
    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await app.run(
                streams[0], streams[1], app.create_initialization_options()
            )

    async def handle_messages(request):
        await sse.handle_post_message(request.scope, request.receive, request._send)

    async def health_check(request):
        return JSONResponse({"status": "ok", "service": "building-automation-mcp"})

    starlette_app = Starlette(
        debug=False,
        routes=[
            Route("/health", health_check),
            Route("/sse", handle_sse),
            Mount("/messages/", routes=[Route("/", handle_messages, methods=["POST"])]),
        ],
    )

    return starlette_app


def main():
    """Run the MCP server."""
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "3001"))

    print(f"Starting Building Automation MCP Server")
    print(f"  URL: http://{host}:{port}/sse")
    print(f"  Health: http://{host}:{port}/health")
    print(f"  InfluxDB: {INFLUXDB_URL}")

    starlette_app = create_starlette_app()
    uvicorn.run(starlette_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
