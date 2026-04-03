import { interval, from, Subscription, EMPTY } from 'rxjs';
import { exhaustMap, withLatestFrom, filter } from 'rxjs/operators';
import { getState, dispatch, state$ } from '../state/store.js';
import { videoEl } from '../dom/elements.js';
import {
  FACE_DETECT_INTERVAL, DETECTIONS_REQUIRED,
  GREETING_COOLDOWN, FACE_GONE_DISMISS_MS, MIN_GREETING_ALIVE_MS,
} from '../config/constants.js';
import { isSpeaking } from '../audio/tts.js';
import { KioskPhase } from '../types/state.js';

let subscription: Subscription | null = null;

let onGreetingTrigger: (() => void) | null = null;
export function setGreetingTrigger(fn: () => void): void {
  onGreetingTrigger = fn;
}

let onDismissTrigger: (() => void) | null = null;
export function setDismissTrigger(fn: () => void): void {
  onDismissTrigger = fn;
}

async function runDetection(): Promise<void> {
  if (!videoEl.videoWidth) return;

  const detection = await faceapi.detectSingleFace(
    videoEl,
    new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.35 }),
  );

  const state = getState();

  if (state.phase === KioskPhase.GREETING) {
    if (detection) {
      dispatch({ type: 'FACE_SEEN', time: Date.now() });
    } else if (state.faceDetection.lastFaceSeenTime > 0) {
      const sinceFace = Date.now() - state.faceDetection.lastFaceSeenTime;
      const overlayAge = Date.now() - state.greeting.overlayStartTime;
      if (sinceFace >= FACE_GONE_DISMISS_MS
          && overlayAge >= MIN_GREETING_ALIVE_MS
          && !isSpeaking()
          && !state.processing) {
        onDismissTrigger?.();
      }
    }
    return;
  }

  if (state.phase === KioskPhase.COOLDOWN) {
    return;
  }

  // KioskPhase.READY
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
}

export function startFaceDetection(): void {
  if (subscription) subscription.unsubscribe();

  // interval(500) emits every 500ms.
  // exhaustMap skips emissions while the previous detection is still running
  // (replaces the manual faceDetectRunning boolean).
  subscription = interval(FACE_DETECT_INTERVAL).pipe(
    withLatestFrom(state$),
    filter(([_, s]) => [KioskPhase.READY, KioskPhase.GREETING, KioskPhase.COOLDOWN].includes(s.phase)),
    exhaustMap(() =>
      from(runDetection().catch(err => {
        console.warn('Face detection error:', err);
        dispatch({ type: 'FACE_LOST' });
      })),
    ),
  ).subscribe();
}

export function stopFaceDetection(): void {
  if (subscription) {
    subscription.unsubscribe();
    subscription = null;
  }
}
