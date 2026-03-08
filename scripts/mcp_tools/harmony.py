"""Logitech Harmony Hub tools: list and control AV activities and devices."""

import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp.types import Tool, TextContent

from .config import HARMONY_HUB_HOST

log = logging.getLogger("mcp-server")


@asynccontextmanager
async def _hub_client():
    """Connect to the Harmony Hub, yield the client, then close."""
    if not HARMONY_HUB_HOST:
        raise ValueError("HARMONY_HUB_HOST env var is not set")
    try:
        from aioharmony.harmonyapi import HarmonyAPI
    except ImportError:
        raise RuntimeError("aioharmony is not installed")

    client = HarmonyAPI(ip_address=HARMONY_HUB_HOST)
    connected = await client.connect()
    if not connected:
        raise ConnectionError(f"Could not connect to Harmony Hub at {HARMONY_HUB_HOST}")
    try:
        yield client
    finally:
        await client.close()


def _get_activities(config: dict) -> list[dict]:
    return [
        {"id": a["id"], "name": a["label"]}
        for a in config.get("activity", [])
        if a.get("id") != "-1"
    ]


def _find_activity(config: dict, name_or_id: str) -> dict | None:
    for a in config.get("activity", []):
        if a["id"] == name_or_id or a["label"].lower() == name_or_id.lower():
            return a
    return None


def _get_devices(config: dict) -> list[dict]:
    return [{"id": d["id"], "name": d["label"]} for d in config.get("device", [])]


def _find_device(config: dict, name_or_id: str) -> dict | None:
    for d in config.get("device", []):
        if d["id"] == name_or_id or d["label"].lower() == name_or_id.lower():
            return d
    return None


def _get_commands(device: dict) -> list[dict]:
    commands = []
    for group in device.get("controlGroup", []):
        for func in group.get("function", []):
            commands.append({"name": func["name"], "group": group["name"]})
    return commands


TOOLS = [
    Tool(
        name="harmony_list_activities",
        description="List all configured Harmony Hub activities (e.g. 'Watch TV', 'Listen to Music'). Returns activity names and IDs.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="harmony_current_activity",
        description="Get the currently active Harmony Hub activity.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="harmony_start_activity",
        description="Start a Harmony Hub activity by name or ID. Use harmony_list_activities to see what's available.",
        inputSchema={
            "type": "object",
            "properties": {
                "activity": {
                    "type": "string",
                    "description": "Activity name (e.g. 'Watch TV') or activity ID",
                }
            },
            "required": ["activity"],
        },
    ),
    Tool(
        name="harmony_power_off",
        description="Turn off all devices controlled by the Harmony Hub.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="harmony_list_devices",
        description="List all devices configured in the Harmony Hub (TV, amplifier, etc.).",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="harmony_list_commands",
        description="List all available commands for a specific device (e.g. VolumeUp, Mute, InputHDMI1). Use harmony_list_devices to get device names.",
        inputSchema={
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Device name or ID",
                }
            },
            "required": ["device"],
        },
    ),
    Tool(
        name="harmony_send_command",
        description="Send a command to a device (e.g. VolumeUp, Mute). Use harmony_list_commands to see what commands a device supports.",
        inputSchema={
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Device name or ID",
                },
                "command": {
                    "type": "string",
                    "description": "Command name (e.g. 'VolumeUp', 'Mute', 'InputHDMI1')",
                },
            },
            "required": ["device", "command"],
        },
    ),
]


async def _list_activities(args: dict[str, Any]) -> list[TextContent]:
    try:
        async with _hub_client() as client:
            activities = _get_activities(client.config)
        if not activities:
            return [TextContent(type="text", text="No activities configured.")]
        lines = [f"- {a['name']} (id: {a['id']})" for a in activities]
        return [TextContent(type="text", text="Harmony activities:\n" + "\n".join(lines))]
    except Exception as e:
        log.exception("harmony_list_activities failed")
        return [TextContent(type="text", text=f"Error: {e}")]


async def _current_activity(args: dict[str, Any]) -> list[TextContent]:
    try:
        async with _hub_client() as client:
            activity_id, activity_name = await client.get_current_activity()
        if str(activity_id) == "-1" or activity_name == "PowerOff":
            return [TextContent(type="text", text="All devices are off.")]
        return [TextContent(type="text", text=f"Current activity: {activity_name} (id: {activity_id})")]
    except Exception as e:
        log.exception("harmony_current_activity failed")
        return [TextContent(type="text", text=f"Error: {e}")]


async def _start_activity(args: dict[str, Any]) -> list[TextContent]:
    name_or_id = args.get("activity", "")
    try:
        async with _hub_client() as client:
            act = _find_activity(client.config, name_or_id)
            if act is None:
                available = ", ".join(a["name"] for a in _get_activities(client.config))
                return [TextContent(type="text", text=f"Activity '{name_or_id}' not found. Available: {available}")]
            result = await client.start_activity(act["id"])
        if result:
            return [TextContent(type="text", text=f"Started: {act['name']}")]
        return [TextContent(type="text", text=f"Failed to start activity: {act['name']}")]
    except Exception as e:
        log.exception("harmony_start_activity failed")
        return [TextContent(type="text", text=f"Error: {e}")]


async def _power_off(args: dict[str, Any]) -> list[TextContent]:
    try:
        async with _hub_client() as client:
            await client.power_off()
        return [TextContent(type="text", text="All devices powered off.")]
    except Exception as e:
        log.exception("harmony_power_off failed")
        return [TextContent(type="text", text=f"Error: {e}")]


async def _list_devices(args: dict[str, Any]) -> list[TextContent]:
    try:
        async with _hub_client() as client:
            devices = _get_devices(client.config)
        if not devices:
            return [TextContent(type="text", text="No devices configured.")]
        lines = [f"- {d['name']} (id: {d['id']})" for d in devices]
        return [TextContent(type="text", text="Harmony devices:\n" + "\n".join(lines))]
    except Exception as e:
        log.exception("harmony_list_devices failed")
        return [TextContent(type="text", text=f"Error: {e}")]


async def _list_commands(args: dict[str, Any]) -> list[TextContent]:
    device_name = args.get("device", "")
    try:
        async with _hub_client() as client:
            dev = _find_device(client.config, device_name)
            if dev is None:
                available = ", ".join(d["name"] for d in _get_devices(client.config))
                return [TextContent(type="text", text=f"Device '{device_name}' not found. Available: {available}")]
            # find full device entry in config for controlGroup
            full_dev = next(
                (d for d in client.config.get("device", []) if d["id"] == dev["id"]),
                {},
            )
            commands = _get_commands(full_dev)
        if not commands:
            return [TextContent(type="text", text=f"No commands found for {dev['name']}.")]
        by_group: dict[str, list[str]] = {}
        for c in commands:
            by_group.setdefault(c["group"], []).append(c["name"])
        lines = [f"\n{group}:\n" + "\n".join(f"  - {cmd}" for cmd in cmds) for group, cmds in by_group.items()]
        return [TextContent(type="text", text=f"Commands for {dev['name']}:" + "".join(lines))]
    except Exception as e:
        log.exception("harmony_list_commands failed")
        return [TextContent(type="text", text=f"Error: {e}")]


async def _send_command(args: dict[str, Any]) -> list[TextContent]:
    device_name = args.get("device", "")
    command = args.get("command", "")
    try:
        from aioharmony.harmonyapi import SendCommandList

        async with _hub_client() as client:
            dev = _find_device(client.config, device_name)
            if dev is None:
                available = ", ".join(d["name"] for d in _get_devices(client.config))
                return [TextContent(type="text", text=f"Device '{device_name}' not found. Available: {available}")]
            await client.send_command(
                SendCommandList(
                    send_list=[
                        {
                            "device": dev["id"],
                            "command": command,
                            "type": "IRCommandType",
                            "delay": 0,
                        }
                    ]
                )
            )
        return [TextContent(type="text", text=f"Sent '{command}' to {dev['name']}.")]
    except Exception as e:
        log.exception("harmony_send_command failed")
        return [TextContent(type="text", text=f"Error: {e}")]


HANDLERS = {
    "harmony_list_activities": _list_activities,
    "harmony_current_activity": _current_activity,
    "harmony_start_activity": _start_activity,
    "harmony_power_off": _power_off,
    "harmony_list_devices": _list_devices,
    "harmony_list_commands": _list_commands,
    "harmony_send_command": _send_command,
}
