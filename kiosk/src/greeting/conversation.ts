import { dispatch, getState } from '../state/store.js';
import { stripThinkTags } from '../content/text-utils.js';
import { randomFallback } from '../content/fallbacks.js';
import { pick } from '../content/text-utils.js';
import { speakAndWait } from '../audio/tts.js';
import { reportText, reportSpinner, userTextEl } from '../dom/elements.js';
import { KioskPhase } from '../types/state.js';

// Only match short farewell-only utterances (max ~30 chars).
// Prevents false matches in longer sentences like "kiitos paljon tiedosta".
const FAREWELL_PATTERNS = /^(heippa|heihei|hei\s*hei|näkemiin|nähdään|moi\s*moi|moikka|kiitos|bye|goodbye|see\s*you)[.!]?\s*$/i;

export function isFarewell(text: string): boolean {
  return FAREWELL_PATTERNS.test(text);
}

export async function generateAIResponse(): Promise<string | null> {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 90000);
    const { greeting } = getState();
    const res = await fetch('/api/chat/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: greeting.conversationHistory }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    if (res.ok) {
      const data = await res.json() as { response?: string };
      const text = data.response?.trim();
      if (text) return stripThinkTags(text);
    }
  } catch { /* Bridge unavailable */ }
  return null;
}


let startListeningFn: (() => void) | null = null;
let dismissGreetingFn: (() => void) | null = null;
let pauseListeningFn: (() => void) | null = null;

export function setConversationHandlers(handlers: {
  startListening: () => void;
  dismissGreeting: () => void;
  pauseListening: () => void;
}): void {
  startListeningFn = handlers.startListening;
  dismissGreetingFn = handlers.dismissGreeting;
  pauseListeningFn = handlers.pauseListening;
}

export async function handleVoiceResult(transcript: string): Promise<void> {
  userTextEl.textContent = `"${transcript}"`;
  dispatch({ type: 'SET_PROCESSING', processing: true });

  // Pause listening while processing
  pauseListeningFn?.();

  try {
    // Farewell detection
    if (isFarewell(transcript)) {
      const goodbye = pick(['Heippa!', 'Nähdään!', 'Moikka!', 'Hei hei!']);
      reportSpinner.classList.add('hidden');
      reportText.textContent = goodbye;
      await speakAndWait(goodbye);
      dismissGreetingFn?.();
      return;
    }

    dispatch({ type: 'CONVERSATION_ADD', message: { role: 'user', content: transcript } });
    reportSpinner.classList.remove('hidden');

    try {
      const response = await generateAIResponse() || randomFallback();
      reportSpinner.classList.add('hidden');
      dispatch({ type: 'CONVERSATION_ADD', message: { role: 'assistant', content: response } });
      dispatch({ type: 'SET_HAD_VOICE_INPUT' });
      await speakAndWait(response, (sentence) => {
        reportText.textContent = sentence;
      });
    } catch {
      const fallback = randomFallback();
      reportSpinner.classList.add('hidden');
      reportText.textContent = fallback;
      await speakAndWait(fallback);
    }

    // Resume listening if still in greeting
    if (getState().phase === KioskPhase.GREETING) {
      startListeningFn?.();
    }
  } finally {
    dispatch({ type: 'SET_PROCESSING', processing: false });
  }
}
