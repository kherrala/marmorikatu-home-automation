// Motion / presence detector — pure JS frame differencing. No model, no WebGL,
// no WASM. Detects movement (someone approaching), not faces, so it greets at
// any angle/distance but can also trip on pets or big lighting changes.
import type { Detector } from './types.js';
import { toGray } from './loader.js';
import { MOTION_W, MOTION_H, MOTION_PIXEL_DELTA, MOTION_RATIO } from '../../config/constants.js';

export class MotionDetector implements Detector {
  readonly name = 'motion';
  private ctx: CanvasRenderingContext2D | null = null;
  private prev: Uint8Array | null = null;

  async init(): Promise<void> {
    const canvas = document.createElement('canvas');
    canvas.width = MOTION_W;
    canvas.height = MOTION_H;
    this.ctx = canvas.getContext('2d', { willReadFrequently: true });
  }

  async detect(video: HTMLVideoElement): Promise<number> {
    if (!this.ctx) return 0;
    const gray = toGray(video, this.ctx, MOTION_W, MOTION_H);
    let changed = 0;
    const prev = this.prev;
    if (prev) {
      for (let i = 0; i < gray.length; i++) {
        if (Math.abs((gray[i] ?? 0) - (prev[i] ?? 0)) > MOTION_PIXEL_DELTA) changed++;
      }
    }
    this.prev = gray;
    const ratio = changed / gray.length;
    return ratio >= MOTION_RATIO ? ratio * 100 : 0;
  }
}
