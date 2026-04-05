import { getState, dispatch, select } from '../state/store.js';
import { KioskPhase } from '../types/state.js';
import { distinctUntilChanged, filter } from 'rxjs/operators';

export function initVersionCheck(): void {
  let pendingReload = false;

  async function checkVersion(): Promise<void> {
    try {
      const res = await fetch('/version.txt', { cache: 'no-store' });
      if (!res.ok) return;
      const version = (await res.text()).trim();
      const s = getState();
      if (s.knownVersion === null) {
        dispatch({ type: 'SET_VERSION', version });
      } else if (version !== s.knownVersion) {
        if (s.phase !== KioskPhase.GREETING) {
          window.location.reload();
        } else {
          // Queue reload for when greeting ends
          pendingReload = true;
        }
      }
    } catch { /* network error — skip */ }
  }

  // Execute queued reload when leaving GREETING phase
  select(s => s.phase).pipe(
    distinctUntilChanged(),
    filter(p => p !== KioskPhase.GREETING),
  ).subscribe(() => {
    if (pendingReload) {
      window.location.reload();
    }
  });

  checkVersion();
  setInterval(checkVersion, 60_000);
}
