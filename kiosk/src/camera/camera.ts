import { videoEl } from '../dom/elements.js';
import { resumeIfSuspended } from '../audio/context.js';

export let audioStream: MediaStream | null = null;

export function setAudioStream(stream: MediaStream | null): void {
  audioStream = stream;
}

let videoRestartPending = false;

export async function setupCamera(): Promise<boolean> {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: 320, height: 240 },
      audio: true,
    });
    videoEl.srcObject = stream;
    // Keep audio tracks alive but disabled -- maintains iOS audio session
    stream.getAudioTracks().forEach(t => t.enabled = false);
    await videoEl.play();
  } catch (err) {
    console.warn('Camera+mic failed, trying video only:', err);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: 320, height: 240 },
        audio: false,
      });
      videoEl.srcObject = stream;
      await videoEl.play();
    } catch (err2) {
      console.warn('Camera init failed:', err2);
      return false;
    }
  }

  try {
    await faceapi.nets.tinyFaceDetector.loadFromUri('/face-api');
  } catch (err) {
    console.warn('Face detection model failed to load:', err);
    return false;
  }

  return true;
}

export function watchCameraTracks(): void {
  const stream = videoEl.srcObject as MediaStream | null;
  if (!stream) return;
  stream.getVideoTracks().forEach(t => {
    t.addEventListener('ended', () => {
      console.warn('[camera] Video track ended -- will restart on next visibility');
      scheduleVideoRestart();
    });
  });
}

export function scheduleVideoRestart(): void {
  if (videoRestartPending) return;
  videoRestartPending = true;
  setTimeout(async () => {
    videoRestartPending = false;
    if (document.visibilityState === 'hidden') return;
    console.log('[camera] Restarting camera stream after interruption');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: 320, height: 240 },
        audio: true,
      });
      videoEl.srcObject = stream;
      stream.getAudioTracks().forEach(t => t.enabled = false);
      await videoEl.play();
      watchCameraTracks();
      // Update mic stream if one was established
      if (audioStream) {
        const audioTracks = stream.getAudioTracks();
        if (audioTracks.length > 0) {
          audioTracks.forEach(t => t.enabled = true);
          setAudioStream(new MediaStream(audioTracks));
        }
      }
      console.log('[camera] Camera stream restarted');
    } catch (err) {
      console.warn('[camera] Camera restart failed:', err);
    }
  }, 800);
}

export function initCameraWatcher(): void {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    resumeIfSuspended();
    const tracks = (videoEl.srcObject as MediaStream | null)?.getVideoTracks() ?? [];
    const needsRestart = tracks.length === 0 || tracks.some(t => t.readyState === 'ended');
    if (needsRestart) {
      scheduleVideoRestart();
    } else if (videoEl.paused) {
      videoEl.play().catch(() => {});
    }
  });
}
