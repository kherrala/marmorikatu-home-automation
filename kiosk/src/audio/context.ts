let sharedContext: AudioContext | null = null;

export function getAudioContext(): AudioContext {
  if (!sharedContext) {
    sharedContext = new (window.AudioContext || (window as any).webkitAudioContext)();
  }
  return sharedContext;
}

export function setAudioContext(ctx: AudioContext): void {
  sharedContext = ctx;
}

export function resumeIfSuspended(): Promise<void> {
  if (sharedContext?.state === 'suspended') {
    // Must be awaited before starting playback: a media element routed
    // through this context plays into a dead graph until resume completes,
    // which audibly cuts the start of the clip on iOS.
    return sharedContext.resume().catch(() => {});
  }
  return Promise.resolve();
}
