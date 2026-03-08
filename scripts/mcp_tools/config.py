"""Configuration and constants for MCP tools."""

import os

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")
WEATHER_API_URL = os.environ.get("WEATHER_API_URL", "http://weather:3020/api/weather")
NEWS_API_URL = os.environ.get("NEWS_API_URL", "http://news:3021/api/news")
BUS_API_URL = os.environ.get("BUS_API_URL", "http://host.docker.internal:3010/api/departures")
CALENDAR_API_URL = os.environ.get("CALENDAR_API_URL", "http://calendar:3022/api/calendar")
HARMONY_HUB_HOST = os.environ.get("HARMONY_HUB_HOST", "")
