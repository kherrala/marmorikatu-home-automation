// Kiosk-side announcement consumer.
//
// Subscribes via SSE to /api/chat/announcements/stream (claude-bridge), queues
// incoming events, and speaks them via the existing Piper TTS path WITHOUT
// requiring a face-detection greeting. Plays only when the kiosk is otherwise
// idle (READY/COOLDOWN, not speaking, not listening, not processing) so we
// never trample over the conversation flow.
//
// Quiet hours (default 22:00–07:00 local) suppress live playback. Events
// received during quiet hours go into a per-day digest scored by priority;
// the top N items play right after the next post-quiet greeting, prefixed
// with "Yön ajalta tärkeimmät tapahtumat:".
//
// Verbosity is server-side (announcer.py decides what to push). The kiosk
// only filters by quiet-hours policy.

import { speakAndWait } from '../audio/tts.js';
import { getState, select } from '../state/store.js';
import { KioskPhase } from '../types/state.js';
import { ttsAudio } from '../dom/elements.js';
import { debugLog } from '../debug.js';

interface AnnouncementEvent {
  readonly id: number;
  readonly text: string;
  readonly kind: string;
  readonly priority: number;  // 0=critical .. 3=debug
  readonly key: string;
  readonly ts: number | null;
}

// Quiet-hours window — local hours, [start, end). Defaults match typical sleep.
const QUIET_START_HOUR = 22;
const QUIET_END_HOUR   = 7;

// Cap on how many digest items we replay in the morning.
const DIGEST_MAX = 3;

// Cap on the live queue — if announcements pile up because the kiosk is in a
// long conversation, drop oldest verbose ones first so we don't read out an
// hour-old "kitchen light turned on" when we finally get a chance to speak.
const LIVE_QUEUE_MAX = 12;

// localStorage key — record the date we last spoke the morning digest, so a
// page reload doesn't replay it.
const DIGEST_DATE_KEY = 'announcer.digestDate';

const liveQueue: AnnouncementEvent[] = [];
const overnightDigest: AnnouncementEvent[] = [];
let digestPending = false;          // digest collected but not yet spoken
let drainScheduled = false;
let lastSeenId = 0;
let eventSource: EventSource | null = null;

function isQuietHours(now: Date = new Date()): boolean {
  const h = now.getHours();
  if (QUIET_START_HOUR <= QUIET_END_HOUR) {
    return h >= QUIET_START_HOUR && h < QUIET_END_HOUR;
  }
  // Wraps midnight (the normal case): >= 22 OR < 7.
  return h >= QUIET_START_HOUR || h < QUIET_END_HOUR;
}

function todayKey(): string {
  const d = new Date();
  // Local date — quiet hours wrap midnight, but we key the digest by the
  // morning it gets played, so use today's local date at speak time.
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function digestAlreadyPlayedToday(): boolean {
  try { return localStorage.getItem(DIGEST_DATE_KEY) === todayKey(); }
  catch { return false; }
}

function markDigestPlayed(): void {
  try { localStorage.setItem(DIGEST_DATE_KEY, todayKey()); } catch {}
}

function canSpeakNow(): boolean {
  const s = getState();
  // Speak in any "idle but live" phase. Skip GREETING (would talk over the
  // greeting), INIT/FAILED (kiosk not usable), and explicit dismiss windows.
  const okPhase =
    s.phase === KioskPhase.READY ||
    s.phase === KioskPhase.COOLDOWN ||
    s.phase === KioskPhase.DASHBOARD_ONLY;
  if (!okPhase) return false;
  if (s.processing) return false;
  if (s.voice.listeningActive) return false;
  // ttsAudio is the shared HTMLAudioElement used by the existing TTS path.
  // If it's currently playing something, we'd race with greeting/conversation.
  if (ttsAudio && !ttsAudio.paused) return false;
  // speechSynthesis fallback path — also a guard.
  if (typeof speechSynthesis !== 'undefined' && speechSynthesis.speaking) return false;
  return true;
}

function enqueueLive(ev: AnnouncementEvent): void {
  // Dedup by key — newer event with the same key replaces the older one.
  if (ev.key) {
    for (let i = liveQueue.length - 1; i >= 0; i--) {
      if (liveQueue[i]!.key === ev.key) liveQueue.splice(i, 1);
    }
  }
  liveQueue.push(ev);
  // Cap the queue: drop the lowest-priority (highest priority number) oldest
  // events first. Keep critical (priority 0) no matter what.
  while (liveQueue.length > LIVE_QUEUE_MAX) {
    let dropIdx = -1;
    let dropPrio = -1;
    for (let i = 0; i < liveQueue.length; i++) {
      const p = liveQueue[i]!.priority;
      if (p > dropPrio) { dropPrio = p; dropIdx = i; }
    }
    if (dropIdx < 0 || dropPrio === 0) break;  // would drop a critical → bail
    liveQueue.splice(dropIdx, 1);
  }
  scheduleDrain();
}

function enqueueDigest(ev: AnnouncementEvent): void {
  // Dedup — keep highest-priority (lowest priority number) per key.
  if (ev.key) {
    for (let i = overnightDigest.length - 1; i >= 0; i--) {
      const x = overnightDigest[i]!;
      if (x.key === ev.key) {
        if (x.priority <= ev.priority) return;  // existing is higher (or equal) priority
        overnightDigest.splice(i, 1);
      }
    }
  }
  overnightDigest.push(ev);
  digestPending = true;
}

function scheduleDrain(): void {
  if (drainScheduled) return;
  drainScheduled = true;
  // Defer one tick so multiple enqueues coalesce and to let phase changes settle.
  setTimeout(drain, 250);
}

async function drain(): Promise<void> {
  drainScheduled = false;
  if (!canSpeakNow()) {
    // Re-arm shortly — we'll wake on phase changes too, but a short fallback
    // poll covers cases where the gate flips for non-state reasons.
    if (liveQueue.length > 0) setTimeout(scheduleDrain, 1500);
    return;
  }
  const ev = liveQueue.shift();
  if (!ev) return;

  debugLog(`announce: speaking [${ev.kind}/p${ev.priority}] ${ev.text.slice(0, 60)}`);
  try {
    await speakAndWait(ev.text);
  } catch (err) {
    debugLog(`announce: speak failed: ${err}`);
  }
  if (liveQueue.length > 0) scheduleDrain();
}

function flushDigestIfDue(): void {
  if (!digestPending) return;
  if (isQuietHours()) return;
  if (digestAlreadyPlayedToday()) {
    // Already played today — discard the digest so it doesn't accumulate.
    overnightDigest.length = 0;
    digestPending = false;
    return;
  }
  if (overnightDigest.length === 0) {
    digestPending = false;
    return;
  }

  // Pick top N by priority (lowest number first), then chronological.
  const sorted = [...overnightDigest].sort((a, b) =>
    a.priority - b.priority || (a.ts ?? 0) - (b.ts ?? 0));
  const top = sorted.slice(0, DIGEST_MAX);

  // Compose a single combined announcement so it plays as one TTS roundtrip.
  const intro = top.length === 1
    ? 'Yön aikana tapahtui:'
    : `Yön aikana ${overnightDigest.length === top.length
        ? top.length
        : `${overnightDigest.length} tapahtumaa, joista tärkeimmät`}:`;
  const combined = `${intro} ${top.map(e => e.text).join(' ')}`;

  enqueueLive({
    id: 0,
    text: combined,
    kind: 'morning_digest',
    priority: 1,
    key: 'morning_digest',
    ts: Date.now() / 1000,
  });
  markDigestPlayed();
  overnightDigest.length = 0;
  digestPending = false;
}

function handleEvent(ev: AnnouncementEvent): void {
  if (ev.id <= lastSeenId) return;
  lastSeenId = ev.id;

  if (isQuietHours()) {
    enqueueDigest(ev);
    debugLog(`announce: queued for digest [${ev.kind}/p${ev.priority}] (quiet hours)`);
    return;
  }
  enqueueLive(ev);
}

function connect(): void {
  if (eventSource) try { eventSource.close(); } catch {}
  const url = '/api/chat/announcements/stream';
  const es = new EventSource(url);
  eventSource = es;

  es.addEventListener('open', () => debugLog('announce: SSE open'));
  es.addEventListener('error', () => {
    debugLog('announce: SSE error — browser will auto-reconnect');
  });
  es.addEventListener('message', (msg) => {
    try {
      const data = JSON.parse(msg.data) as AnnouncementEvent;
      if (typeof data.id === 'number') lastSeenId = Math.max(lastSeenId, data.id);
      handleEvent(data);
    } catch (err) {
      debugLog(`announce: parse error: ${err}`);
    }
  });
}

export function initAnnouncer(): void {
  if (typeof EventSource === 'undefined') {
    debugLog('announce: EventSource not available — announcer disabled');
    return;
  }
  connect();

  // Kick the drain on every phase change — that's when canSpeakNow() can
  // flip true (e.g., GREETING → COOLDOWN) and we want to start speaking
  // immediately rather than wait for the 1.5s fallback poll.
  select(s => s.phase).subscribe(() => {
    flushDigestIfDue();
    if (liveQueue.length > 0) scheduleDrain();
  });
  // Same for processing flag — speaking gate flips when AI processing ends.
  select(s => s.processing).subscribe(() => {
    if (liveQueue.length > 0) scheduleDrain();
  });

  // Periodic digest check — when the local clock crosses out of quiet hours
  // mid-session (e.g., 06:59 → 07:00) without any phase change.
  setInterval(flushDigestIfDue, 60_000);
}
