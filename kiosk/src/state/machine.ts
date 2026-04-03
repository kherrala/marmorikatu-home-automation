import { type AppState, type ConversationMessage, KioskPhase } from '../types/state.js';

export type Action =
  | { type: 'SET_PHASE'; phase: KioskPhase }
  | { type: 'CAROUSEL_SHOW'; slide: number }
  | { type: 'CAROUSEL_PAUSE' }
  | { type: 'CAROUSEL_RESUME' }
  | { type: 'FACE_DETECTED' }
  | { type: 'FACE_LOST' }
  | { type: 'FACE_RESET_ABSENT' }
  | { type: 'FACE_SEEN'; time: number }
  | { type: 'GREETING_START'; time: number }
  | { type: 'GREETING_DISMISS'; time: number }
  | { type: 'CONVERSATION_ADD'; message: ConversationMessage }
  | { type: 'CONVERSATION_CLEAR' }
  | { type: 'AUTO_SUMMARY_GIVEN'; date: string }
  | { type: 'VOICE_INPUT_RECEIVED' }
  | { type: 'SET_LISTENING'; active: boolean }
  | { type: 'NATIVE_FAILED' }
  | { type: 'NATIVE_SILENCE_INCREMENT' }
  | { type: 'NATIVE_SILENCE_RESET' }
  | { type: 'AUDIO_UNLOCKED' }
  | { type: 'MIC_READY' }
  | { type: 'SET_QUOTE_TIME'; time: number }
  | { type: 'SET_HAD_VOICE_INPUT' }
  | { type: 'SET_VERSION'; version: string }
  ;

export const INITIAL_STATE: AppState = {
  phase: KioskPhase.INIT,
  carousel: { currentSlide: 0, paused: false },
  greeting: {
    overlayStartTime: 0,
    lastDismissTime: 0,
    lastQuoteTime: 0,
    conversationHistory: [],
    autoSummaryGiven: false,
    lastReportDate: '',
    hadVoiceInput: false,
  },
  faceDetection: {
    consecutiveDetections: 0,
    faceAbsentSinceLastGreeting: true,
    lastFaceSeenTime: 0,
  },
  voice: {
    listeningActive: false,
    nativeFailed: false,
    nativeSilenceCount: 0,
    voiceInputReceived: false,
  },
  audioUnlocked: false,
  micReady: false,
  knownVersion: null,
};

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'SET_PHASE':
      return { ...state, phase: action.phase };

    case 'CAROUSEL_SHOW':
      return { ...state, carousel: { ...state.carousel, currentSlide: action.slide } };
    case 'CAROUSEL_PAUSE':
      return { ...state, carousel: { ...state.carousel, paused: true } };
    case 'CAROUSEL_RESUME':
      return { ...state, carousel: { ...state.carousel, paused: false } };

    case 'FACE_DETECTED':
      return {
        ...state,
        faceDetection: {
          ...state.faceDetection,
          consecutiveDetections: state.faceDetection.consecutiveDetections + 1,
        },
      };
    case 'FACE_LOST':
      return {
        ...state,
        faceDetection: {
          ...state.faceDetection,
          consecutiveDetections: 0,
          faceAbsentSinceLastGreeting: true,
        },
      };
    case 'FACE_RESET_ABSENT':
      return {
        ...state,
        faceDetection: {
          ...state.faceDetection,
          consecutiveDetections: 0,
          faceAbsentSinceLastGreeting: false,
        },
      };
    case 'FACE_SEEN':
      return {
        ...state,
        faceDetection: { ...state.faceDetection, lastFaceSeenTime: action.time },
      };

    case 'GREETING_START':
      return {
        ...state,
        greeting: {
          ...state.greeting,
          overlayStartTime: action.time,
          conversationHistory: [],
          autoSummaryGiven: false,
          hadVoiceInput: false,
        },
        voice: { ...state.voice, voiceInputReceived: false },
      };
    case 'GREETING_DISMISS':
      return {
        ...state,
        greeting: { ...state.greeting, lastDismissTime: action.time },
        faceDetection: {
          ...state.faceDetection,
          consecutiveDetections: 0,
          faceAbsentSinceLastGreeting: false,
        },
      };

    case 'CONVERSATION_ADD':
      return {
        ...state,
        greeting: {
          ...state.greeting,
          conversationHistory: [...state.greeting.conversationHistory, action.message],
        },
      };
    case 'CONVERSATION_CLEAR':
      return {
        ...state,
        greeting: { ...state.greeting, conversationHistory: [] },
      };

    case 'AUTO_SUMMARY_GIVEN':
      return {
        ...state,
        greeting: {
          ...state.greeting,
          autoSummaryGiven: true,
          lastReportDate: action.date,
        },
      };

    case 'VOICE_INPUT_RECEIVED':
      return { ...state, voice: { ...state.voice, voiceInputReceived: true } };
    case 'SET_LISTENING':
      return { ...state, voice: { ...state.voice, listeningActive: action.active } };
    case 'NATIVE_FAILED':
      return { ...state, voice: { ...state.voice, nativeFailed: true } };
    case 'NATIVE_SILENCE_INCREMENT':
      return {
        ...state,
        voice: { ...state.voice, nativeSilenceCount: state.voice.nativeSilenceCount + 1 },
      };
    case 'NATIVE_SILENCE_RESET':
      return { ...state, voice: { ...state.voice, nativeSilenceCount: 0 } };

    case 'AUDIO_UNLOCKED':
      return { ...state, audioUnlocked: true };
    case 'MIC_READY':
      return { ...state, micReady: true };

    case 'SET_QUOTE_TIME':
      return { ...state, greeting: { ...state.greeting, lastQuoteTime: action.time } };
    case 'SET_HAD_VOICE_INPUT':
      return { ...state, greeting: { ...state.greeting, hadVoiceInput: true } };
    case 'SET_VERSION':
      return { ...state, knownVersion: action.version };

    default:
      return state;
  }
}
