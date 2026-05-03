import './styles.css';
import './debug.js'; // initialize debug log (window.__kioskDebug)
import { debugLog } from './debug.js';

import { dispatch, getState, select } from './state/store.js';
import { KioskPhase } from './types/state.js';

debugLog(
  `boot: ua="${navigator.userAgent.slice(0, 90)}" ` +
  `secure=${window.isSecureContext} ` +
  `dpr=${window.devicePixelRatio} ` +
  `vp=${window.innerWidth}x${window.innerHeight} ` +
  `touch=${navigator.maxTouchPoints} ` +
  `lang=${navigator.language}`
);

window.addEventListener('error', (e) => {
  debugLog(`window.error: ${e.message} @ ${e.filename}:${e.lineno}:${e.colno}`);
});
window.addEventListener('unhandledrejection', (e) => {
  const reason = e.reason instanceof Error ? `${e.reason.name}: ${e.reason.message}` : String(e.reason);
  debugLog(`unhandled rejection: ${reason}`);
});
document.addEventListener('visibilitychange', () => {
  debugLog(`visibility: ${document.visibilityState}`);
});
import { initCarousel, showSlide } from './carousel/carousel.js';
import { unlockAudio } from './audio/audio-unlock.js';
import { setupCamera, watchCameraTracks } from './camera/camera.js';
import { startFaceDetection, setGreetingTrigger, setDismissTrigger } from './camera/face-detection.js';
import { initMicrophone, isIOS } from './voice/microphone.js';
import { startListening, pauseListening, stopListening } from './voice/listening.js';
import {
  triggerGreeting, dismissGreeting, setGreetingHandlers,
} from './greeting/greeting.js';
import { setConversationHandlers } from './greeting/conversation.js';
import { initVersionCheck } from './version/auto-reload.js';
import {
  startOverlay, startLabel, startSublabel, initSpinner, initError, cameraDot,
  greetingCard,
} from './dom/elements.js';
import { distinctUntilChanged } from 'rxjs/operators';

// =========================================================================
//  STATUS DOT
// =========================================================================
let _prevPhase: KioskPhase | null = null;
select(s => s.phase).pipe(distinctUntilChanged()).subscribe(phase => {
  debugLog(`phase: ${_prevPhase ?? '(init)'} -> ${KioskPhase[phase]}`);
  _prevPhase = phase;
  cameraDot.classList.remove('active', 'failed');
  if ([KioskPhase.READY, KioskPhase.GREETING, KioskPhase.COOLDOWN].includes(phase)) {
    cameraDot.classList.add('active');
  } else if (phase === KioskPhase.FAILED) {
    cameraDot.classList.add('failed');
  }
});

// =========================================================================
//  WIRE UP CROSS-MODULE CALLBACKS
// =========================================================================
setGreetingTrigger(() => { triggerGreeting(); });
setDismissTrigger(() => { dismissGreeting(); });
setGreetingHandlers({
  showSlide,
  startListening,
  stopListening,
  pauseListening,
});
setConversationHandlers({
  startListening,
  dismissGreeting,
  pauseListening,
});

// =========================================================================
//  CAROUSEL
// =========================================================================
initCarousel();

// =========================================================================
//  GREETING CARD TAP HANDLER
// =========================================================================
import { getGreetingActiveAt } from './greeting/greeting.js';
greetingCard.addEventListener('click', () => {
  if (Date.now() - getGreetingActiveAt() < 600) return;
  dismissGreeting();
});

// =========================================================================
//  INITIALIZATION
// =========================================================================
async function tryAutoActivate(): Promise<boolean> {
  if (isIOS) return false;
  try {
    const cam = await navigator.permissions.query({ name: 'camera' as PermissionName });
    const mic = await navigator.permissions.query({ name: 'microphone' as PermissionName });
    if (cam.state === 'granted' && mic.state === 'granted') {
      unlockAudio();
      const cameraOk = await setupCamera();
      if (cameraOk) {
        startOverlay.classList.add('hidden');
        initMicrophone();
        dispatch({ type: 'SET_PHASE', phase: KioskPhase.READY });
        startFaceDetection();
        watchCameraTracks();
        return true;
      }
    }
  } catch { /* permissions API not available */ }
  return false;
}

function showCameraError(): void {
  dispatch({ type: 'SET_PHASE', phase: KioskPhase.FAILED });
  initSpinner.classList.add('hidden');
  startLabel.textContent = '';
  startSublabel.textContent = '';
  initError.classList.add('visible');
}

// Try auto-activate (non-iOS only)
tryAutoActivate();

// Manual activation via start overlay tap
startOverlay.addEventListener('click', async function handleStart() {
  startOverlay.removeEventListener('click', handleStart);
  unlockAudio();

  startLabel.textContent = 'Aktivoidaan...';
  startSublabel.textContent = '';
  initSpinner.classList.remove('hidden');
  startOverlay.style.cursor = 'default';

  const cameraOk = await setupCamera();
  if (cameraOk) {
    initSpinner.classList.add('hidden');
    startOverlay.classList.add('hidden');
    initMicrophone();
    dispatch({ type: 'SET_PHASE', phase: KioskPhase.READY });
    startFaceDetection();
    watchCameraTracks();
  } else {
    showCameraError();
  }
});

document.getElementById('retry-btn')?.addEventListener('click', async (e) => {
  e.stopPropagation();
  initError.classList.remove('visible');
  startLabel.textContent = 'Aktivoidaan kamera...';
  startSublabel.textContent = '';
  initSpinner.classList.remove('hidden');
  const ok = await setupCamera();
  if (ok) {
    initSpinner.classList.add('hidden');
    startLabel.textContent = 'Valmis!';
    startSublabel.textContent = '';
    await new Promise(r => setTimeout(r, 500));
    startOverlay.classList.add('hidden');
    initMicrophone();
    dispatch({ type: 'SET_PHASE', phase: KioskPhase.READY });
    startFaceDetection();
    watchCameraTracks();
  } else {
    showCameraError();
  }
});

document.getElementById('skip-btn')?.addEventListener('click', (e) => {
  e.preventDefault();
  e.stopPropagation();
  dispatch({ type: 'SET_PHASE', phase: KioskPhase.DASHBOARD_ONLY });
  startOverlay.classList.add('hidden');
  initMicrophone();
});

// =========================================================================
//  VERSION AUTO-RELOAD
// =========================================================================
initVersionCheck();
