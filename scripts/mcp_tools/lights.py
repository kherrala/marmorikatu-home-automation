"""
Light-control tools.

Replaces the old `wago-webvisu-adapter` MCP server (which hit a REST API on
the now-defunct webvisu adapter). Reads come from InfluxDB (`lights`
measurement, populated by `plc_mqtt_subscriber.py`). Writes publish to the
PLC's per-light command topic `marmorikatu/light/<index>/set` with payload
`"true"` or `"false"` (matching the manual command form
`mqttx pub -h freenas.kherrala.fi -t 'marmorikatu/light/8/set' -m 'true'`).

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
        name="set_light",
        description=(
            "Turn a light explicitly on or off. Publishes 'true' or 'false' "
            "to `marmorikatu/light/<index>/set`. The `light` parameter may be "
            "its Controls index or Finnish name. The new state will appear "
            "in `marmorikatu/lights` within ~13 s and is confirmable with "
            "get_light_status."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "light": {
                    "type": "string",
                    "description": "Controls index or Finnish name of the light",
                },
                "on": {
                    "type": "boolean",
                    "description": "True to turn on, false to turn off",
                },
            },
            "required": ["light", "on"],
        },
    ),
    Tool(
        name="set_all_lights",
        description=(
            "Turn every known light on or off in one batch. Use for 'sammuta "
            "kaikki valot' / 'turn everything off'. Publishes to every "
            "Controls index in the LIGHT_LABELS table over a single MQTT "
            "connection."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "on": {"type": "boolean",
                       "description": "True for everything on, false for off"},
            },
            "required": ["on"],
        },
    ),
    Tool(
        name="set_lights_by_floor",
        description=(
            "Turn all lights on a given floor on or off. Floor is "
            "0=Kellari (basement), 1=Alakerta (ground), 2=Yläkerta "
            "(upstairs). Pass floor=null to target outdoor / unclassified "
            "lights (Autokatos, Varasto, Sisäänkäynti, etc.)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "floor": {
                    "type": ["integer", "null"],
                    "description": "0, 1, 2, or null for outdoor",
                },
                "on": {"type": "boolean"},
            },
            "required": ["floor", "on"],
        },
    ),
    Tool(
        name="set_lights_matching",
        description=(
            "Turn on/off every light whose Finnish name contains the given "
            "substring (case-insensitive). Useful for groups like 'Saareke' "
            "(8 island spots), 'Kellari' (every basement light), 'kattovalo' "
            "(every ceiling light). Returns the list of lights that matched."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Substring to match against light names",
                },
                "on": {"type": "boolean"},
            },
            "required": ["query", "on"],
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


def _publish_set(idx, on, client_id):
    """Publish 'true'/'false' to marmorikatu/light/<idx>/set."""
    topic = f"{MQTT_TOPIC_PREFIX}/light/{idx}/set"
    payload = "true" if on else "false"
    mqtt_publish.single(
        topic=topic,
        payload=payload,
        qos=1,
        retain=False,
        hostname=MQTT_BROKER,
        port=MQTT_PORT,
        client_id=client_id,
    )
    return topic, payload


def _publish_batch(indices, on, client_id):
    """Publish 'true'/'false' to a set of light indices over one connection."""
    payload = "true" if on else "false"
    msgs = [
        {
            "topic": f"{MQTT_TOPIC_PREFIX}/light/{idx}/set",
            "payload": payload,
            "qos": 1,
            "retain": False,
        }
        for idx in indices
    ]
    if msgs:
        mqtt_publish.multiple(
            msgs,
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            client_id=client_id,
        )
    return payload


async def handle_set_light(arguments):
    query = arguments.get("light")
    on = arguments.get("on")
    if not query:
        return [TextContent(type="text",
                            text='{"error": "light parameter is required"}')]
    if on is None:
        return [TextContent(type="text",
                            text='{"error": "on parameter is required"}')]
    try:
        idx = find_light_index(query)
    except LookupError as e:
        return [TextContent(type="text",
                            text=json.dumps({"error": str(e)}, ensure_ascii=False))]

    name, _ = LIGHT_LABELS[idx]
    try:
        topic, payload = _publish_set(idx, bool(on), "marmorikatu-mcp-set")
        log.info("set light %d (%s) → %s", idx, name, payload)
        result = {
            "id": idx,
            "name": name,
            "topic": topic,
            "payload": payload,
            "status": "command sent — verify with get_light_status",
        }
        return [TextContent(type="text",
                            text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as e:
        log.error("set_light error: %s\n%s", e, traceback.format_exc())
        return [TextContent(type="text", text=f"Error: {e}")]


async def handle_set_all_lights(arguments):
    on = arguments.get("on")
    if on is None:
        return [TextContent(type="text",
                            text='{"error": "on parameter is required"}')]
    try:
        indices = sorted(LIGHT_LABELS.keys())
        payload = _publish_batch(indices, bool(on), "marmorikatu-mcp-batch")
        log.info("set_all_lights → %s for %d lights", payload, len(indices))
        return [TextContent(type="text", text=json.dumps({
            "count": len(indices),
            "payload": payload,
            "status": "batch sent — verify with list_lights",
        }, ensure_ascii=False, indent=2))]
    except Exception as e:
        log.error("set_all_lights error: %s\n%s", e, traceback.format_exc())
        return [TextContent(type="text", text=f"Error: {e}")]


async def handle_set_lights_by_floor(arguments):
    floor = arguments.get("floor")
    on = arguments.get("on")
    if on is None:
        return [TextContent(type="text",
                            text='{"error": "on parameter is required"}')]
    if floor not in (0, 1, 2, None):
        return [TextContent(type="text",
                            text='{"error": "floor must be 0, 1, 2, or null"}')]
    try:
        indices = sorted(idx for idx, (_, f) in LIGHT_LABELS.items() if f == floor)
        if not indices:
            return [TextContent(type="text", text=json.dumps({
                "error": f"No lights on floor {floor}",
            }, ensure_ascii=False))]
        payload = _publish_batch(indices, bool(on), "marmorikatu-mcp-floor")
        log.info("set_lights_by_floor(%s) → %s for %d lights",
                 floor, payload, len(indices))
        return [TextContent(type="text", text=json.dumps({
            "floor": floor,
            "floor_name": floor_name(floor),
            "count": len(indices),
            "lights": [LIGHT_LABELS[i][0] for i in indices],
            "payload": payload,
            "status": "batch sent — verify with list_lights",
        }, ensure_ascii=False, indent=2))]
    except Exception as e:
        log.error("set_lights_by_floor error: %s\n%s", e, traceback.format_exc())
        return [TextContent(type="text", text=f"Error: {e}")]


async def handle_set_lights_matching(arguments):
    query = arguments.get("query")
    on = arguments.get("on")
    if not query:
        return [TextContent(type="text",
                            text='{"error": "query parameter is required"}')]
    if on is None:
        return [TextContent(type="text",
                            text='{"error": "on parameter is required"}')]
    needle = str(query).strip().lower()
    matches = [(idx, name) for idx, (name, _) in LIGHT_LABELS.items()
               if needle in name.lower()]
    if not matches:
        return [TextContent(type="text", text=json.dumps({
            "error": f"No lights match '{query}'",
        }, ensure_ascii=False))]
    try:
        indices = [idx for idx, _ in matches]
        payload = _publish_batch(indices, bool(on), "marmorikatu-mcp-match")
        log.info("set_lights_matching('%s') → %s for %d lights",
                 query, payload, len(indices))
        return [TextContent(type="text", text=json.dumps({
            "query": query,
            "count": len(indices),
            "lights": [name for _, name in matches],
            "payload": payload,
            "status": "batch sent — verify with list_lights",
        }, ensure_ascii=False, indent=2))]
    except Exception as e:
        log.error("set_lights_matching error: %s\n%s", e, traceback.format_exc())
        return [TextContent(type="text", text=f"Error: {e}")]


HANDLERS = {
    "list_lights": handle_list_lights,
    "get_light_status": handle_get_light_status,
    "set_light": handle_set_light,
    "set_all_lights": handle_set_all_lights,
    "set_lights_by_floor": handle_set_lights_by_floor,
    "set_lights_matching": handle_set_lights_matching,
}
