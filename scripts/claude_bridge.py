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

import anthropic
import edge_tts
import uvicorn
from mcp.client.sse import sse_client
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
        f"Käytä AINA työkaluja. ÄLÄ keksi tietoja.\n"
        f"Nyt on {date_str}, kello {time_str}.\n"
        f"Vastaa lyhyesti suomeksi ilman muotoilua."
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


# Tools excluded from Ollama — too low-level or technical for a voice kiosk.
# Claude fallback still gets the full set.
_OLLAMA_EXCLUDED_TOOLS = {
    "describe_schema", "list_measurements", "describe_measurement",
    "query_data", "get_time_range", "get_statistics",
    "get_thermia_register_data",
}


def _aggregated_tools(for_ollama: bool = False) -> list[dict]:
    """Return combined Claude-format tools from all connected servers."""
    tools = []
    for info in _servers.values():
        tools.extend(info["tools_claude"])
    if for_ollama:
        tools = [t for t in tools if t["name"] not in _OLLAMA_EXCLUDED_TOOLS]
    return tools


def _find_session(tool_name: str) -> ClientSession | None:
    """Find the MCP session that owns a given tool."""
    for info in _servers.values():
        if tool_name in info["tool_names"]:
            return info["session"]
    return None


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
        ollama_messages.append({"role": m["role"], "content": m["content"]})

    async with httpx.AsyncClient(timeout=120) as client:
        for iteration in range(MAX_TOOL_ITERATIONS):
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": ollama_messages,
                    "tools": openai_tools,
                    "stream": False,
                    "options": {
                        "num_ctx": OLLAMA_NUM_CTX,
                        "temperature": 0.1,
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
                        log.info("Ollama tool result [%d]: %s → %d chars", iteration + 1, tool_name, len(result_text))
                    except Exception as e:
                        result_text = f"Error calling {tool_name}: {type(e).__name__}: {e}"
                        log.error("Tool error: %s", result_text, exc_info=True)

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
    try:
        log.info("Trying Ollama (%s) with %d tools...", OLLAMA_MODEL, len(ollama_tools))
        result = await run_ollama_agentic_loop(messages, ollama_tools)
        return JSONResponse(result)
    except Exception as e:
        log.warning("Ollama failed (%s), falling back to Claude", e)

    try:
        log.info("Falling back to Claude (%s) with %d tools...", CLAUDE_MODEL, len(all_tools))
        result = await run_claude_agentic_loop(messages, all_tools)
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

    async def audio_stream():
        try:
            communicate = edge_tts.Communicate(text, voice)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]
        except Exception as e:
            log.error("TTS streaming error: %s", e)

    return StreamingResponse(
        audio_stream(),
        media_type="audio/mpeg",
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

    log.info("Transcribe: received %d bytes (%s)", len(audio_bytes), filename)

    try:
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "webm"
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            loop = asyncio.get_event_loop()
            model = await loop.run_in_executor(None, _get_whisper_model)
            segments, _info = await loop.run_in_executor(
                None, lambda: model.transcribe(tmp.name, language="fi", beam_size=3)
            )
            text = " ".join(s.text.strip() for s in segments).strip()
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
        Route("/tts", tts_endpoint, methods=["POST"]),
        Route("/transcribe", transcribe_endpoint, methods=["POST"]),
        Route("/health", health_endpoint),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=BRIDGE_PORT, log_level="info")
