"""HVAC tools: heat recovery, freezing probability, room temps, air quality, indoor/outdoor comparison."""

import json

from mcp.types import Tool, TextContent

from .config import INFLUXDB_BUCKET
from .schema import SCHEMA
from .influxdb import execute_flux_query

TOOLS = [
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
]


async def handle_get_heat_recovery_efficiency(arguments):
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


async def handle_get_freezing_probability(arguments):
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

        margin = exhaust - dew_point

        dew_raw = (5.0 - margin) / 5.0
        dew_risk = max(0.0, min(1.0, dew_raw))
        dew_score = dew_risk * 60.0

        temp_raw = (-5.0 - outdoor) / 20.0
        temp_risk = max(0.0, min(1.0, temp_raw))
        temp_score = temp_risk * 25.0

        exh_raw = (5.0 - exhaust) / 5.0
        exh_risk = max(0.0, min(1.0, exh_raw))
        exh_score = exh_risk * 15.0

        total = dew_score + temp_score + exh_score

        if exhaust < 0.0:
            probability = 60.0
        elif margin < 0.0:
            probability = max(80.0, min(95.0, total))
        else:
            probability = min(95.0, total)

        probability = round(probability, 1)

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


async def handle_get_room_temperatures(arguments):
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

        for room in rooms:
            desc = SCHEMA["measurements"]["rooms"]["fields"].get(room, {}).get("description", "")
            rooms[room]["description"] = desc

        return [TextContent(type="text", text=json.dumps(rooms, indent=2, ensure_ascii=False, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def handle_get_air_quality(arguments):
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


async def handle_compare_indoor_outdoor(arguments):
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


HANDLERS = {
    "get_heat_recovery_efficiency": handle_get_heat_recovery_efficiency,
    "get_freezing_probability": handle_get_freezing_probability,
    "get_room_temperatures": handle_get_room_temperatures,
    "get_air_quality": handle_get_air_quality,
    "compare_indoor_outdoor": handle_compare_indoor_outdoor,
}
