// pico.js (Pixel Intensity Comparison-based Object detection) — pure-JS,
// no WebGL/WASM/tfjs. Vendored at /pico/pico.js + /pico/facefinder cascade.
import type { Detector } from './types.js';
import { loadScript, toGray } from './loader.js';
import { PICO_W, PICO_H, PICO_QTHRESH, PICO_MINSIZE } from '../../config/constants.js';
import { debugLog } from '../../debug.js';

type ClassifyFn = (r: number, c: number, s: number, pixels: Uint8Array, ldim: number) => number;
type Image = { pixels: Uint8Array; nrows: number; ncols: number; ldim: number };
type Params = { shiftfactor: number; minsize: number; maxsize: number; scalefactor: number };
declare const pico: {
  unpack_cascade(bytes: Int8Array): ClassifyFn;
  run_cascade(image: Image, classify: ClassifyFn, params: Params): number[][];
  cluster_detections(dets: number[][], iouThreshold: number): number[][];
  instantiate_detection_memory(size: number): (dets: number[][]) => number[][];
};

export class PicoDetector implements Detector {
  readonly name = 'pico';
  private ctx: CanvasRenderingContext2D | null = null;
  private classify: ClassifyFn | null = null;
  private updateMemory: ((d: number[][]) => number[][]) | null = null;
  private ready = false;

  async init(): Promise<void> {
    if (this.ready) return;
    await loadScript('/pico/pico.js');
    const bytes = await fetch('/pico/facefinder').then(r => r.arrayBuffer());
    this.classify = pico.unpack_cascade(new Int8Array(bytes));
    // Temporal memory: accumulate detections over the last few frames before
    // clustering, which stabilises the per-frame quality and cuts flicker.
    this.updateMemory = pico.instantiate_detection_memory(5);
    const canvas = document.createElement('canvas');
    canvas.width = PICO_W;
    canvas.height = PICO_H;
    this.ctx = canvas.getContext('2d', { willReadFrequently: true });
    this.ready = true;
    debugLog('pico: cascade loaded');
  }

  async detect(video: HTMLVideoElement): Promise<number> {
    if (!this.ready || !this.ctx || !this.classify || !this.updateMemory) return 0;
    const gray = toGray(video, this.ctx, PICO_W, PICO_H);
    const image: Image = { pixels: gray, nrows: PICO_H, ncols: PICO_W, ldim: PICO_W };
    const params: Params = { shiftfactor: 0.1, minsize: PICO_MINSIZE, maxsize: 1000, scalefactor: 1.1 };
    let dets = pico.run_cascade(image, this.classify, params);
    dets = this.updateMemory(dets);
    dets = pico.cluster_detections(dets, 0.2); // [row, col, scale, quality]
    let best = 0;
    for (const d of dets) {
      const q = d[3] ?? 0;
      if (q > best) best = q;
    }
    return best >= PICO_QTHRESH ? best : 0;
  }
}
