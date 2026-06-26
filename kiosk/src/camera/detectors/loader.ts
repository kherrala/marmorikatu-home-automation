// Shared helpers for detector backends.

const _loaded = new Set<string>();

/** Inject a classic (non-module) <script> once and resolve when it has run.
 *  Used so each backend pulls its own library (face-api.min.js, pico.js)
 *  on demand instead of every page load paying for all of them. */
export function loadScript(src: string): Promise<void> {
  if (_loaded.has(src)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.async = true;
    s.onload = () => { _loaded.add(src); resolve(); };
    s.onerror = () => reject(new Error(`failed to load ${src}`));
    document.head.appendChild(s);
  });
}

/** Draw the video downscaled into a reused canvas and return its luminance
 *  bytes (one Uint8Array of length w*h). Fast integer luma. */
export function toGray(
  video: HTMLVideoElement,
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
): Uint8Array {
  ctx.drawImage(video, 0, 0, w, h);
  const d = ctx.getImageData(0, 0, w, h).data;
  const gray = new Uint8Array(w * h);
  for (let i = 0, p = 0; i < gray.length; i++, p += 4) {
    // 0.299R + 0.587G + 0.114B, fixed-point (77/150/29 over 256)
    gray[i] = ((d[p] ?? 0) * 77 + (d[p + 1] ?? 0) * 150 + (d[p + 2] ?? 0) * 29) >> 8;
  }
  return gray;
}
