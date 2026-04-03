# Kiosk Avatar — Business Logic & State Machine

This document describes the intended behaviour of the kiosk avatar for acceptance testing purposes.

## Overview

The kiosk is a wall-mounted iPad running in Safari kiosk mode. It displays rotating Grafana dashboards, weather, news, calendar, and bus departures. When a person approaches, the camera detects their face, triggers a greeting with an AI-powered voice assistant, and enters a conversational mode. The assistant can answer questions about the home, weather, news, and calendar using MCP tools, and stores/recalls memories between sessions using the remind memory layer.

## State Machine

```
                    ┌──────────┐
                    │   INIT   │
                    └────┬─────┘
                         │ user tap (iOS) or auto-activate (desktop)
                         ▼
                    ┌──────────┐
             ┌──────│  READY   │◄─────────────────┐
             │      └────┬─────┘                   │
             │           │ face detected            │ 30s cooldown
             │           │ (5 consecutive, 2.5s)    │
             │           ▼                          │
             │      ┌──────────┐                    │
             │      │ GREETING │──────────────┐     │
             │      └──────────┘              │     │
             │                           dismiss    │
             │                                │     │
             │                           ┌────▼─────┤
             │                           │ COOLDOWN │
             │                           └──────────┘
             │
             ├───► FAILED (camera/model init error)
             └───► DASHBOARD_ONLY (user skipped camera)
```

### Phase Descriptions

| Phase | Description |
|-------|-------------|
| **INIT** | Before any setup. Start overlay is visible. |
| **READY** | Camera running, face detection active. Dashboard carousel rotates. |
| **GREETING** | Conversation active. Avatar overlay visible, voice input enabled. |
| **COOLDOWN** | Post-greeting pause (30s). Face detection runs but does NOT update tracking — prevents immediate re-trigger. |
| **FAILED** | Camera or face detection model failed to initialize. |
| **DASHBOARD_ONLY** | User skipped camera. Carousel runs, no face detection. |

## Initialization Flow

### Intent
The kiosk must unlock iOS audio (requires user gesture) and initialize the camera + face detection model before entering READY state.

### Behaviour

1. **Non-iOS browsers**: Auto-activate if camera+mic permissions are already granted. No tap required.
2. **iOS Safari**: Always show "Kosketa aloittaaksesi" overlay. User must tap to unlock audio.
3. On tap: unlock audio (speechSynthesis, AudioContext, audio elements), initialize camera, load face-api model.
4. On success: enter READY, start face detection, initialize microphone.
5. On failure: show error with retry/skip options.

### Acceptance Criteria
- [ ] iPad: overlay appears, tap initializes camera and audio
- [ ] Desktop Chrome: auto-activates without tap if permissions granted
- [ ] Camera failure: error message shown with retry and skip buttons
- [ ] Skip: enters DASHBOARD_ONLY, carousel works, no face detection

## Face Detection

### Intent
Detect when a person approaches the kiosk and trigger a greeting. Prevent false triggers and rapid re-triggers.

### Behaviour

1. Face detection runs every **500ms** using face-api.js TinyFaceDetector (score threshold 0.35).
2. Requires **5 consecutive** detections (2.5s) to trigger a greeting.
3. Face must have been **absent since the last greeting** — prevents re-triggering while still standing in front.
4. Must respect the **30s cooldown** after the previous greeting dismiss.
5. During **COOLDOWN phase**: detection runs (for status dot) but does NOT update face tracking state. This prevents the `faceAbsentSinceLastGreeting` flag from being set during cooldown, which would allow immediate re-trigger.
6. During **GREETING phase**: tracks face presence for auto-dismiss (see below).

### Acceptance Criteria
- [ ] Walking past briefly (< 2.5s) does NOT trigger greeting
- [ ] Standing in front for 2.5s triggers greeting
- [ ] After dismissal, walking away and returning within 30s does NOT trigger
- [ ] After dismissal, walking away, waiting 30s, and returning DOES trigger
- [ ] Walking away during 30s cooldown and returning does NOT trigger immediately when cooldown expires

## Greeting Session

### Intent
Greet the user with a time-appropriate message, optionally play a jingle and random quote, then enter voice conversation mode.

### Behaviour

1. **Slide switch**: Show news slide (or Nysse if bus departs within 15 min).
2. **Greeting**: "Huomenta!" / "Päivää!" / "Iltaa!" / "Yötä!" based on time of day.
3. **Morning jingle** (05:00-10:00): Play jingle.mp3 for up to 30s.
4. **Random quote** (once per 3 hours): Speak an absurd Finnish sentence.
5. **Minimize overlay**: After greeting speech, move avatar to bottom-right corner.
6. **Start listening**: Wait for jingle to finish, then open microphone.

### Epoch Safety
Each greeting has an epoch counter. All async operations (speakAndWait, fetch) check the epoch after each await — if the greeting was dismissed during the await, the function bails out.

### Acceptance Criteria
- [ ] Morning: jingle plays, greeting spoken, avatar minimizes
- [ ] Afternoon: no jingle, greeting spoken, avatar minimizes
- [ ] Random quote appears at most once per 3 hours
- [ ] If greeting is dismissed during the greeting speech, no further actions occur

## Voice Input

### Intent
Listen for user speech, transcribe it, and process it. Use native Web Speech API on Chrome, MediaRecorder + server-side Whisper on iOS/Safari.

### Platform Detection
- **iOS/Safari**: `webkitSpeechRecognition` exists but is unreliable in kiosk mode. Always use MediaRecorder + Whisper.
- **Chrome/Edge**: Use native `SpeechRecognition` with MediaRecorder as fallback after 5 consecutive failures.

### Native Speech Recognition (Chrome)

1. Start `SpeechRecognition` with `lang: 'fi-FI'`, `continuous: true`, `interimResults: true`.
2. Show interim results in the UI as they arrive.
3. On final result: wait 200ms pause, then process.
4. On interim only: wait 2s (first utterance) or 800ms (subsequent) for pause, then process.
5. Hard timeout: 15s — if no usable result, abort.
6. On `no-speech` or `aborted`: increment silence counter, restart.
7. After 5 consecutive silence failures: fall back to MediaRecorder permanently.
8. On other errors (`not-allowed`, `network`): fall back to MediaRecorder permanently.

### MediaRecorder + Whisper (iOS/Safari)

1. Record audio in WebM/Opus (or WebM/mp4 fallback) with 250ms chunks.
2. Silence detection via RMS analysis (threshold 0.015):
   - First utterance: 1.5s silence after speech → stop and send.
   - Subsequent: 700ms silence after speech → stop and send.
   - No analyser: stop after 5s duration.
   - Hard cap: 10s maximum recording.
3. Send to `/api/chat/transcribe` for Whisper transcription.
4. Recordings under 500ms or with no audio chunks are discarded and restarted.
5. Server rejects recordings under 1KB (empty WebM containers from dead mic streams).

### Acceptance Criteria
- [ ] iPad Safari: MediaRecorder path used, voice is transcribed
- [ ] Desktop Chrome: native recognition used, interim results shown live
- [ ] Chrome: after 5 failures, falls back to MediaRecorder
- [ ] Silence: no spurious transcriptions sent
- [ ] Long speech (up to 10s): captured and transcribed

## Conversation Flow

### Intent
Process transcribed text: detect farewells, generate AI responses, speak them, and resume listening.

### Behaviour

1. **Set processing flag** (prevents face-gone dismiss during AI response wait).
2. **Farewell detection**: Regex matches Finnish/English goodbye phrases. On match: say goodbye, dismiss greeting.
3. **AI response**: Send conversation history to `/api/chat/chat` (90s timeout). Ollama primary (qwen2.5:14b, temperature 0.3), Claude fallback.
4. **Fallback**: If AI unavailable, generate a random Finnish absurd sentence/musing/fake statistic.
5. **TTS**: Speak response via server-side Piper TTS (NDJSON streaming). Browser speechSynthesis as fallback.
6. **Resume listening**: If still within MAX_OVERLAY_DURATION (5 min) and still in GREETING phase.

### Memory Integration
- System prompt instructs the model to call `recall` at conversation start and `remember` to store user preferences, news highlights, and home concerns.
- The bridge strips `episode_type` from remember calls (workaround for a remind library bug).
- After any `remember` call, the bridge triggers non-forced consolidation in the background.

### Acceptance Criteria
- [ ] User says "Heippa" → goodbye spoken, greeting dismissed
- [ ] User asks "Mikä on sää?" → AI calls get_weather_forecast tool, speaks result
- [ ] User says "Muista että nimeni on Kyösti" → AI calls remember tool
- [ ] AI timeout (90s) → random fallback spoken
- [ ] Avatar stays visible during entire AI response wait (processing flag)

## Auto-Dismiss

### Intent
Dismiss the greeting when the user walks away, but not while speaking or processing.

### Behaviour

1. During GREETING: face detection tracks `lastFaceSeenTime`.
2. If face not seen for **15s** AND overlay has been alive for **30s** AND not speaking AND not processing → dismiss.
3. If dismiss is requested while processing (AI response being fetched/spoken): defer dismiss, retry every 2s until processing completes.
4. Safety net: absolute maximum overlay duration of **5 minutes** (RxJS timer, auto-cancels when greeting ends).
5. Manual dismiss: user can tap the greeting card to dismiss.
6. Tap-through guard: taps within 600ms of overlay appearing are ignored (iOS phantom tap protection).

### Acceptance Criteria
- [ ] User walks away for 15s → greeting dismissed (after minimum 30s alive)
- [ ] User walks away while AI is processing → greeting stays until response is spoken, then dismisses
- [ ] User taps greeting card → dismissed immediately
- [ ] Greeting never stays longer than 5 minutes
- [ ] Greeting doesn't dismiss in the first 30s even if face disappears

## Daily Report

### Intent
If the user stands silently for 5 seconds after the greeting, automatically generate and speak a daily summary.

### Behaviour

1. Timer starts when mic opens (after jingle finishes).
2. If no voice input detected within **5 seconds**: pause listening, generate daily report.
3. Report prompt: asks AI to use `get_daily_report` tool and summarize news, weather, home status, calendar.
4. Only once per calendar day (tracked by `lastReportDate`).
5. Sets processing flag during generation (prevents dismiss).
6. After report is spoken: resume listening.
7. Timer auto-cancels via RxJS `takeUntil(greetingEnd$)` if greeting is dismissed.

### Acceptance Criteria
- [ ] Silent user gets daily report after ~5s
- [ ] Report includes news, weather, home status
- [ ] Only generated once per day
- [ ] User speaking within 5s cancels the report timer
- [ ] Report doesn't trigger if voice input was detected

## Carousel

### Intent
Rotate through dashboard slides automatically, with manual navigation.

### Behaviour

1. Auto-advance every **30 seconds**.
2. Pauses on interaction (click, swipe, keyboard, mouse move) for **30 seconds**.
3. Navigation: left/right swipe zones, arrow keys, edge click areas.
4. Grafana theme: auto-switches light/dark based on sunrise/sunset (Tampere coordinates), checks every 60s.
5. Bus departure: auto-switches to Nysse slide if a bus departs within 15 minutes, checks every 30s.
6. Initial slide: Nysse during peak hours (06:00-09:00), otherwise overview.
7. During GREETING: carousel doesn't auto-advance (bus check skipped).

### Acceptance Criteria
- [ ] Slides rotate every 30s
- [ ] Swipe left/right changes slide
- [ ] Mouse movement pauses carousel for 30s
- [ ] Grafana dashboard switches to dark mode after sunset
- [ ] Bus departing soon: auto-switches to Nysse slide

## Version Auto-Reload

### Intent
Automatically reload the page when a new version is deployed.

### Behaviour

1. Checks `/version.txt` every **60 seconds** (cache: no-store).
2. First check: stores the version as baseline.
3. Subsequent checks: if version changed AND not in GREETING phase → reload page.
4. Never reloads during an active greeting (would interrupt conversation).

### Acceptance Criteria
- [ ] New deployment → page reloads within 60s
- [ ] During greeting → reload deferred until greeting ends

## Timer Summary (RxJS)

All greeting-scoped timers use `takeUntil(greetingEnd$)` for automatic cleanup when the greeting ends.

| Timer | Duration | Mechanism | Auto-cancel |
|-------|----------|-----------|-------------|
| Overlay safety | 5 min | `scheduleOverlay$.pipe(switchMap(timer))` | Yes (greetingEnd$) |
| Cooldown→READY | 30s | `select(phase=COOLDOWN).pipe(switchMap(timer))` | Yes (greetingEnd$) |
| Deferred dismiss retry | 2s | `deferredDismiss$.pipe(switchMap(timer))` | Yes (greetingEnd$) |
| Daily report silence | 5s | `timer(5000).pipe(takeUntil(greetingEnd$))` | Yes (greetingEnd$) |
| Face detection | 500ms | `interval(500).pipe(exhaustMap)` | Manual (stopFaceDetection) |
| Carousel advance | 30s | `setInterval` | Never (runs for app lifetime) |
| Bus departure check | 30s | `setInterval` | Never |
| Grafana theme check | 60s | `setInterval` | Never |
| Version check | 60s | `setInterval` | Never |
| Jingle max duration | 30s | `setTimeout` | Cleared on stopJingle |
| SpeechSynthesis keepalive | 10s | `setInterval` | Never (iOS requirement) |

## Constants Reference

| Constant | Value | Purpose |
|----------|-------|---------|
| `FACE_DETECT_INTERVAL` | 500ms | Face detection polling rate |
| `DETECTIONS_REQUIRED` | 5 | Consecutive detections to trigger greeting |
| `GREETING_COOLDOWN` | 30s | Minimum time between greetings |
| `MAX_OVERLAY_DURATION` | 5 min | Absolute max greeting duration |
| `FACE_GONE_DISMISS_MS` | 15s | Face absence before auto-dismiss |
| `MIN_GREETING_ALIVE_MS` | 30s | Minimum greeting duration before face-gone dismiss |
| `SILENCE_AUTO_SUMMARY_MS` | 5s | Silence before daily report triggers |
| `SILENCE_THRESHOLD` | 0.015 RMS | Microphone silence detection threshold |
| `SILENCE_DURATION` | 1.5s | Silence after speech (first utterance) |
| `SILENCE_DURATION_SHORT` | 700ms | Silence after speech (subsequent) |
| `MAX_RECORDING_MS` | 10s | Maximum single recording duration |
| `MAX_NATIVE_SILENCE` | 5 | Native speech failures before MediaRecorder fallback |
| `QUOTE_COOLDOWN` | 3 hours | Minimum time between random quotes |
