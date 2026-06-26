// Pluggable "is someone in front of the tablet?" detector.
//
// Backends are runtime-selectable (KIOSK_DETECTOR env → /config.js, or a
// ?detector= URL override) so we can A/B them on the actual wall iPad —
// e.g. flip from the WebGL/tfjs face-api backend to a pure-JS one (pico /
// motion) to test whether the heavy GPU backend is what drives the periodic
// iOS memory-reap reloads.
export interface Detector {
  readonly name: string;

  /** Load model/assets. Idempotent; backends load their OWN deps here (and
   *  only when selected) so an unused backend never pulls tfjs/WebGL into
   *  memory. */
  init(): Promise<void>;

  /** Returns a positive confidence score when a face/person is detected this
   *  frame (already thresholded by the backend), or 0 when not. Must resolve
   *  to 0 (not throw) for the ordinary "no one there" case; throw only on a
   *  genuine error. Returns 0 while init() is still in flight. */
  detect(video: HTMLVideoElement): Promise<number>;

  /** Optional one-line stats appended to the 30s face summary (e.g. faceapi
   *  reports tf.memory() so we can watch for a tensor leak). */
  debugStats?(): string;

  /** Optional teardown when switching backends. */
  dispose?(): void;
}
