import { videoEl } from '../dom/elements.js';

/** Capture a frame from the camera as a base64 JPEG (no data URI prefix). */
export function captureFrame(): string | null {
  if (!videoEl.videoWidth || !videoEl.videoHeight) return null;

  const canvas = document.createElement('canvas');
  canvas.width = videoEl.videoWidth;
  canvas.height = videoEl.videoHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;

  ctx.drawImage(videoEl, 0, 0);
  // Return base64 without the "data:image/jpeg;base64," prefix
  return canvas.toDataURL('image/jpeg', 0.8).split(',')[1] ?? null;
}

const VISION_PATTERNS = /\b(mitä näet|katso kameraa|näytä mitä näet|kuvaa mitä näet|mitä kamerassa|katso ympärille)\b/i;

/** Check if the user is asking for camera vision analysis. */
export function isVisionRequest(text: string): boolean {
  return VISION_PATTERNS.test(text);
}
