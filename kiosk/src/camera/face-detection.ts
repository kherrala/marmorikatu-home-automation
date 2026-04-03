import { getState, dispatch } from '../state/store.js';
import { videoEl } from '../dom/elements.js';
import {
  FACE_DETECT_INTERVAL, DETECTIONS_REQUIRED,
  GREETING_COOLDOWN, FACE_GONE_DISMISS_MS, MIN_GREETING_ALIVE_MS,
} from '../config/constants.js';
import { isSpeaking } from '../audio/tts.js';
import { isProcessing } from '../greeting/conversation.js';
import { KioskPhase } from '../types/state.js';

let detectInterval: ReturnType<typeof setInterval> | null = null;
let faceDetectRunning = false;

let onGreetingTrigger: (() => void) | null = null;
export function setGreetingTrigger(fn: () => void): void {
  onGreetingTrigger = fn;
}

let onDismissTrigger: (() => void) | null = null;
export function setDismissTrigger(fn: () => void): void {
  onDismissTrigger = fn;
}

export function startFaceDetection(): void {
  if (detectInterval !== null) clearInterval(detectInterval);

  detectInterval = setInterval(async () => {
    const s = getState();
    if (![KioskPhase.READY, KioskPhase.GREETING, KioskPhase.COOLDOWN].includes(s.phase)) return;
    if (faceDetectRunning) return;
    faceDetectRunning = true;

    try {
      if (!videoEl.videoWidth) return;

      const detection = await faceapi.detectSingleFace(
        videoEl,
        new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.35 })
      );

      const state = getState();

      if (state.phase === KioskPhase.GREETING) {
        // During greeting: track face for auto-dismiss via timestamp
        if (detection) {
          dispatch({ type: 'FACE_SEEN', time: Date.now() });
        } else if (state.faceDetection.lastFaceSeenTime > 0) {
          const sinceFace = Date.now() - state.faceDetection.lastFaceSeenTime;
          const overlayAge = Date.now() - state.greeting.overlayStartTime;
          if (sinceFace >= FACE_GONE_DISMISS_MS
              && overlayAge >= MIN_GREETING_ALIVE_MS
              && !isSpeaking()
              && !isProcessing()) {
            onDismissTrigger?.();
          }
        }
        return;
      }

      if (state.phase === KioskPhase.COOLDOWN) {
        // During cooldown: don't update face tracking -- prevents immediate
        // re-trigger when cooldown expires with face still present.
        return;
      }

      // KioskPhase.READY: track face for greeting trigger
      if (detection) {
        dispatch({ type: 'FACE_DETECTED' });
        const updated = getState();
        if (updated.faceDetection.consecutiveDetections >= DETECTIONS_REQUIRED
            && updated.faceDetection.faceAbsentSinceLastGreeting
            && Date.now() - updated.greeting.lastDismissTime >= GREETING_COOLDOWN) {
          dispatch({ type: 'FACE_RESET_ABSENT' });
          dispatch({ type: 'FACE_SEEN', time: Date.now() });
          onGreetingTrigger?.();
        }
      } else {
        dispatch({ type: 'FACE_LOST' });
      }
    } catch (err) {
      console.warn('Face detection error:', err);
      dispatch({ type: 'FACE_LOST' });
    } finally {
      faceDetectRunning = false;
    }
  }, FACE_DETECT_INTERVAL);
}
