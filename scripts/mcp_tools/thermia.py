"""Thermia heat pump tools: status, temperatures, COP, brine, hotwater, registers, compressor duty."""

import json

from mcp.types import Tool, TextContent

from .config import INFLUXDB_BUCKET
from .schema import SCHEMA
from .influxdb import execute_flux_query

TOOLS = [
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
]


async def handle_get_thermia_status(arguments):
    try:
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


async def handle_get_thermia_temperatures(arguments):
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


async def handle_get_heatpump_cop(arguments):
    time_range = arguments.get("time_range", "-24h")
    aggregation = arguments.get("aggregation", "5m")

    temp_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "temperature")
  |> filter(fn: (r) => r._field == "supply_temp" or r._field == "return_temp")
  |> aggregateWindow(every: {aggregation}, fn: mean, createEmpty: false)
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
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

        status_by_time = {r["_time"]: r for r in status_results}

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


async def handle_get_brine_circuit(arguments):
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

        brine_min_t = -15.0
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


async def handle_get_hotwater_analysis(arguments):
    time_range = arguments.get("time_range", "-24h")

    temp_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "temperature")
  |> filter(fn: (r) => r._field == "hotwater_temp")
'''
    status_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "status")
  |> filter(fn: (r) => r._field == "hotwater_production")
'''
    settings_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "setting")
  |> filter(fn: (r) => r._field == "hotwater_start_temp" or r._field == "hotwater_stop_temp")
  |> last()
'''
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

        settings = {r["_field"]: r["_value"] for r in settings_results}
        start_temp = settings.get("hotwater_start_temp")
        stop_temp = settings.get("hotwater_stop_temp")

        temps = [r["_value"] for r in temp_results if r.get("_value") is not None]
        current_temp = temps[-1] if temps else None

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


async def handle_get_thermia_register_data(arguments):
    data_type = arguments.get("data_type")
    fields = arguments.get("fields")
    time_range = arguments.get("time_range", "-24h")
    aggregation = arguments.get("aggregation")

    valid_types = ["temperature", "status", "alarm", "performance", "runtime", "setting"]
    if data_type not in valid_types:
        return [TextContent(type="text", text=f"Invalid data_type: {data_type}. Must be one of: {', '.join(valid_types)}")]

    if fields:
        field_filter = " or ".join([f'r._field == "{f}"' for f in fields])
        field_filter = f"\n  |> filter(fn: (r) => {field_filter})"
    else:
        field_filter = ""

    if aggregation:
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

            field_stats = {}
            if results:
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


async def handle_get_compressor_duty_cycle(arguments):
    time_range = arguments.get("time_range", "-24h")

    status_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "thermia" and r.data_type == "status")
  |> filter(fn: (r) => r._field == "compressor" or r._field == "aux_heater_3kw" or r._field == "aux_heater_6kw")
'''
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

        by_field = {}
        for r in status_results:
            field = r.get("_field")
            if field not in by_field:
                by_field[field] = []
            by_field[field].append(r.get("_value", 0))

        def duty_cycle(values):
            if not values:
                return None
            on_count = sum(1 for v in values if v == 1)
            return round(on_count / len(values) * 100, 1)

        comp_values = by_field.get("compressor", [])
        comp_cycles = 0
        for i in range(1, len(comp_values)):
            if comp_values[i - 1] == 1 and comp_values[i] == 0:
                comp_cycles += 1

        rt_first = {r["_field"]: r["_value"] for r in rt_first_results}
        rt_last = {r["_field"]: r["_value"] for r in rt_last_results}

        runtime_deltas = {}
        for field in ["runtime_compressor", "runtime_3kw", "runtime_6kw", "runtime_hotwater"]:
            start = rt_first.get(field)
            end = rt_last.get(field)
            if start is not None and end is not None:
                runtime_deltas[field] = {"start_h": start, "end_h": end, "delta_h": round(end - start, 1)}

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


HANDLERS = {
    "get_thermia_status": handle_get_thermia_status,
    "get_thermia_temperatures": handle_get_thermia_temperatures,
    "get_heatpump_cop": handle_get_heatpump_cop,
    "get_brine_circuit": handle_get_brine_circuit,
    "get_hotwater_analysis": handle_get_hotwater_analysis,
    "get_thermia_register_data": handle_get_thermia_register_data,
    "get_compressor_duty_cycle": handle_get_compressor_duty_cycle,
}
