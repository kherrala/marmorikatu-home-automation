"""Sauna tools: status and session detection."""

import json
import logging
import traceback

from mcp.types import Tool, TextContent

from .config import INFLUXDB_BUCKET
from .influxdb import execute_flux_query

log = logging.getLogger("mcp-server")

TOOLS = [
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
]


async def handle_get_sauna_status(arguments):
    try:
        history_days = arguments.get("history_days", 7)

        flux_current = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Sauna")
  |> filter(fn: (r) => r._field == "temperature")
  |> last()
"""
        current_results = execute_flux_query(flux_current)
        current_temp = current_results[0].get("_value") if current_results else None

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

        flux_sessions = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{history_days}d)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Sauna")
  |> filter(fn: (r) => r._field == "temperature")
  |> aggregateWindow(every: 15m, fn: mean, createEmpty: false)
  |> sort(columns: ["_time"])
"""
        session_data = execute_flux_query(flux_sessions)

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
                    sessions.append({
                        "start": session_start,
                        "end": ts,
                        "peak_temp_c": round(peak_temp, 1),
                    })
                    in_session = False
                    session_start = None
                    peak_temp = 0

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
            "recent_sessions": sessions[-10:],
            "total_sessions_in_period": len(sessions),
            "history_days": history_days,
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
    except Exception as e:
        log.error("get_sauna_status error: %s\n%s", e, traceback.format_exc())
        return [TextContent(type="text", text=f"Error: {str(e)}")]


HANDLERS = {
    "get_sauna_status": handle_get_sauna_status,
}
