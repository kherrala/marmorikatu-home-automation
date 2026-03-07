#!/usr/bin/env python3
"""
MCP Server for Building Automation Data Analytics.

Provides tools for Claude Desktop to query and analyze measurement data
from InfluxDB (HVAC, room temperatures, Ruuvi sensors).

Runs as an SSE (Server-Sent Events) server for URL-based MCP integration.
"""

import os
import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Any
import asyncio
import uvicorn
import httpx

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent, Resource
from influxdb_client import InfluxDBClient
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse

log = logging.getLogger("mcp-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Configuration
INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")
WEATHER_API_URL = os.environ.get("WEATHER_API_URL", "http://weather:3020/api/weather")
NEWS_API_URL = os.environ.get("NEWS_API_URL", "http://news:3021/api/news")
BUS_API_URL = os.environ.get("BUS_API_URL", "http://host.docker.internal:3010/api/departures")
CALENDAR_API_URL = os.environ.get("CALENDAR_API_URL", "http://calendar:3022/api/calendar")

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
                "integral_limit_a1": {"unit": "°C*min", "description": "Integral limit A1 (aux heater step 1 threshold)"},
                "integral_limit_a2": {"unit": "°C*min", "description": "Integral limit A2 (aux heater step 2 threshold, raw×10)"},
                "hotwater_supply_temp": {"unit": "°C", "description": "Hot water supply line temperature"},
                "calibration_outdoor": {"unit": "°C", "description": "Outdoor sensor calibration offset"},
                "calibration_supply": {"unit": "°C", "description": "Supply line sensor calibration offset"},
                "calibration_return": {"unit": "°C", "description": "Return line sensor calibration offset"},
                "calibration_hotwater": {"unit": "°C", "description": "Hot water sensor calibration offset"},
                "calibration_brine_out": {"unit": "°C", "description": "Brine out sensor calibration offset"},
                "calibration_brine_in": {"unit": "°C", "description": "Brine in sensor calibration offset"},
                "heating_system_type": {"unit": "#", "description": "Heating system type (0=VL, 4=D)"},
            },
            "tags": {
                "data_type": ["temperature", "status", "alarm", "performance", "runtime", "setting"]
            }
        },
        "electricity": {
            "description": "Nord Pool Finland electricity spot prices (updated daily around 14:15 EET)",
            "fields": {
                "price_no_tax": {"unit": "c/kWh", "description": "Spot price without tax"},
                "price_with_tax": {"unit": "c/kWh", "description": "Spot price with tax (25% VAT)"},
            },
            "tags": {
                "source": ["spot-hinta.fi"],
                "market": ["FI"]
            }
        },
        "heating_optimizer": {
            "description": "Floor heating optimizer decisions (logged every 15 minutes at price slot boundaries)",
            "fields": {
                "setpoint": {"unit": "°C", "description": "Heat pump indoor target temperature"},
                "evu_active": {"unit": "bool", "description": "EVU mode active (1=on, reduces target by reduction_t)"},
                "boiler_steps": {"unit": "-", "description": "Aux heater steps (0=OFF, 1=3kW, 2=3+6kW)"},
                "tier": {"unit": "-", "description": "Price tier (CHEAP/NORMAL/EXPENSIVE/PRE_HEAT)"},
                "effective_target": {"unit": "°C", "description": "Effective target temperature (setpoint - reduction if EVU)"},
                "price": {"unit": "c/kWh", "description": "Current electricity price"},
                "outdoor_temp": {"unit": "°C", "description": "Outdoor temperature at decision time"},
            },
            "tags": {}
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
            "formula": "Weighted composite: 60% dew point proximity (Jateilma - Kastepiste, 0-5°C margin) + 25% outdoor temp (-5 to -25°C) + 15% exhaust temp (0-5°C)",
            "description": "Heat exchanger freezing probability based on dew point proximity",
            "fields_used": ["Ulkolampotila", "Kastepiste", "Jateilma"],
            "typical_range": "0-95%"
        },
        "heatpump_cop": {
            "formula": "COP_hp = P_heat / 2.3, where P_heat = 1.965 × (supply_temp - return_temp)",
            "description": "Heat pump COP from heating circuit ΔT and nominal compressor power (2.3 kW fixed-speed)",
            "fields_used": ["supply_temp", "return_temp", "compressor", "aux_heater_3kw", "aux_heater_6kw"],
            "typical_range": "COP 3.0-5.0 (nominal 4.6 at B0/W35)"
        },
        "brine_circuit_health": {
            "formula": "ΔT_brine = brine_in_temp - brine_out_temp",
            "description": "Ground source brine circuit temperature differential and health",
            "fields_used": ["brine_in_temp", "brine_out_temp"],
            "typical_range": "ΔT ~3°C, brine_out > -15°C"
        },
        "hotwater_cycle": {
            "formula": "Duty cycle = production_time / total_time × 100%",
            "description": "Hot water production cycle analysis",
            "fields_used": ["hotwater_temp", "hotwater_production", "hotwater_start_temp", "hotwater_stop_temp", "runtime_hotwater"],
            "typical_range": "Tank 43-55°C, duty cycle varies"
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
                try:
                    t = record.get_time()
                    row = {"_time": t.isoformat() if t else None}
                except KeyError:
                    row = {"_time": None}
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
            description="""Get estimated energy consumption breakdown by component.

Estimates consumption (kWh) for: heat pump (compressor + aux heaters),
sauna, lighting, and HVAC fan. Uses component status data from InfluxDB
with the same calculation as the Energy Cost dashboard.""",
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
- Dew point proximity (60% weight): risk increases as exhaust temp approaches dew point (5°C margin → 0°C margin)
- Outdoor temperature (25% weight): risk increases from -5°C to -25°C
- Exhaust air temperature (15% weight): risk increases from 5°C to 0°C

Override rules: exhaust below 0°C forces 60%, dew point margin below 0°C forces minimum 80%.
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
        Tool(
            name="get_heatpump_cop",
            description="""Calculate heat pump COP (Coefficient of Performance) and thermal power.

Joins temperature data (supply_temp, return_temp) with component status (compressor, aux heaters)
to compute:
- P_heat = 1.965 × (supply_temp - return_temp) kW
- COP_hp = P_heat / 2.3 (compressor-only, filtered to compressor=1)
- COP_system = P_heat / (2.3 + P_aux) (whole system including aux heaters)
- Ground energy extraction, compressor duty cycle

Reference: Thermia Diplomat 8, nominal COP 4.6 at B0/W35, 2.3 kW fixed-speed compressor.""",
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
        Tool(
            name="get_brine_circuit",
            description="""Analyze ground source brine circuit health.

Returns brine_in_temp and brine_out_temp statistics, temperature differential (ΔT),
and risk assessment if brine_out approaches -15°C (brine_min_t setting).
Optimal ΔT is ~3°C. Large ΔT may indicate high extraction or low flow.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_range": {
                        "type": "string",
                        "description": "Time range (e.g., '-7d', '-30d')",
                        "default": "-7d"
                    },
                    "aggregation": {
                        "type": "string",
                        "description": "Aggregation window (e.g., '1h', '6h')",
                        "default": "1h"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_hotwater_analysis",
            description="""Analyze hot water production cycles.

Returns current tank temperature vs start/stop thresholds, production cycle count,
total production time, duty cycle percentage, and runtime counter delta.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_range": {
                        "type": "string",
                        "description": "Time range (e.g., '-24h', '-7d')",
                        "default": "-24h"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_thermia_register_data",
            description="""Query any combination of Thermia heat pump register values.

Flexible tool to access all register categories without needing a dedicated tool for each.
Available data_types: temperature, status, alarm, performance, runtime, setting.

If aggregation is provided, returns aggregated time series with statistics.
If omitted, returns latest snapshot values only.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "data_type": {
                        "type": "string",
                        "description": "Register category: 'temperature', 'status', 'alarm', 'performance', 'runtime', 'setting'",
                        "enum": ["temperature", "status", "alarm", "performance", "runtime", "setting"]
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific field names to fetch. If omitted, returns all fields for the data_type."
                    },
                    "time_range": {
                        "type": "string",
                        "description": "Time range (e.g., '-24h', '-7d')",
                        "default": "-24h"
                    },
                    "aggregation": {
                        "type": "string",
                        "description": "Aggregation window (e.g., '5m', '1h'). If omitted, returns latest values only."
                    }
                },
                "required": ["data_type"]
            }
        ),
        Tool(
            name="get_compressor_duty_cycle",
            description="""Analyze compressor and auxiliary heater runtime and cycling statistics.

Returns compressor duty cycle %, aux heater duty cycles, runtime counter deltas,
aux-to-compressor runtime ratio, and number of compressor start/stop cycles.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_range": {
                        "type": "string",
                        "description": "Time range (e.g., '-24h', '-7d')",
                        "default": "-24h"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_electricity_prices",
            description="""Get current and upcoming electricity spot prices (Nord Pool Finland).

Returns the current hour's price, today's price range (min/max/average),
hourly price schedule for today and tomorrow (if available), and highlights
the cheapest and most expensive hours. Prices are in c/kWh including tax.""",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_heating_status",
            description="""Get the current heating optimizer status.

Returns the current price tier (CHEAP/NORMAL/EXPENSIVE/PRE_HEAT), heat pump
setpoint, EVU mode (energy utility lockout) status, auxiliary heater status,
effective target temperature, and current electricity price. Shows how the
optimizer is adjusting heating based on electricity prices.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_range": {
                        "type": "string",
                        "description": "Time range for status history (e.g., '-6h', '-24h'). Default returns only latest status.",
                        "default": "-1h"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_sauna_status",
            description="""Get sauna status: current temperature and recent heating sessions.

Detects whether the sauna is currently heating by analyzing the temperature
rate of change from the Ruuvi sensor in the sauna room. Also lists recent
sauna sessions from the past 7 days with approximate start times, peak
temperatures, and durations.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "history_days": {
                        "type": "integer",
                        "description": "How many days of sauna session history to include (default 7)",
                        "default": 7
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_energy_cost",
            description="""Estimate electricity consumption and cost breakdown by component.

Calculates estimated consumption (kWh) and cost (EUR) for: heat pump
(compressor + aux heaters), sauna, lighting, and HVAC fan. Uses component
status data from InfluxDB combined with spot electricity prices. Cost model:
spot price + 0.49 c/kWh margin + 6.09 c/kWh transfer fee.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_range": {
                        "type": "string",
                        "description": "Time range (e.g., '-24h', '-7d', '-1d')",
                        "default": "-24h"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_weather_forecast",
            description="Get current weather and forecast for Tampere. Returns temperature, conditions, wind, humidity, hourly and daily forecast.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_news_headlines",
            description="Get latest Finnish news headlines from Yle (national + Pirkanmaa regional). Returns titles, descriptions, sources and publish times.",
            inputSchema={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of headlines to return (default 5, max 20)",
                        "default": 5
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_bus_departures",
            description="Get upcoming bus departures from nearby stops (Kaipanen and Pitkäniitynkatu) towards Tampere city centre. Returns real-time departure times, line numbers, destinations, delays, and when to leave home.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of departures to return (default 5)",
                        "default": 5
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_calendar_events",
            description="Get upcoming family calendar events. Returns event summaries, times, locations grouped by date.",
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days ahead to fetch (default 7, max 14)",
                        "default": 7
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
  |> filter(fn: (r) => r._field == "Ulkolampotila" or r._field == "Kastepiste" or r._field == "Jateilma")
  |> last()
'''
        try:
            results = execute_flux_query(query)
            values = {r["_field"]: r["_value"] for r in results}

            outdoor = values.get("Ulkolampotila")
            dew_point = values.get("Kastepiste")
            exhaust = values.get("Jateilma")

            if outdoor is None or dew_point is None or exhaust is None:
                missing = [k for k, v in {"Ulkolampotila": outdoor, "Kastepiste": dew_point, "Jateilma": exhaust}.items() if v is None]
                return [TextContent(type="text", text=json.dumps({
                    "error": "Missing sensor data",
                    "missing_fields": missing,
                    "hint": "WAGO data is logged every ~2 hours. Try a wider time range or check if the controller is online."
                }, indent=2, ensure_ascii=False))]

            # Dew point margin
            margin = exhaust - dew_point

            # Dew point proximity risk: linear from 5°C margin (0%) to 0°C margin (100%), weight 60%
            dew_raw = (5.0 - margin) / 5.0
            dew_risk = max(0.0, min(1.0, dew_raw))
            dew_score = dew_risk * 60.0

            # Temperature risk: linear from -5°C (0%) to -25°C (100%), weight 25%
            temp_raw = (-5.0 - outdoor) / 20.0
            temp_risk = max(0.0, min(1.0, temp_raw))
            temp_score = temp_risk * 25.0

            # Exhaust temp risk: linear from 5°C (0%) to 0°C (100%), weight 15%
            exh_raw = (5.0 - exhaust) / 5.0
            exh_risk = max(0.0, min(1.0, exh_raw))
            exh_score = exh_risk * 15.0

            total = dew_score + temp_score + exh_score

            # Override rules
            if exhaust < 0.0:
                probability = 60.0
            elif margin < 0.0:
                probability = max(80.0, min(95.0, total))
            else:
                probability = min(95.0, total)

            probability = round(probability, 1)

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
                "dew_point_margin": round(margin, 1),
                "components": {
                    "dew_point_proximity": {
                        "margin": round(margin, 1),
                        "unit": "°C",
                        "score": round(dew_score, 1),
                        "max_score": 60,
                        "risk_range": "5°C margin (0%) to 0°C margin (100%)"
                    },
                    "outdoor_temperature": {
                        "value": outdoor,
                        "unit": "°C",
                        "score": round(temp_score, 1),
                        "max_score": 25,
                        "risk_range": "-5°C (0%) to -25°C (100%)"
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
                    "forced_60": "Exhaust air below 0°C",
                    "forced_min_80": "Dew point margin below 0°C (condensation occurring)"
                }
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_energy_consumption":
        try:
            time_range = arguments.get("time_range", "-7d")
            WATT_PER_LIGHT = 10
            WATT_FAN = 300

            # Heat pump: join supply_temp with compressor/aux status
            flux_hp_temps = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "temperature")
  |> filter(fn: (r) => r._field == "supply_temp")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
"""
            flux_hp_status = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "status")
  |> filter(fn: (r) => r._field == "compressor" or r._field == "aux_heater_3kw" or r._field == "aux_heater_6kw")
  |> toFloat()
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value", "_field"])
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
            hp_temps = execute_flux_query(flux_hp_temps)
            hp_status = execute_flux_query(flux_hp_status)
            temp_by_time = {r.get("_time"): r.get("_value", 35) or 35 for r in hp_temps}

            hp_comp_kwh = 0.0
            hp_aux_kwh = 0.0
            for row in hp_status:
                comp = row.get("compressor", 0) or 0
                aux3 = row.get("aux_heater_3kw", 0) or 0
                aux6 = row.get("aux_heater_6kw", 0) or 0
                sup_t = temp_by_time.get(row.get("_time"), 35)
                hp_comp_kwh += comp * (1.77 + (sup_t - 35.0) * 0.5 / 15.0)
                hp_aux_kwh += aux3 * 3.0 + aux6 * 6.0

            # Lighting
            flux_lights = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "lights" and r._field == "is_on")
  |> toFloat()
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> group(columns: ["_time"])
  |> sum()
  |> group()
"""
            light_data = execute_flux_query(flux_lights)
            light_kwh = sum((r.get("_value", 0) or 0) * WATT_PER_LIGHT / 1000.0 for r in light_data)

            # Sauna
            flux_sauna = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Sauna")
  |> filter(fn: (r) => r._field == "temperature")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> derivative(unit: 1m, nonNegative: false)
  |> map(fn: (r) => ({{r with _value: if r._value > 0.05 then 1.0 else 0.0}}))
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
"""
            sauna_data = execute_flux_query(flux_sauna)
            sauna_kwh = sum((r.get("_value", 0) or 0) * 6.0 for r in sauna_data)

            # HVAC fan
            flux_fan = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "ruuvi" and (r.sensor_name == "Keittio" or r.sensor_name == "Keittiö"))
  |> filter(fn: (r) => r._field == "pressure")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
"""
            fan_data = execute_flux_query(flux_fan)
            fan_kwh = len(fan_data) * WATT_FAN / 1000.0

            total_kwh = hp_comp_kwh + hp_aux_kwh + light_kwh + sauna_kwh + fan_kwh

            result = {
                "time_range": time_range,
                "heat_pump_compressor_kwh": round(hp_comp_kwh, 2),
                "heat_pump_aux_heaters_kwh": round(hp_aux_kwh, 2),
                "lighting_kwh": round(light_kwh, 2),
                "sauna_kwh": round(sauna_kwh, 2),
                "hvac_fan_kwh": round(fan_kwh, 2),
                "total_kwh": round(total_kwh, 2),
                "note": "Estimates based on component status data and assumed wattages",
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

    elif name == "get_heatpump_cop":
        time_range = arguments.get("time_range", "-24h")
        aggregation = arguments.get("aggregation", "5m")

        # Query temperatures (pivoted wide format)
        temp_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "temperature")
  |> filter(fn: (r) => r._field == "supply_temp" or r._field == "return_temp")
  |> aggregateWindow(every: {aggregation}, fn: mean, createEmpty: false)
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
        # Query status (pivoted wide format)
        status_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "status")
  |> filter(fn: (r) => r._field == "compressor" or r._field == "aux_heater_3kw" or r._field == "aux_heater_6kw")
  |> aggregateWindow(every: {aggregation}, fn: last, createEmpty: false)
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
        try:
            temp_results = execute_flux_query(temp_query)
            status_results = execute_flux_query(status_query)

            # Index status by time for joining
            status_by_time = {r["_time"]: r for r in status_results}

            # Join and compute
            cop_values = []
            cop_system_values = []
            p_heat_values = []
            p_ground_values = []
            compressor_on_count = 0
            aux_active_count = 0
            total_points = 0
            recent = []

            for t in temp_results:
                time_key = t.get("_time")
                s = status_by_time.get(time_key)
                if not s:
                    continue

                supply = t.get("supply_temp")
                ret = t.get("return_temp")
                comp = s.get("compressor", 0)
                aux_3kw = s.get("aux_heater_3kw", 0)
                aux_6kw = s.get("aux_heater_6kw", 0)

                if supply is None or ret is None:
                    continue

                total_points += 1
                p_heat = 1.965 * (supply - ret)
                p_aux = 3.0 * aux_3kw + 6.0 * aux_6kw

                if comp == 1:
                    compressor_on_count += 1
                    p_compressor = 2.3
                    cop_hp = p_heat / p_compressor
                    p_ground = p_heat - p_compressor
                    cop_values.append(cop_hp)
                    p_ground_values.append(p_ground)

                    total_input = p_compressor + p_aux
                    if total_input > 0:
                        cop_sys = p_heat / total_input
                        cop_system_values.append(cop_sys)

                if aux_3kw == 1 or aux_6kw == 1:
                    aux_active_count += 1

                p_heat_values.append(p_heat)

                recent.append({
                    "_time": time_key,
                    "supply_temp": supply,
                    "return_temp": ret,
                    "delta_t": round(supply - ret, 1),
                    "p_heat_kw": round(p_heat, 2),
                    "compressor": comp,
                    "cop_hp": round(p_heat / 2.3, 2) if comp == 1 else None,
                })

            result = {
                "time_range": time_range,
                "aggregation": aggregation,
                "total_data_points": total_points,
                "compressor_running_points": compressor_on_count,
                "compressor_duty_pct": round(compressor_on_count / total_points * 100, 1) if total_points > 0 else None,
                "aux_heater_active_pct": round(aux_active_count / total_points * 100, 1) if total_points > 0 else None,
                "cop_hp": {
                    "mean": round(sum(cop_values) / len(cop_values), 2) if cop_values else None,
                    "min": round(min(cop_values), 2) if cop_values else None,
                    "max": round(max(cop_values), 2) if cop_values else None,
                    "description": "Heat pump COP (compressor only, when running)",
                    "nominal_reference": 4.6,
                },
                "cop_system": {
                    "mean": round(sum(cop_system_values) / len(cop_system_values), 2) if cop_system_values else None,
                    "min": round(min(cop_system_values), 2) if cop_system_values else None,
                    "max": round(max(cop_system_values), 2) if cop_system_values else None,
                    "description": "System COP (including aux heaters)",
                },
                "thermal_power_kw": {
                    "mean": round(sum(p_heat_values) / len(p_heat_values), 2) if p_heat_values else None,
                    "min": round(min(p_heat_values), 2) if p_heat_values else None,
                    "max": round(max(p_heat_values), 2) if p_heat_values else None,
                },
                "ground_energy_kw": {
                    "mean": round(sum(p_ground_values) / len(p_ground_values), 2) if p_ground_values else None,
                    "description": "Estimated ground heat extraction (P_heat - 2.3 kW)",
                },
                "recent_values": recent[-5:] if len(recent) > 5 else recent,
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_brine_circuit":
        time_range = arguments.get("time_range", "-7d")
        aggregation = arguments.get("aggregation", "1h")

        query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "temperature")
  |> filter(fn: (r) => r._field == "brine_in_temp" or r._field == "brine_out_temp")
  |> aggregateWindow(every: {aggregation}, fn: mean, createEmpty: false)
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
        try:
            results = execute_flux_query(query)

            brine_in_vals = [r["brine_in_temp"] for r in results if r.get("brine_in_temp") is not None]
            brine_out_vals = [r["brine_out_temp"] for r in results if r.get("brine_out_temp") is not None]
            delta_t_vals = [r["brine_in_temp"] - r["brine_out_temp"] for r in results
                           if r.get("brine_in_temp") is not None and r.get("brine_out_temp") is not None]

            brine_min_t = -15.0  # Setting from register d77
            min_brine_out = min(brine_out_vals) if brine_out_vals else None
            risk_margin = round(min_brine_out - brine_min_t, 1) if min_brine_out is not None else None

            if risk_margin is not None:
                if risk_margin < 3:
                    risk_level = "high"
                elif risk_margin < 6:
                    risk_level = "moderate"
                else:
                    risk_level = "low"
            else:
                risk_level = "unknown"

            result = {
                "time_range": time_range,
                "aggregation": aggregation,
                "data_points": len(results),
                "brine_in_temp": {
                    "mean": round(sum(brine_in_vals) / len(brine_in_vals), 1) if brine_in_vals else None,
                    "min": round(min(brine_in_vals), 1) if brine_in_vals else None,
                    "max": round(max(brine_in_vals), 1) if brine_in_vals else None,
                    "unit": "°C",
                    "description": "Brine returning from ground (warmer)",
                },
                "brine_out_temp": {
                    "mean": round(sum(brine_out_vals) / len(brine_out_vals), 1) if brine_out_vals else None,
                    "min": round(min(brine_out_vals), 1) if brine_out_vals else None,
                    "max": round(max(brine_out_vals), 1) if brine_out_vals else None,
                    "unit": "°C",
                    "description": "Brine going to ground (colder)",
                },
                "delta_t": {
                    "mean": round(sum(delta_t_vals) / len(delta_t_vals), 1) if delta_t_vals else None,
                    "min": round(min(delta_t_vals), 1) if delta_t_vals else None,
                    "max": round(max(delta_t_vals), 1) if delta_t_vals else None,
                    "unit": "°C",
                    "optimal": "~3°C",
                    "description": "Heat extraction indicator (brine_in - brine_out)",
                },
                "risk_assessment": {
                    "brine_min_limit": brine_min_t,
                    "lowest_brine_out": min_brine_out,
                    "margin_to_limit": risk_margin,
                    "risk_level": risk_level,
                    "unit": "°C",
                    "description": "Risk of brine circuit freeze protection triggering",
                },
                "recent_values": results[-5:] if len(results) > 5 else results,
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_hotwater_analysis":
        time_range = arguments.get("time_range", "-24h")

        # Query hotwater temp time series
        temp_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "temperature")
  |> filter(fn: (r) => r._field == "hotwater_temp")
'''
        # Query hotwater production status
        status_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "status")
  |> filter(fn: (r) => r._field == "hotwater_production")
'''
        # Query settings (latest)
        settings_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "setting")
  |> filter(fn: (r) => r._field == "hotwater_start_temp" or r._field == "hotwater_stop_temp")
  |> last()
'''
        # Query runtime counters (first and last in range)
        runtime_first_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "runtime")
  |> filter(fn: (r) => r._field == "runtime_hotwater")
  |> first()
'''
        runtime_last_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "runtime")
  |> filter(fn: (r) => r._field == "runtime_hotwater")
  |> last()
'''
        try:
            temp_results = execute_flux_query(temp_query)
            status_results = execute_flux_query(status_query)
            settings_results = execute_flux_query(settings_query)
            rt_first = execute_flux_query(runtime_first_query)
            rt_last = execute_flux_query(runtime_last_query)

            # Parse settings
            settings = {r["_field"]: r["_value"] for r in settings_results}
            start_temp = settings.get("hotwater_start_temp")
            stop_temp = settings.get("hotwater_stop_temp")

            # Temperature stats
            temps = [r["_value"] for r in temp_results if r.get("_value") is not None]
            current_temp = temps[-1] if temps else None

            # Count production cycles (0→1 transitions)
            production_states = [r["_value"] for r in status_results if r.get("_value") is not None]
            cycle_count = 0
            production_time = 0
            for i in range(len(production_states)):
                if production_states[i] == 1:
                    production_time += 1
                    if i > 0 and production_states[i - 1] == 0:
                        cycle_count += 1

            total_samples = len(production_states)
            duty_cycle = round(production_time / total_samples * 100, 1) if total_samples > 0 else None

            # Runtime delta
            rt_start = rt_first[0]["_value"] if rt_first else None
            rt_end = rt_last[0]["_value"] if rt_last else None
            rt_delta = round(rt_end - rt_start, 1) if rt_start is not None and rt_end is not None else None

            result = {
                "time_range": time_range,
                "current_tank_temp": current_temp,
                "settings": {
                    "start_temp": start_temp,
                    "stop_temp": stop_temp,
                    "description": "Production starts when tank drops below start_temp, stops at stop_temp",
                },
                "temperature_stats": {
                    "mean": round(sum(temps) / len(temps), 1) if temps else None,
                    "min": round(min(temps), 1) if temps else None,
                    "max": round(max(temps), 1) if temps else None,
                    "unit": "°C",
                },
                "production_cycles": {
                    "count": cycle_count,
                    "total_samples": total_samples,
                    "production_samples": production_time,
                    "duty_cycle_pct": duty_cycle,
                    "description": "Number of 0→1 transitions in hotwater_production status",
                },
                "runtime_counter": {
                    "start_h": rt_start,
                    "end_h": rt_end,
                    "delta_h": rt_delta,
                    "description": "Hot water production runtime counter delta over period",
                },
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_thermia_register_data":
        data_type = arguments.get("data_type")
        fields = arguments.get("fields")
        time_range = arguments.get("time_range", "-24h")
        aggregation = arguments.get("aggregation")

        valid_types = ["temperature", "status", "alarm", "performance", "runtime", "setting"]
        if data_type not in valid_types:
            return [TextContent(type="text", text=f"Invalid data_type: {data_type}. Must be one of: {', '.join(valid_types)}")]

        # Build field filter
        if fields:
            field_filter = " or ".join([f'r._field == "{f}"' for f in fields])
            field_filter = f"\n  |> filter(fn: (r) => {field_filter})"
        else:
            field_filter = ""

        if aggregation:
            # Time series mode with aggregation
            # Use mean for numeric types, last for status/alarm
            agg_fn = "last" if data_type in ["status", "alarm"] else "mean"
            query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "{data_type}"){field_filter}
  |> aggregateWindow(every: {aggregation}, fn: {agg_fn}, createEmpty: false)
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
            try:
                results = execute_flux_query(query)

                # Compute per-field statistics
                field_stats = {}
                if results:
                    # Get all field columns (exclude metadata)
                    skip_keys = {"_time", "_start", "_stop", "_measurement", "data_type", "table", "result"}
                    field_names = [k for k in results[0].keys() if k not in skip_keys]
                    for fname in field_names:
                        vals = [r[fname] for r in results if r.get(fname) is not None]
                        if vals:
                            if isinstance(vals[0], (int, float)):
                                field_stats[fname] = {
                                    "mean": round(sum(vals) / len(vals), 2),
                                    "min": round(min(vals), 2),
                                    "max": round(max(vals), 2),
                                    "count": len(vals),
                                }
                            else:
                                field_stats[fname] = {"count": len(vals), "last": vals[-1]}

                return [TextContent(type="text", text=json.dumps({
                    "data_type": data_type,
                    "time_range": time_range,
                    "aggregation": aggregation,
                    "data_points": len(results),
                    "field_statistics": field_stats,
                    "recent_values": results[-5:] if len(results) > 5 else results,
                    "truncated": len(results) > 100,
                }, indent=2, ensure_ascii=False, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=f"Error: {str(e)}")]
        else:
            # Latest snapshot mode
            query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "{data_type}"){field_filter}
  |> last()
'''
            try:
                results = execute_flux_query(query)
                formatted = {}
                for r in results:
                    field = r.get("_field", "unknown")
                    schema_info = SCHEMA["measurements"].get("thermia", {}).get("fields", {}).get(field, {})
                    formatted[field] = {
                        "value": r.get("_value"),
                        "time": r.get("_time"),
                        "unit": schema_info.get("unit", ""),
                        "description": schema_info.get("description", ""),
                    }

                return [TextContent(type="text", text=json.dumps({
                    "data_type": data_type,
                    "mode": "latest_snapshot",
                    "fields": formatted,
                }, indent=2, ensure_ascii=False, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_compressor_duty_cycle":
        time_range = arguments.get("time_range", "-24h")

        # Query component status time series
        status_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "status")
  |> filter(fn: (r) => r._field == "compressor" or r._field == "aux_heater_3kw" or r._field == "aux_heater_6kw")
'''
        # Runtime counters (first and last)
        runtime_first_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "runtime")
  |> first()
'''
        runtime_last_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "runtime")
  |> last()
'''
        try:
            status_results = execute_flux_query(status_query)
            rt_first_results = execute_flux_query(runtime_first_query)
            rt_last_results = execute_flux_query(runtime_last_query)

            # Organize status by field
            by_field = {}
            for r in status_results:
                field = r.get("_field")
                if field not in by_field:
                    by_field[field] = []
                by_field[field].append(r.get("_value", 0))

            # Duty cycles
            def duty_cycle(values):
                if not values:
                    return None
                on_count = sum(1 for v in values if v == 1)
                return round(on_count / len(values) * 100, 1)

            # Compressor start/stop cycles (1→0 transitions)
            comp_values = by_field.get("compressor", [])
            comp_cycles = 0
            for i in range(1, len(comp_values)):
                if comp_values[i - 1] == 1 and comp_values[i] == 0:
                    comp_cycles += 1

            # Runtime counter deltas
            rt_first = {r["_field"]: r["_value"] for r in rt_first_results}
            rt_last = {r["_field"]: r["_value"] for r in rt_last_results}

            runtime_deltas = {}
            for field in ["runtime_compressor", "runtime_3kw", "runtime_6kw", "runtime_hotwater"]:
                start = rt_first.get(field)
                end = rt_last.get(field)
                if start is not None and end is not None:
                    runtime_deltas[field] = {"start_h": start, "end_h": end, "delta_h": round(end - start, 1)}

            # Aux-to-compressor ratio
            comp_rt = runtime_deltas.get("runtime_compressor", {}).get("delta_h")
            aux_3kw_rt = runtime_deltas.get("runtime_3kw", {}).get("delta_h")
            aux_6kw_rt = runtime_deltas.get("runtime_6kw", {}).get("delta_h")
            if comp_rt and comp_rt > 0 and aux_3kw_rt is not None and aux_6kw_rt is not None:
                aux_ratio = round((aux_3kw_rt + aux_6kw_rt) / comp_rt, 3)
            else:
                aux_ratio = None

            result = {
                "time_range": time_range,
                "total_samples": len(comp_values),
                "compressor": {
                    "duty_cycle_pct": duty_cycle(comp_values),
                    "start_stop_cycles": comp_cycles,
                    "samples_on": sum(1 for v in comp_values if v == 1),
                    "samples_off": sum(1 for v in comp_values if v == 0),
                },
                "aux_heater_3kw": {
                    "duty_cycle_pct": duty_cycle(by_field.get("aux_heater_3kw", [])),
                },
                "aux_heater_6kw": {
                    "duty_cycle_pct": duty_cycle(by_field.get("aux_heater_6kw", [])),
                },
                "runtime_counters": runtime_deltas,
                "aux_to_compressor_ratio": {
                    "value": aux_ratio,
                    "description": "Total aux runtime / compressor runtime (lower = better efficiency)",
                },
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_electricity_prices":
        try:
            now_utc = datetime.now(timezone.utc)

            # Fetch today's and tomorrow's prices
            flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -24h, stop: 48h)
  |> filter(fn: (r) => r._measurement == "electricity" and r._field == "price_with_tax")
  |> group()
  |> sort(columns: ["_time"])
"""
            results = execute_flux_query(flux)

            if not results:
                return [TextContent(type="text", text="Sähkön hintatietoja ei ole saatavilla.")]

            # Parse into hourly prices
            prices = []
            for r in results:
                prices.append({
                    "time": r["_time"],
                    "price_c_kwh": round(r.get("_value", 0), 2),
                })

            # Find current price
            current_price = None
            for p in prices:
                pt = datetime.fromisoformat(p["time"])
                if pt.tzinfo is None:
                    pt = pt.replace(tzinfo=timezone.utc)
                if pt <= now_utc:
                    current_price = p
                else:
                    break

            # Split into today and tomorrow (EET)
            from datetime import timedelta
            eet_offset = timedelta(hours=2)
            today_date = (now_utc + eet_offset).date()
            tomorrow_date = today_date + timedelta(days=1)

            today_prices = []
            tomorrow_prices = []
            for p in prices:
                pt = datetime.fromisoformat(p["time"])
                if pt.tzinfo is None:
                    pt = pt.replace(tzinfo=timezone.utc)
                d = (pt + eet_offset).date()
                if d == today_date:
                    today_prices.append(p)
                elif d == tomorrow_date:
                    tomorrow_prices.append(p)

            # Stats
            def price_stats(price_list):
                vals = [p["price_c_kwh"] for p in price_list]
                if not vals:
                    return None
                cheapest = min(price_list, key=lambda p: p["price_c_kwh"])
                most_expensive = max(price_list, key=lambda p: p["price_c_kwh"])
                return {
                    "min_c_kwh": min(vals),
                    "max_c_kwh": max(vals),
                    "avg_c_kwh": round(sum(vals) / len(vals), 2),
                    "cheapest_hour": cheapest["time"],
                    "most_expensive_hour": most_expensive["time"],
                    "hours_count": len(vals),
                }

            result = {
                "current_price_c_kwh": current_price["price_c_kwh"] if current_price else None,
                "current_hour": current_price["time"] if current_price else None,
                "today": price_stats(today_prices),
                "today_prices": today_prices,
                "tomorrow_available": len(tomorrow_prices) > 0,
            }
            if tomorrow_prices:
                result["tomorrow"] = price_stats(tomorrow_prices)
                result["tomorrow_prices"] = tomorrow_prices

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_heating_status":
        try:
            time_range = arguments.get("time_range", "-1h")

            # Get latest heating optimizer decision
            flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "heating_optimizer")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"], desc: true)
"""
            results = execute_flux_query(flux)

            if not results:
                return [TextContent(type="text", text="Lämmitysoptimoinnin tilatietoja ei ole saatavilla.")]

            latest = results[0]

            tier = latest.get("tier", "UNKNOWN")
            setpoint = latest.get("setpoint")
            evu_active = latest.get("evu_active")
            boiler_steps = latest.get("boiler_steps")
            effective_target = latest.get("effective_target")
            price = latest.get("price")
            outdoor_temp = latest.get("outdoor_temp")

            boiler_label = {0: "OFF", 1: "3kW", 2: "3+6kW"}.get(
                int(boiler_steps) if boiler_steps is not None else -1, str(boiler_steps)
            )

            tier_descriptions = {
                "CHEAP": "Halpa sähkö — normaali lämmitys",
                "NORMAL": "Normaali hinta — normaali lämmitys",
                "EXPENSIVE": "Kallis sähkö — EVU-tila päällä, lämmitystä rajoitettu",
                "PRE_HEAT": "Esilämmitys — nostettu lämpötila ennen kallista jaksoa",
            }

            result = {
                "timestamp": latest.get("_time"),
                "tier": tier,
                "tier_description": tier_descriptions.get(tier, tier),
                "setpoint_c": setpoint,
                "effective_target_c": effective_target,
                "evu_active": bool(int(evu_active)) if evu_active is not None else None,
                "boiler_steps": boiler_label,
                "current_price_c_kwh": round(price, 2) if price is not None else None,
                "outdoor_temp_c": round(outdoor_temp, 1) if outdoor_temp is not None else None,
            }

            # Include history if more than just latest
            if len(results) > 1:
                history = []
                for r in results[:24]:  # last 24 entries max
                    history.append({
                        "time": r.get("_time"),
                        "tier": r.get("tier"),
                        "setpoint": r.get("setpoint"),
                        "effective_target": r.get("effective_target"),
                        "price": round(r["price"], 2) if r.get("price") is not None else None,
                    })
                result["history"] = history

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_sauna_status":
        try:
            history_days = arguments.get("history_days", 7)

            # Current sauna temperature
            flux_current = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Sauna")
  |> filter(fn: (r) => r._field == "temperature")
  |> last()
"""
            current_results = execute_flux_query(flux_current)
            current_temp = current_results[0].get("_value") if current_results else None

            # Detect if currently heating (rate of change over last 15 min)
            flux_heating = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Sauna")
  |> filter(fn: (r) => r._field == "temperature")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> derivative(unit: 1m, nonNegative: false)
  |> map(fn: (r) => ({{r with _value: if r._value > 0.05 then 1.0 else 0.0}}))
  |> mean()
"""
            heating_results = execute_flux_query(flux_heating)
            heating_ratio = heating_results[0].get("_value", 0) if heating_results else 0
            is_heating = heating_ratio >= 0.3

            # Find sauna sessions: detect heating periods by hourly derivative analysis
            flux_sessions = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{history_days}d)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Sauna")
  |> filter(fn: (r) => r._field == "temperature")
  |> aggregateWindow(every: 15m, fn: mean, createEmpty: false)
  |> sort(columns: ["_time"])
"""
            session_data = execute_flux_query(flux_sessions)

            # Detect sessions: temperature rising above 40°C then falling back
            sessions = []
            in_session = False
            session_start = None
            peak_temp = 0

            for row in session_data:
                temp = row.get("_value", 0)
                ts = row.get("_time")

                if not in_session and temp > 40:
                    in_session = True
                    session_start = ts
                    peak_temp = temp
                elif in_session:
                    if temp > peak_temp:
                        peak_temp = temp
                    if temp < 35:
                        # Session ended
                        sessions.append({
                            "start": session_start,
                            "end": ts,
                            "peak_temp_c": round(peak_temp, 1),
                        })
                        in_session = False
                        session_start = None
                        peak_temp = 0

            # If currently in a session
            if in_session and session_start:
                sessions.append({
                    "start": session_start,
                    "end": None,
                    "peak_temp_c": round(peak_temp, 1),
                    "in_progress": True,
                })

            status = "heating" if is_heating else ("hot" if current_temp and current_temp > 40 else "idle")

            result = {
                "current_temp_c": round(current_temp, 1) if current_temp is not None else None,
                "status": status,
                "is_heating": is_heating,
                "recent_sessions": sessions[-10:],  # last 10 sessions max
                "total_sessions_in_period": len(sessions),
                "history_days": history_days,
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            log.error("get_sauna_status error: %s\n%s", e, traceback.format_exc())
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_energy_cost":
        try:
            time_range = arguments.get("time_range", "-24h")
            MARGIN = 0.49   # c/kWh
            TRANSFER = 6.09  # c/kWh
            WATT_PER_LIGHT = 10
            WATT_FAN = 300

            # Heat pump: join supply_temp with compressor/aux status (matches Energy Cost dashboard)
            flux_hp_temps = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "temperature")
  |> filter(fn: (r) => r._field == "supply_temp")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
"""
            flux_hp_status = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "status")
  |> filter(fn: (r) => r._field == "compressor" or r._field == "aux_heater_3kw" or r._field == "aux_heater_6kw")
  |> toFloat()
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value", "_field"])
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
            hp_temps = execute_flux_query(flux_hp_temps)
            hp_status = execute_flux_query(flux_hp_status)

            # Build time-indexed lookup for supply temps
            temp_by_time = {r.get("_time"): r.get("_value", 35) or 35 for r in hp_temps}

            hp_kwh = 0.0
            for row in hp_status:
                comp = row.get("compressor", 0) or 0
                aux3 = row.get("aux_heater_3kw", 0) or 0
                aux6 = row.get("aux_heater_6kw", 0) or 0
                sup_t = temp_by_time.get(row.get("_time"), 35)
                # Compressor power varies with supply temp (1.77-2.27 kW)
                comp_kw = comp * (1.77 + (sup_t - 35.0) * 0.5 / 15.0)
                hp_kwh += comp_kw + aux3 * 3.0 + aux6 * 6.0  # 1h window = kWh directly

            # Lighting: mean on-state per light per hour, sum across lights (matches dashboard)
            flux_lights = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "lights" and r._field == "is_on")
  |> toFloat()
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> group(columns: ["_time"])
  |> sum()
  |> group()
"""
            light_data = execute_flux_query(flux_lights)
            light_kwh = sum((r.get("_value", 0) or 0) * WATT_PER_LIGHT / 1000.0 for r in light_data)

            # HVAC fan: assume constant power for each hour we have pressure data
            flux_fan = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "ruuvi" and (r.sensor_name == "Keittio" or r.sensor_name == "Keittiö"))
  |> filter(fn: (r) => r._field == "pressure")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
"""
            fan_data = execute_flux_query(flux_fan)
            fan_kwh = len(fan_data) * WATT_FAN / 1000.0

            # Sauna: detect heating hours via temperature derivative (matches dashboard)
            flux_sauna = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Sauna")
  |> filter(fn: (r) => r._field == "temperature")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> derivative(unit: 1m, nonNegative: false)
  |> map(fn: (r) => ({{r with _value: if r._value > 0.05 then 1.0 else 0.0}}))
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
"""
            sauna_data = execute_flux_query(flux_sauna)
            sauna_kwh = sum((r.get("_value", 0) or 0) * 6.0 for r in sauna_data)

            total_kwh = hp_kwh + light_kwh + fan_kwh + sauna_kwh

            # Average electricity price for the period
            flux_price = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "electricity" and r._field == "price_with_tax")
  |> mean()
"""
            price_data = execute_flux_query(flux_price)
            avg_price = price_data[0].get("_value", 5.0) if price_data else 5.0

            total_price_c_kwh = avg_price + MARGIN + TRANSFER
            total_cost_eur = total_kwh * total_price_c_kwh / 100.0

            result = {
                "time_range": time_range,
                "consumption_kwh": {
                    "heat_pump": round(hp_kwh, 2),
                    "lighting": round(light_kwh, 2),
                    "sauna": round(sauna_kwh, 2),
                    "hvac_fan": round(fan_kwh, 2),
                    "total": round(total_kwh, 2),
                },
                "cost": {
                    "avg_spot_price_c_kwh": round(avg_price, 2),
                    "margin_c_kwh": MARGIN,
                    "transfer_c_kwh": TRANSFER,
                    "total_price_c_kwh": round(total_price_c_kwh, 2),
                    "estimated_total_eur": round(total_cost_eur, 2),
                },
                "note": "Estimates based on component status data and assumed wattages",
            }

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            log.error("get_energy_cost error: %s\n%s", e, traceback.format_exc())
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_weather_forecast":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(WEATHER_API_URL)
                resp.raise_for_status()
                data = resp.json()

            WMO_CODES = {
                0: "Selkeää", 1: "Enimmäkseen selkeää", 2: "Puolipilvistä", 3: "Pilvistä",
                45: "Sumua", 48: "Huurretta", 51: "Kevyttä tihkua", 53: "Tihkua",
                55: "Tiheää tihkua", 61: "Kevyttä sadetta", 63: "Sadetta", 65: "Rankkasadetta",
                66: "Jäätävää tihkua", 67: "Jäätävää sadetta",
                71: "Kevyttä lumisadetta", 73: "Lumisadetta", 75: "Tiheää lumisadetta",
                77: "Lumijyväsiä", 80: "Kevyitä sadekuuroja", 81: "Sadekuuroja",
                82: "Rankkoja sadekuuroja", 85: "Lumikuuroja", 86: "Rankkoja lumikuuroja",
                95: "Ukkosta", 96: "Ukkosta ja rakeita", 99: "Ukkosta ja rankkoja rakeita",
            }

            current = data.get("current", {})
            hourly = data.get("hourly", {})
            daily = data.get("daily", {})

            result = {
                "current": {
                    "temperature": current.get("temperature_2m"),
                    "feels_like": current.get("apparent_temperature"),
                    "humidity": current.get("relative_humidity_2m"),
                    "wind_speed_ms": current.get("wind_speed_10m"),
                    "wind_direction": current.get("wind_direction_10m"),
                    "condition": WMO_CODES.get(current.get("weather_code", -1), "Tuntematon"),
                    "weather_code": current.get("weather_code"),
                },
                "hourly_next_4h": [],
                "daily_forecast": [],
            }

            times = hourly.get("time", [])
            temps = hourly.get("temperature_2m", [])
            codes = hourly.get("weather_code", [])
            precip = hourly.get("precipitation_probability", [])
            now = datetime.now(timezone.utc).isoformat()
            count = 0
            for i, t in enumerate(times):
                if t >= now and count < 4:
                    result["hourly_next_4h"].append({
                        "time": t,
                        "temperature": temps[i] if i < len(temps) else None,
                        "condition": WMO_CODES.get(codes[i] if i < len(codes) else -1, "?"),
                        "precipitation_probability": precip[i] if i < len(precip) else None,
                    })
                    count += 1

            d_times = daily.get("time", [])
            d_codes = daily.get("weather_code", [])
            d_max = daily.get("temperature_2m_max", [])
            d_min = daily.get("temperature_2m_min", [])
            d_precip = daily.get("precipitation_probability_max", [])
            for i in range(min(4, len(d_times))):
                result["daily_forecast"].append({
                    "date": d_times[i],
                    "condition": WMO_CODES.get(d_codes[i] if i < len(d_codes) else -1, "?"),
                    "temp_max": d_max[i] if i < len(d_max) else None,
                    "temp_min": d_min[i] if i < len(d_min) else None,
                    "precipitation_probability": d_precip[i] if i < len(d_precip) else None,
                })

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            log.error("get_weather_forecast error: %s\n%s", e, traceback.format_exc())
            return [TextContent(type="text", text=f"Error fetching weather: {str(e)}")]

    elif name == "get_news_headlines":
        try:
            count = min(int(arguments.get("count", 5)), 20)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(NEWS_API_URL)
                resp.raise_for_status()
                items = resp.json()

            items = items[:count]
            now = datetime.now(timezone.utc)
            headlines = []
            for item in items:
                pub = item.get("pubDate", "")
                age = ""
                if pub:
                    try:
                        pub_dt = datetime.fromisoformat(pub)
                        delta = now - pub_dt.astimezone(timezone.utc)
                        mins = int(delta.total_seconds() / 60)
                        if mins < 60:
                            age = f"{mins} min sitten"
                        elif mins < 1440:
                            age = f"{mins // 60} h sitten"
                        else:
                            age = f"{mins // 1440} pv sitten"
                    except (ValueError, TypeError):
                        age = pub
                headlines.append({
                    "title": item.get("title", ""),
                    "description": item.get("description", ""),
                    "source": item.get("source", ""),
                    "published": age or pub,
                })

            return [TextContent(type="text", text=json.dumps(headlines, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            log.error("get_news_headlines error: %s\n%s", e, traceback.format_exc())
            return [TextContent(type="text", text=f"Error fetching news: {str(e)}")]

    elif name == "get_bus_departures":
        try:
            limit = int(arguments.get("limit", 5))
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(BUS_API_URL)
                resp.raise_for_status()
                data = resp.json()

            departures = data.get("departures", [])
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            # Only include departures the user can still catch
            catchable = [d for d in departures if d.get("leaveByMs", 0) > now_ms]
            formatted = []
            for d in catchable[:limit]:
                mins_departure = round((d["departureTimeMs"] - now_ms) / 60000)
                mins_leave = round((d["leaveByMs"] - now_ms) / 60000)
                entry = {
                    "line": d.get("lineRef"),
                    "destination": d.get("destinationName"),
                    "stop": d.get("stopName"),
                    "departure_minutes": mins_departure,
                    "leave_home_minutes": mins_leave,
                    "source": d.get("source"),
                }
                if d.get("delaySeconds"):
                    entry["delay_seconds"] = d["delaySeconds"]
                if d.get("vehicleAtStop"):
                    entry["vehicle_at_stop"] = True
                if d.get("arrivalTimeMs"):
                    entry["city_arrival_minutes"] = round((d["arrivalTimeMs"] - now_ms) / 60000)
                formatted.append(entry)

            result = {"departures": formatted, "fetched_at": data.get("fetchedAt")}
            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            log.error("get_bus_departures error: %s\n%s", e, traceback.format_exc())
            return [TextContent(type="text", text=f"Error fetching bus departures: {str(e)}")]

    elif name == "get_calendar_events":
        try:
            days = min(int(arguments.get("days", 7)), 14)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{CALENDAR_API_URL}?days={days}")
                resp.raise_for_status()
                data = resp.json()

            events = data.get("events", [])

            # Group by date for readable output
            grouped: dict[str, list] = {}
            for ev in events:
                d = ev.get("date", "")
                if d not in grouped:
                    grouped[d] = []
                grouped[d].append(ev)

            result = []
            for date_str in sorted(grouped.keys()):
                day_events = []
                for ev in grouped[date_str]:
                    entry: dict[str, Any] = {"summary": ev.get("summary", "")}
                    if ev.get("allDay"):
                        entry["time"] = "koko päivä"
                    else:
                        start = ev.get("start", "")
                        end = ev.get("end", "")
                        if start:
                            entry["start"] = start[11:16] if "T" in start else start
                        if end:
                            entry["end"] = end[11:16] if "T" in end else end
                    if ev.get("location"):
                        entry["location"] = ev["location"]
                    day_events.append(entry)
                result.append({"date": date_str, "events": day_events})

            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
        except Exception as e:
            log.error("get_calendar_events error: %s\n%s", e, traceback.format_exc())
            return [TextContent(type="text", text=f"Error fetching calendar: {str(e)}")]

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

    async def health_check(request):
        return JSONResponse({"status": "ok", "service": "building-automation-mcp"})

    starlette_app = Starlette(
        debug=False,
        routes=[
            Route("/health", health_check),
            Route("/sse", handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
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
