// Detector registry + runtime selection.
//
// Precedence: ?detector= URL query  >  window.__KIOSK_CONFIG.detector
// (written by entrypoint.sh from the KIOSK_DETECTOR env)  >  DEFAULT_DETECTOR.
import type { Detector } from './types.js';
import { FaceApiDetector } from './faceapi-detector.js';
import { MotionDetector } from './motion-detector.js';
import { PicoDetector } from './pico-detector.js';
import { DEFAULT_DETECTOR } from '../../config/constants.js';
import { debugLog } from '../../debug.js';

const NAMES = ['faceapi', 'pico', 'motion'] as const;
type Name = typeof NAMES[number];

function resolveName(): Name {
  const q = new URLSearchParams(location.search).get('detector');
  const cfg = (window as unknown as { __KIOSK_CONFIG?: { detector?: string } }).__KIOSK_CONFIG?.detector;
  const pick = (q || cfg || DEFAULT_DETECTOR).toLowerCase();
  return (NAMES as readonly string[]).includes(pick) ? (pick as Name) : 'faceapi';
}

let active: Detector | null = null;

export function getDetector(): Detector {
  if (active) return active;
  const name = resolveName();
  active = name === 'pico' ? new PicoDetector()
    : name === 'motion' ? new MotionDetector()
    : new FaceApiDetector();
  debugLog(`detector: selected "${active.name}"`);
  return active;
}
