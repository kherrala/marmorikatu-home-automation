"""External service tools: weather, news, bus departures, calendar."""

import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Any

import httpx
from mcp.types import Tool, TextContent

from .config import WEATHER_API_URL, NEWS_API_URL, BUS_API_URL, CALENDAR_API_URL

log = logging.getLogger("mcp-server")

TOOLS = [
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
        description="Get upcoming family calendar events and garbage collection schedule. Returns event summaries, times, locations grouped by date.",
        inputSchema={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days ahead to fetch (default 90, max 90)",
                    "default": 90
                }
            },
            "required": []
        }
    ),
]

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


async def handle_get_weather_forecast(arguments):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(WEATHER_API_URL)
            resp.raise_for_status()
            data = resp.json()

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


async def handle_get_news_headlines(arguments):
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


async def handle_get_bus_departures(arguments):
    try:
        limit = int(arguments.get("limit", 5))
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(BUS_API_URL)
            resp.raise_for_status()
            data = resp.json()

        departures = data.get("departures", [])
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

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


async def handle_get_calendar_events(arguments):
    try:
        days = min(int(arguments.get("days", 90)), 90)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{CALENDAR_API_URL}?days={days}")
            resp.raise_for_status()
            data = resp.json()

        events = data.get("events", [])

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


HANDLERS = {
    "get_weather_forecast": handle_get_weather_forecast,
    "get_news_headlines": handle_get_news_headlines,
    "get_bus_departures": handle_get_bus_departures,
    "get_calendar_events": handle_get_calendar_events,
}
