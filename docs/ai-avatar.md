# AI Avatar Agent

A voice-activated AI assistant displayed on the kiosk. When the kiosk detects
a face via its camera, an animated avatar greets the user, listens for spoken
Finnish, queries building automation data through MCP tools, and speaks the
response aloud.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Kiosk (browser)                                                 │
│                                                                  │
│  Camera ──▶ face-api.js ──▶ Avatar overlay                       │
│  Microphone ──▶ Web Speech API / MediaRecorder ──▶ Transcript    │
│  Speaker ◀── <audio> element ◀── TTS audio stream                │
└────────┬────────────────────────────────────────────┬────────────┘
         │ /chat                        /tts          │ /transcribe
         ▼                                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  Claude Bridge (:3002)                                           │
│                                                                  │
│  /chat ────▶ Ollama (primary) ──┐                                │
│             Claude (fallback) ──┼──▶ Agentic loop with tools     │
│                                 │                                │
│  /tts ─────▶ edge-tts ─────────▶ audio/mpeg stream               │
│  /transcribe ▶ faster-whisper ─▶ {"text": "..."}                 │
└────────────────────────┬─────────────────────────────────────────┘
                         │ MCP (SSE)
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│  MCP Server (:3001)                                              │
│                                                                  │
│  get_latest, get_thermia_status, get_room_temperatures,          │
│  get_air_quality, get_freezing_probability, ...                  │
│                                     │                            │
│                                     ▼                            │
│                                InfluxDB                          │
└──────────────────────────────────────────────────────────────────┘
```

## Claude Bridge

The bridge (`scripts/claude_bridge.py`) is an HTTP server that sits between
the kiosk UI and the LLM backends. It handles three concerns:

1. **Chat** — agentic loop with MCP tool calling
2. **TTS** — server-side Finnish speech synthesis
3. **Transcription** — server-side speech-to-text

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/chat` | POST | Send conversation messages, receive AI response with tool results |
| `/tts` | POST | Convert text to Finnish speech, returns audio/mpeg stream |
| `/transcribe` | POST | Convert audio to text (Finnish), returns `{"text": "..."}` |
| `/health` | GET | MCP connection status, tool count, model availability |

### Model Selection

The bridge tries models in order, falling back automatically on failure:

| Priority | Model | Configuration | Tool Set |
|----------|-------|---------------|----------|
| Primary | Ollama (local) | `OLLAMA_MODEL` (default: `qwen3.5:9b`) | Reduced — excludes low-level query tools |
| Fallback | Claude API | `CLAUDE_MODEL` (default: `claude-haiku-4-5-20251001`) | Full tool set |

Ollama runs on a separate machine (`OLLAMA_URL`) and gets a filtered tool set
that excludes technical tools like `query_data`, `describe_schema`, and
`get_statistics` — these are too low-level for a voice assistant context.
If Ollama fails (network error, timeout, model not loaded), the bridge falls
back to Claude via the Anthropic API.

### Agentic Loop

Both model backends run an agentic loop:

1. User message + system prompt + available tools sent to LLM
2. If the LLM returns tool calls, the bridge executes them against the MCP
   server and feeds results back
3. Loop repeats until the LLM produces a final text response (or hits
   `MAX_TOOL_ITERATIONS`, default 10)

Each tool call has a 15-second timeout. Dead MCP sessions are detected
automatically and trigger reconnection.

### System Prompt

Generated dynamically with the current Finnish date, time, and weekday.
Instructs the model to:

- Always use tools — never fabricate data
- Respond in short, spoken Finnish (responses are read aloud)
- Understand that the user is at home
- Not flag the basement temperature as a problem (it's intentionally lower)

### Audio Processing

**Text-to-speech** uses Microsoft Edge TTS (`edge-tts`) with the
`fi-FI-NooraNeural` voice at +15% speed. Audio streams as chunked
`audio/mpeg` and plays through the browser's `<audio>` element. This approach
was chosen over the browser's `speechSynthesis` API because iOS does not honor
the volume property of `SpeechSynthesisUtterance`.

**Speech-to-text** uses `faster-whisper` (CTranslate2-based Whisper) running
on CPU with int8 quantization. The model is lazy-loaded on first request.
Accepts audio in any ffmpeg-compatible format (webm, mp4, etc.) and transcribes
in Finnish.

### MCP Integration

The bridge maintains persistent SSE connections to one or more MCP servers
(comma-separated `MCP_URLS`). Features:

- Automatic reconnection with exponential backoff (5s → 60s)
- Health checks every 5 minutes
- Dead session detection on `ClosedResourceError`, `EndOfStream`, etc.
- Tools from multiple MCP servers are aggregated into a single tool set

## Kiosk Integration

### Face Detection

The kiosk uses `face-api.js` with the TinyFaceDetector model:

- Runs every 500ms on the camera feed
- Requires 3 consecutive detections (~1.5s) to trigger a greeting
- Requires 8 consecutive non-detections before registering face as "gone"
- Dismisses the avatar overlay 15 seconds after the face disappears
- Never dismisses within the first 30 seconds of a greeting
- Absolute maximum overlay duration: 5 minutes

### Conversation Flow

1. **Face detected** — avatar appears with a time-aware Finnish greeting
   ("Huomenta!", "Päivää!", "Iltaa!", "Yötä!"), optionally with a morning
   jingle (5–10 AM)
2. **Listening** — speech captured via Web Speech API (Chrome/Edge) or
   MediaRecorder + Whisper (Safari/iOS). Silence detection (1.5s) ends input.
3. **Processing** — transcript sent to bridge `/chat` with conversation
   history. LLM calls MCP tools as needed.
4. **Speaking** — response displayed and spoken via TTS. Avatar mouth
   animates.
5. **Loop** — if face is still visible, returns to listening
6. **Dismiss** — face disappears, user says farewell ("heippa", "näkemiin"),
   or timeout

If the user doesn't speak within 5 seconds, the avatar generates an
auto-summary: a daily briefing covering news highlights, weather, and home
status.

### Speech Recognition Fallback Chain

| Browser | Method | Details |
|---------|--------|---------|
| Chrome, Edge | Web Speech API | Native, `fi-FI` language, interim results |
| Safari, iOS | MediaRecorder + Whisper | Records audio, sends to `/transcribe` |

After 3 consecutive Web Speech API failures, the kiosk switches to the
MediaRecorder path for the remainder of the session.

### Fallback Responses

When both LLM backends are unavailable, the kiosk generates whimsical Finnish
responses locally — absurd observations, existential musings, or fake
statistics.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_URLS` | `http://mcp:3001/mcp` | Comma-separated MCP server URLs |
| `OLLAMA_URL` | `http://192.168.1.36:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `qwen3.5:9b` | Ollama model name |
| `OLLAMA_NUM_CTX` | `16384` | Ollama context window size |
| `CLAUDE_MODEL` | `claude-haiku-4-5-20251001` | Claude model ID |
| `ANTHROPIC_API_KEY` | — | Required for Claude fallback |
| `MAX_TOOL_ITERATIONS` | `10` | Max agentic loop iterations |
| `MAX_TOKENS` | `300` | Max LLM output tokens |
| `TTS_VOICE` | `fi-FI-NooraNeural` | Edge TTS voice |
| `TTS_RATE` | `+15%` | TTS speaking rate |
| `BRIDGE_PORT` | `3002` | HTTP server port |

### Docker Compose

```yaml
claude-bridge:
  build:
    context: .
    dockerfile: Dockerfile.claude-bridge
  container_name: marmorikatu-claude-bridge
  ports:
    - "3002:3002"
  environment:
    - MCP_URLS=http://mcp:3001/mcp
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
    - CLAUDE_MODEL=claude-haiku-4-5-20251001
    - OLLAMA_URL=http://192.168.1.36:11434
    - OLLAMA_MODEL=qwen3.5:9b
    - OLLAMA_NUM_CTX=32768
    - BRIDGE_PORT=3002
  depends_on:
    mcp:
      condition: service_healthy
  restart: unless-stopped
```

### Using a Different Model

To use a different Ollama model:

```bash
# Pull the model on your Ollama host
ollama pull llama3.1:8b

# Update docker-compose.yml or override
OLLAMA_MODEL=llama3.1:8b docker compose up -d claude-bridge
```

To use Claude as the primary model (skip Ollama), either stop the Ollama
server or set `OLLAMA_URL` to an unreachable address — the bridge will fail
over to Claude on every request.

## Troubleshooting

```bash
# Check bridge health (MCP connections, model status)
curl http://localhost:3002/health

# View bridge logs
docker compose logs -f claude-bridge

# Test TTS
curl -X POST http://localhost:3002/tts \
  -H 'Content-Type: application/json' \
  -d '{"text": "Hei, tämä on testi"}' \
  --output test.mp3

# Test chat
curl -X POST http://localhost:3002/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "Mikä on ulkolämpötila?"}]}'
```
