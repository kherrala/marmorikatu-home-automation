import { jingleAudio, ttsAudio } from '../dom/elements.js';
import { setAudioContext } from './context.js';
import { dispatch } from '../state/store.js';

let audioUnlocked = false;

export function unlockAudio(): void {
  if (audioUnlocked) return;
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
  ttsAudio.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=';
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

  // iOS speechSynthesis keepalive -- prevent the speech engine from going dormant
  setInterval(() => {
    if (!speechSynthesis.speaking) {
      speechSynthesis.cancel();
      const keepalive = new SpeechSynthesisUtterance('');
      keepalive.volume = 0;
      speechSynthesis.speak(keepalive);
    }
  }, 10_000);

  dispatch({ type: 'AUDIO_UNLOCKED' });
  console.log('[audio] Unlocked all audio channels');
}
