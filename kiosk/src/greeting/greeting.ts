import { Subject, Subscription, timer, EMPTY } from 'rxjs';
import { switchMap, takeUntil, filter } from 'rxjs/operators';
import { dispatch, getState, select } from '../state/store.js';
import { KioskPhase } from '../types/state.js';
import {
  GREETING_COOLDOWN, MAX_OVERLAY_DURATION, JINGLE_DURATION,
  QUOTE_COOLDOWN, SILENCE_AUTO_SUMMARY_MS, BUS_LEAVE_SOON_MS,
} from '../config/constants.js';
import { NYSSE_IDX, NEWS_IDX } from '../config/slides.js';
import { speakAndWait } from '../audio/tts.js';
import { randomFallback } from '../content/fallbacks.js';
import { generateAIResponse } from './conversation.js';
import { clearAvatar } from '../dom/avatar.js';
import {
  greetingOverlay, greetingText, reportText, reportSpinner,
  userTextEl, jingleAudio, ttsAudio,
} from '../dom/elements.js';

// -- RxJS cancellation signals --
// Emits when the current greeting ends (dismiss or new greeting).
// All greeting-scoped timers use takeUntil(greetingEnd$) for auto-cleanup.
const greetingEnd$ = new Subject<void>();

// Emits to (re)schedule the overlay safety timeout
const scheduleOverlay$ = new Subject<void>();

let greetingActiveAt = 0;
let greetingEpoch = 0;
let jingleTimeout: ReturnType<typeof setTimeout> | null = null;

// Active RxJS subscriptions for the current greeting session
let greetingSubs = new Subscription();

// External handlers wired up by main.ts
let showSlideFn: ((idx: number) => void) | null = null;
let startListeningFn: (() => void) | null = null;
let stopListeningFn: (() => void) | null = null;
let pauseListeningFn: (() => void) | null = null;

export function setGreetingHandlers(handlers: {
  showSlide: (idx: number) => void;
  startListening: () => void;
  stopListening: () => void;
  pauseListening: () => void;
}): void {
  showSlideFn = handlers.showSlide;
  startListeningFn = handlers.startListening;
  stopListeningFn = handlers.stopListening;
  pauseListeningFn = handlers.pauseListening;
}

export function getGreetingActiveAt(): number {
  return greetingActiveAt;
}

export function getGreetingEpoch(): number {
  return greetingEpoch;
}

export async function triggerGreeting(): Promise<void> {
  // End previous greeting (cancels all its timers)
  endGreeting();

  greetingEpoch++;
  const epoch = greetingEpoch;
  greetingSubs = new Subscription();

  dispatch({ type: 'SET_PHASE', phase: KioskPhase.GREETING });
  const now = Date.now();
  dispatch({ type: 'GREETING_START', time: now });

  // -- Set up RxJS timers for this greeting session --

  // Overlay safety timeout: auto-dismiss after MAX_OVERLAY_DURATION.
  // Re-schedulable via scheduleOverlay$.next() (called from startListening).
  greetingSubs.add(
    scheduleOverlay$.pipe(
      switchMap(() => {
        const remaining = MAX_OVERLAY_DURATION - (Date.now() - getState().greeting.overlayStartTime);
        return remaining > 0 ? timer(remaining) : EMPTY;
      }),
      takeUntil(greetingEnd$),
    ).subscribe(() => dismissGreeting()),
  );

  // Cooldown → READY transition (activated when phase enters COOLDOWN)
  greetingSubs.add(
    select(s => s.phase).pipe(
      filter(p => p === KioskPhase.COOLDOWN),
      switchMap(() => timer(GREETING_COOLDOWN)),
      takeUntil(greetingEnd$),
    ).subscribe(() => {
      if (getState().phase === KioskPhase.COOLDOWN) {
        dispatch({ type: 'SET_PHASE', phase: KioskPhase.READY });
      }
    }),
  );

  // Deferred dismiss retry: when dismiss is called during processing,
  // poll every 2s until processing clears.
  greetingSubs.add(
    deferredDismiss$.pipe(
      switchMap(() => timer(2000)),
      takeUntil(greetingEnd$),
    ).subscribe(() => dismissGreeting()),
  );

  // Switch to relevant slide
  try {
    const res = await fetch('/api/departures');
    if (res.ok) {
      const departures = await res.json() as Array<{ departureMs: number }>;
      const busSoon = departures.some(d => d.departureMs > 0 && d.departureMs <= BUS_LEAVE_SOON_MS);
      showSlideFn?.(busSoon && NYSSE_IDX >= 0 ? NYSSE_IDX : NEWS_IDX);
    } else {
      showSlideFn?.(NEWS_IDX);
    }
  } catch {
    showSlideFn?.(NEWS_IDX);
  }

  if (epoch !== greetingEpoch) return;

  userTextEl.textContent = '';

  // Time-appropriate greeting
  const h = new Date().getHours();
  let greeting: string;
  if (h >= 5 && h < 10) greeting = 'Huomenta!';
  else if (h >= 10 && h < 17) greeting = 'Päivää!';
  else if (h >= 17 && h < 22) greeting = 'Iltaa!';
  else greeting = 'Yötä!';

  if (h >= 5 && h < 10) startJingle();

  greetingText.textContent = greeting;
  reportText.textContent = '';
  greetingActiveAt = Date.now();
  greetingOverlay.classList.add('visible');

  // Random quote (once per 3 hours)
  const s = getState();
  if (now - s.greeting.lastQuoteTime >= QUOTE_COOLDOWN) {
    dispatch({ type: 'SET_QUOTE_TIME', time: now });
    const quote = randomFallback();
    reportText.textContent = quote;
    dispatch({ type: 'CONVERSATION_ADD', message: { role: 'assistant', content: quote } });
    await speakAndWait(greeting);
    if (epoch !== greetingEpoch) return;
    await speakAndWait(quote);
  } else {
    await speakAndWait(greeting);
  }

  if (epoch !== greetingEpoch) return;

  greetingOverlay.classList.add('minimized');
  startListeningFn?.();
}

// Trigger to reschedule the overlay safety timeout
export function scheduleOverlayDismiss(): void {
  scheduleOverlay$.next();
}

// Signal for deferred dismiss retries
const deferredDismiss$ = new Subject<void>();

export function dismissGreeting(): void {
  const s = getState();
  if (s.phase !== KioskPhase.GREETING) return;

  // Defer if processing — the deferred timer (set up in triggerGreeting) will retry
  if (s.processing) {
    deferredDismiss$.next();
    return;
  }

  console.log('[kiosk] dismissGreeting — overlayAge=%ds epoch=%d',
    Math.round((Date.now() - s.greeting.overlayStartTime) / 1000), greetingEpoch);

  dispatch({ type: 'SET_PHASE', phase: KioskPhase.COOLDOWN });
  dispatch({ type: 'GREETING_DISMISS', time: Date.now() });

  // endGreeting cancels all RxJS timers; the cooldown→READY timer
  // is already subscribed and will fire because it listens for COOLDOWN phase.
  // But we need to NOT end the greeting subs yet — the cooldown timer needs them.
  // So we only signal greetingEnd$ after the cooldown timer fires.
  // Actually, the cooldown timer uses takeUntil(greetingEnd$), so we must NOT
  // emit greetingEnd$ here. It's emitted only when a NEW greeting starts.

  stopJingle();
  stopListeningFn?.();
  speechSynthesis.cancel();
  ttsAudio.pause();
  clearAvatar();
  greetingOverlay.classList.remove('visible', 'minimized');
}

// Called when a new greeting starts or when cleaning up
function endGreeting(): void {
  greetingEnd$.next();
  greetingSubs.unsubscribe();
  greetingSubs = new Subscription();
  stopJingle();
}

// -- Daily report --
export function scheduleDailyReport(): void {
  const s = getState();
  if (s.greeting.autoSummaryGiven || new Date().toISOString().slice(0, 10) === s.greeting.lastReportDate) {
    return;
  }

  const epoch = greetingEpoch;

  // Use RxJS timer with takeUntil for auto-cleanup
  greetingSubs.add(
    timer(SILENCE_AUTO_SUMMARY_MS).pipe(
      takeUntil(greetingEnd$),
    ).subscribe(async () => {
      const st = getState();
      if (epoch !== greetingEpoch) return;
      if (st.phase !== KioskPhase.GREETING || !st.voice.listeningActive
          || st.greeting.autoSummaryGiven || st.voice.voiceInputReceived) return;

      dispatch({ type: 'SET_PROCESSING', processing: true });
      pauseListeningFn?.();
      reportSpinner.classList.remove('hidden');
      try {
        dispatch({
          type: 'CONVERSATION_ADD',
          message: {
            role: 'user',
            content: 'Hae päiväraportti get_daily_report-työkalulla ja tiivistä se lyhyeksi katsaukseksi. Aloita tärkeimmästä uutisesta, sitten sää, kodin tilanne ja kalenterin tapahtumat. Älä luettele lukemia, vaan kerro olennainen.',
          },
        });
        const summary = await generateAIResponse();
        reportSpinner.classList.add('hidden');
        if (epoch !== greetingEpoch) return;
        if (summary) {
          dispatch({ type: 'AUTO_SUMMARY_GIVEN', date: new Date().toISOString().slice(0, 10) });
          reportText.textContent = summary;
          dispatch({ type: 'CONVERSATION_ADD', message: { role: 'assistant', content: summary } });
          await speakAndWait(summary);
        }
      } catch {
        reportSpinner.classList.add('hidden');
      } finally {
        dispatch({ type: 'SET_PROCESSING', processing: false });
      }

      if (epoch !== greetingEpoch) return;
      const after = getState();
      if (after.phase === KioskPhase.GREETING
          && Date.now() - after.greeting.overlayStartTime < MAX_OVERLAY_DURATION) {
        startListeningFn?.();
      }
    }),
  );
}

// No longer needed — RxJS takeUntil handles cleanup
export function clearSilenceTimer(): void {
  // Kept as no-op for API compatibility with listening.ts
}

// -- Jingle --
function startJingle(): void {
  jingleAudio.currentTime = 0;
  jingleAudio.play().catch(() => {});
  if (jingleTimeout !== null) clearTimeout(jingleTimeout);
  jingleTimeout = setTimeout(stopJingle, JINGLE_DURATION);
}

function stopJingle(): void {
  if (jingleTimeout !== null) {
    clearTimeout(jingleTimeout);
    jingleTimeout = null;
  }
  jingleAudio.pause();
  jingleAudio.currentTime = 0;
}
