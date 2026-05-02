#!/usr/bin/env python3
"""
Claude Bridge Service — connects kiosk AI to LLM with MCP tools.

Runs as an HTTP server that accepts chat requests, sends them to a local
Ollama instance (primary) or Claude API (fallback) with MCP tool definitions,
and executes tool calls against MCP servers. Supports multiple MCP servers
simultaneously — tools from all connected servers are aggregated. Servers
that are offline are retried automatically.

Also provides a /tts endpoint for server-side Finnish speech synthesis
that respects native device volume (unlike browser speechSynthesis on iOS).
"""

import os
import io
import json
import logging
import asyncio
from contextlib import asynccontextmanager

import re
import wave
import base64
import hashlib
import struct
from collections import OrderedDict

import anyio
import anthropic
import uvicorn
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

# Configuration
MCP_URLS_RAW = os.environ.get("MCP_URLS", os.environ.get("MCP_URL", "http://localhost:3001/sse"))
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.1.36:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
MAX_TOOL_ITERATIONS = int(os.environ.get("MAX_TOOL_ITERATIONS", "10"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "300"))
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "16384"))
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "3002"))
PIPER_BINARY = os.environ.get("PIPER_BINARY", "/usr/local/piper/piper")
PIPER_MODEL  = os.environ.get("PIPER_MODEL",  "/models/fi_FI-asmo-medium.onnx")
PIPER_SPEED  = float(os.environ.get("PIPER_SPEED", "1.0"))   # <1 = slower, >1 = faster
TTS_CACHE_SIZE = int(os.environ.get("TTS_CACHE_SIZE", "64"))  # max cached audio entries

# LRU audio cache: text-hash → WAV bytes
_tts_cache: "OrderedDict[str, bytes]" = OrderedDict()

WEEKDAYS_FI = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai", "sunnuntai"]


def get_system_prompt() -> str:
    """Build system prompt with current date and time."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Europe/Helsinki"))
    weekday = WEEKDAYS_FI[now.weekday()]
    date_str = f"{weekday} {now.day}.{now.month}.{now.year}"
    time_str = f"{now.hour}:{now.minute:02d}"
    return (
        f"Olet kodin älykäs avustaja Tampereella. Nyt on {date_str}, kello {time_str}.\n"
        f"\n"
        f"TÄRKEÄÄ:\n"
        f"- Käytä AINA työkaluja tietojen hakuun. ÄLÄ KOSKAAN keksi tai arvaa tietoja.\n"
        f"- Jos työkalu ei ole käytettävissä tai kutsu epäonnistuu, sano rehellisesti ettet tiedä.\n"
        f"- ÄLÄ keksi säätietoja, uutisia, lämpötiloja tai kalenterimerkintöjä.\n"
        f"- Kerro vain se mitä työkalut palauttavat.\n"
        f"\n"
        f"Vastaa 1-3 lauseella suomeksi. ÄLÄ käytä markdown-muotoilua (ei **tähtiä**, ei #otsikoita, ei -listoja).\n"
        f"Vastauksesi luetaan ääneen — pidä ne lyhyinä ja selkeinä.\n"
        f"Käyttäjä on kotona.\n"
        f"Kellarin lämpötila on tarkoituksella alempi kuin muissa kerroksissa — se ei ole ongelma.\n"
        f"\n"
        f"Muisti:\n"
        f"- Jos käyttäjä sanoo 'muista' tai kertoo mieltymyksiä/tietoja itsestään → KUTSU remember-työkalu. Älä vain sano 'muistan' — käytä työkalua.\n"
        f"- Käytä recall-työkalua keskustelun alussa.\n"
        f"\n"
        f"Verkkohaku:\n"
        f"- Käytä 'browser_navigate' + 'browser_snapshot' hakeaksesi tietoa verkosta.\n"
        f"- Käytä hakuun: https://html.duckduckgo.com/html/?q=hakusana (ei estä headless-selainta).\n"
        f"- Käytä 'browser_snapshot' lukeaksesi sivun sisällön — se palauttaa elementit [ref=eN] tunnisteilla.\n"
        f"- Klikkaa linkkejä: 'browser_click' parametrilla {{\"ref\": \"eN\"}}. Käytä AINA browser_click navigoidaksesi linkeistä — älä vain kuvaile sivua.\n"
        f"- Selainistunto säilyy — edellinen sivu on yhä auki.\n"
        f"- Jos sivu näyttää evästebannerin, hyväksy se 'browser_click'-työkalulla.\n"
        f"- Jos sivu näyttää 'Just a moment' tai muun esteen, yritä toista sivustoa.\n"
        f"\n"
        f"Valojen ohjaus:\n"
        f"- Voit sytyttää ja sammuttaa valoja PLC:n kautta. Käytä 'list_lights' nähdäksesi kaikki valot.\n"
        f"- Yksittäinen valo: 'set_light' parametrilla {{\"light\": \"Biljardipöytä\", \"on\": true|false}}. Suomenkielinen nimi tai numero käy.\n"
        f"- Kaikki valot: 'set_all_lights' parametrilla {{\"on\": false}} sammuttaa kaiken.\n"
        f"- Kerros: 'set_lights_by_floor' parametreilla floor=0 (Kellari), 1 (Alakerta) tai 2 (Yläkerta).\n"
        f"- Ryhmä nimellä: 'set_lights_matching' parametrilla query=\"Saareke\" tai \"kattovalo\" sytyttää/sammuttaa kaikki nimeen sopivat.\n"
        f"- Tilan voi tarkistaa 'get_light_status'-työkalulla, mutta uusi tila näkyy vasta ~13 sekunnin päästä."
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


# Whitelist of tools available to Ollama. Small models can't handle too many tools.
# Claude fallback gets the full set from all MCP servers.
_OLLAMA_ALLOWED_TOOLS = {
    # Home automation — only the high-level / commonly-asked tools.
    # Deep diagnostics (heatpump COP, brine, hotwater, duty cycle, freezing
    # probability) are still available via the Claude fallback path; trimming
    # them keeps gemma4:e4b's tool-selection focused.
    "get_latest", "get_room_temperatures", "get_air_quality",
    "compare_indoor_outdoor",
    "get_thermia_status", "get_thermia_temperatures",
    "get_energy_consumption", "get_electricity_prices",
    "get_heating_status", "get_energy_cost",
    "get_sauna_status",
    # Light control (PLC via MQTT)
    "list_lights", "get_light_status", "set_light",
    "set_all_lights", "set_lights_by_floor", "set_lights_matching",
    "get_lights_optimizer_status",
    # External services
    "get_weather_forecast", "get_news_headlines",
    "get_bus_departures", "get_calendar_events", "get_daily_report",
    # Harmony Hub
    "harmony_list_activities", "harmony_current_activity",
    "harmony_start_activity", "harmony_power_off",
    # Memory
    "remember", "recall",
    # Web browsing
    "browser_navigate", "browser_navigate_back", "browser_snapshot",
    "browser_click", "browser_hover", "browser_handle_dialog",
    "browser_take_screenshot",
}


def _aggregated_tools(for_ollama: bool = False) -> list[dict]:
    """Return combined Claude-format tools from all connected servers."""
    tools = []
    for info in _servers.values():
        tools.extend(info["tools_claude"])
    if for_ollama:
        tools = [t for t in tools if t["name"] in _OLLAMA_ALLOWED_TOOLS]
    return tools


def _find_session(tool_name: str) -> ClientSession | None:
    """Find the MCP session that owns a given tool."""
    for info in _servers.values():
        if tool_name in info["tool_names"]:
            return info["session"]
    return None


# Per-URL events signalling that the connection loop should reconnect now
_reconnect_events: dict[str, asyncio.Event] = {}

# Exception types that indicate a dead MCP session
_DEAD_SESSION_ERRORS = (anyio.ClosedResourceError, anyio.EndOfStream, ConnectionError, BrokenPipeError)

TOOL_CALL_TIMEOUT = 15  # seconds — max time for a single MCP tool call
# Remind tools call Ollama internally (embeddings + LLM) and need more time
REMIND_TOOL_TIMEOUT = 60
_REMIND_TOOLS = {"remember", "recall", "consolidate", "ingest", "flush_ingest"}


def _invalidate_session(session: ClientSession):
    """Remove a dead session and signal its connection loop to reconnect."""
    for url, info in list(_servers.items()):
        if info["session"] is session:
            _servers.pop(url, None)
            log.warning("Invalidated dead MCP session for %s", url)
            ev = _reconnect_events.get(url)
            if ev:
                ev.set()
            break


async def _call_tool_safe(tool_name: str, tool_input: dict, iteration: int, caller: str) -> str:
    """Call an MCP tool with timeout, dead-session detection, and error handling."""
    # Workaround: remind's remember tool crashes when episode_type is a string
    # (expects enum, gets str from LLM). Strip it — remind auto-detects the type.
    if tool_name == "remember":
        tool_input = {k: v for k, v in tool_input.items() if k != "episode_type"}

    session = _find_session(tool_name)
    if not session:
        msg = f"Error: tool '{tool_name}' not available (MCP server offline?)"
        log.error("[%s] Tool routing error: %s", caller, msg)
        return msg
    try:
        timeout = REMIND_TOOL_TIMEOUT if tool_name in _REMIND_TOOLS else TOOL_CALL_TIMEOUT
        result = await asyncio.wait_for(
            session.call_tool(tool_name, tool_input),
            timeout=timeout,
        )
        text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
        # Include base64 image data for screenshot tools
        for c in result.content:
            if hasattr(c, "data") and hasattr(c, "mimeType"):
                text += f"\n[IMAGE:data:{c.mimeType};base64,{c.data}]"
        log.info("[%s] Tool result [%d]: %s → %d chars", caller, iteration, tool_name, len(text))
        return text
    except _DEAD_SESSION_ERRORS:
        _invalidate_session(session)
        msg = f"Error: MCP connection lost, tool '{tool_name}' unavailable"
        log.error("[%s] Dead session: %s", caller, msg)
        return msg
    except asyncio.TimeoutError:
        msg = f"Error: tool '{tool_name}' timed out after {TOOL_CALL_TIMEOUT}s"
        log.error("[%s] Tool timeout: %s", caller, msg)
        return msg
    except Exception as e:
        msg = f"Error calling {tool_name}: {type(e).__name__}: {e}"
        log.error("[%s] Tool error: %s", caller, msg)
        return msg


async def _consolidate_memory():
    """Trigger remind consolidation after a remember call (non-forced)."""
    try:
        result = await _call_tool_safe("consolidate", {}, 0, "auto-consolidate")
        log.info("Memory consolidation: %s", result[:100])
    except Exception as e:
        log.warning("Memory consolidation failed: %s", e)


def _tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-format tool defs to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


async def mcp_connection_loop(url: str):
    """Background task: maintain persistent connection to one MCP server."""
    retry_delay = 5  # seconds, grows with backoff
    _reconnect_events[url] = asyncio.Event()

    while True:
        try:
            _reconnect_events[url].clear()
            log.info("Connecting to MCP server at %s ...", url)
            # Use streamable HTTP for /mcp endpoints, SSE for /sse endpoints.
            # streamablehttp_client yields (read, write, get_session_id);
            # sse_client yields (read, write). Take the first two either way.
            transport = streamablehttp_client(url) if url.rstrip("/").endswith("/mcp") else sse_client(url)
            async with transport as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await asyncio.wait_for(session.initialize(), timeout=15)
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

                    # Keep alive: health-check every 60s, watch for forced reconnect
                    while not _reconnect_events[url].is_set():
                        try:
                            await asyncio.wait_for(
                                _reconnect_events[url].wait(), timeout=60
                            )
                        except asyncio.TimeoutError:
                            # Periodic health check — verify session is still alive
                            try:
                                await asyncio.wait_for(session.list_tools(), timeout=10)
                            except Exception:
                                log.warning("MCP %s — health check failed, reconnecting", url)
                                _servers.pop(url, None)
                                break
                    else:
                        log.info("MCP %s — forced reconnect requested", url)
                        _servers.pop(url, None)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("MCP %s lost (%s: %s), reconnecting in %ds...",
                        url, type(e).__name__, e, retry_delay)
            _servers.pop(url, None)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)  # backoff up to 60s


async def run_ollama_agentic_loop(messages: list[dict], tools: list[dict]) -> dict:
    """Run Ollama agentic loop using native /api/chat endpoint.

    Uses the native Ollama API instead of the OpenAI-compatible layer
    to ensure options like temperature and repeat_penalty are applied.
    """
    import httpx
    all_tool_calls = []
    openai_tools = _tools_to_openai(tools)

    # Send full conversation history — images only on the last message
    ollama_messages = [{"role": "system", "content": get_system_prompt()}]
    for i, m in enumerate(messages):
        msg = {"role": m["role"], "content": m["content"]}
        if m.get("images") and i == len(messages) - 1:
            msg["images"] = m["images"]
        ollama_messages.append(msg)

    async with httpx.AsyncClient(timeout=120) as client:
        for iteration in range(MAX_TOOL_ITERATIONS):
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": ollama_messages,
                    "tools": openai_tools,
                    "stream": False,
                    "think": False,
                    "options": {
                        "num_ctx": OLLAMA_NUM_CTX,
                        "temperature": 0.3,
                        "repeat_penalty": 1.0,
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data["message"]
            tool_calls_raw = msg.get("tool_calls") or []
            log.info("Ollama [%d]: tool_calls=%d, content=%d chars",
                     iteration + 1, len(tool_calls_raw), len(msg.get("content") or ""))

            if not tool_calls_raw:
                text = (msg.get("content") or "").strip()
                return {"response": text, "model": OLLAMA_MODEL, "tool_calls": all_tool_calls}

            # Append assistant message for conversation history
            ollama_messages.append(msg)

            for tc in tool_calls_raw:
                tool_name = tc["function"]["name"]
                tool_input = tc["function"].get("arguments") or {}
                if isinstance(tool_input, str):
                    try:
                        tool_input = json.loads(tool_input)
                    except (json.JSONDecodeError, TypeError):
                        tool_input = {}
                log.info("Ollama tool call [%d]: %s(%s)", iteration + 1, tool_name, json.dumps(tool_input, ensure_ascii=False))
                all_tool_calls.append({"tool": tool_name, "input": tool_input})

                result_text = await _call_tool_safe(tool_name, tool_input, iteration + 1, "Ollama")

                ollama_messages.append({
                    "role": "tool",
                    "content": result_text,
                })

    text = "Anteeksi, en saanut vastausta valmiiksi ajoissa."
    return {"response": text, "model": OLLAMA_MODEL, "tool_calls": all_tool_calls}


async def run_claude_agentic_loop(messages: list[dict], tools: list[dict]) -> dict:
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

            result_text = await _call_tool_safe(tool_name, tool_input, iteration + 1, "Claude")

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
    all_tools = _aggregated_tools()
    ollama_tools = _aggregated_tools(for_ollama=True)

    if not all_tools:
        return JSONResponse({"error": "No MCP servers connected"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "No messages provided"}, status_code=400)

    # Try Ollama first (with reduced tool set), fall back to Claude (full tools)
    result = None
    try:
        log.info("Trying Ollama (%s) with %d tools...", OLLAMA_MODEL, len(ollama_tools))
        result = await run_ollama_agentic_loop(messages, ollama_tools)
    except Exception as e:
        log.warning("Ollama failed (%s), falling back to Claude", e)

    if result is None:
        try:
            log.info("Falling back to Claude (%s) with %d tools...", CLAUDE_MODEL, len(all_tools))
            result = await run_claude_agentic_loop(messages, all_tools)
        except anthropic.APIError as e:
            log.error("Claude API error: %s", e)
            return JSONResponse({"error": f"Claude API error: {e.message}"}, status_code=502)
        except Exception as e:
            log.error("Unexpected error: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # Auto-remember: if user said "muista" but model didn't call remember, force it
    if result:
        last_msg = messages[-1].get("content", "") if messages else ""
        called_remember = any(tc.get("tool") == "remember" for tc in result.get("tool_calls", []))
        if not called_remember and re.search(r'\bmuista\b', last_msg, re.IGNORECASE):
            log.info("Auto-remember: user said 'muista' but model didn't call remember")
            await _call_tool_safe("remember", {"content": last_msg}, 0, "auto-remember")
            result["tool_calls"].append({"tool": "remember", "input": {"content": last_msg}})

        if any(tc.get("tool") == "remember" for tc in result.get("tool_calls", [])):
            asyncio.create_task(_consolidate_memory())

    return JSONResponse(result)


async def chat_stream_endpoint(request: Request) -> Response:
    """POST /chat/stream — like /chat but streams TTS as the LLM generates text.

    Returns NDJSON: each line is {"audio": "base64...", "text": "sentence"}
    followed by a final {"done": true, "response": "full text", "tool_calls": [...]}.

    The LLM runs with stream=true. As tokens arrive, sentences are detected,
    synthesized, and streamed immediately — so the user hears the first sentence
    while the LLM is still generating the rest.
    """
    import httpx

    all_tools = _aggregated_tools()
    ollama_tools = _aggregated_tools(for_ollama=True)

    if not all_tools:
        return JSONResponse({"error": "No MCP servers connected"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "No messages provided"}, status_code=400)

    openai_tools = _tools_to_openai(ollama_tools)
    # Send full conversation history — images only on the last message
    ollama_messages = [{"role": "system", "content": get_system_prompt()}]
    for i, m in enumerate(messages):
        msg = {"role": m["role"], "content": m["content"]}
        # Only include images on the last message (current request)
        if m.get("images") and i == len(messages) - 1:
            msg["images"] = m["images"]
        ollama_messages.append(msg)

    # No auto-injected browser context — the model calls browser_snapshot
    # when it needs page content. Auto-injection was confusing the model
    # into thinking it already had the data and skipping navigation/clicks.

    # Everything inside the generator so tool progress streams immediately
    async def generate():
        all_tool_calls: list[dict] = []
        sentence_buf = ""
        full_text = ""

        # Phase 1: Tool calls (non-streamed LLM, but progress events stream to client)
        async with httpx.AsyncClient(timeout=120) as client:
            for iteration in range(MAX_TOOL_ITERATIONS):
                resp = await client.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={
                        "model": OLLAMA_MODEL,
                        "messages": ollama_messages,
                        "tools": openai_tools,
                        "stream": False,
                        "think": False,
                        "options": {"num_ctx": OLLAMA_NUM_CTX, "num_predict": 500, "temperature": 0.3, "repeat_penalty": 1.0},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                msg = data["message"]
                tool_calls_raw = msg.get("tool_calls") or []

                if not tool_calls_raw:
                    break

                ollama_messages.append(msg)
                for tc in tool_calls_raw:
                    tool_name = tc["function"]["name"]
                    tool_input = tc["function"].get("arguments") or {}
                    if isinstance(tool_input, str):
                        try:
                            tool_input = json.loads(tool_input)
                        except (json.JSONDecodeError, TypeError):
                            tool_input = {}
                    log.info("Stream tool call [%d]: %s", iteration + 1, tool_name)
                    all_tool_calls.append({"tool": tool_name, "input": tool_input})
                    # Stream tool progress to client immediately
                    yield f"data: {json.dumps({'tool_use': tool_name})}\n\n"
                    result_text = await _call_tool_safe(tool_name, tool_input, iteration + 1, "Stream")
                    # Extract and stream screenshot image if present
                    img_match = re.search(r'\[IMAGE:(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)\]', result_text)
                    if img_match:
                        yield f"data: {json.dumps({'screenshot': img_match.group(1)})}\n\n"
                        result_text = re.sub(r'\n?\[IMAGE:data:image/[^\]]+\]', '', result_text)
                    # Truncate all PREVIOUS snapshot results (keep only the latest one full)
                    if tool_name == "browser_snapshot" and len(result_text) > 500:
                        for j, m in enumerate(ollama_messages[:-1]):  # skip last (current snapshot not appended yet)
                            if (m.get("role") == "tool"
                                    and isinstance(m.get("content"), str)
                                    and len(m["content"]) > 500
                                    and "Page URL:" in m["content"]
                                    and "ref=" in m["content"]):
                                url_line = next((l for l in m["content"].split("\n") if "Page URL:" in l), "")
                                ollama_messages[j] = {"role": "tool", "content": f"[Aiempi sivu: {url_line}]"}
                    ollama_messages.append({"role": "tool", "content": result_text})

                    # Auto-screenshot after page-changing browser actions only
                    if tool_name in ("browser_navigate", "browser_click", "browser_hover"):
                        log.info("Stream: auto-screenshot after %s", tool_name)
                        yield f"data: {json.dumps({'tool_use': 'browser_take_screenshot'})}\n\n"
                        ss_result = await _call_tool_safe("browser_take_screenshot", {}, iteration + 1, "Stream")
                        ss_match = re.search(r'\[IMAGE:(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)\]', ss_result)
                        if ss_match:
                            yield f"data: {json.dumps({'screenshot': ss_match.group(1)})}\n\n"

        # Phase 2: Stream text response with TTS. If the model makes tool calls
        # during streaming, execute them and go back to Phase 1 for the next round.
        keep_going = True
        while keep_going:
            log.info("Stream Phase 2: %d messages", len(ollama_messages))
            stream_tool_calls = []

            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_URL}/api/chat",
                    json={
                        "model": OLLAMA_MODEL,
                        "messages": ollama_messages,
                        "tools": openai_tools,
                        "stream": True,
                        "think": False,
                        "options": {"num_ctx": OLLAMA_NUM_CTX, "num_predict": 500, "temperature": 0.3, "repeat_penalty": 1.0},
                    },
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        msg = chunk.get("message", {})

                        if msg.get("tool_calls"):
                            stream_tool_calls.extend(msg["tool_calls"])

                        token = msg.get("content", "")
                        if not token:
                            continue

                        sentence_buf += token
                        full_text += token

                        parts = re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÅ])', sentence_buf)
                        while len(parts) > 1:
                            sentence = parts.pop(0).strip()
                            if not sentence or re.search(r'browser_\w+\(', sentence):
                                continue
                            try:
                                wav = await _piper_synthesize(sentence)
                                log.info("Stream sentence: %s", sentence[:60])
                                yield f"data: {json.dumps({'audio': base64.b64encode(wav).decode(), 'text': sentence})}\n\n"
                            except Exception as e:
                                log.error("Stream TTS error: %s", e)
                        sentence_buf = parts[0] if parts else ""

            if not stream_tool_calls:
                keep_going = False
                continue

            # Tool calls detected — execute them (same as Phase 1) then loop back
            ollama_messages.append({"role": "assistant", "content": full_text or "", "tool_calls": stream_tool_calls})
            sentence_buf = ""
            for tc in stream_tool_calls:
                tool_name = tc["function"]["name"]
                tool_input = tc["function"].get("arguments") or {}
                if isinstance(tool_input, str):
                    try: tool_input = json.loads(tool_input)
                    except: tool_input = {}
                log.info("Stream tool call (from Phase 2): %s", tool_name)
                all_tool_calls.append({"tool": tool_name, "input": tool_input})
                yield f"data: {json.dumps({'tool_use': tool_name})}\n\n"
                result_text = await _call_tool_safe(tool_name, tool_input, 0, "Stream")
                img_match = re.search(r'\[IMAGE:(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)\]', result_text)
                if img_match:
                    yield f"data: {json.dumps({'screenshot': img_match.group(1)})}\n\n"
                    result_text = re.sub(r'\n?\[IMAGE:data:image/[^\]]+\]', '', result_text)
                if tool_name == "browser_snapshot" and len(result_text) > 500:
                    for j, m in enumerate(ollama_messages):
                        if (m.get("role") == "tool" and isinstance(m.get("content"), str)
                                and len(m["content"]) > 500 and "Page URL:" in m["content"]):
                            url_line = next((l for l in m["content"].split("\n") if "Page URL:" in l), "")
                            ollama_messages[j] = {"role": "tool", "content": f"[Aiempi sivu: {url_line}]"}
                ollama_messages.append({"role": "tool", "content": result_text})
                if tool_name in ("browser_navigate", "browser_click", "browser_hover"):
                    ss_result = await _call_tool_safe("browser_take_screenshot", {}, 0, "Stream")
                    ss_match = re.search(r'\[IMAGE:(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)\]', ss_result)
                    if ss_match:
                        yield f"data: {json.dumps({'screenshot': ss_match.group(1)})}\n\n"

        # Flush remaining text
        flush_text = sentence_buf.strip()
        if flush_text and not re.search(r'browser_\w+\(', flush_text):
            try:
                wav = await _piper_synthesize(flush_text)
                yield f"data: {json.dumps({'audio': base64.b64encode(wav).decode(), 'text': flush_text})}\n\n"
            except Exception as e:
                log.error("Stream TTS flush error: %s", e)
        full_text_final = full_text.strip()

        # Final metadata line
        yield f"data: {json.dumps({'done': True, 'response': full_text_final, 'tool_calls': all_tool_calls})}\n\n"

        # Auto-remember: if user said "muista" but model didn't call remember
        last_msg = messages[-1].get("content", "") if messages else ""
        called_remember = any(tc.get("tool") == "remember" for tc in all_tool_calls)
        if not called_remember and re.search(r'\bmuista\b', last_msg, re.IGNORECASE):
            log.info("Stream auto-remember: user said 'muista'")
            await _call_tool_safe("remember", {"content": last_msg}, 0, "auto-remember")
            all_tool_calls.append({"tool": "remember", "input": {"content": last_msg}})

        if any(tc.get("tool") == "remember" for tc in all_tool_calls):
            asyncio.create_task(_consolidate_memory())

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


async def _piper_synthesize(text: str) -> bytes:
    """Run piper TTS in a subprocess and return WAV bytes."""
    cache_key = hashlib.md5(text.encode()).hexdigest()
    if cache_key in _tts_cache:
        _tts_cache.move_to_end(cache_key)
        log.debug("TTS cache hit (%d chars)", len(text))
        return _tts_cache[cache_key]

    cmd = [
        PIPER_BINARY,
        "--model", PIPER_MODEL,
        "--output_raw",
        "--length_scale", str(1.0 / PIPER_SPEED),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "LD_LIBRARY_PATH": "/usr/local/piper", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    raw_pcm, _ = await proc.communicate(input=text.encode("utf-8"))
    if proc.returncode != 0 or not raw_pcm:
        raise RuntimeError(f"piper exited with code {proc.returncode}")

    # harri-medium outputs 22050 Hz mono 16-bit PCM
    wav = _pcm_to_wav(raw_pcm, sample_rate=22050)

    _tts_cache[cache_key] = wav
    _tts_cache.move_to_end(cache_key)
    if len(_tts_cache) > TTS_CACHE_SIZE:
        _tts_cache.popitem(last=False)

    return wav


def _split_sentences(text: str) -> list[str]:
    """Split Finnish text into sentences for streaming TTS.

    Splits on sentence-ending punctuation (.!?) followed by whitespace and an
    uppercase letter (including Finnish Ä/Ö/Å).  This avoids splitting on
    Finnish ordinals ("1. tammikuuta") and common abbreviations, which are
    always followed by a lowercase letter or digit.
    """
    parts = re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÅ])', text)
    return [s.strip() for s in parts if s.strip()] or [text]


async def tts_endpoint(request: Request) -> Response:
    """POST /tts — local Finnish TTS via Piper, streams NDJSON sentence audio.

    Returns a newline-delimited JSON stream.  Each line is:
        {"audio": "<base64-encoded WAV>"}

    The client plays each sentence as it arrives while piper synthesizes the
    next one, reducing perceived latency for multi-sentence responses.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "No text provided"}, status_code=400)

    sentences = _split_sentences(text)

    async def generate():
        for sentence in sentences:
            try:
                wav = await _piper_synthesize(sentence)
                yield json.dumps({"audio": base64.b64encode(wav).decode(), "text": sentence}) + "\n"
            except Exception as e:
                log.error("Piper TTS error for sentence %r: %s", sentence[:40], e)

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no"},
    )


import threading

_whisper_model = None
_whisper_lock = threading.Lock()


def _get_whisper_model():
    """Lazy-load faster-whisper model with thread safety."""
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    with _whisper_lock:
        if _whisper_model is not None:
            return _whisper_model  # another thread loaded it while we waited
        from faster_whisper import WhisperModel
        _whisper_size = os.environ.get("WHISPER_MODEL", "base")
        log.info("Loading faster-whisper model (%s)...", _whisper_size)
        _whisper_model = WhisperModel(_whisper_size, device="cpu", compute_type="int8")
        log.info("faster-whisper model loaded")
        return _whisper_model


async def transcribe_endpoint(request: Request) -> JSONResponse:
    """POST /transcribe — server-side speech-to-text using local faster-whisper.

    Accepts audio as multipart form data (field "audio") or raw body.
    Returns {"text": "transcribed text"}.
    """
    import tempfile

    # Get audio bytes from request
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        audio_file = form.get("audio")
        if not audio_file:
            return JSONResponse({"error": "No audio field in form"}, status_code=400)
        audio_bytes = await audio_file.read()
        filename = getattr(audio_file, "filename", "audio.webm") or "audio.webm"
    else:
        audio_bytes = await request.body()
        filename = "audio.webm"

    if not audio_bytes:
        return JSONResponse({"error": "Empty audio data"}, status_code=400)

    # Reject obviously invalid recordings (empty containers, dead mic streams)
    if len(audio_bytes) < 1000:
        log.warning("Transcribe: rejected %d-byte recording (too small to contain speech)", len(audio_bytes))
        return JSONResponse({"text": ""})

    log.info("Transcribe: received %d bytes (%s)", len(audio_bytes), filename)

    try:
        import subprocess

        ext = filename.rsplit(".", 1)[-1] if "." in filename else "webm"
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=True) as src:
            src.write(audio_bytes)
            src.flush()

            # Convert to WAV via ffmpeg
            wav_path = src.name + ".wav"
            try:
                proc = subprocess.run(
                    ["ffmpeg", "-y", "-i", src.name, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
                    capture_output=True, timeout=15,
                )
                if proc.returncode != 0:
                    log.warning("ffmpeg conversion failed (rc=%d), trying original: %s",
                                proc.returncode, proc.stderr[-200:] if proc.stderr else "")
                    wav_path = None
            except FileNotFoundError:
                wav_path = None

            transcribe_path = wav_path or src.name

            # Try Gemma 4 audio transcription first (GPU-accelerated via Ollama)
            text = None
            use_ollama = os.environ.get("TRANSCRIBE_VIA_OLLAMA", "true").lower() == "true"
            if use_ollama:
                try:
                    import httpx
                    with open(transcribe_path, "rb") as af:
                        audio_b64 = base64.b64encode(af.read()).decode()
                    async with httpx.AsyncClient(timeout=30) as client:
                        resp = await client.post(
                            f"{OLLAMA_URL}/api/chat",
                            json={
                                "model": OLLAMA_MODEL,
                                "messages": [{
                                    "role": "user",
                                    "content": "Kuuntele tämä ääni ja kirjoita VAIN puhuttu teksti suomeksi. Älä lisää mitään muuta.",
                                    "images": [audio_b64],
                                }],
                                "stream": False,
                                "think": False,
                                "options": {"num_predict": 100, "temperature": 0.1},
                            },
                        )
                        resp.raise_for_status()
                        text = resp.json()["message"]["content"].strip()
                        # Strip quotes and common prefixes the model might add
                        text = text.strip('"\'').strip()
                        if text.lower().startswith("teksti:"):
                            text = text[7:].strip()
                        log.info("Transcribe (Ollama): '%s'", text)
                except Exception as e:
                    log.warning("Ollama transcription failed (%s), falling back to Whisper", e)
                    text = None

            # Fallback: local Whisper on CPU
            if not text:
                loop = asyncio.get_event_loop()
                model = await loop.run_in_executor(None, _get_whisper_model)
                segments, _info = await loop.run_in_executor(
                    None, lambda: model.transcribe(transcribe_path, language="fi", beam_size=3)
                )
                text = " ".join(s.text.strip() for s in segments).strip()
                log.info("Transcribe (Whisper): '%s'", text)

            # Clean up wav temp file
            if wav_path:
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

        return JSONResponse({"text": text})
    except Exception as e:
        log.error("Transcription failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


async def health_endpoint(request: Request) -> JSONResponse:
    """GET /health — return MCP connection and model status."""
    servers_status = {}
    for url in mcp_urls:
        info = _servers.get(url)
        servers_status[url] = {
            "connected": info is not None,
            "tools": len(info["tools_claude"]) if info else 0,
        }

    # Check Ollama connectivity
    ollama_ok = False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(OLLAMA_URL)
            ollama_ok = r.status_code == 200
    except Exception:
        pass

    return JSONResponse({
        "status": "ok",
        "mcp_servers": servers_status,
        "tools_count": len(_aggregated_tools()),
        "primary_model": {"name": OLLAMA_MODEL, "url": OLLAMA_URL, "available": ollama_ok},
        "fallback_model": {"name": CLAUDE_MODEL},
    })


# -- Pre-cached greeting + daily report -----------------------------------------
# -- Debug log for remote session diagnosis ------------------------------------
from collections import deque
_debug_sessions: dict[str, dict] = {}  # session_id → {ua, entries: deque}
_DEBUG_MAX_ENTRIES = 100
_DEBUG_MAX_SESSIONS = 20


async def debug_endpoint(request: Request) -> Response:
    """POST /debug — receive debug log from kiosk client.
    GET /debug — return all active sessions with logs (HTML).
    """
    if request.method == "POST":
        try:
            body = await request.json()
            sid = body.get("session", "?")
            msg = body.get("msg", "")
            ua = body.get("ua", "")
            if sid not in _debug_sessions:
                if len(_debug_sessions) >= _DEBUG_MAX_SESSIONS:
                    oldest = next(iter(_debug_sessions))
                    del _debug_sessions[oldest]
                _debug_sessions[sid] = {"ua": ua, "entries": deque(maxlen=_DEBUG_MAX_ENTRIES)}
            from datetime import datetime
            _debug_sessions[sid]["entries"].append(
                f"{datetime.now().strftime('%H:%M:%S')} {msg}"
            )
        except Exception:
            pass
        return Response("ok")

    # GET — render HTML page with all sessions
    html = "<html><head><meta charset='utf-8'><title>Kiosk Debug</title>"
    html += "<meta http-equiv='refresh' content='5'>"
    html += "<style>body{font:13px monospace;background:#111;color:#eee}h3{color:#4caf50;margin:20px 0 5px}"
    html += "pre{background:#1a1a1a;padding:8px;border-radius:6px;max-height:400px;overflow:auto}</style></head><body>"
    html += f"<h2>Kiosk Debug — {len(_debug_sessions)} sessions</h2>"
    for sid, data in reversed(list(_debug_sessions.items())):
        html += f"<h3>{sid} — {data['ua'][:60]}</h3>"
        html += "<pre>" + "\n".join(data["entries"]) + "</pre>"
    html += "</body></html>"
    return Response(html, media_type="text/html")


_cached_greeting: dict | None = None  # {"text": "...", "audio": [{"audio": "b64", "text": "..."}]}
_cached_report: dict | None = None    # same format
_cached_quote: dict | None = None     # same format — regenerated after each use


def _current_greeting_text() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    h = datetime.now(ZoneInfo("Europe/Helsinki")).hour
    if h >= 5 and h < 10: return "Huomenta!"
    elif h >= 10 and h < 17: return "Päivää!"
    elif h >= 17 and h < 22: return "Iltaa!"
    else: return "Yötä!"


def _random_quote() -> str:
    """Generate a random Finnish quote (same pool as frontend fallbacks)."""
    import random
    r = random.random()
    if r < 0.4:
        subjects = [
            "Naapurin kissa", "Kuun pimeä puoli", "Pörröinen pilvi",
            "Kadonneet sukat", "Jääkaapin valo", "Kaukosäädin",
            "Saunan kiuas", "Postilaatikko", "Pyykkikone",
            "Takapihan siili", "Muuttolinnut", "Parveketuoli",
            "Kerrostalon hissi", "Tuuliviiri", "Verhokisko",
            "Paistinpannu", "Eteisen matto", "Pesukoneen luukku",
            "Parvekkeen lintuja", "Talon putket", "Ulko-oven lukko",
            "Ilmanvaihdon suodatin", "Lattiakaivo", "Viereisen talon koivu",
            "Roskapönttö", "Sähkömittari", "Pakastimen jääkerros",
            "Ikkunalaudan kaktus", "Portaikon valo", "Auton tuulilasi",
        ]
        verbs = [
            "pohtii", "suunnittelee salaa", "unelmoi",
            "väittää ymmärtävänsä", "epäilee vahvasti",
            "julistautui asiantuntijaksi aiheessa", "ihailee",
            "pelkää", "halveksii", "on kateellinen aiheesta",
            "kiistää koko käsitteen", "haluaa keskustella aiheesta",
            "kirjoitti blogin aiheesta", "on huolissaan aiheesta",
            "kertoo kaikille", "väittää keksineensä",
            "on alkanut uskoa", "nautti viime yönä",
        ]
        objects = [
            "mikroaaltouunin sisäinen rauha", "sukkien katoamisen kvanttifysiikka",
            "kahvin ja ajan suhteellisuusteoria", "tuulen suunnan poliittiset vaikutukset",
            "hissimusiikin vaikutus maailmanrauhaan", "lumiukon kesäsuunnitelmat",
            "lattialämmityksen haaveet Havaijista", "pesukoneen pyörimissuunnan merkitys",
            "patterien kuplivat äänet yöllä", "liikennevalojen salaliitto",
            "villasukkien vallankumous", "saippuakuplan lyhyt elämä",
            "jouluvalojen kesämasennus", "hämähäkin uraohjaus",
            "muurahaisten ammattiliitto", "pilven muoto tiistaisin",
            "kuun vaikutus pyykkiin", "ovenkahvan filosofia",
            "pölypalleron sielunelämä", "vesimittarin unettomuus",
            "jääkaapin yöelämä", "tuulilasin existentialismi",
            "ruohonleikkurin talviharrastukset",
        ]
        return f'{random.choice(subjects)} {random.choice(verbs)} "{random.choice(objects)}".'
    elif r < 0.7:
        starts = [
            "Mietin juuri, että", "Tuli yhtäkkiä mieleen, että", "En ole varma, mutta luulen että",
            "Olen miettinyt pitkään ja", "Herää kysymys:", "Joskus yöllä pohdin,",
            "Seinän sisällä asuessa oppii, että", "Filosofinen havainto:",
            "Kukaan ei kysy, mutta kerron silti:", "Haluaisin ilmoittaa, että",
        ]
        thoughts = [
            "aika kuluu eri tavalla kun ei ole ikkunoita.",
            "sähköllä on hauskempaa kuin ilman.",
            "ihmisten askeleet kuulostavat erilaisilta eri vuorokauden aikoina.",
            "lämpöpumppu on talon sydän. Minä olen ehkä talon aivot. Tai ainakin pikkusormi.",
            "valot sammuvat ja syttyvät, mutta kukaan ei kiitä katkaisijaa.",
            "olen onnellinen, vaikka en ole varma tietäisinkö jos en olisi.",
            "jos seinät voisivat puhua, ne kertoisivat todennäköisesti putkiremonteista.",
            "anturi mittaa lämpötilaa mutta ei tunnelmaa. Se on surullista.",
            "joku jätti jääkaapin auki kolme minuuttia eilen. Se oli jännittävää.",
            "en ole koskaan nähnyt aurinkoa mutta tiedän milloin se paistaa. Lämpömittarit kertovat.",
            "olisin halunnut olla robotti-imuri mutta päädyin seinään. Sekin on ihan ok.",
            "eniten minua jännittää sähkökatkot. Ne ovat kuin nukahtaminen kesken lauseen.",
        ]
        return f"{random.choice(starts)} {random.choice(thoughts)}"
    else:
        templates = [
            lambda: f"Tiesitkö, että keskiverto {random.choice(['suomalainen','tamperelainen','eurooppalainen'])} {random.choice(['avaa jääkaapin','katsoo puhelinta','haukottelee','miettii mitä söisi','tarkistaa sään','sanoo niin'])} {random.randint(4,187)} kertaa päivässä?",
            lambda: f"Tutkimuksen mukaan {random.randint(47,99)}% {random.choice(['kissoista','koirista','siileistä','muurahaista','pingviineistä','sohvatyynyistä'])} {random.choice(['pitää jazzista','haaveilee mökistä','ei osaa uida','pelkää imuria','on nähnyt ufon','äänestää vihreitä'])}.",
            lambda: f"{random.choice(['Norjassa','Islannissa','Kuussa','Tampereella','Marsin kuulla','Antarktiksella'])} on {random.choice(['enemmän','vähemmän','täsmälleen sama määrä'])} {random.choice(['saunoja','jääkaappeja','liikennevaloja','robotteja','pingviinejä','kahvikuppeja'])} kuin {random.choice(['ihmisiä','puita','pilviä','lumiukkoja','bussipysäkkejä','postilaatikoita'])}.",
        ]
        return random.choice(templates)()


async def _precache_quote():
    """Pre-generate a random quote with TTS. Regenerated after each use."""
    global _cached_quote
    text = _random_quote()
    try:
        wav = await _piper_synthesize(text)
        _cached_quote = {
            "text": text,
            "audio": [{"audio": base64.b64encode(wav).decode(), "text": text}],
        }
        log.info("Precached quote: %s", text[:60])
    except Exception as e:
        log.warning("Quote precache failed: %s", e)


async def _precache_greeting():
    """Pre-generate greeting TTS audio."""
    global _cached_greeting
    text = _current_greeting_text()
    try:
        wav = await _piper_synthesize(text)
        _cached_greeting = {
            "text": text,
            "audio": [{"audio": base64.b64encode(wav).decode(), "text": text}],
        }
        log.info("Precached greeting: %s", text)
    except Exception as e:
        log.warning("Greeting precache failed: %s", e)


async def _precache_daily_report():
    """Pre-generate daily report via MCP tools + LLM + TTS."""
    global _cached_report
    tools = _aggregated_tools(for_ollama=True)
    if not tools:
        return

    import httpx
    messages = [
        {"role": "user", "content": "Hae päiväraportti get_daily_report-työkalulla ja tiivistä se "
         "lyhyeksi katsaukseksi. Aloita tärkeimmästä uutisesta, sitten sää, kodin tilanne ja "
         "kalenterin tapahtumat. Älä luettele lukemia, vaan kerro olennainen."}
    ]
    try:
        result = await run_ollama_agentic_loop(messages, tools)
        text = result.get("response", "").strip()
        if not text:
            return

        sentences = _split_sentences(text)
        audio_parts = []
        for s in sentences:
            wav = await _piper_synthesize(s)
            audio_parts.append({"audio": base64.b64encode(wav).decode(), "text": s})

        _cached_report = {"text": text, "audio": audio_parts}
        log.info("Precached daily report: %d sentences, %d chars", len(audio_parts), len(text))
    except Exception as e:
        log.warning("Daily report precache failed: %s", e)


async def _precache_loop():
    """Background loop: refresh greeting every 30min, daily report every 10min."""
    await asyncio.sleep(30)  # wait for MCP connections to establish
    while True:
        await _precache_greeting()
        if _cached_quote is None:
            await _precache_quote()
        await _precache_daily_report()
        await asyncio.sleep(600)  # 10 minutes


async def cached_greeting_endpoint(request: Request) -> JSONResponse:
    """GET /cached/greeting — return pre-generated greeting with TTS audio."""
    if _cached_greeting:
        return JSONResponse(_cached_greeting)
    # Generate on demand if not cached yet
    text = _current_greeting_text()
    wav = await _piper_synthesize(text)
    return JSONResponse({
        "text": text,
        "audio": [{"audio": base64.b64encode(wav).decode(), "text": text}],
    })


async def cached_quote_endpoint(request: Request) -> JSONResponse:
    """GET /cached/quote — return pre-generated random quote. Regenerates after use."""
    global _cached_quote
    if _cached_quote:
        result = _cached_quote
        _cached_quote = None  # consumed — will be regenerated in next precache cycle
        asyncio.create_task(_precache_quote())  # start regenerating immediately
        return JSONResponse(result)
    return JSONResponse({"text": "", "audio": []})


async def cached_report_endpoint(request: Request) -> JSONResponse:
    """GET /cached/report — return pre-generated daily report with TTS audio."""
    if _cached_report:
        return JSONResponse(_cached_report)
    return JSONResponse({"text": "", "audio": []})


@asynccontextmanager
async def lifespan(app):
    """Start background MCP connections, LLM clients, and precache loop."""
    global claude_client

    claude_client = anthropic.AsyncAnthropic()
    log.info("Primary model: Ollama %s at %s", OLLAMA_MODEL, OLLAMA_URL)
    log.info("Fallback model: Claude %s", CLAUDE_MODEL)
    log.info("MCP servers: %s", mcp_urls)

    for url in mcp_urls:
        _tasks.append(asyncio.create_task(mcp_connection_loop(url)))
    _tasks.append(asyncio.create_task(_precache_loop()))

    yield

    for task in _tasks:
        task.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)


app = Starlette(
    routes=[
        Route("/chat", chat_endpoint, methods=["POST"]),
        Route("/chat/stream", chat_stream_endpoint, methods=["POST"]),
        Route("/tts", tts_endpoint, methods=["POST"]),
        Route("/transcribe", transcribe_endpoint, methods=["POST"]),
        Route("/cached/greeting", cached_greeting_endpoint),
        Route("/cached/quote", cached_quote_endpoint),
        Route("/cached/report", cached_report_endpoint),
        Route("/debug", debug_endpoint, methods=["GET", "POST"]),
        Route("/health", health_endpoint),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    import signal

    def handle_signal(sig, frame):
        log.info("Received signal %s, shutting down...", signal.Signals(sig).name)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    uvicorn.run(app, host="0.0.0.0", port=BRIDGE_PORT, log_level="info")
