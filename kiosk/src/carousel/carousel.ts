import { dispatch, getState, select } from '../state/store.js';
import { KioskPhase } from '../types/state.js';
import { SLIDE_DEFS, NYSSE_IDX, TOTAL } from '../config/slides.js';
import { grafanaTheme } from '../config/sun.js';
import {
  CAROUSEL_MS, PAUSE_MS, PEAK_START, PEAK_END,
  BUS_CHECK_INTERVAL, BUS_LEAVE_SOON_MS, SWIPE_THRESHOLD,
} from '../config/constants.js';
import {
  slidesContainer, navContainer, pauseBadge, edgeLeft, edgeRight,
} from '../dom/elements.js';
import { dismissGreeting } from '../greeting/greeting.js';
import type { Subscription } from 'rxjs';
import { distinctUntilChanged } from 'rxjs/operators';

let slides: HTMLElement[] = [];
let dots: HTMLElement[] = [];
let carouselTimer: ReturnType<typeof setInterval> | null = null;
let pauseTimer: ReturnType<typeof setTimeout> | null = null;

export function showSlide(idx: number): void {
  const next = ((idx % TOTAL) + TOTAL) % TOTAL;
  const current = getState().carousel.currentSlide;
  slides[current]?.classList.remove('active');
  dots[current]?.classList.remove('active');
  dispatch({ type: 'CAROUSEL_SHOW', slide: next });
  slides[next]?.classList.add('active');
  dots[next]?.classList.add('active');
}

function startCarousel(): void {
  if (carouselTimer !== null) clearInterval(carouselTimer);
  carouselTimer = setInterval(() => {
    const s = getState();
    // Don't advance while paused, greeting active, or face recently detected
    const facePresent = s.faceDetection.lastFaceSeenTime > 0
      && Date.now() - s.faceDetection.lastFaceSeenTime < 5000;
    if (!s.carousel.paused && !facePresent) {
      showSlide(s.carousel.currentSlide + 1);
    }
  }, CAROUSEL_MS);
}

function handleInteraction(): void {
  dispatch({ type: 'CAROUSEL_PAUSE' });
  pauseBadge.classList.add('visible');
  if (pauseTimer !== null) clearTimeout(pauseTimer);
  pauseTimer = setTimeout(() => {
    dispatch({ type: 'CAROUSEL_RESUME' });
    pauseBadge.classList.remove('visible');
  }, PAUSE_MS);
}

export function initCarousel(): Subscription {
  // Build slides and dots from config
  SLIDE_DEFS.forEach((def, i) => {
    const div = document.createElement('div');
    div.className = 'slide';
    div.id = `slide-${i}`;
    div.innerHTML = `<iframe title="${def.title}" src="${def.src}"></iframe>`;
    slidesContainer.appendChild(div);

    const dot = document.createElement('div');
    dot.className = 'dot';
    dot.dataset.idx = String(i);
    navContainer.appendChild(dot);
  });

  slides = Array.from(document.querySelectorAll('.slide')) as HTMLElement[];
  dots = Array.from(document.querySelectorAll('.dot')) as HTMLElement[];

  // Edge click navigation
  edgeLeft.addEventListener('click', () => { handleInteraction(); showSlide(getState().carousel.currentSlide - 1); });
  edgeRight.addEventListener('click', () => { handleInteraction(); showSlide(getState().carousel.currentSlide + 1); });

  // Swipe navigation
  let swipeStartX = 0;
  let swipeStartY = 0;

  function handleSwipeStart(e: TouchEvent): void {
    const t = e.touches[0];
    if (!t) return;
    swipeStartX = t.clientX;
    swipeStartY = t.clientY;
  }
  function handleSwipeEnd(e: TouchEvent): void {
    const t = e.changedTouches[0];
    if (!t) return;
    const dx = t.clientX - swipeStartX;
    const dy = t.clientY - swipeStartY;
    if (Math.abs(dx) > SWIPE_THRESHOLD && Math.abs(dx) > Math.abs(dy)) {
      handleInteraction();
      showSlide(dx < 0 ? getState().carousel.currentSlide + 1 : getState().carousel.currentSlide - 1);
    }
  }

  [edgeLeft, edgeRight, document as unknown as HTMLElement].forEach(el => {
    el.addEventListener('touchstart', handleSwipeStart as EventListener, { passive: true });
    el.addEventListener('touchend', handleSwipeEnd as EventListener, { passive: true });
  });

  // Mouse/keyboard/blur
  let lastMoveReset = 0;
  document.addEventListener('mousemove', () => {
    const now = Date.now();
    if (now - lastMoveReset > 1000) {
      lastMoveReset = now;
      handleInteraction();
    }
  });

  window.addEventListener('blur', handleInteraction);

  document.addEventListener('keydown', (e: KeyboardEvent) => {
    if (e.key === 'Escape') dismissGreeting();
    if (e.key === 'ArrowLeft') { handleInteraction(); showSlide(getState().carousel.currentSlide - 1); }
    if (e.key === 'ArrowRight') { handleInteraction(); showSlide(getState().carousel.currentSlide + 1); }
  });

  // Grafana theme auto-switch
  let lastGrafanaTheme = grafanaTheme();
  setInterval(() => {
    const theme = grafanaTheme();
    if (theme === lastGrafanaTheme) return;
    lastGrafanaTheme = theme;
    document.querySelectorAll('.slide iframe').forEach(iframe => {
      const src = iframe.getAttribute('src');
      if (src?.startsWith('/grafana/')) {
        (iframe as HTMLIFrameElement).src = src.replace(/([?&])theme=(light|dark)/, '$1theme=' + theme);
      }
    });
  }, 60_000);

  // Bus departure auto-switch
  async function checkBusDepartures(): Promise<void> {
    const s = getState();
    if (s.carousel.paused || s.phase === KioskPhase.GREETING) return;
    try {
      const res = await fetch('/api/departures');
      if (!res.ok) return;
      const departures = await res.json() as Array<{ departureMs: number }>;
      const soon = departures.some(d => d.departureMs > 0 && d.departureMs <= BUS_LEAVE_SOON_MS);
      if (soon && getState().carousel.currentSlide !== NYSSE_IDX) {
        showSlide(NYSSE_IDX);
      }
    } catch { /* network error */ }
  }
  setInterval(checkBusDepartures, BUS_CHECK_INTERVAL);

  // Initial slide
  const hour = new Date().getHours();
  const initialSlide = (hour >= PEAK_START && hour < PEAK_END) ? NYSSE_IDX : 0;
  showSlide(initialSlide);
  startCarousel();

  // Subscribe to carousel state for DOM sync
  const sub = select(s => s.carousel.currentSlide).pipe(
    distinctUntilChanged(),
  ).subscribe(() => {
    // DOM updates happen in showSlide directly
  });

  return sub;
}
