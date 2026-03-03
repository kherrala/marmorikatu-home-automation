#!/usr/bin/env python3
"""
Claude Bridge Service — connects kiosk AI to Claude API with MCP tools.

Runs as an HTTP server that accepts chat requests, sends them to Claude
with MCP tool definitions, and executes tool calls against the MCP server.
This allows Claude to dynamically query building automation data.
"""

import os
import json
import logging
import asyncio
from contextlib import asynccontextmanager

import anthropic
import uvicorn
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse

# Configuration
MCP_URL = os.environ.get("MCP_URL", "http://localhost:3001/sse")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_TOOL_ITERATIONS = int(os.environ.get("MAX_TOOL_ITERATIONS", "10"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "300"))
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "3002"))

SYSTEM_PROMPT = (
    "Olet kotiautomaatioavustaja Marmorikadun omakotitalossa. "
    "Vastaa aina lyhyesti suomeksi (1–3 lausetta). "
    "Vastauksesi luetaan ääneen, joten älä käytä markdown-muotoilua, listoja tai erikoismerkkejä. "
    "Käytä työkaluja hakeaksesi ajantasaiset tiedot ennen vastaamista."
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("claude-bridge")

# Shared state — persistent MCP connection managed by background task
mcp_session: ClientSession | None = None
mcp_tools: list = []
mcp_tools_claude: list[dict] = []  # Pre-converted for Claude API
claude_client: anthropic.AsyncAnthropic | None = None
_mcp_task: asyncio.Task | None = None


def convert_mcp_tools_to_claude(tools) -> list[dict]:
    """Convert MCP tool definitions to Claude API tool format."""
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
        }
        for tool in tools
    ]


async def mcp_connection_loop():
    """Background task: maintain persistent MCP connection with reconnection."""
    global mcp_session, mcp_tools, mcp_tools_claude

    while True:
        try:
            log.info("Connecting to MCP server at %s ...", MCP_URL)
            async with sse_client(MCP_URL) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    tools_result = await asyncio.wait_for(session.list_tools(), timeout=10)
                    mcp_tools = tools_result.tools
                    mcp_tools_claude = convert_mcp_tools_to_claude(mcp_tools)
                    mcp_session = session
                    log.info("MCP connected — %d tools available:", len(mcp_tools))
                    for t in mcp_tools:
                        log.info("  • %s", t.name)

                    # Keep alive until connection drops
                    while True:
                        await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("MCP connection lost (%s), reconnecting in 5s...", e)
            mcp_session = None
            mcp_tools = []
            mcp_tools_claude = []
            await asyncio.sleep(5)


async def run_agentic_loop(session: ClientSession, messages: list[dict], tools: list[dict]) -> dict:
    """Run Claude agentic loop with tool execution against MCP server."""
    all_tool_calls = []

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = await claude_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=tools,
        )

        # Check if Claude wants to use tools
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_use_blocks:
            # No tool calls — extract final text
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            return {"response": text, "model": CLAUDE_MODEL, "tool_calls": all_tool_calls}

        # Append assistant message with all content blocks
        messages.append({"role": "assistant", "content": response.content})

        # Execute each tool call against MCP server
        tool_results = []
        for block in tool_use_blocks:
            tool_name = block.name
            tool_input = block.input
            log.info("Tool call [%d]: %s(%s)", iteration + 1, tool_name, json.dumps(tool_input, ensure_ascii=False))
            all_tool_calls.append({"tool": tool_name, "input": tool_input})

            try:
                result = await session.call_tool(tool_name, tool_input)
                result_text = "\n".join(
                    c.text for c in result.content if hasattr(c, "text")
                )
                log.info("Tool result [%d]: %s → %d chars", iteration + 1, tool_name, len(result_text))
            except Exception as e:
                result_text = f"Error calling {tool_name}: {e}"
                log.error("Tool error: %s", result_text)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
            })

        messages.append({"role": "user", "content": tool_results})

    # Reached max iterations
    text = "Anteeksi, en saanut vastausta valmiiksi ajoissa."
    return {"response": text, "model": CLAUDE_MODEL, "tool_calls": all_tool_calls}


async def chat_endpoint(request: Request) -> JSONResponse:
    """POST /chat — run Claude agentic loop with MCP tools."""
    session = mcp_session
    tools = mcp_tools_claude

    if not session or not tools:
        return JSONResponse({"error": "MCP not connected"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "No messages provided"}, status_code=400)

    try:
        result = await run_agentic_loop(session, messages, tools)
        return JSONResponse(result)
    except anthropic.APIError as e:
        log.error("Claude API error: %s", e)
        return JSONResponse({"error": f"Claude API error: {e.message}"}, status_code=502)
    except Exception as e:
        log.error("Unexpected error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


async def health_endpoint(request: Request) -> JSONResponse:
    """GET /health — return MCP connection status."""
    return JSONResponse({
        "status": "ok",
        "mcp_connected": mcp_session is not None,
        "mcp_url": MCP_URL,
        "tools_count": len(mcp_tools),
        "model": CLAUDE_MODEL,
    })


@asynccontextmanager
async def lifespan(app):
    """Start background MCP connection and Claude client."""
    global claude_client, _mcp_task

    claude_client = anthropic.AsyncAnthropic()
    log.info("Claude client initialized (model: %s)", CLAUDE_MODEL)

    _mcp_task = asyncio.create_task(mcp_connection_loop())

    yield

    _mcp_task.cancel()
    try:
        await _mcp_task
    except asyncio.CancelledError:
        pass


app = Starlette(
    routes=[
        Route("/chat", chat_endpoint, methods=["POST"]),
        Route("/health", health_endpoint),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=BRIDGE_PORT, log_level="info")
