import { videoEl } from '../dom/elements.js';
import { getAudioContext, setAudioContext } from '../audio/context.js';
import { dispatch } from '../state/store.js';
import { audioStream, setAudioStream } from '../camera/camera.js';
import { debugLog } from '../debug.js';

export const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
  || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);

const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent)
  || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);

// Mobile browsers (Android/iOS) have unreliable native SpeechRecognition —
// it silently stops after ~5s on Android Chrome. Use MediaRecorder + Whisper instead.
const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
  || navigator.maxTouchPoints > 1;

export const NativeSpeechRecognition: (new () => SpeechRecognition) | null =
  !isIOS && !isSafari && !isMobile
    ? ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition) ?? null
    : null;

export let analyserNode: AnalyserNode | null = null;

function setupAudioAnalyser(): void {
  try {
    let audioContext: AudioContext;
    try {
      audioContext = getAudioContext();
    } catch {
      audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
      setAudioContext(audioContext);
    }
    debugLog(`audioContext: state=${audioContext.state} sampleRate=${audioContext.sampleRate}`);
    if (audioContext.state === 'suspended') {
      audioContext.resume().then(
        () => debugLog(`audioContext: resumed -> ${audioContext.state}`),
        (err) => debugLog(`audioContext: resume failed (${err})`),
      );
    }
    const source = audioContext.createMediaStreamSource(audioStream!);
    analyserNode = audioContext.createAnalyser();
    analyserNode.fftSize = 512;
    source.connect(analyserNode);
    debugLog('audioContext: analyser wired');
  } catch (err) {
    const e = err as Error;
    debugLog(`audioContext: setup FAILED (${e.name}: ${e.message})`);
  }
}

export function initMicrophone(): void {
  const existingStream = videoEl.srcObject as MediaStream | null;
  const audioTracks = existingStream?.getAudioTracks() || [];
  debugLog(`initMicrophone: existing audioTracks=${audioTracks.length} nativeSpeechRecognition=${!!NativeSpeechRecognition}`);

  if (audioTracks.length > 0) {
    audioTracks.forEach(t => t.enabled = true);
    setAudioStream(new MediaStream(audioTracks));
    setupAudioAnalyser();
    debugLog('initMicrophone: ready (reused camera audio tracks)');
  } else {
    navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
      setAudioStream(stream);
      setupAudioAnalyser();
      debugLog(`initMicrophone: ready (new stream, tracks=${stream.getAudioTracks().length})`);
    }).catch(err => {
      const e = err as Error;
      debugLog(`initMicrophone: getUserMedia(audio) FAILED (${e.name}: ${e.message})`);
    });
  }

  dispatch({ type: 'MIC_READY' });
}

export function getRMS(): number {
  if (!analyserNode) return 0;
  const data = new Float32Array(analyserNode.fftSize);
  analyserNode.getFloatTimeDomainData(data);
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i]! * data[i]!;
  return Math.sqrt(sum / data.length);
}
