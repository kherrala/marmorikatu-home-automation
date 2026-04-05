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
        f"Vastaa lyhyesti suomeksi ilman muotoilua.\n"
        f"Vastauksesi luetaan ääneen — pidä ne lyhyinä ja selkeinä.\n"
        f"Käyttäjä on kotona.\n"
        f"Kellarin lämpötila on tarkoituksella alempi kuin muissa kerroksissa — se ei ole ongelma.\n"
        f"\n"
        f"Muisti:\n"
        f"- Käytä 'recall'-työkalua AINA keskustelun alussa — hae käyttäjän mieltymykset ja aiemmat keskustelut.\n"
        f"- Käytä 'remember'-työkalua AKTIIVISESTI tallentaaksesi:\n"
        f"  * Kaikki mitä käyttäjä kertoo itsestään, perheestään tai mieltymyksistään\n"
        f"  * Kiinnostavat uutiset ja sääilmiöt päiväraporteista\n"
        f"  * Käyttäjän kysymykset ja huolenaiheet kodista\n"
        f"  * Tapahtumat ja suunnitelmat joista käyttäjä puhuu\n"
        f"- Älä mainitse muistijärjestelmää käyttäjälle ellei hän kysy.\n"
        f"\n"
        f"Verkkohaku:\n"
        f"- Käytä 'browser_navigate' + 'browser_snapshot' hakeaksesi tietoa verkosta.\n"
        f"- Käytä hakuun: https://html.duckduckgo.com/html/?q=hakusana (ei estä headless-selainta).\n"
        f"- Käytä 'browser_snapshot' lukeaksesi sivun sisällön.\n"
        f"- Jos sivu näyttää evästebannerin, hyväksy se 'browser_click'-työkalulla.\n"
        f"- Jos sivu näyttää 'Just a moment' tai muun esteen, yritä toista sivustoa."
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
    # Home automation
    "get_latest", "get_room_temperatures", "get_air_quality",
    "compare_indoor_outdoor", "get_heat_recovery_efficiency",
    "get_freezing_probability", "get_thermia_status", "get_thermia_temperatures",
    "get_heatpump_cop", "get_brine_circuit", "get_hotwater_analysis",
    "get_compressor_duty_cycle", "get_energy_consumption",
    "get_electricity_prices", "get_heating_status", "get_energy_cost",
    "get_sauna_status",
    # External services
    "get_weather_forecast", "get_news_headlines", "get_news_article",
    "get_bus_departures", "get_calendar_events", "get_daily_report",
    # Harmony Hub
    "harmony_list_activities", "harmony_current_activity",
    "harmony_start_activity", "harmony_power_off",
    # Memory
    "remember", "recall",
    # Web browsing
    "browser_navigate", "browser_snapshot", "browser_click", "browser_handle_dialog",
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
            # Use streamable HTTP for /mcp endpoints, SSE for /sse endpoints
            transport = streamablehttp_client(url) if url.rstrip("/").endswith("/mcp") else sse_client(url)
            async with transport as (read_stream, write_stream):
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

    # Build messages with system prompt
    ollama_messages = [{"role": "system", "content": get_system_prompt()}]
    for m in messages:
        msg = {"role": m["role"], "content": m["content"]}
        if m.get("images"):
            msg["images"] = m["images"]  # base64 JPEG frames for vision
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

    # Trigger memory consolidation in background if any remember calls were made
    if result and any(tc.get("tool") == "remember" for tc in result.get("tool_calls", [])):
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

    # Phase 1: Run tool calls (non-streamed) until we get a text-only response
    openai_tools = _tools_to_openai(ollama_tools)
    ollama_messages = [{"role": "system", "content": get_system_prompt()}]
    for m in messages:
        msg = {"role": m["role"], "content": m["content"]}
        if m.get("images"):
            msg["images"] = m["images"]  # base64 JPEG frames for vision
        ollama_messages.append(msg)

    all_tool_calls: list[dict] = []

    async with httpx.AsyncClient(timeout=120) as client:
        for iteration in range(MAX_TOOL_ITERATIONS):
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": ollama_messages,
                    "tools": openai_tools,
                    "stream": False,
                    "options": {"num_ctx": OLLAMA_NUM_CTX, "temperature": 0.3, "repeat_penalty": 1.0},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data["message"]
            tool_calls_raw = msg.get("tool_calls") or []

            if not tool_calls_raw:
                # No tool calls — this is the final text response.
                # Re-request with streaming to get token-by-token output.
                break

            # Execute tool calls
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
                result_text = await _call_tool_safe(tool_name, tool_input, iteration + 1, "Stream")
                ollama_messages.append({"role": "tool", "content": result_text})

    # Phase 2: Stream the final text response with inline TTS
    async def generate():
        sentence_buf = ""
        full_text = ""

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
                    "options": {"num_ctx": OLLAMA_NUM_CTX, "temperature": 0.3, "repeat_penalty": 1.0},
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if not token:
                        continue

                    sentence_buf += token
                    full_text += token

                    # Check for sentence boundary
                    parts = re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÅ])', sentence_buf)
                    while len(parts) > 1:
                        sentence = parts.pop(0).strip()
                        if sentence:
                            try:
                                wav = await _piper_synthesize(sentence)
                                yield json.dumps({"audio": base64.b64encode(wav).decode(), "text": sentence}) + "\n"
                            except Exception as e:
                                log.error("Stream TTS error: %s", e)
                    sentence_buf = parts[0] if parts else ""

        # Flush remaining text
        if sentence_buf.strip():
            try:
                wav = await _piper_synthesize(sentence_buf.strip())
                yield json.dumps({"audio": base64.b64encode(wav).decode(), "text": sentence_buf.strip()}) + "\n"
            except Exception as e:
                log.error("Stream TTS flush error: %s", e)
            full_text_final = full_text.strip()
        else:
            full_text_final = full_text.strip()

        # Final metadata line
        yield json.dumps({"done": True, "response": full_text_final, "tool_calls": all_tool_calls}) + "\n"

        # Trigger consolidation in background if any remember calls
        if any(tc.get("tool") == "remember" for tc in all_tool_calls):
            asyncio.create_task(_consolidate_memory())

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no"},
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
        log.info("Loading faster-whisper model (base)...")
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
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

            # Re-mux through ffmpeg to fix malformed webm containers
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
                wav_path = None  # ffmpeg not installed, use original

            transcribe_path = wav_path or src.name

            loop = asyncio.get_event_loop()
            model = await loop.run_in_executor(None, _get_whisper_model)
            segments, _info = await loop.run_in_executor(
                None, lambda: model.transcribe(transcribe_path, language="fi", beam_size=3)
            )
            text = " ".join(s.text.strip() for s in segments).strip()

            # Clean up wav temp file
            if wav_path:
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

        log.info("Transcribe: '%s'", text)
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


@asynccontextmanager
async def lifespan(app):
    """Start background MCP connections and LLM clients."""
    global claude_client

    claude_client = anthropic.AsyncAnthropic()
    log.info("Primary model: Ollama %s at %s", OLLAMA_MODEL, OLLAMA_URL)
    log.info("Fallback model: Claude %s", CLAUDE_MODEL)
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
        Route("/chat/stream", chat_stream_endpoint, methods=["POST"]),
        Route("/tts", tts_endpoint, methods=["POST"]),
        Route("/transcribe", transcribe_endpoint, methods=["POST"]),
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
