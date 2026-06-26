// face-api.js (TinyFaceDetector, tfjs/WebGL backend) — the original detector,
// kept as the default. Loads tfjs ONLY when this backend is selected.
import type { Detector } from './types.js';
import { loadScript } from './loader.js';
import { FACE_INPUT_SIZE } from '../../config/constants.js';
import { debugLog } from '../../debug.js';

export class FaceApiDetector implements Detector {
  readonly name = 'faceapi';
  private ready = false;

  async init(): Promise<void> {
    if (this.ready) return;
    await loadScript('/face-api/face-api.min.js');
    const t0 = performance.now();
    await faceapi.nets.tinyFaceDetector.loadFromUri('/face-api');
    debugLog(`faceapi: model loaded in ${Math.round(performance.now() - t0)}ms`);
    // Warm up the WebGL shaders on a blank canvas at the live input size, so
    // the first real detection isn't a ~20s shader compile.
    try {
      const tw = performance.now();
      const warm = document.createElement('canvas');
      warm.width = FACE_INPUT_SIZE;
      warm.height = FACE_INPUT_SIZE;
      await faceapi.detectSingleFace(
        warm as unknown as HTMLVideoElement,
        new faceapi.TinyFaceDetectorOptions({ inputSize: FACE_INPUT_SIZE, scoreThreshold: 0.5 }),
      );
      debugLog(`faceapi: warmup ${Math.round(performance.now() - tw)}ms`);
    } catch (e) {
      debugLog(`faceapi: warmup failed (${(e as Error).message})`);
    }
    this.ready = true;
  }

  async detect(video: HTMLVideoElement): Promise<number> {
    if (!this.ready) return 0;
    const det = await faceapi.detectSingleFace(
      video,
      new faceapi.TinyFaceDetectorOptions({ inputSize: FACE_INPUT_SIZE, scoreThreshold: 0.5 }),
    );
    return det ? det.score : 0;
  }

  debugStats(): string {
    try {
      type TfLike = { memory: () => { numTensors: number; numBytes: number } };
      const tf: TfLike | undefined =
        (faceapi as unknown as { tf?: TfLike }).tf
        ?? (window as unknown as { tf?: TfLike }).tf;
      if (tf?.memory) {
        const m = tf.memory();
        return ` tf=${m.numTensors}t/${(m.numBytes / 1048576).toFixed(0)}MB`;
      }
    } catch { /* tf not exposed */ }
    return '';
  }
}
