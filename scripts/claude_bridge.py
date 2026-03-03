#!/usr/bin/env python3
"""
Claude Bridge Service — connects kiosk AI to Claude API with MCP tools.

Runs as an HTTP server that accepts chat requests, sends them to Claude
with MCP tool definitions, and executes tool calls against MCP servers.
Supports multiple MCP servers simultaneously — tools from all connected
servers are aggregated and presented to Claude. Servers that are offline
are retried automatically.

Also provides a /tts endpoint for server-side Finnish speech synthesis
that respects native device volume (unlike browser speechSynthesis on iOS).
"""

import os
import io
import json
import logging
import asyncio
from contextlib import asynccontextmanager

import anthropic
import edge_tts
import uvicorn
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Configuration
MCP_URLS_RAW = os.environ.get("MCP_URLS", os.environ.get("MCP_URL", "http://localhost:3001/sse"))
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
MAX_TOOL_ITERATIONS = int(os.environ.get("MAX_TOOL_ITERATIONS", "10"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "300"))
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "3002"))
TTS_VOICE = os.environ.get("TTS_VOICE", "fi-FI-NooraNeural")

WEEKDAYS_FI = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai", "sunnuntai"]


def get_system_prompt() -> str:
    """Build system prompt with current date and time."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=2)))  # EET
    weekday = WEEKDAYS_FI[now.weekday()]
    date_str = f"{weekday} {now.day}.{now.month}.{now.year}"
    time_str = f"{now.hour}:{now.minute:02d}"
    return (
        f"Olet kotiautomaatioavustaja Marmorikadun omakotitalossa. "
        f"Nyt on {date_str}, kello {time_str}. "
        f"Vastaa aina lyhyesti suomeksi (1–3 lausetta). "
        f"Vastauksesi luetaan ääneen, joten älä käytä markdown-muotoilua, listoja tai erikoismerkkejä. "
        f"Käytä työkaluja hakeaksesi ajantasaiset tiedot ennen vastaamista."
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("claude-bridge")

# Parse comma-separated MCP URLs
mcp_urls: list[str] = [u.strip() for u in MCP_URLS_RAW.split(",") if u.strip()]

# Per-server state: url → { session, tools_claude, tool_names }
_servers: dict[str, dict] = {}
_lock = asyncio.Lock()
_tasks: list[asyncio.Task] = []
claude_client: anthropic.AsyncAnthropic | None = None


def _aggregated_tools() -> list[dict]:
    """Return combined Claude-format tools from all connected servers."""
    tools = []
    for info in _servers.values():
        tools.extend(info["tools_claude"])
    return tools


def _find_session(tool_name: str) -> ClientSession | None:
    """Find the MCP session that owns a given tool."""
    for info in _servers.values():
        if tool_name in info["tool_names"]:
            return info["session"]
    return None


async def mcp_connection_loop(url: str):
    """Background task: maintain persistent connection to one MCP server."""
    retry_delay = 5  # seconds, grows with backoff

    while True:
        try:
            log.info("Connecting to MCP server at %s ...", url)
            async with sse_client(url) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools_result = await asyncio.wait_for(session.list_tools(), timeout=10)

                    tools_claude = [
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "input_schema": t.inputSchema or {"type": "object", "properties": {}},
                        }
                        for t in tools_result.tools
                    ]
                    tool_names = {t.name for t in tools_result.tools}

                    async with _lock:
                        _servers[url] = {
                            "session": session,
                            "tools_claude": tools_claude,
                            "tool_names": tool_names,
                        }

                    log.info("MCP %s — %d tools:", url, len(tools_claude))
                    for t in tools_result.tools:
                        log.info("  • %s", t.name)

                    retry_delay = 5  # reset on successful connection

                    # Keep alive until connection drops
                    while True:
                        await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("MCP %s lost (%s), reconnecting in %ds...", url, e, retry_delay)
            async with _lock:
                _servers.pop(url, None)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)  # backoff up to 60s


async def run_agentic_loop(messages: list[dict], tools: list[dict]) -> dict:
    """Run Claude agentic loop with tool execution against MCP servers."""
    all_tool_calls = []

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = await claude_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=get_system_prompt(),
            messages=messages,
            tools=tools,
        )

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_use_blocks:
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            return {"response": text, "model": CLAUDE_MODEL, "tool_calls": all_tool_calls}

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in tool_use_blocks:
            tool_name = block.name
            tool_input = block.input
            log.info("Tool call [%d]: %s(%s)", iteration + 1, tool_name, json.dumps(tool_input, ensure_ascii=False))
            all_tool_calls.append({"tool": tool_name, "input": tool_input})

            session = _find_session(tool_name)
            if not session:
                result_text = f"Error: tool '{tool_name}' not available (MCP server offline?)"
                log.error("Tool routing error: %s", result_text)
            else:
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

    text = "Anteeksi, en saanut vastausta valmiiksi ajoissa."
    return {"response": text, "model": CLAUDE_MODEL, "tool_calls": all_tool_calls}


async def chat_endpoint(request: Request) -> JSONResponse:
    """POST /chat — run Claude agentic loop with MCP tools."""
    tools = _aggregated_tools()

    if not tools:
        return JSONResponse({"error": "No MCP servers connected"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "No messages provided"}, status_code=400)

    try:
        result = await run_agentic_loop(messages, tools)
        return JSONResponse(result)
    except anthropic.APIError as e:
        log.error("Claude API error: %s", e)
        return JSONResponse({"error": f"Claude API error: {e.message}"}, status_code=502)
    except Exception as e:
        log.error("Unexpected error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


async def tts_endpoint(request: Request) -> Response:
    """POST /tts — server-side Finnish TTS, returns audio/mpeg.

    Plays through <audio> element in the browser, which respects
    the native device volume (unlike speechSynthesis on iOS).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "No text provided"}, status_code=400)

    voice = body.get("voice", TTS_VOICE)

    try:
        communicate = edge_tts.Communicate(text, voice)
        audio_buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buf.write(chunk["data"])
        return Response(audio_buf.getvalue(), media_type="audio/mpeg")
    except Exception as e:
        log.error("TTS error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


async def health_endpoint(request: Request) -> JSONResponse:
    """GET /health — return MCP connection status."""
    servers_status = {}
    for url in mcp_urls:
        info = _servers.get(url)
        servers_status[url] = {
            "connected": info is not None,
            "tools": len(info["tools_claude"]) if info else 0,
        }
    return JSONResponse({
        "status": "ok",
        "mcp_servers": servers_status,
        "tools_count": len(_aggregated_tools()),
        "model": CLAUDE_MODEL,
    })


@asynccontextmanager
async def lifespan(app):
    """Start background MCP connections and Claude client."""
    global claude_client

    claude_client = anthropic.AsyncAnthropic()
    log.info("Claude client initialized (model: %s)", CLAUDE_MODEL)
    log.info("MCP servers: %s", mcp_urls)

    for url in mcp_urls:
        _tasks.append(asyncio.create_task(mcp_connection_loop(url)))

    yield

    for task in _tasks:
        task.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)


app = Starlette(
    routes=[
        Route("/chat", chat_endpoint, methods=["POST"]),
        Route("/tts", tts_endpoint, methods=["POST"]),
        Route("/health", health_endpoint),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=BRIDGE_PORT, log_level="info")
