import { videoEl } from '../dom/elements.js';
import { resumeIfSuspended } from '../audio/context.js';
import { debugLog } from '../debug.js';

export let audioStream: MediaStream | null = null;

export function setAudioStream(stream: MediaStream | null): void {
  audioStream = stream;
}

let videoRestartPending = false;

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
      video: { facingMode: 'user', width: 320, height: 240 },
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
        video: { facingMode: 'user', width: 320, height: 240 },
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

  const tModel = performance.now();
  try {
    await faceapi.nets.tinyFaceDetector.loadFromUri('/face-api');
    debugLog(`setupCamera: face-api model loaded in ${Math.round(performance.now() - tModel)}ms`);
  } catch (err) {
    const e = err as Error;
    debugLog(`setupCamera: face-api model load FAILED (${e.name}: ${e.message})`);
    return false;
  }

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
        video: { facingMode: 'user', width: 320, height: 240 },
        audio: true,
      });
      videoEl.srcObject = stream;
      stream.getAudioTracks().forEach(t => t.enabled = false);
      await videoEl.play();
      watchCameraTracks();
      if (audioStream) {
        const audioTracks = stream.getAudioTracks();
        if (audioTracks.length > 0) {
          audioTracks.forEach(t => t.enabled = true);
          setAudioStream(new MediaStream(audioTracks));
        }
      }
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
