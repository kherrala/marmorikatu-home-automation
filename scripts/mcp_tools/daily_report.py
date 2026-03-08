"""Daily report tool: aggregates weather, news, calendar, and home status."""

import json
import logging
import traceback
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from mcp.types import Tool, TextContent

from .config import (
    INFLUXDB_BUCKET, WEATHER_API_URL, NEWS_API_URL, CALENDAR_API_URL,
)
from .influxdb import execute_flux_query
from .external import WMO_CODES

log = logging.getLogger("mcp-server")

TOOLS = [
    Tool(
        name="get_daily_report",
        description="""Generate a daily briefing with weather, news, calendar, and home status.

Returns a structured summary covering:
- Current weather and today's forecast
- Top news headlines (3 items)
- Today's and tomorrow's calendar events (family + garbage collection, excludes school)
- Home status: indoor/outdoor temperatures, heat pump, air quality

This is a single tool call that replaces multiple individual calls for daily briefings.""",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),
]


async def _fetch_weather() -> dict | None:
    """Fetch current weather and forecast."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(WEATHER_API_URL)
            resp.raise_for_status()
            data = resp.json()

        current = data.get("current", {})
        daily = data.get("daily", {})

        result = {
            "temperature": current.get("temperature_2m"),
            "feels_like": current.get("apparent_temperature"),
            "condition": WMO_CODES.get(current.get("weather_code", -1), "Tuntematon"),
            "wind_speed_ms": current.get("wind_speed_10m"),
            "humidity": current.get("relative_humidity_2m"),
        }

        # Today's and tomorrow's forecast
        d_times = daily.get("time", [])
        d_codes = daily.get("weather_code", [])
        d_max = daily.get("temperature_2m_max", [])
        d_min = daily.get("temperature_2m_min", [])
        d_precip = daily.get("precipitation_probability_max", [])

        forecast = []
        for i in range(min(2, len(d_times))):
            forecast.append({
                "date": d_times[i],
                "condition": WMO_CODES.get(d_codes[i] if i < len(d_codes) else -1, "?"),
                "temp_max": d_max[i] if i < len(d_max) else None,
                "temp_min": d_min[i] if i < len(d_min) else None,
                "precipitation_probability": d_precip[i] if i < len(d_precip) else None,
            })
        result["forecast"] = forecast

        return result
    except Exception as e:
        log.error("Daily report — weather fetch failed: %s", e)
        return None


async def _fetch_news(count: int = 3) -> list[dict] | None:
    """Fetch top news headlines."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(NEWS_API_URL)
            resp.raise_for_status()
            items = resp.json()

        now = datetime.now(timezone.utc)
        headlines = []
        for item in items[:count]:
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
                "source": item.get("source", ""),
                "published": age or pub,
            })
        return headlines
    except Exception as e:
        log.error("Daily report — news fetch failed: %s", e)
        return None


async def _fetch_calendar() -> list[dict] | None:
    """Fetch today's and tomorrow's calendar events, excluding school calendar."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{CALENDAR_API_URL}?days=2")
            resp.raise_for_status()
            data = resp.json()

        events = data.get("events", [])

        # Filter out school calendar events
        events = [ev for ev in events if ev.get("type") != "school"]

        now_eet = datetime.now(timezone(timedelta(hours=2)))
        today_str = now_eet.date().isoformat()
        tomorrow_str = (now_eet.date() + timedelta(days=1)).isoformat()

        def format_event(ev: dict) -> dict:
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
            if ev.get("type") == "garbage":
                entry["type"] = "garbage"
            return entry

        today_events = [format_event(ev) for ev in events if ev.get("date") == today_str]
        tomorrow_events = [format_event(ev) for ev in events if ev.get("date") == tomorrow_str]

        result = {}
        if today_events:
            result["today"] = today_events
        if tomorrow_events:
            result["tomorrow"] = tomorrow_events
        if not result:
            result["note"] = "Ei tapahtumia tänään tai huomenna"

        return result
    except Exception as e:
        log.error("Daily report — calendar fetch failed: %s", e)
        return None


def _fetch_home_status() -> dict | None:
    """Fetch home status: temperatures, heat pump, air quality."""
    try:
        result = {}

        # Outdoor temperature from HVAC
        outdoor_q = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> filter(fn: (r) => r._field == "Ulkolampotila")
  |> last()
'''
        outdoor = execute_flux_query(outdoor_q)
        if outdoor:
            result["outdoor_temp_c"] = outdoor[0].get("_value")

        # Indoor temperatures (Ruuvi living room + kitchen)
        indoor_q = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "ruuvi")
  |> filter(fn: (r) => r.sensor_name == "Olohuone" or r.sensor_name == "Keittiö")
  |> filter(fn: (r) => r._field == "temperature")
  |> last()
'''
        indoor = execute_flux_query(indoor_q)
        for r in indoor:
            name = r.get("sensor_name", "")
            if name == "Olohuone":
                result["living_room_temp_c"] = r.get("_value")
            elif name in ("Keittiö", "Keittio"):
                result["kitchen_temp_c"] = r.get("_value")

        # Heat pump status
        hp_q = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "thermia")
  |> filter(fn: (r) => (r.data_type == "status" and (r._field == "compressor" or r._field == "aux_heater_3kw" or r._field == "aux_heater_6kw")) or (r.data_type == "temperature" and (r._field == "supply_temp" or r._field == "hotwater_temp")))
  |> last()
'''
        hp = execute_flux_query(hp_q)
        hp_data = {}
        for r in hp:
            hp_data[r.get("_field")] = r.get("_value")
        if hp_data:
            result["heat_pump"] = {
                "compressor": "päällä" if hp_data.get("compressor") == 1 else "pois",
                "aux_3kw": "päällä" if hp_data.get("aux_heater_3kw") == 1 else "pois",
                "aux_6kw": "päällä" if hp_data.get("aux_heater_6kw") == 1 else "pois",
            }
            if hp_data.get("supply_temp") is not None:
                result["heat_pump"]["supply_temp_c"] = round(hp_data["supply_temp"], 1)
            if hp_data.get("hotwater_temp") is not None:
                result["heat_pump"]["hotwater_temp_c"] = round(hp_data["hotwater_temp"], 1)

        # Air quality (kitchen Ruuvi)
        aq_q = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Keittiö")
  |> filter(fn: (r) => r._field == "co2" or r._field == "pm2_5")
  |> last()
'''
        aq = execute_flux_query(aq_q)
        aq_data = {}
        for r in aq:
            aq_data[r.get("_field")] = r.get("_value")
        if aq_data:
            result["air_quality"] = {}
            if "co2" in aq_data:
                co2 = aq_data["co2"]
                status = "hyvä" if co2 < 800 else ("kohtalainen" if co2 < 1200 else "huono")
                result["air_quality"]["co2_ppm"] = co2
                result["air_quality"]["co2_status"] = status
            if "pm2_5" in aq_data:
                pm = aq_data["pm2_5"]
                status = "hyvä" if pm < 10 else ("kohtalainen" if pm < 25 else "huono")
                result["air_quality"]["pm2_5"] = pm
                result["air_quality"]["pm2_5_status"] = status

        return result if result else None
    except Exception as e:
        log.error("Daily report — home status failed: %s", e)
        return None


async def handle_get_daily_report(arguments):
    try:
        import asyncio

        # Fetch weather, news, calendar concurrently; home status is sync
        weather_task = asyncio.ensure_future(_fetch_weather())
        news_task = asyncio.ensure_future(_fetch_news())
        calendar_task = asyncio.ensure_future(_fetch_calendar())
        home_status = _fetch_home_status()

        weather = await weather_task
        news = await news_task
        calendar = await calendar_task

        report: dict[str, Any] = {}

        if weather:
            report["weather"] = weather
        if news:
            report["news"] = news
        if calendar:
            report["calendar"] = calendar
        if home_status:
            report["home"] = home_status

        if not report:
            return [TextContent(type="text", text='{"error": "Ei saatu tietoja raporttiin"}')]

        return [TextContent(type="text", text=json.dumps(report, indent=2, ensure_ascii=False, default=str))]
    except Exception as e:
        log.error("get_daily_report error: %s\n%s", e, traceback.format_exc())
        return [TextContent(type="text", text=f"Error: {str(e)}")]


HANDLERS = {
    "get_daily_report": handle_get_daily_report,
}
