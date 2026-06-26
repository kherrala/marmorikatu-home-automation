import { videoEl } from '../dom/elements.js';
import { resumeIfSuspended } from '../audio/context.js';
import { debugLog } from '../debug.js';
import { rewireAudioAnalyser } from '../voice/microphone.js';
import { CAMERA_WIDTH, CAMERA_HEIGHT } from '../config/constants.js';

export let audioStream: MediaStream | null = null;

export function setAudioStream(stream: MediaStream | null): void {
  audioStream = stream;
}

let videoRestartPending = false;

// Stop every track on a MediaStream we're about to discard. Without this,
// the previous stream stays open in WebKit native memory after we reassign
// videoEl.srcObject — over hours/days of camera restarts on the wall iPad,
// the accumulated native resources contribute to the periodic Safari OOM.
function stopAllTracks(stream: MediaStream | null): void {
  if (!stream) return;
  try {
    stream.getTracks().forEach(t => {
      try { t.stop(); } catch {}
    });
  } catch {}
}

function describeStream(stream: MediaStream): string {
  const v = stream.getVideoTracks();
  const a = stream.getAudioTracks();
  return `v=${v.length}(${v.map(t => t.readyState).join(',')}) ` +
         `a=${a.length}(${a.map(t => t.readyState).join(',')})`;
}

export async function setupCamera(): Promise<boolean> {
  debugLog('setupCamera: start');
  const t0 = performance.now();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: CAMERA_WIDTH, height: CAMERA_HEIGHT },
      audio: true,
    });
    videoEl.srcObject = stream;
    stream.getAudioTracks().forEach(t => t.enabled = false);
    await videoEl.play();
    debugLog(`setupCamera: video+audio ok in ${Math.round(performance.now() - t0)}ms ${describeStream(stream)}`);
  } catch (err) {
    const e = err as Error;
    debugLog(`setupCamera: video+audio failed (${e.name}: ${e.message}), trying video-only`);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: CAMERA_WIDTH, height: CAMERA_HEIGHT },
        audio: false,
      });
      videoEl.srcObject = stream;
      await videoEl.play();
      debugLog(`setupCamera: video-only ok ${describeStream(stream)}`);
    } catch (err2) {
      const e2 = err2 as Error;
      debugLog(`setupCamera: FAILED (${e2.name}: ${e2.message})`);
      return false;
    }
  }

  debugLog(`setupCamera: video readyState=${videoEl.readyState} dim=${videoEl.videoWidth}x${videoEl.videoHeight}`);

  // Detector model/asset loading + warmup now lives in the selected detector
  // backend (see camera/detectors/), so an unused backend never loads tfjs.
  // startFaceDetection() kicks off getDetector().init() in the background.
  return true;
}

export function watchCameraTracks(): void {
  const stream = videoEl.srcObject as MediaStream | null;
  if (!stream) return;
  stream.getVideoTracks().forEach(t => {
    t.addEventListener('ended', () => {
      debugLog(`camera: video track ended (label=${t.label.slice(0, 30)}) -- scheduling restart`);
      scheduleVideoRestart();
    });
  });
}

export function scheduleVideoRestart(): void {
  if (videoRestartPending) return;
  videoRestartPending = true;
  setTimeout(async () => {
    videoRestartPending = false;
    if (document.visibilityState === 'hidden') {
      debugLog('camera: restart skipped (page hidden)');
      return;
    }
    debugLog('camera: restarting stream');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: CAMERA_WIDTH, height: CAMERA_HEIGHT },
        audio: true,
      });
      // Stop the previous stream's tracks before swapping. Otherwise the
      // old MediaStream stays alive in WebKit; over repeated restarts the
      // accumulated native handles are a real contributor to long-running
      // memory growth.
      const prevVideoStream = videoEl.srcObject as MediaStream | null;
      const prevAudioStream = audioStream;
      videoEl.srcObject = stream;
      stream.getAudioTracks().forEach(t => t.enabled = false);
      await videoEl.play();
      watchCameraTracks();
      if (prevAudioStream) {
        const audioTracks = stream.getAudioTracks();
        if (audioTracks.length > 0) {
          audioTracks.forEach(t => t.enabled = true);
          setAudioStream(new MediaStream(audioTracks));
          // Re-point the analyser at the new source — without this,
          // getRMS() reads from a detached source forever, silence
          // detection in recorder.ts always sees ~0, and every
          // recording runs the full MAX_RECORDING_MS (10s).
          rewireAudioAnalyser();
        }
      }
      stopAllTracks(prevVideoStream);
      stopAllTracks(prevAudioStream);
      debugLog(`camera: restart ok ${describeStream(stream)} dim=${videoEl.videoWidth}x${videoEl.videoHeight}`);
    } catch (err) {
      const e = err as Error;
      debugLog(`camera: restart FAILED (${e.name}: ${e.message})`);
    }
  }, 800);
}

export function initCameraWatcher(): void {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    resumeIfSuspended();
    const tracks = (videoEl.srcObject as MediaStream | null)?.getVideoTracks() ?? [];
    const endedTracks = tracks.filter(t => t.readyState === 'ended').length;
    const needsRestart = tracks.length === 0 || endedTracks > 0;
    debugLog(`visibility->visible: vtracks=${tracks.length} ended=${endedTracks} paused=${videoEl.paused}`);
    if (needsRestart) {
      scheduleVideoRestart();
    } else if (videoEl.paused) {
      videoEl.play().catch((err) => debugLog(`videoEl.play failed: ${err}`));
    }
  });
}
