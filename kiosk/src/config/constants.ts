export const FACE_DETECT_INTERVAL = 500;
export const DETECTIONS_REQUIRED = 5;
// TinyFaceDetector input size (square). Larger = detects smaller/farther
// faces (better range) at higher CPU cost. 320 picks up faces ~1.5-2m back
// from the 640x480 feed; 224 (the old value) needed you right at the lens.
// The warmup detection in camera.ts MUST use this same value, or the first
// real inference recompiles WebGL shaders and stalls ~20s.
export const FACE_INPUT_SIZE = 320;
// Camera capture resolution. 640x480 gives distant faces enough pixels to
// survive the downscale to FACE_INPUT_SIZE; 320x240 did not.
export const CAMERA_WIDTH = 640;
export const CAMERA_HEIGHT = 480;
export const GREETING_COOLDOWN = 10_000;
export const JINGLE_DURATION = 30_000;
export const FACE_GONE_DISMISS_MS = 30_000;
export const MIN_GREETING_ALIVE_MS = 30_000;
export const CAROUSEL_MS = 30_000;
export const PAUSE_MS = 30_000;
export const PEAK_START = 6;
export const PEAK_END = 9;
export const QUOTE_COOLDOWN = 12 * 60 * 60 * 1000; // 12 hours (was 3 hours)
export const SILENCE_AUTO_SUMMARY_MS = 5_000;
export const BUS_CHECK_INTERVAL = 30_000;
export const BUS_LEAVE_SOON_MS = 15 * 60_000;
export const SILENCE_THRESHOLD = 0.015;
export const SILENCE_DURATION = 1500;
export const SILENCE_DURATION_SHORT = 700;
export const MIN_RECORDING_MS = 500;
export const MAX_RECORDING_MS = 10_000;
export const MAX_NATIVE_SILENCE = 5;
export const SWIPE_THRESHOLD = 50;
