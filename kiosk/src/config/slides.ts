import { grafanaTheme } from './sun.js';
import type { SlideDefinition } from '../types/config.js';

export const SLIDE_DEFS: readonly SlideDefinition[] = [
  // refresh=1m overrides the dashboard's baked-in 5s auto-refresh — on the
  // tablet a 5s refresh meant ~20 InfluxDB queries + a full panel re-render
  // every 5 seconds, which made Grafana sluggish and churned memory. 1 min is
  // plenty for an ambient wall overview.
  { title: 'Yleiskatsaus', src: '/grafana/d/wago-overview/yleiskuva?kiosk&refresh=1m&theme=' + grafanaTheme() },
  { title: 'Sää', src: '/weather/' },
  { title: 'Uutiset', src: '/news/' },
  { title: 'Kalenteri', src: '/calendar/' },
  { title: 'Nysse', src: '/nysse/' },
  { title: 'Kuulutukset', src: '#announcements', kind: 'native', nativeId: 'announcements-slide' },
];

export const NYSSE_IDX = SLIDE_DEFS.findIndex(s => s.title === 'Nysse');
export const NEWS_IDX = SLIDE_DEFS.findIndex(s => s.title === 'Uutiset');
export const ANNOUNCEMENTS_IDX = SLIDE_DEFS.findIndex(s => s.title === 'Kuulutukset');
export const TOTAL = SLIDE_DEFS.length;
