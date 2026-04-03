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

export function resumeIfSuspended(): void {
  if (sharedContext?.state === 'suspended') {
    sharedContext.resume();
  }
}
