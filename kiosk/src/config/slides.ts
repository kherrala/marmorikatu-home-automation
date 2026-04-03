import { grafanaTheme } from './sun.js';
import type { SlideDefinition } from '../types/config.js';

export const SLIDE_DEFS: readonly SlideDefinition[] = [
  { title: 'Yleiskatsaus', src: '/grafana/d/wago-overview/building-overview?kiosk&theme=' + grafanaTheme() },
  { title: 'Sää', src: '/weather/' },
  { title: 'Uutiset', src: '/news/' },
  { title: 'Kalenteri', src: '/calendar/' },
  { title: 'Nysse', src: '/nysse/' },
];

export const NYSSE_IDX = SLIDE_DEFS.findIndex(s => s.title === 'Nysse');
export const NEWS_IDX = SLIDE_DEFS.findIndex(s => s.title === 'Uutiset');
export const TOTAL = SLIDE_DEFS.length;
