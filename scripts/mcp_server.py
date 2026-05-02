#!/usr/bin/env python3
"""
MCP Server for Building Automation Data Analytics.

Provides tools for Claude Desktop / claude-bridge to query and analyze
measurement data from InfluxDB (HVAC, room temperatures, Ruuvi sensors,
Thermia heat pump) and to control lights via the WAGO PLC over MQTT.

Uses the streamable HTTP transport at `/mcp`. SSE was previously served at
`/sse`, but its in-memory session map made every server restart fatal for
Claude Desktop's built-in client; streamable HTTP supports session
resumption so a redeploy no longer requires Claude Desktop to be toggled.
"""

import os
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
from starlette.types import Scope, Receive, Send

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


def create_starlette_app() -> Starlette:
    """Create Starlette app with streamable HTTP transport for MCP."""
    session_manager = StreamableHTTPSessionManager(
        app=app,
        event_store=None,
        json_response=False,
        stateless=False,
    )

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    async def health_check(_request):
        return JSONResponse({"status": "ok", "service": "building-automation-mcp"})

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    return Starlette(
        debug=False,
        routes=[
            Route("/health", health_check),
            Mount("/mcp", app=handle_mcp),
        ],
        lifespan=lifespan,
    )


def main():
    """Run the MCP server."""
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "3001"))

    print(f"Starting Building Automation MCP Server")
    print(f"  URL: http://{host}:{port}/mcp/")
    print(f"  Health: http://{host}:{port}/health")

    uvicorn.run(create_starlette_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
