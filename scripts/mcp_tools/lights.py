"""
Light-control tools.

Replaces the old `wago-webvisu-adapter` MCP server (which hit a REST API on
the now-defunct webvisu adapter). Reads come from InfluxDB (`lights`
measurement, populated by `plc_mqtt_subscriber.py`). Writes publish to the
PLC's per-light command topic `marmorikatu/light/<index>/set` — per
`../marmorikatu-plc/MQTT.md`, an empty payload toggles the light.

Light identifiers can be either the bare `Controls[]` index ("51") or the
Finnish display name from `light_labels.py` ("Biljardipöytä", "Keittiö
katto"). Substring matches are accepted as long as they're unambiguous.
"""

import json
import logging
import os
import sys
import traceback

import paho.mqtt.publish as mqtt_publish
from mcp.types import Tool, TextContent

from .config import INFLUXDB_BUCKET
from .influxdb import execute_flux_query

# Importing from the project-root scripts/ directory. mcp_server.py adds
# /app to sys.path; in the production image, light_labels.py sits beside
# mcp_server.py at /app/light_labels.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from light_labels import LIGHT_LABELS, find_light_index, floor_name  # noqa: E402

log = logging.getLogger("mcp-server")

MQTT_BROKER = os.environ.get("MQTT_BROKER", "freenas.kherrala.fi")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "marmorikatu")


TOOLS = [
    Tool(
        name="list_lights",
        description=(
            "List all controllable lights in the Marmorikatu home automation "
            "system with their current on/off state, Finnish name, floor "
            "(0=Kellari, 1=Alakerta, 2=Yläkerta, missing=outdoor), and "
            "Controls index. Use this to discover valid identifiers before "
            "calling toggle_light or get_light_status."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="get_light_status",
        description=(
            "Get the current on/off state of one light. The `light` parameter "
            "may be its Controls index ('51') or its Finnish name ('Biljardipöytä', "
            "'Keittiö katto'). Substrings are accepted if unambiguous."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "light": {
                    "type": "string",
                    "description": "Controls index or Finnish name of the light",
                },
            },
            "required": ["light"],
        },
    ),
    Tool(
        name="toggle_light",
        description=(
            "Toggle a light on/off by publishing to the PLC's command topic "
            "`marmorikatu/light/<index>/set`. The `light` parameter may be "
            "its Controls index or Finnish name. The command is fire-and-"
            "forget; the resulting state will appear in the next round of "
            "`marmorikatu/lights` (within ~13 s) and can be confirmed with "
            "get_light_status."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "light": {
                    "type": "string",
                    "description": "Controls index or Finnish name of the light",
                },
            },
            "required": ["light"],
        },
    ),
]


def _latest_states():
    """Return {controls_index: 0|1} for every light with a recent reading."""
    flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "lights" and r._field == "is_on")
  |> filter(fn: (r) => r.switch_type == "primary")
  |> last()
  |> keep(columns: ["light_id", "_value"])
"""
    rows = execute_flux_query(flux)
    out = {}
    for r in rows:
        light_id = r.get("light_id")
        if light_id is None:
            continue
        try:
            idx = int(light_id)
        except (TypeError, ValueError):
            continue
        out[idx] = int(r.get("_value") or 0)
    return out


async def handle_list_lights(arguments):
    try:
        states = _latest_states()
        lights = []
        for idx, (name, floor) in sorted(LIGHT_LABELS.items()):
            lights.append({
                "id": idx,
                "name": name,
                "floor": floor,
                "floor_name": floor_name(floor),
                "is_on": states.get(idx),
            })
        return [TextContent(type="text", text=json.dumps(
            {"lights": lights, "count": len(lights)},
            ensure_ascii=False, indent=2,
        ))]
    except Exception as e:
        log.error("list_lights error: %s\n%s", e, traceback.format_exc())
        return [TextContent(type="text", text=f"Error: {e}")]


async def handle_get_light_status(arguments):
    query = arguments.get("light")
    if not query:
        return [TextContent(type="text",
                            text='{"error": "light parameter is required"}')]
    try:
        idx = find_light_index(query)
    except LookupError as e:
        return [TextContent(type="text",
                            text=json.dumps({"error": str(e)}, ensure_ascii=False))]

    try:
        states = _latest_states()
        name, floor = LIGHT_LABELS[idx]
        result = {
            "id": idx,
            "name": name,
            "floor": floor,
            "floor_name": floor_name(floor),
            "is_on": states.get(idx),
        }
        return [TextContent(type="text",
                            text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as e:
        log.error("get_light_status error: %s\n%s", e, traceback.format_exc())
        return [TextContent(type="text", text=f"Error: {e}")]


async def handle_toggle_light(arguments):
    query = arguments.get("light")
    if not query:
        return [TextContent(type="text",
                            text='{"error": "light parameter is required"}')]
    try:
        idx = find_light_index(query)
    except LookupError as e:
        return [TextContent(type="text",
                            text=json.dumps({"error": str(e)}, ensure_ascii=False))]

    name, floor = LIGHT_LABELS[idx]
    topic = f"{MQTT_TOPIC_PREFIX}/light/{idx}/set"
    try:
        # Per ../marmorikatu-plc/MQTT.md, an empty payload to this topic
        # toggles the corresponding Controls[] entry.
        mqtt_publish.single(
            topic=topic,
            payload=b"",
            qos=1,
            retain=False,
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            client_id="marmorikatu-mcp-toggle",
        )
        log.info("toggled light %d (%s) via %s", idx, name, topic)
        result = {
            "id": idx,
            "name": name,
            "topic": topic,
            "status": "command sent — verify with get_light_status",
        }
        return [TextContent(type="text",
                            text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as e:
        log.error("toggle_light error: %s\n%s", e, traceback.format_exc())
        return [TextContent(type="text", text=f"Error: {e}")]


HANDLERS = {
    "list_lights": handle_list_lights,
    "get_light_status": handle_get_light_status,
    "toggle_light": handle_toggle_light,
}
