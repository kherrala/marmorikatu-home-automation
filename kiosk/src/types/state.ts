export enum KioskPhase {
  INIT = 'INIT',
  READY = 'READY',
  GREETING = 'GREETING',
  COOLDOWN = 'COOLDOWN',
  FAILED = 'FAILED',
  DASHBOARD_ONLY = 'DASHBOARD_ONLY',
}

export interface ConversationMessage {
  readonly role: 'user' | 'assistant';
  readonly content: string;
}

export interface CarouselState {
  readonly currentSlide: number;
  readonly paused: boolean;
}

export interface GreetingState {
  readonly overlayStartTime: number;
  readonly lastDismissTime: number;
  readonly lastQuoteTime: number;
  readonly conversationHistory: readonly ConversationMessage[];
  readonly autoSummaryGiven: boolean;
  readonly lastReportDate: string;
  readonly hadVoiceInput: boolean;
}

export interface FaceDetectionState {
  readonly consecutiveDetections: number;
  readonly faceAbsentSinceLastGreeting: boolean;
  readonly lastFaceSeenTime: number;
}

export interface VoiceState {
  readonly listeningActive: boolean;
  readonly nativeFailed: boolean;
  readonly nativeSilenceCount: number;
  readonly voiceInputReceived: boolean;
}

export interface AppState {
  readonly phase: KioskPhase;
  readonly carousel: CarouselState;
  readonly greeting: GreetingState;
  readonly faceDetection: FaceDetectionState;
  readonly voice: VoiceState;
  readonly audioUnlocked: boolean;
  readonly micReady: boolean;
  readonly processing: boolean;
  readonly knownVersion: string | null;
}
