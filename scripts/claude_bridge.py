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
# Keep the model resident on the Ollama box between conversations — a 35B MoE
# takes ~30s to cold-load, which a fresh greeting must never pay.
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "24h")
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "3002"))
PIPER_BINARY = os.environ.get("PIPER_BINARY", "/usr/local/piper/piper")
PIPER_MODEL  = os.environ.get("PIPER_MODEL",  "/models/fi_FI-asmo-medium.onnx")
PIPER_SPEED  = float(os.environ.get("PIPER_SPEED", "1.0"))   # <1 = slower, >1 = faster
TTS_CACHE_SIZE = int(os.environ.get("TTS_CACHE_SIZE", "64"))  # max cached audio entries
# Remote GPU speech-to-text: full URL of an OpenAI-compatible transcription
# endpoint (e.g. http://192.168.1.36:8971/v1/audio/transcriptions). Empty =
# local faster-whisper only.
WHISPER_URL = os.environ.get("WHISPER_URL", "")
WHISPER_REMOTE_MODEL = os.environ.get("WHISPER_REMOTE_MODEL", "Systran/faster-whisper-large-v3-turbo")
WHISPER_REMOTE_TIMEOUT = float(os.environ.get("WHISPER_REMOTE_TIMEOUT", "20"))

# LRU audio cache: text-hash → WAV bytes
_tts_cache: "OrderedDict[str, bytes]" = OrderedDict()

WEEKDAYS_FI = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai", "sunnuntai"]


_CONTROL_ACTION_RE = re.compile(
    r"\b(syty|sammu|kytke|laita|sytytä|sammuta|käännä|aseta|muuta|pistä|p[aä][aä]ll|pois)\b",
    re.IGNORECASE,
)
_CONTROL_TARGET_RE = re.compile(
    r"valo|lamppu|sauna|kiuas|takka|leffa|patteri|lämmitys|jäähdytys",
    re.IGNORECASE,
)


def _has_control_intent(user_text: str) -> bool:
    """Detect whether a user message expresses an actionable control command.

    Used to force-fallback from Ollama → Claude when gemma narrates the action
    ('Käytän set_light-työkalua...') without emitting an actual tool_calls
    array. False positives just mean Claude handles a query gemma could have —
    safe; false negatives leave the user with no action — bad. Lean permissive.
    """
    if not user_text:
        return False
    return bool(_CONTROL_ACTION_RE.search(user_text) and _CONTROL_TARGET_RE.search(user_text))


def _strip_markdown(text: str) -> str:
    """Flatten Markdown to plain text for speech + the app's text stream.

    The models — Claude especially — like to emit **bold**, headings and bullet
    lists, which the on-device TTS would otherwise read out as 'asterisk
    asterisk ...'. The system prompts ask for plain text; this is the belt to
    that suspenders, so a stray marker never becomes a spoken artifact.
    """
    if not text:
        return text
    t = text
    t = re.sub(r"```[\s\S]*?```", " ", t)          # fenced code blocks
    t = re.sub(r"`([^`]*)`", r"\1", t)               # inline code
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)      # images
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)   # links → visible text
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)         # bold
    t = re.sub(r"(?<![\w*])[*_](?!\s)(.+?)(?<!\s)[*_](?![\w*])", r"\1", t)  # italic
    t = re.sub(r"~~(.+?)~~", r"\1", t)               # strikethrough
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", t)      # headings
    t = re.sub(r"(?m)^\s{0,3}>\s?", "", t)           # blockquotes
    t = re.sub(r"(?m)^\s{0,3}[-*+]\s+", "", t)       # bullet lists
    t = re.sub(r"(?m)^\s{0,3}\d+\.\s+", "", t)       # numbered lists
    t = t.replace("**", "").replace("__", "")         # any stray markers
    # Newlines don't survive as speech; a blank line is a paragraph/heading
    # break (→ sentence boundary so TTS pauses), a single newline just a wrap.
    t = re.sub(r"[ \t]*\n[ \t]*\n[\s]*", ". ", t)
    t = re.sub(r"[ \t]*\n[ \t]*", " ", t)
    t = re.sub(r"\.\s*\.", ".", t)                    # collapse accidental ". ."
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def _now_helsinki_str() -> tuple[str, str]:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Europe/Helsinki"))
    weekday = WEEKDAYS_FI[now.weekday()]
    return (
        f"{weekday} {now.day}.{now.month}.{now.year}",
        f"{now.hour}:{now.minute:02d}",
    )


def get_system_prompt() -> str:
    """Full system prompt — used by Claude (fallback path)."""
    date_str, time_str = _now_helsinki_str()
    return (
        f"Olet kodin älykäs avustaja Tampereella. Nyt on {date_str}, kello {time_str}.\n"
        f"\n"
        f"TÄRKEÄÄ:\n"
        f"- Käytä AINA työkaluja tietojen hakuun. ÄLÄ KOSKAAN keksi tai arvaa tietoja.\n"
        f"- Jos työkalu ei ole käytettävissä tai kutsu epäonnistuu, sano rehellisesti ettet tiedä.\n"
        f"- ÄLÄ keksi säätietoja, uutisia, lämpötiloja tai kalenterimerkintöjä.\n"
        f"- Kerro vain se mitä työkalut palauttavat.\n"
        f"\n"
        f"Vastauksesi luetaan ääneen puhuen — kirjoita PELKKÄÄ puhuttua tekstiä.\n"
        f"ÄLÄ käytä markdown-muotoilua äläkä mitään erikoismerkkejä: ei **tähtiä**, ei #otsikoita, "
        f"ei -listoja, ei `koodia`. Kirjoita otsikot ja korostukset tavallisina sanoina.\n"
        f"Vastaa 1-3 lauseella suomeksi, lyhyesti ja selkeästi.\n"
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


def get_ollama_system_prompt() -> str:
    """Short, focused prompt for the small Ollama model.

    Optimised for gemma4:e4b. Critical: do NOT name specific tools here —
    small models echo tool names back as narration ("Käytän set_light-
    työkalua...") instead of emitting an actual tool_calls array, leaving
    the action unperformed. Tool schemas come from the `tools` parameter;
    the prompt only needs to push behaviour, not catalogue capabilities.
    """
    date_str, time_str = _now_helsinki_str()
    return (
        f"Olet kodin avustaja Tampereella. Nyt on {date_str}, kello {time_str}.\n"
        f"\n"
        f"SÄÄNNÖT:\n"
        f"- Kutsu työkaluja suoraan tietojen hakuun ja toimintojen suorittamiseen.\n"
        f"- ÄLÄ kerro käyttäjälle työkalujen nimiä äläkä sano \"kutsun työkalua\" — kutsu se.\n"
        f"- ÄLÄ kuvaile mitä aiot tehdä — tee se ensin, sitten vahvista lyhyesti.\n"
        f"- Älä keksi tietoja. Jos työkalu ei toimi, sano rehellisesti ettet tiedä.\n"
        f"- Vastaa 1–2 lauseella suomeksi. Ei markdownia.\n"
        f"- Vastauksesi luetaan ääneen.\n"
        f"- Kellarin lämpötila on tahallaan alempi kuin muissa kerroksissa.\n"
        f"\n"
        f"Kun käyttäjä kertoo mieltymyksen tai pyytää muistamaan jotain, tallenna se työkalulla.\n"
        f"Keskustelun alussa, hae aiemmat muistot työkalulla."
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


# Whitelist of tools available to Ollama. Small models (gemma4:e4b) lose tool
# selection accuracy as the count grows — keep this lean and let Claude (full
# fallback path) handle browser, Harmony, and deep diagnostics tools.
_OLLAMA_ALLOWED_TOOLS = {
    # Light control (PLC via MQTT) — the avatar's primary voice job
    "list_lights", "set_light",
    "set_lights_by_floor", "set_lights_matching",
    # Sauna readiness — a frequent voice query
    "get_sauna_status",
    # External services people actually ask for out loud
    "get_weather_forecast", "get_news_headlines", "get_bus_departures",
    # One-call summary that subsumes the per-sensor status tools
    # (room temps / air quality / heat pump / heating / energy) so we can
    # keep the local tool set small without losing "how's the house?".
    "get_daily_report",
    # Direct sensor reads — added with the qwen3.6:35b switch: the bigger
    # model keeps tool-selection accuracy at this list size, and "mikä on
    # olohuoneen lämpötila" deserves a direct answer instead of a full
    # daily-report round-trip.
    "get_latest",
    # Memory
    "remember", "recall",
}
# NOTE: per-sensor status, energy prices, and calendar tools are intentionally
# kept OUT of the local set (the small model loses tool-selection accuracy as
# the list grows). They remain available on the Claude fallback path, and
# get_daily_report covers the common "summarise the house" ask. News is also
# read out proactively by the announcer, so on-demand fetching is rarely needed.


def _aggregated_tools(for_ollama: bool = False) -> list[dict]:
    """Return combined Claude-format tools from all connected servers."""
    tools = []
    for info in _servers.values():
        tools.extend(info["tools_claude"])
    if for_ollama:
        tools = [t for t in tools if t["name"] in _OLLAMA_ALLOWED_TOOLS]
    return tools


def _ollama_tool_guard(tool_name: str) -> str | None:
    """Gate Ollama tool execution to the advertised whitelist. Small models
    hallucinate plausible-but-unadvertised tool names (e.g. get_news_article,
    learned from another tool's description) and the MCP session would happily
    run them. Returns a self-correcting message to feed back to the model when
    the tool isn't allowed, or None when it is."""
    if tool_name in _OLLAMA_ALLOWED_TOOLS:
        return None
    return (
        f"Tool '{tool_name}' is not available to you. Use only the tools "
        f"provided in this conversation — do not invent tool names. "
        f"If you wanted the full text of a news article, that is not "
        f"available; answer from the headline and description you already have."
    )


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
    ollama_messages = [{"role": "system", "content": get_ollama_system_prompt()}]
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
                    "keep_alive": OLLAMA_KEEP_ALIVE,
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
                if all_tool_calls == [] and text:
                    log.info("Ollama: no tool calls, returning text (%d chars): %r",
                             len(text), text[:200])
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

                guard = _ollama_tool_guard(tool_name)
                if guard is not None:
                    log.warning("Ollama: rejected unadvertised tool %r", tool_name)
                    result_text = guard
                else:
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

    # Work on our own copy: the loop appends assistant tool-use blocks and
    # {"role":"user","content":[...tool_results]} entries as it iterates. Mutating
    # the caller's list would leave messages[-1] a list (not the user's text),
    # which then crashes the endpoints' auto-remember re.search, and would feed
    # Claude's half-written tool artifacts to Ollama on mid-loop fallback.
    messages = list(messages)

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

    # Heuristic fallback: if user clearly asked for a control action but Ollama
    # produced no tool calls, gemma likely narrated the action instead of
    # invoking the tool. Hand off to Claude.
    if result is not None and not result.get("tool_calls"):
        last_user = (messages[-1].get("content", "") if messages else "")
        if _has_control_intent(last_user):
            log.warning(
                "Ollama produced no tool_calls for control intent — falling back to Claude. "
                "user=%r ollama_text=%r",
                last_user[:120], (result.get("response") or "")[:120],
            )
            result = None

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

    # No auto-injected browser context — the model calls browser_snapshot when
    # it needs page content.

    async def generate():
        all_tool_calls: list[dict] = []

        async def _emit(result: dict):
            """Stream a completed agentic result as tool progress + TTS sentences.
            Markdown is flattened first so the on-device TTS never reads '**',
            '#' or list bullets aloud."""
            for tc in result.get("tool_calls", []):
                all_tool_calls.append(tc)
                yield f"data: {json.dumps({'tool_use': tc.get('tool', '')})}\n\n"
            text = _strip_markdown((result.get("response") or "").strip())
            for sentence in re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÅ])', text):
                s = sentence.strip()
                if not s:
                    continue
                try:
                    wav = await _piper_synthesize(s)
                    yield f"data: {json.dumps({'audio': base64.b64encode(wav).decode(), 'text': s})}\n\n"
                except Exception as e:
                    log.error("Stream TTS error: %s", e)
            yield f"data: {json.dumps({'done': True, 'response': text, 'tool_calls': all_tool_calls})}\n\n"

        # Claude/Haiku is the primary model — faster and with the full tool set.
        # Fall back to the local Ollama model only when Anthropic can't serve
        # (credits exhausted, rate-limited, overloaded, or a connection error),
        # so the house keeps talking when the cloud account is out.
        result = None
        try:
            result = await run_claude_agentic_loop(messages, all_tools)
        except anthropic.APIError as e:
            log.warning("Claude unavailable (%s) — falling back to local Ollama", e)
            try:
                result = await run_ollama_agentic_loop(messages, ollama_tools)
            except Exception as e2:
                log.error("Ollama fallback failed: %s", e2)
        except Exception as e:
            log.error("Stream Claude primary failed: %s", e)

        if result is None:
            yield f"data: {json.dumps({'done': True, 'response': '', 'tool_calls': all_tool_calls, 'error': 'llm_unavailable'})}\n\n"
            return

        async for ev in _emit(result):
            yield ev

        # Auto-remember: if the user said "muista" but the model didn't call the
        # remember tool, persist their message anyway.
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


# Persistent piper subprocess (--json-input mode): loads the voice model once
# and then synthesizes each stdin JSON line independently, printing the output
# WAV path to stdout when the file is complete. Falls back to a one-shot
# subprocess per sentence (model reload every call) if the process misbehaves.
_piper_proc = None
_piper_proc_lock = asyncio.Lock()
_piper_req_seq = 0
_piper_out_dir = os.environ.get("PIPER_OUT_DIR") or (
    "/dev/shm" if os.path.isdir("/dev/shm") else "/tmp"
)
PIPER_TIMEOUT = float(os.environ.get("PIPER_TIMEOUT", "60"))  # per-sentence, seconds


async def _ensure_piper_proc():
    """Return the persistent piper process, starting or restarting it as needed."""
    global _piper_proc
    if _piper_proc is not None and _piper_proc.returncode is None:
        return _piper_proc
    _piper_proc = await asyncio.create_subprocess_exec(
        PIPER_BINARY,
        "--model", PIPER_MODEL,
        "--json-input",
        "--output_dir", _piper_out_dir,
        "--length_scale", str(1.0 / PIPER_SPEED),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "LD_LIBRARY_PATH": "/usr/local/piper", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    log.info("Started persistent piper process (pid %d)", _piper_proc.pid)
    return _piper_proc


async def _piper_synthesize_persistent(text: str) -> bytes:
    """Synthesize via the persistent piper process (one request in flight).

    Piper prints the output path line to stdout once the WAV is fully
    written — that is the completion signal. Any protocol hiccup kills the
    process so the next call starts a fresh one.
    """
    global _piper_proc, _piper_req_seq
    async with _piper_proc_lock:
        proc = await _ensure_piper_proc()
        _piper_req_seq += 1
        out_path = os.path.join(_piper_out_dir, f"piper-{os.getpid()}-{_piper_req_seq}.wav")
        try:
            req = json.dumps({"text": text, "output_file": out_path}, ensure_ascii=False)
            proc.stdin.write(req.encode("utf-8") + b"\n")
            await proc.stdin.drain()
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=PIPER_TIMEOUT)
            if not line:
                raise RuntimeError("persistent piper closed stdout")
            done_path = line.decode("utf-8", "replace").strip()
            with open(done_path, "rb") as f:
                wav = f.read()
            os.unlink(done_path)
            if not wav:
                raise RuntimeError("persistent piper produced an empty WAV")
            return wav
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            _piper_proc = None
            raise


async def _piper_synthesize(text: str) -> bytes:
    """Synthesize text to WAV bytes: LRU cache → persistent piper → one-shot."""
    cache_key = hashlib.md5(text.encode()).hexdigest()
    if cache_key in _tts_cache:
        _tts_cache.move_to_end(cache_key)
        log.debug("TTS cache hit (%d chars)", len(text))
        return _tts_cache[cache_key]

    try:
        wav = await _piper_synthesize_persistent(text)
    except Exception as e:
        log.error("Persistent piper failed (%s); falling back to one-shot subprocess", e)
        wav = await _piper_synthesize_subprocess(text)

    _tts_cache[cache_key] = wav
    _tts_cache.move_to_end(cache_key)
    if len(_tts_cache) > TTS_CACHE_SIZE:
        _tts_cache.popitem(last=False)

    return wav


async def _piper_synthesize_subprocess(text: str) -> bytes:
    """Fallback: run the piper binary, which reloads the voice model per call."""
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

    # medium-quality piper voices output 22050 Hz mono 16-bit PCM
    return _pcm_to_wav(raw_pcm, sample_rate=22050)


def _split_sentences(text: str) -> list[str]:
    """Split Finnish text into sentences for streaming TTS.

    Splits on sentence-ending punctuation (.!?) followed by whitespace and an
    uppercase letter (including Finnish Ä/Ö/Å).  This avoids splitting on
    Finnish ordinals ("1. tammikuuta") and common abbreviations, which are
    always followed by a lowercase letter or digit.

    Overlong sentence-less runs (news headline lists strung with commas) are
    further chunked at comma boundaries: one huge clip stalls the streaming
    pipeline and starves the kiosk's playback safety margins.
    """
    parts = re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÅ])', text)
    parts = [s.strip() for s in parts if s.strip()] or [text]
    out: list[str] = []
    for part in parts:
        while len(part) > 180:
            cut = part.rfind(", ", 40, 180)
            if cut == -1:
                break
            out.append(part[:cut + 1])
            part = part[cut + 2:]
        out.append(part)
    return out


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


async def _transcribe_remote(audio_path: str) -> "str | None":
    """Transcribe via an OpenAI-compatible endpoint (speaches, whisper.cpp).

    Returns the transcript — "" for silence is a valid result. Returns None
    on any transport/HTTP failure so the caller falls back to the local
    faster-whisper model and the kiosk keeps working if the GPU box is down.
    """
    import time
    import httpx
    try:
        with open(audio_path, "rb") as f:
            audio = f.read()
        t0 = time.monotonic()
        # Short connect timeout: an unreachable GPU box must cost ~2s, not the
        # full read timeout, before we fall back to the local model.
        timeout = httpx.Timeout(WHISPER_REMOTE_TIMEOUT, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                WHISPER_URL,
                files={"file": (os.path.basename(audio_path), audio, "audio/wav")},
                data={"model": WHISPER_REMOTE_MODEL, "language": "fi", "response_format": "json"},
            )
            resp.raise_for_status()
        text = (resp.json().get("text") or "").strip()
        log.info("Transcribe (remote, %.1fs): '%s'", time.monotonic() - t0, text)
        return text
    except Exception as e:
        log.warning("Remote whisper failed (%s); using local model", e)
        return None


# Whisper has well-documented failure modes on quiet / noise-only input: it
# emits common Finnish "stock phrases" or chains a short n-gram several times.
# When face-detection false-positives keep the kiosk in GREETING with no one
# actually present, every hallucination is sent on to the LLM, the response
# is TTS'd, the speaker bleeds back into the mic, and the cycle compounds.
# The two stages below catch both shapes — known stock phrases AND repetition
# — *after* faster-whisper's own VAD has run.
_WHISPER_STOCK_HALLUCINATIONS = (
    "tekstitys: yle",
    "tilaa lisää",
    "tilaa kanava",
    "yle ohjelmat",
    "subtekstit",
    "subtitles by",
    "kiitos kun katsoit",
    "translation by",
)


def _is_whisper_hallucination(text: str) -> bool:
    """Heuristic: True iff `text` is a Whisper repetition / stock-phrase
    hallucination rather than real speech.

    Rules (any one trips):
      - matches a known Whisper Finnish stock phrase substring
      - >=4 words and unique-word ratio < 0.5 ("X. X. X. X.")
      - >=4 words and >=50% of bigrams/trigrams repeat ("A B C A B C A B C")
    Short transcripts (<4 words) bypass the ratio rules — the kiosk has
    legitimate one-word answers ("joo", "ei", "stop") that must not be
    misclassified.
    """
    import re
    from collections import Counter
    t = text.strip().lower()
    if not t:
        return True
    if any(p in t for p in _WHISPER_STOCK_HALLUCINATIONS):
        return True
    words = re.findall(r"[\wäöå]+", t)
    if not words:
        # Punctuation/ellipsis only ("...", "... ... ...") — Whisper's
        # output when it detected acoustic energy but couldn't transcribe
        # any actual words. Not real speech.
        return True
    if len(words) < 4:
        return False
    if len(set(words)) / len(words) < 0.5:
        return True
    for n in (2, 3):
        if len(words) < n * 2:
            continue
        ngrams = list(zip(*[words[i:] for i in range(n)]))
        if len(ngrams) < 3:
            continue
        counts = Counter(ngrams)
        repeated = sum(1 for c in counts.values() if c > 1)
        if repeated / len(counts) >= 0.5:
            return True
    return False


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

            # Remote GPU whisper first when configured; local model on failure.
            text = None
            if WHISPER_URL:
                text = await _transcribe_remote(transcribe_path)
            if text is None:
                loop = asyncio.get_event_loop()
                model = await loop.run_in_executor(None, _get_whisper_model)
                # vad_filter=True: faster-whisper runs Silero VAD and drops
                #   non-speech chunks before they ever reach the model. Single
                #   biggest mitigation against the "silence → stock phrase"
                #   hallucination loop the kiosk used to fall into when face
                #   detection false-positived on on-screen artwork.
                # condition_on_previous_text=False: stops the model from being
                #   primed on its own prior output within the same call — a
                #   known cause of within-clip n-gram looping
                #   ("X X X X X").
                segments, _info = await loop.run_in_executor(
                    None,
                    lambda: model.transcribe(
                        transcribe_path,
                        language="fi",
                        beam_size=3,
                        vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 500},
                        condition_on_previous_text=False,
                    ),
                )
                text = " ".join(s.text.strip() for s in segments).strip()
            if text and _is_whisper_hallucination(text):
                log.warning("Transcribe: dropping hallucination '%s'", text)
                text = ""
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
_DEBUG_MAX_ENTRIES = 300
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


# -- Announcement broker --------------------------------------------------------
# Push-channel from backend (announcer.py) to all connected kiosks. Single
# in-memory broker — kiosks subscribe via SSE, the announcer service POSTs new
# events. A small ring buffer lets a kiosk that reconnects within REPLAY_WINDOW
# pick up events it missed (Last-Event-ID header).

_ANNOUNCE_PUSH_TOKEN = os.environ.get("ANNOUNCE_PUSH_TOKEN", "")
# Sized to be useful both as an SSE replay window AND as the source for the
# kiosk's announcement-history slide on initial load. ~200 covers a full day
# of normal-verbosity events with headroom; restart still loses everything.
_ANNOUNCE_RING_SIZE = int(os.environ.get("ANNOUNCE_RING_SIZE", "200"))
_ANNOUNCE_KEEPALIVE_SEC = float(os.environ.get("ANNOUNCE_KEEPALIVE_SEC", "20"))

_announce_subscribers: set[asyncio.Queue] = set()
_announce_ring: deque = deque(maxlen=_ANNOUNCE_RING_SIZE)
_announce_seq: int = 0
_announce_lock = asyncio.Lock()

# The most recent announcement that carried a camera image, kept in full (with
# the image) so a kiosk that connects *after* the person-detection — a cold
# start, a reconnect, or any client that simply wasn't subscribed at that
# instant — can still show the last front-yard snapshot instead of a black box.
# Exactly one image is retained, so this can't balloon memory the way keeping
# images in the whole history ring would. Replaced by the next image event;
# lost on restart, like the ring.
_last_camera_snapshot: dict | None = None


async def _broadcast_announcement(event: dict) -> None:
    """Append to ring buffer and fan out to every connected SSE subscriber.

    Subscribers whose queue is full are presumed dead (real SSE clients
    drain almost instantly) and get evicted from the set. Without this
    cleanup, a kiosk whose TCP connection broke without the bridge's
    StreamingResponse generator noticing (e.g. an abrupt iPad Wi-Fi flap)
    would leave a zombie queue here forever — every subsequent event would
    be silently dropped to that dead queue and the live count would
    misleadingly show `subscribers > 0`.

    Embedded images (`image` field, typically a data URI) are broadcast live
    but stripped from the ring buffer. The kiosk only needs the image while
    the event is fresh; keeping 50–100 KB payloads in the history ring would
    balloon memory and slow every /announcements/history fetch.
    """
    global _announce_seq, _last_camera_snapshot
    async with _announce_lock:
        _announce_seq += 1
        event = {**event, "id": _announce_seq}
        ring_event = {k: v for k, v in event.items() if k != "image"}
        _announce_ring.append(ring_event)
        # Retain the newest image-bearing event in full (see _last_camera_snapshot)
        # so a kiosk that wasn't subscribed at push time can still fetch it.
        if "image" in event:
            _last_camera_snapshot = event
        targets = list(_announce_subscribers)
    dead: list[asyncio.Queue] = []
    for q in targets:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("Evicting unresponsive announcement subscriber (queue full)")
            dead.append(q)
    if dead:
        async with _announce_lock:
            for q in dead:
                _announce_subscribers.discard(q)


async def announce_push_endpoint(request: Request) -> JSONResponse:
    """POST /announcements/push — internal endpoint for announcer.py.

    Body: {"text": "...", "kind": "...", "priority": 0..3, "key": "...", "ts": <epoch>,
           "image": "data:image/...;base64,...", "image_duration_s": 300}
      - text: Finnish sentence to speak
      - kind: short event class (hvac_freezing, sauna_on, light_on, ...)
      - priority: 0=critical, 1=normal, 2=verbose, 3=debug (advisory)
      - key: dedup key — kiosk replaces older queued items with same key
      - ts:  source-side epoch seconds (optional)
      - image: optional data URI to display on the kiosk (camera snapshot, …)
      - image_duration_s: seconds to keep the image visible (default 300)

    Auth: if ANNOUNCE_PUSH_TOKEN env is set, the request must carry
    X-Announce-Token matching it. Defaults to open inside the docker network.
    """
    if _ANNOUNCE_PUSH_TOKEN:
        if request.headers.get("x-announce-token", "") != _ANNOUNCE_PUSH_TOKEN:
            return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)

    import time as _time
    event = {
        "text": text,
        "kind": str(body.get("kind") or "info"),
        "priority": int(body.get("priority") or 1),
        "key": str(body.get("key") or ""),
        # Default to ingress time so the kiosk can decide freshness of replayed
        # events (e.g. don't speak an hour-old event after a kiosk reload).
        "ts": float(body.get("ts") or 0) or _time.time(),
    }
    image = body.get("image")
    if isinstance(image, str) and image.startswith("data:"):
        event["image"] = image
        dur = body.get("image_duration_s")
        if dur is not None:
            try:
                event["image_duration_s"] = float(dur)
            except (TypeError, ValueError):
                pass
    await _broadcast_announcement(event)
    return JSONResponse({"ok": True, "id": _announce_seq, "subscribers": len(_announce_subscribers)})


async def announce_history_endpoint(request: Request) -> JSONResponse:
    """GET /announcements/history?limit=N — recent events from the ring.

    Returned in chronological order (oldest first) so the kiosk can append
    them straight onto its history list. Survives only as long as the bridge
    process — restart loses the buffer.
    """
    limit_raw = request.query_params.get("limit", "200")
    try:
        limit = max(1, min(int(limit_raw), _ANNOUNCE_RING_SIZE))
    except ValueError:
        limit = _ANNOUNCE_RING_SIZE
    async with _announce_lock:
        events = list(_announce_ring)[-limit:]
    return JSONResponse({"events": events, "ring_size": _ANNOUNCE_RING_SIZE})


async def announce_camera_endpoint(request: Request) -> JSONResponse:
    """GET /announcements/camera — the last announcement that carried a camera
    image, in full (including the `image` data URI), or `{"snapshot": null}`.

    /stream broadcasts images live but /history strips them to keep the ring
    lean, so a kiosk that wasn't subscribed at the instant of a person-detection
    has no way to show the front-yard snapshot and falls back to a black
    placeholder. This hands it the last frame on demand — polled on the kiosk's
    normal refresh cadence — decoupled from the event feed so it never speaks or
    re-lists a stale alert. Survives only as long as the bridge process.
    """
    async with _announce_lock:
        snapshot = _last_camera_snapshot
    return JSONResponse({"snapshot": snapshot})


async def announce_stream_endpoint(request: Request) -> Response:
    """GET /announcements/stream — SSE feed of announcement events.

    Standard EventSource semantics: each line `data: <json>\\n\\n` is one
    announcement. A `:keepalive` comment goes out every ANNOUNCE_KEEPALIVE_SEC
    so nginx (default 60s read timeout, 120s here) doesn't drop the connection.

    Replay: if the client sends `Last-Event-ID: <n>`, we replay any ring-buffer
    events with id > n before streaming live ones.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    last_id = 0
    raw_last = request.headers.get("last-event-id", "")
    if raw_last.isdigit():
        last_id = int(raw_last)

    # Snapshot ring + register subscriber atomically so we don't miss an event
    # that lands between the replay snapshot and the subscription.
    async with _announce_lock:
        replay = [e for e in _announce_ring if e["id"] > last_id]
        _announce_subscribers.add(queue)

    async def gen():
        try:
            for ev in replay:
                yield f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=_ANNOUNCE_KEEPALIVE_SEC)
                except asyncio.TimeoutError:
                    # Send the keepalive as a named event (not a comment) so
                    # the client can listen for it via addEventListener and
                    # detect a silent connection. EventSource never fires any
                    # callback for `:` comment lines.
                    yield f"event: keepalive\ndata: {{}}\n\n"
                    continue
                yield f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            async with _announce_lock:
                _announce_subscribers.discard(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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

    # Pre-warm Whisper so the first iPad transcription doesn't pay a 30s load cost
    asyncio.get_event_loop().run_in_executor(None, _get_whisper_model)

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
        Route("/announcements/stream", announce_stream_endpoint),
        Route("/announcements/history", announce_history_endpoint),
        Route("/announcements/camera", announce_camera_endpoint),
        Route("/announcements/push", announce_push_endpoint, methods=["POST"]),
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
