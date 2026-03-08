#!/usr/bin/env python3
"""
MCP Server for Building Automation Data Analytics.

Provides tools for Claude Desktop to query and analyze measurement data
from InfluxDB (HVAC, room temperatures, Ruuvi sensors, Thermia heat pump).

Runs as an SSE (Server-Sent Events) server for URL-based MCP integration.
"""

import os
import logging
from typing import Any

import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse

from mcp_tools import ALL_TOOLS, ALL_HANDLERS

log = logging.getLogger("mcp-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Server("building-automation")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return ALL_TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls by dispatching to the appropriate handler."""
    handler = ALL_HANDLERS.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return await handler(arguments)


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

    starlette_app = create_starlette_app()
    uvicorn.run(starlette_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
