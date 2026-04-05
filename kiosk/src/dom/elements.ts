function getEl<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Element #${id} not found`);
  return el as T;
}

export const slides = () => Array.from(document.querySelectorAll('.slide')) as HTMLElement[];
export const dots = () => Array.from(document.querySelectorAll('.dot')) as HTMLElement[];

export const pauseBadge = getEl<HTMLDivElement>('pause-badge');
export const edgeLeft = getEl<HTMLDivElement>('swipe-left');
export const edgeRight = getEl<HTMLDivElement>('swipe-right');
export const startOverlay = getEl<HTMLDivElement>('start-overlay');
export const cameraDot = getEl<HTMLDivElement>('camera-dot');
export const greetingOverlay = getEl<HTMLDivElement>('greeting-overlay');
export const greetingCard = getEl<HTMLDivElement>('greeting-card');
export const greetingText = getEl<HTMLDivElement>('greeting-text');
export const reportText = getEl<HTMLDivElement>('report-text');
export const reportSpinner = getEl<HTMLDivElement>('report-spinner');
export const videoEl = getEl<HTMLVideoElement>('camera');
export const jingleAudio = getEl<HTMLAudioElement>('jingle');
export const ttsAudio = getEl<HTMLAudioElement>('tts-audio');
export const userTextEl = getEl<HTMLDivElement>('user-text');
export const listeningIndicator = getEl<HTMLDivElement>('listening-indicator');
export const initSpinner = getEl<HTMLDivElement>('init-spinner');
export const initError = getEl<HTMLDivElement>('init-error');
export const startLabel = startOverlay.querySelector('.label') as HTMLDivElement;
export const startSublabel = startOverlay.querySelector('.sublabel') as HTMLDivElement;
export const avatarEl = document.getElementById('avatar')!;
export const slidesContainer = getEl<HTMLDivElement>('slides');
export const navContainer = getEl<HTMLDivElement>('nav');
export const screenshotBubble = getEl<HTMLDivElement>('screenshot-bubble');
export const screenshotImg = getEl<HTMLImageElement>('screenshot-img');
