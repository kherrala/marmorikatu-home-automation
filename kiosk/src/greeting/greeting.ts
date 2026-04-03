import { dispatch, getState } from '../state/store.js';
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

let overlayTimeout: ReturnType<typeof setTimeout> | null = null;
let jingleTimeout: ReturnType<typeof setTimeout> | null = null;
let silenceAutoSummaryTimer: ReturnType<typeof setTimeout> | null = null;
let greetingActiveAt = 0;

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

export async function triggerGreeting(): Promise<void> {
  dispatch({ type: 'SET_PHASE', phase: KioskPhase.GREETING });
  const now = Date.now();
  dispatch({ type: 'GREETING_START', time: now });

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

  userTextEl.textContent = '';

  // Time-appropriate greeting
  const h = new Date().getHours();
  let greeting: string;
  if (h >= 5 && h < 10) greeting = 'Huomenta!';
  else if (h >= 10 && h < 17) greeting = 'Päivää!';
  else if (h >= 17 && h < 22) greeting = 'Iltaa!';
  else greeting = 'Yötä!';

  if (h >= 5 && h < 10) startJingle();

  // Show overlay
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
    await speakAndWait(quote);
  } else {
    await speakAndWait(greeting);
  }

  // Minimize avatar to bottom-right
  greetingOverlay.classList.add('minimized');

  clearSilenceTimer();
  startListeningFn?.();
}

export function scheduleOverlayDismiss(): void {
  if (overlayTimeout !== null) clearTimeout(overlayTimeout);
  const remaining = MAX_OVERLAY_DURATION - (Date.now() - getState().greeting.overlayStartTime);
  if (remaining <= 0) { dismissGreeting(); return; }
  overlayTimeout = setTimeout(() => dismissGreeting(), remaining);
}

export function clearOverlayTimeout(): void {
  if (overlayTimeout !== null) {
    clearTimeout(overlayTimeout);
    overlayTimeout = null;
  }
}

export function dismissGreeting(): void {
  if (getState().phase !== KioskPhase.GREETING) return;
  console.log('[kiosk] dismissGreeting — overlayAge=%ds',
    Math.round((Date.now() - getState().greeting.overlayStartTime) / 1000));

  const now = Date.now();
  dispatch({ type: 'SET_PHASE', phase: KioskPhase.COOLDOWN });
  dispatch({ type: 'GREETING_DISMISS', time: now });

  clearOverlayTimeout();
  stopJingle();
  stopListeningFn?.();
  clearSilenceTimer();
  speechSynthesis.cancel();
  ttsAudio.pause();
  clearAvatar();
  greetingOverlay.classList.remove('visible', 'minimized');

  setTimeout(() => {
    if (getState().phase === KioskPhase.COOLDOWN) {
      dispatch({ type: 'SET_PHASE', phase: KioskPhase.READY });
    }
  }, GREETING_COOLDOWN);
}

// -- Daily report timer management --
export function scheduleDailyReport(): void {
  const s = getState();
  if (s.greeting.autoSummaryGiven || new Date().toISOString().slice(0, 10) === s.greeting.lastReportDate) {
    return;
  }

  clearSilenceTimer();
  silenceAutoSummaryTimer = setTimeout(async () => {
    const st = getState();
    if (st.phase !== KioskPhase.GREETING || !st.voice.listeningActive
        || st.greeting.autoSummaryGiven || st.voice.voiceInputReceived) return;

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
      if (summary) {
        dispatch({ type: 'AUTO_SUMMARY_GIVEN', date: new Date().toISOString().slice(0, 10) });
        reportText.textContent = summary;
        dispatch({ type: 'CONVERSATION_ADD', message: { role: 'assistant', content: summary } });
        await speakAndWait(summary);
      }
    } catch {
      reportSpinner.classList.add('hidden');
    }

    const after = getState();
    if (after.phase === KioskPhase.GREETING
        && Date.now() - after.greeting.overlayStartTime < MAX_OVERLAY_DURATION) {
      startListeningFn?.();
    }
  }, SILENCE_AUTO_SUMMARY_MS);
}

export function clearSilenceTimer(): void {
  if (silenceAutoSummaryTimer !== null) {
    clearTimeout(silenceAutoSummaryTimer);
    silenceAutoSummaryTimer = null;
  }
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
