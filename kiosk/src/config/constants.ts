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

// --- Pluggable detector backends (faceapi | pico | motion) -------------------
// Default backend when neither ?detector= query nor KIOSK_DETECTOR env is set.
export const DEFAULT_DETECTOR = 'faceapi';

// Motion (frame-difference) backend: downscale the frame to this size, count
// pixels whose luma changed by > DELTA between consecutive frames; "present"
// when the changed fraction exceeds RATIO.
export const MOTION_W = 64;
export const MOTION_H = 48;
export const MOTION_PIXEL_DELTA = 24;   // 0-255 per-pixel luma change
export const MOTION_RATIO = 0.04;       // 4% of pixels moved → presence

// pico.js backend: grayscale working resolution + cascade tuning. QTHRESH is
// the detection-quality cut (facefinder typically ~50 for a confident frontal
// face); MINSIZE is the smallest face in px at the working resolution.
export const PICO_W = 320;
export const PICO_H = 240;
export const PICO_QTHRESH = 50.0;
export const PICO_MINSIZE = 60;
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
