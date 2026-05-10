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
import { ttsAudio, greetingOverlay, reportText } from '../dom/elements.js';
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

// Cap on announcements played in a single conversation interlude (between
// the AI reply finishing and listening reopening). Keeps the conversation
// from turning into an announcement reading session — the rest stays
// queued for the next idle window. Critical (priority 0) events are
// always played and don't count against this cap.
const PER_INTERLUDE_MAX = 2;

// Cap on events shown on the Kuulutukset carousel slide. Older entries are
// pruned from the DOM. Sized large enough to span a normal day's worth of
// events at verbosity 3.
const HISTORY_MAX = 200;

// Replayed events older than this (server `ts` field) are kept in the slide
// for context but NOT enqueued for playback. Without this, a kiosk reload
// would cause the bridge's SSE replay to suddenly speak hours of backlog at
// the user. Kept generous enough that an event pushed seconds before a
// reload still gets spoken once the kiosk recovers.
const PLAYBACK_FRESHNESS_S = 120;

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

/**
 * Speak an announcement with a brief visible avatar so the user can look up
 * and see what's being said. Reuses the greeting overlay in `minimized` mode
 * (188px avatar in bottom-right + report text) for the duration of the TTS,
 * then hides it again. If the overlay is already visible (greeting in
 * progress, conversation interlude), leaves the existing UI untouched —
 * the caller has already arranged the right visual context.
 */
async function speakWithOverlay(text: string): Promise<void> {
  const ownsOverlay = !greetingOverlay.classList.contains('visible');
  let prevReport: string | null = null;
  if (ownsOverlay) {
    prevReport = reportText.textContent;
    reportText.textContent = text;
    greetingOverlay.classList.add('visible', 'minimized', 'announcement');
  }
  try {
    await speakAndWait(text);
  } finally {
    if (ownsOverlay) {
      greetingOverlay.classList.remove('visible', 'minimized', 'announcement');
      reportText.textContent = prevReport ?? '';
    }
  }
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
    await speakWithOverlay(ev.text);
  } catch (err) {
    debugLog(`announce: speak failed: ${err}`);
  }
  if (liveQueue.length > 0) scheduleDrain();
}

/**
 * Play pending announcements in the conversation interlude — between the
 * avatar's reply and re-opening the mic. Bypasses the canSpeakNow gate
 * because the caller has already asserted it's safe to speak. Plays at most
 * PER_INTERLUDE_MAX queued events plus all critical (priority 0) ones, then
 * returns; the rest stays for the next idle drain so we don't turn each
 * turn-take into an announcement reading session.
 */
export async function speakPendingInInterlude(): Promise<void> {
  if (liveQueue.length === 0) return;

  let normalSpoken = 0;
  while (liveQueue.length > 0) {
    const next = liveQueue[0]!;
    const isCritical = next.priority === 0;
    if (!isCritical && normalSpoken >= PER_INTERLUDE_MAX) break;
    liveQueue.shift();
    if (!isCritical) normalSpoken++;
    debugLog(`announce: interlude speaking [${next.kind}/p${next.priority}] ${next.text.slice(0, 60)}`);
    try {
      await speakWithOverlay(next.text);
    } catch (err) {
      debugLog(`announce: interlude speak failed: ${err}`);
    }
  }
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
  // Slide dedup is driven by the `data-id` attribute inside appendHistoryItem
  // — the same event arriving via history-fetch + SSE replay only renders
  // once. We deliberately do NOT short-circuit on lastSeenId here, because
  // the SSE replay still contains items the kiosk may need to speak (e.g.
  // events generated in the last few seconds before the kiosk reconnected).
  if (typeof ev.id === 'number' && ev.id > lastSeenId) lastSeenId = ev.id;

  appendHistoryItem(ev);

  // Replayed old events stay in the slide but don't get spoken — see
  // PLAYBACK_FRESHNESS_S for the rationale. Test pushes without a ts have
  // ts === null and are treated as fresh.
  if (typeof ev.ts === 'number') {
    const ageS = Date.now() / 1000 - ev.ts;
    if (ageS > PLAYBACK_FRESHNESS_S) {
      debugLog(`announce: skipping playback [${ev.kind}] — ${Math.round(ageS)}s old (replay)`);
      return;
    }
  }

  // Critical events (priority 0) bypass quiet hours — these are the
  // "wake the house" cases: HVAC freezing, sauna left on overnight,
  // overheated heater. Anything else gets deferred to the morning digest.
  if (isQuietHours() && ev.priority > 0) {
    enqueueDigest(ev);
    debugLog(`announce: queued for digest [${ev.kind}/p${ev.priority}] (quiet hours)`);
    return;
  }
  enqueueLive(ev);
}

// -- History slide --------------------------------------------------------

function formatTime(ts: number | null): string {
  const d = ts ? new Date(ts * 1000) : new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function ensureHistoryDom(): { list: HTMLElement; count: HTMLElement; empty: HTMLElement } | null {
  const slide = document.getElementById('announcements-slide');
  if (!slide) return null;
  if (!slide.dataset.initialized) {
    slide.innerHTML = `
      <div class="ann-header">
        <h2>Kuulutukset</h2>
        <span class="ann-count">0</span>
      </div>
      <div class="ann-empty">Ei vielä kuulutuksia.</div>
      <div class="ann-list" hidden></div>
    `;
    slide.dataset.initialized = '1';
  }
  const list  = slide.querySelector('.ann-list')  as HTMLElement;
  const count = slide.querySelector('.ann-count') as HTMLElement;
  const empty = slide.querySelector('.ann-empty') as HTMLElement;
  return { list, count, empty };
}

function appendHistoryItem(ev: AnnouncementEvent): void {
  const dom = ensureHistoryDom();
  if (!dom) return;
  // Dedup the slide: same event from history-fetch + SSE replay must only
  // render once. id is monotonic on the bridge so it's a stable key.
  if (ev.id && dom.list.querySelector(`[data-id="${CSS.escape(String(ev.id))}"]`)) {
    return;
  }

  // Demote the previously-latest item — only one row carries the .ann-latest
  // highlight so the user's eye lands on the new one.
  dom.list.querySelector('.ann-item.ann-latest')?.classList.remove('ann-latest');

  const item = document.createElement('div');
  item.className = `ann-item ann-latest ann-prio-${Math.max(0, Math.min(3, ev.priority))}`;
  item.dataset.id = String(ev.id);
  item.innerHTML = `
    <span class="ann-dot"></span>
    <span class="ann-time"></span>
    <span class="ann-text"></span>
  `;
  (item.querySelector('.ann-time') as HTMLElement).textContent = formatTime(ev.ts);
  (item.querySelector('.ann-text') as HTMLElement).textContent = ev.text;

  // Newest at the BOTTOM — list reads top-to-bottom chronologically.
  dom.list.appendChild(item);
  while (dom.list.childElementCount > HISTORY_MAX) {
    dom.list.firstElementChild?.remove();
  }
  dom.count.textContent = String(dom.list.childElementCount);
  dom.empty.hidden = true;
  dom.list.hidden = false;
  // Keep the latest visible — scroll to the bottom whenever a new item lands.
  dom.list.scrollTop = dom.list.scrollHeight;
}

async function loadInitialHistory(): Promise<void> {
  ensureHistoryDom();  // create the empty-state DOM up-front
  try {
    const res = await fetch('/api/chat/announcements/history?limit=200');
    if (!res.ok) return;
    const data = await res.json() as { events?: AnnouncementEvent[] };
    if (!data.events || data.events.length === 0) return;
    // Server returns oldest-first; appendHistoryItem appends to the bottom.
    // Iterate as-is and the newest ends up at the bottom (latest-highlight
    // chasing each successive item until the final one keeps the badge).
    for (const ev of data.events) {
      if (typeof ev.id === 'number' && ev.id > lastSeenId) lastSeenId = ev.id;
      appendHistoryItem(ev);
    }
    debugLog(`announce: loaded ${data.events.length} history items`);
  } catch (err) {
    debugLog(`announce: history load failed: ${err}`);
  }
}

// Last time we received ANY traffic on the SSE stream — message OR keepalive.
// Used by the watchdog below to force reconnect when iPad Safari quietly
// loses the connection without firing the EventSource error callback.
let lastSseActivity = 0;

// Bridge sends a `keepalive` event every 20s. Allow ~3 missed keepalives
// before declaring the stream dead and forcing a reconnect.
const SSE_SILENCE_TIMEOUT_MS = 70_000;

function connect(): void {
  if (eventSource) try { eventSource.close(); } catch {}
  const url = '/api/chat/announcements/stream';
  const es = new EventSource(url);
  eventSource = es;
  lastSseActivity = Date.now();

  es.addEventListener('open', () => {
    lastSseActivity = Date.now();
    debugLog('announce: SSE open');
  });
  es.addEventListener('error', () => {
    debugLog(`announce: SSE error (state=${es.readyState}) — will reconnect`);
    // Browser may auto-reconnect, but on iPad Safari the auto path can
    // stall silently. Close and let the watchdog reopen on its next tick.
    try { es.close(); } catch {}
    if (eventSource === es) eventSource = null;
  });
  es.addEventListener('keepalive', () => {
    lastSseActivity = Date.now();
  });
  es.addEventListener('message', (msg) => {
    lastSseActivity = Date.now();
    try {
      const data = JSON.parse(msg.data) as AnnouncementEvent;
      if (typeof data.id === 'number') lastSeenId = Math.max(lastSeenId, data.id);
      handleEvent(data);
    } catch (err) {
      debugLog(`announce: parse error: ${err}`);
    }
  });
}

function watchdog(): void {
  const idleMs = Date.now() - lastSseActivity;
  const noConn  = !eventSource || eventSource.readyState === 2 /* CLOSED */;
  if (noConn || idleMs > SSE_SILENCE_TIMEOUT_MS) {
    debugLog(`announce: watchdog reconnect (idle=${Math.round(idleMs/1000)}s state=${eventSource?.readyState ?? 'null'})`);
    connect();
  }
}

export function initAnnouncer(): void {
  if (typeof EventSource === 'undefined') {
    debugLog('announce: EventSource not available — announcer disabled');
    return;
  }
  // Backfill the history slide before opening SSE — that way replayed events
  // (Last-Event-ID-driven) and the initial fetch don't double-count, since
  // the history endpoint and the SSE replay share the same ring + ids.
  loadInitialHistory().finally(connect);

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

  // Watchdog: iPad Safari can hold an EventSource open in a state where it
  // looks live (readyState=OPEN) but no traffic actually flows. The bridge
  // emits a `keepalive` event every 20s; if we go > 70s without ANY data,
  // tear down and reconnect.
  setInterval(watchdog, 25_000);
}
