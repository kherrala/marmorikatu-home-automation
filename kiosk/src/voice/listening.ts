import { getState, dispatch } from '../state/store.js';
import { videoEl, listeningIndicator, jingleAudio, reportText } from '../dom/elements.js';
import { setListening } from '../dom/avatar.js';
import { resumeIfSuspended } from '../audio/context.js';
import { KioskPhase } from '../types/state.js';
import { NativeSpeechRecognition } from './microphone.js';
import { audioStream } from '../camera/camera.js';
import { scheduleOverlayDismiss, scheduleDailyReport, clearSilenceTimer } from '../greeting/greeting.js';
import {
  startNativeListening,
  activeRecognizer,
  setVoiceResultHandler as setNativeVoiceHandler,
  setFallbackHandler,
  setRestartHandler as setNativeRestartHandler,
} from './native-recognition.js';
import {
  startRecording,
  activeRecorder,
  silenceTimer,
  setVoiceResultHandler as setRecorderVoiceHandler,
  setRestartHandler as setRecorderRestartHandler,
} from './recorder.js';
import { handleVoiceResult } from '../greeting/conversation.js';

// Wire up callbacks from native-recognition and recorder to avoid circular deps
setNativeVoiceHandler((text: string) => handleVoiceResult(text));
setNativeRestartHandler(() => startListening());
setFallbackHandler(() => {
  const s = getState();
  if (s.voice.listeningActive && audioStream) {
    resumeIfSuspended();
    startRecording();
  }
});

setRecorderVoiceHandler((text: string) => handleVoiceResult(text));
setRecorderRestartHandler(() => startRecording());

export function startListening(): void {
  const s = getState();
  if (!s.micReady || s.phase !== KioskPhase.GREETING) return;

  // Re-enable audio tracks so iOS shows mic indicator
  (videoEl.srcObject as MediaStream | null)?.getAudioTracks().forEach(t => { t.enabled = true; });

  // Wait for jingle to finish before opening mic
  if (!jingleAudio.paused) {
    const onJingleDone = () => {
      jingleAudio.removeEventListener('ended', onJingleDone);
      jingleAudio.removeEventListener('pause', onJingleDone);
      if (getState().phase === KioskPhase.GREETING) startListening();
    };
    jingleAudio.addEventListener('ended', onJingleDone);
    jingleAudio.addEventListener('pause', onJingleDone);
    return;
  }

  dispatch({ type: 'SET_LISTENING', active: true });
  reportText.textContent = '';
  listeningIndicator.classList.remove('hidden');
  setListening(true);
  scheduleOverlayDismiss();

  // Daily-report timer: fires if user says nothing for 5s after mic opens
  scheduleDailyReport();

  // Start voice recognition
  const state = getState();
  if (NativeSpeechRecognition && !state.voice.nativeFailed) {
    startNativeListening();
  } else if (audioStream) {
    resumeIfSuspended();
    startRecording();
  }
}

export function pauseListening(): void {
  dispatch({ type: 'SET_LISTENING', active: false });
  listeningIndicator.classList.add('hidden');
  setListening(false);
  if (silenceTimer !== null) clearTimeout(silenceTimer);
  if (activeRecognizer) {
    try { activeRecognizer.abort(); } catch {}
    // Note: activeRecognizer is set to null inside native-recognition.ts on finish
  }
  if (activeRecorder?.state === 'recording') {
    try { activeRecorder.stop(); } catch {}
  }
}

export function stopListening(): void {
  pauseListening();
  clearSilenceTimer();
  // Disable audio tracks -- clears iOS mic indicator
  (videoEl.srcObject as MediaStream | null)?.getAudioTracks().forEach(t => { t.enabled = false; });
}
