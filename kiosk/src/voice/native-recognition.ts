import { getState, dispatch } from '../state/store.js';
import { KioskPhase } from '../types/state.js';
import { userTextEl, listeningIndicator } from '../dom/elements.js';
import { setListening } from '../dom/avatar.js';
import { NativeSpeechRecognition } from './microphone.js';
import { MAX_NATIVE_SILENCE } from '../config/constants.js';

export let activeRecognizer: SpeechRecognition | null = null;

let onVoiceResult: ((text: string) => void) | null = null;
export function setVoiceResultHandler(fn: (text: string) => void): void {
  onVoiceResult = fn;
}

let onFallbackToRecorder: (() => void) | null = null;
export function setFallbackHandler(fn: () => void): void {
  onFallbackToRecorder = fn;
}

let onRestartListening: (() => void) | null = null;
export function setRestartHandler(fn: () => void): void {
  onRestartListening = fn;
}

export function startNativeListening(): void {
  if (!NativeSpeechRecognition) return;
  const s = getState();
  if (!s.voice.listeningActive) return;

  const recognizer = new NativeSpeechRecognition();
  activeRecognizer = recognizer;
  recognizer.lang = 'fi-FI';
  recognizer.continuous = true;
  recognizer.interimResults = true;
  recognizer.maxAlternatives = 1;

  let finalText = '';
  let bestInterim = '';
  let resolved = false;
  let pauseTimer: ReturnType<typeof setTimeout> | null = null;
  let hardTimer: ReturnType<typeof setTimeout> | null = null;

  function resetHardTimer(): void {
    if (hardTimer !== null) clearTimeout(hardTimer);
    hardTimer = setTimeout(() => {
      if (resolved) return;
      console.warn('[voice] Native hard timeout (15s)');
      try { recognizer.abort(); } catch {}
    }, 15_000);
  }

  function finish(text: string | null): void {
    if (resolved) return;
    resolved = true;
    activeRecognizer = null;
    if (hardTimer !== null) clearTimeout(hardTimer);
    if (pauseTimer !== null) clearTimeout(pauseTimer);

    if (text?.trim()) {
      dispatch({ type: 'NATIVE_SILENCE_RESET' });
      onVoiceResult?.(text.trim());
    } else {
      const st = getState();
      if (st.voice.listeningActive && st.phase === KioskPhase.GREETING) {
        dispatch({ type: 'NATIVE_SILENCE_INCREMENT' });
        const updated = getState();
        if (updated.voice.nativeSilenceCount >= MAX_NATIVE_SILENCE) {
          // User is genuinely silent -- stop gracefully
          dispatch({ type: 'SET_LISTENING', active: false });
          listeningIndicator.classList.add('hidden');
          setListening(false);
        } else {
          onRestartListening?.();
        }
      }
    }
  }

  resetHardTimer();

  recognizer.onresult = (event: SpeechRecognitionEvent) => {
    if (resolved) return;

    finalText = '';
    bestInterim = '';
    for (let i = 0; i < event.results.length; i++) {
      const result = event.results[i]!;
      if (result.isFinal) {
        finalText += result[0]!.transcript;
      } else {
        bestInterim += result[0]!.transcript;
      }
    }

    const liveText = finalText || bestInterim;
    if (liveText) {
      dispatch({ type: 'VOICE_INPUT_RECEIVED' });
      userTextEl.textContent = '\u201c' + liveText + '\u201d';
    }

    if (pauseTimer !== null) clearTimeout(pauseTimer);
    resetHardTimer();

    if (finalText.trim()) {
      // Final result -- short pause then process
      pauseTimer = setTimeout(() => finish(finalText.trim()), 200);
    } else {
      // Interim only -- wait for pause before processing
      const hadVoice = getState().greeting.hadVoiceInput;
      pauseTimer = setTimeout(() => {
        if (!resolved && (finalText || bestInterim)) {
          try { recognizer.stop(); } catch {}
          finish((finalText || bestInterim).trim());
        }
      }, hadVoice ? 800 : 2000);
    }
  };

  recognizer.onerror = (event: SpeechRecognitionErrorEvent) => {
    if (resolved) return;
    console.warn('[voice] Native error:', event.error);

    if (event.error === 'no-speech' || event.error === 'aborted') {
      // Use accumulated text if any
      const accumulated = (finalText || bestInterim)?.trim();
      if (accumulated) {
        finish(accumulated);
      } else {
        finish(null);
      }
    } else {
      // Non-recoverable error -- fall back to MediaRecorder permanently
      console.warn('[voice] Falling back to MediaRecorder + server transcription');
      dispatch({ type: 'NATIVE_FAILED' });
      resolved = true;
      activeRecognizer = null;
      if (hardTimer !== null) clearTimeout(hardTimer);
      if (pauseTimer !== null) clearTimeout(pauseTimer);
      onFallbackToRecorder?.();
    }
  };

  recognizer.onend = () => {
    finish((finalText || bestInterim)?.trim() || null);
  };

  try {
    recognizer.start();
    console.log('[voice] Native recognition started');
  } catch (err) {
    console.warn('[voice] Native recognition start failed:', err);
    dispatch({ type: 'NATIVE_FAILED' });
    resolved = true;
    activeRecognizer = null;
    onFallbackToRecorder?.();
  }
}
