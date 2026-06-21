import { jingleAudio, ttsAudio, audioLockedHint } from '../dom/elements.js';
import { getAudioContext, setAudioContext } from './context.js';
import { dispatch } from '../state/store.js';
import { debugLog } from '../debug.js';

let audioUnlocked = false;

// Silent 44-byte WAV used to (re)arm the persistent TTS <audio> element.
const SILENT_WAV =
  'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=';

/** Show/hide the "tap to enable sound" hint. Called when a queued
 *  announcement or greeting can't play because iOS audio is still locked. */
export function showAudioHint(): void {
  if (audioLockedHint.classList.contains('hidden')) {
    debugLog('audio: playback blocked — showing "tap to enable sound" hint');
  }
  audioLockedHint.classList.remove('hidden');
}
export function hideAudioHint(): void {
  audioLockedHint.classList.add('hidden');
}

/** Re-arm audio after a re-lock that did NOT reload the page (iOS suspends the
 *  AudioContext / drops the <audio> unlock after long idle). Safe to call on
 *  every user tap — cheap and idempotent. Resumes the context and replays the
 *  silent WAV inside the gesture so the next real playSentence() succeeds. */
export function ensureAudioReady(): void {
  try {
    const ctx = getAudioContext();
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  } catch {}
  // Only re-arm the element if it's not mid-playback, to avoid cutting off a
  // sentence that's currently speaking.
  if (ttsAudio.paused) {
    const prevSrc = ttsAudio.src;
    ttsAudio.muted = true;
    ttsAudio.src = SILENT_WAV;
    ttsAudio.play()
      .then(() => { ttsAudio.pause(); ttsAudio.muted = false; if (prevSrc.startsWith('blob:')) ttsAudio.removeAttribute('src'); })
      .catch(() => { ttsAudio.muted = false; });
  }
  hideAudioHint();
}

export function unlockAudio(): void {
  if (audioUnlocked) { ensureAudioReady(); return; }
  audioUnlocked = true;

  // Unlock speechSynthesis
  const silentUtterance = new SpeechSynthesisUtterance('');
  silentUtterance.volume = 0;
  speechSynthesis.speak(silentUtterance);

  // Unlock jingle <audio>
  jingleAudio.muted = true;
  jingleAudio.play()
    .then(() => { jingleAudio.pause(); jingleAudio.currentTime = 0; jingleAudio.muted = false; })
    .catch(() => { jingleAudio.muted = false; });

  // Unlock persistent TTS <audio> with silent WAV
  ttsAudio.muted = true;
  ttsAudio.src = SILENT_WAV;
  ttsAudio.play()
    .then(() => { ttsAudio.pause(); ttsAudio.muted = false; })
    .catch(() => { ttsAudio.muted = false; });

  // Unlock AudioContext and route ttsAudio through a gain node
  try {
    const ttsAudioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
    const buf = ttsAudioCtx.createBuffer(1, 1, 22050);
    const src = ttsAudioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(ttsAudioCtx.destination);
    src.start(0);
    const ttsSource = ttsAudioCtx.createMediaElementSource(ttsAudio);
    const ttsGain = ttsAudioCtx.createGain();
    ttsGain.gain.value = 1.0;
    ttsSource.connect(ttsGain);
    ttsGain.connect(ttsAudioCtx.destination);
    ttsAudioCtx.resume();
    setAudioContext(ttsAudioCtx);
  } catch {}

  // iOS speechSynthesis keepalive -- prevent the speech engine from going
  // dormant. Reuse a single utterance object instead of allocating a fresh
  // one every 10s (≈60k SpeechSynthesisUtterance objects per week on a
  // 24/7 kiosk, which iOS's speech engine is known to retain).
  const keepaliveUtterance = new SpeechSynthesisUtterance('');
  keepaliveUtterance.volume = 0;
  setInterval(() => {
    if (!speechSynthesis.speaking) {
      speechSynthesis.cancel();
      speechSynthesis.speak(keepaliveUtterance);
    }
  }, 10_000);

  dispatch({ type: 'AUDIO_UNLOCKED' });
  console.log('[audio] Unlocked all audio channels');
}
