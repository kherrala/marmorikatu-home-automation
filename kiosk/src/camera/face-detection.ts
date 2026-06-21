import { interval, from, Subscription, EMPTY } from 'rxjs';
import { exhaustMap, withLatestFrom, filter } from 'rxjs/operators';
import { getState, dispatch, state$ } from '../state/store.js';
import { videoEl } from '../dom/elements.js';
import {
  FACE_DETECT_INTERVAL, DETECTIONS_REQUIRED, FACE_INPUT_SIZE,
  GREETING_COOLDOWN, FACE_GONE_DISMISS_MS, MIN_GREETING_ALIVE_MS,
} from '../config/constants.js';
import { isSpeaking } from '../audio/tts.js';
import { KioskPhase } from '../types/state.js';
import { screenshotBubble } from '../dom/elements.js';
import { debugLog } from '../debug.js';

let subscription: Subscription | null = null;

let _detectStartTime = 0;
let _firstHitLogged = false;
let _attempts = 0;
let _hits = 0;
let _skipsNoVideo = 0;
let _errors = 0;
let _scoreSum = 0;
let _maxScore = 0;
let _lastSummaryTime = 0;
const SUMMARY_INTERVAL_MS = 30_000;

// Frame-brightness probe. Distinguishes "camera works but no face present"
// (brightness > 0) from "iOS is handing us black frames" (brightness ~0,
// which makes face detection impossible no matter who stands there). Sampled
// from a tiny 16x16 downscale so it's cheap.
let _brightCanvas: HTMLCanvasElement | null = null;
let _brightCtx: CanvasRenderingContext2D | null = null;
let _brightSum = 0;
let _brightSamples = 0;
let _brightMax = 0;

function sampleBrightness(): void {
  try {
    if (!_brightCanvas) {
      _brightCanvas = document.createElement('canvas');
      _brightCanvas.width = 16;
      _brightCanvas.height = 16;
      _brightCtx = _brightCanvas.getContext('2d', { willReadFrequently: true });
    }
    if (!_brightCtx) return;
    _brightCtx.drawImage(videoEl, 0, 0, 16, 16);
    const data = _brightCtx.getImageData(0, 0, 16, 16).data;
    let sum = 0;
    for (let i = 0; i < data.length; i += 4) {
      sum += ((data[i] ?? 0) + (data[i + 1] ?? 0) + (data[i + 2] ?? 0)) / 3;
    }
    const avg = sum / (data.length / 4);  // 0..255
    _brightSum += avg;
    _brightSamples++;
    if (avg > _brightMax) _brightMax = avg;
  } catch { /* tainted/blank canvas — ignore */ }
}

function maybeLogSummary(): void {
  const now = Date.now();
  if (now - _lastSummaryTime < SUMMARY_INTERVAL_MS) return;
  _lastSummaryTime = now;
  const avg = _hits > 0 ? (_scoreSum / _hits).toFixed(2) : '-';
  const bAvg = _brightSamples > 0 ? (_brightSum / _brightSamples).toFixed(1) : '-';
  debugLog(
    `face: 30s summary attempts=${_attempts} hits=${_hits} ` +
    `skipsNoVideo=${_skipsNoVideo} errors=${_errors} ` +
    `avgScore=${avg} maxScore=${_maxScore.toFixed(2)} ` +
    `bright=${bAvg}/${_brightMax.toFixed(0)} (0-255)`
  );
  _attempts = 0; _hits = 0; _skipsNoVideo = 0; _errors = 0; _scoreSum = 0; _maxScore = 0;
  _brightSum = 0; _brightSamples = 0; _brightMax = 0;
}

let onGreetingTrigger: (() => void) | null = null;
export function setGreetingTrigger(fn: () => void): void {
  onGreetingTrigger = fn;
}

let onDismissTrigger: (() => void) | null = null;
export function setDismissTrigger(fn: () => void): void {
  onDismissTrigger = fn;
}

async function runDetection(): Promise<void> {
  _attempts++;
  if (!videoEl.videoWidth) {
    _skipsNoVideo++;
    if (_skipsNoVideo === 1 || _skipsNoVideo % 20 === 0) {
      debugLog(`face: skipped (videoWidth=0, readyState=${videoEl.readyState}, paused=${videoEl.paused}) [n=${_skipsNoVideo}]`);
    }
    maybeLogSummary();
    return;
  }

  sampleBrightness();

  // scoreThreshold=0.5 is face-api.js's own default and the empirical break
  // point between real faces (avgScore typically ≥0.6) and face-shaped
  // false-positives in on-screen artwork or room photos (which clustered
  // around 0.40 in production debug logs and kept the kiosk pinned in
  // GREETING with no human present).
  const detection = await faceapi.detectSingleFace(
    videoEl,
    new faceapi.TinyFaceDetectorOptions({ inputSize: FACE_INPUT_SIZE, scoreThreshold: 0.5 }),
  );

  if (detection) {
    const score = detection.score;
    _hits++;
    _scoreSum += score;
    if (score > _maxScore) _maxScore = score;
    if (!_firstHitLogged) {
      _firstHitLogged = true;
      const elapsed = Date.now() - _detectStartTime;
      debugLog(`face: first hit score=${score.toFixed(2)} after ${elapsed}ms / ${_attempts} attempts`);
    }
  }
  maybeLogSummary();

  const state = getState();

  if (state.phase === KioskPhase.GREETING) {
    if (detection) {
      dispatch({ type: 'FACE_SEEN', time: Date.now() });
    } else if (state.faceDetection.lastFaceSeenTime > 0) {
      const sinceFace = Date.now() - state.faceDetection.lastFaceSeenTime;
      const overlayAge = Date.now() - state.greeting.overlayStartTime;
      const screenshotVisible = !screenshotBubble.classList.contains('hidden');
      if (sinceFace >= FACE_GONE_DISMISS_MS
          && overlayAge >= MIN_GREETING_ALIVE_MS
          && !isSpeaking()
          && !state.processing
          && !screenshotVisible) {
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

  _detectStartTime = Date.now();
  _firstHitLogged = false;
  _attempts = 0; _hits = 0; _skipsNoVideo = 0; _errors = 0; _scoreSum = 0; _maxScore = 0;
  _lastSummaryTime = Date.now();
  debugLog(
    `startFaceDetection: interval=${FACE_DETECT_INTERVAL}ms required=${DETECTIONS_REQUIRED} ` +
    `dim=${videoEl.videoWidth}x${videoEl.videoHeight} ready=${videoEl.readyState}`
  );

  subscription = interval(FACE_DETECT_INTERVAL).pipe(
    withLatestFrom(state$),
    filter(([_, s]) => [KioskPhase.READY, KioskPhase.GREETING, KioskPhase.COOLDOWN].includes(s.phase)),
    exhaustMap(() =>
      from(runDetection().catch(err => {
        _errors++;
        debugLog(`face: detection error: ${err}`);
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
