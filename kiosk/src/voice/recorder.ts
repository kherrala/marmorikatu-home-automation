import { getState, dispatch } from '../state/store.js';
import { audioStream } from '../camera/camera.js';
import { analyserNode, getRMS } from './microphone.js';
import { debugLog } from '../debug.js';
import {
  SILENCE_THRESHOLD, SILENCE_DURATION, SILENCE_DURATION_SHORT,
  MIN_RECORDING_MS, MAX_RECORDING_MS,
} from '../config/constants.js';
import { KioskPhase } from '../types/state.js';

export let activeRecorder: MediaRecorder | null = null;
export let silenceTimer: ReturnType<typeof setTimeout> | null = null;

let onVoiceResult: ((text: string) => void) | null = null;
export function setVoiceResultHandler(fn: (text: string) => void): void {
  onVoiceResult = fn;
}

let onRestartRecording: (() => void) | null = null;
export function setRestartHandler(fn: () => void): void {
  onRestartRecording = fn;
}

export function startRecording(): void {
  if (!audioStream || !getState().voice.listeningActive) {
    debugLog(`startRecording skipped: stream=${!!audioStream} listening=${getState().voice.listeningActive}`);
    return;
  }

  let audioChunks: Blob[] = [];
  const recordingStartTime = Date.now();

  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus'
                 : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm'
                 : MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4'
                 : '';

  debugLog(`startRecording: mime=${mimeType || 'default'} tracks=${audioStream.getAudioTracks().length}`);

  try {
    const recorder = new MediaRecorder(audioStream, mimeType ? { mimeType } : {});
    activeRecorder = recorder;

    recorder.ondataavailable = (e: BlobEvent) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    recorder.onstop = async () => {
      activeRecorder = null;
      const duration = Date.now() - recordingStartTime;
      const totalSize = audioChunks.reduce((s, c) => s + c.size, 0);
      debugLog(`recorder.onstop: duration=${duration}ms chunks=${audioChunks.length} size=${totalSize}b`);
      if (duration < MIN_RECORDING_MS || audioChunks.length === 0) {
        const s = getState();
        if (s.voice.listeningActive && s.phase === KioskPhase.GREETING) {
          onRestartRecording?.();
        }
        return;
      }

      const ext = (recorder.mimeType || '').includes('mp4') ? 'mp4' : 'webm';
      const blob = new Blob(audioChunks, { type: recorder.mimeType || 'audio/webm' });
      audioChunks = [];

      try {
        const formData = new FormData();
        formData.append('audio', blob, `recording.${ext}`);
        debugLog(`Sending ${blob.size}b to transcribe`);
        const res = await fetch('/api/chat/transcribe', { method: 'POST', body: formData });
        if (res.ok) {
          const data = await res.json() as { text?: string };
          const text = data.text?.trim();
          debugLog(`Transcription: "${text || '(empty)'}"`);
          if (text) {
            onVoiceResult?.(text);
            return;
          }
        } else {
          debugLog(`Transcription HTTP ${res.status}`);
        }
      } catch (err) {
        debugLog(`Transcription error: ${err}`);
      }

      const s = getState();
      if (s.voice.listeningActive && s.phase === KioskPhase.GREETING) {
        onRestartRecording?.();
      }
    };

    recorder.start(250);
    startSilenceDetection(recorder, recordingStartTime);
  } catch (err) {
    console.warn('[voice] MediaRecorder failed:', err);
  }
}

export function startSilenceDetection(
  recorder: MediaRecorder,
  recordingStartTime: number,
): void {
  if (silenceTimer !== null) clearTimeout(silenceTimer);
  let silenceStart: number | null = null;
  let speechDetected = false;

  const check = () => {
    const s = getState();
    if (!s.voice.listeningActive || !recorder || recorder.state !== 'recording') return;

    const elapsed = Date.now() - recordingStartTime;

    if (elapsed >= MAX_RECORDING_MS) {
      console.log('[voice] Max recording duration reached, stopping');
      recorder.stop();
      return;
    }

    const rms = getRMS();

    // If analyser isn't working, fall back to duration-based cutoff
    if (!analyserNode) {
      if (elapsed >= 5000) {
        console.log('[voice] No analyser, stopping after 5s');
        recorder.stop();
      } else {
        silenceTimer = setTimeout(check, 150);
      }
      return;
    }

    if (rms >= SILENCE_THRESHOLD) {
      if (!speechDetected) {
        dispatch({ type: 'VOICE_INPUT_RECEIVED' });
      }
      speechDetected = true;
      silenceStart = null;
    } else if (speechDetected) {
      if (!silenceStart) silenceStart = Date.now();
      const silentFor = Date.now() - silenceStart;
      const hadVoice = getState().greeting.hadVoiceInput;
      if (silentFor >= (hadVoice ? SILENCE_DURATION_SHORT : SILENCE_DURATION)) {
        console.log('[voice] Silence after speech, stopping recording');
        recorder.stop();
        return;
      }
    }

    silenceTimer = setTimeout(check, 150);
  };

  silenceTimer = setTimeout(check, 500);
}
