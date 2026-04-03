declare namespace faceapi {
  namespace nets {
    const tinyFaceDetector: {
      loadFromUri(uri: string): Promise<void>;
    };
  }
  class TinyFaceDetectorOptions {
    constructor(options: { inputSize: number; scoreThreshold: number });
  }
  function detectSingleFace(
    input: HTMLVideoElement,
    options: TinyFaceDetectorOptions
  ): Promise<{ score: number } | undefined>;
}
