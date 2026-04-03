import { getState, dispatch } from '../state/store.js';
import { KioskPhase } from '../types/state.js';

export function initVersionCheck(): void {
  async function checkVersion(): Promise<void> {
    try {
      const res = await fetch('/version.txt', { cache: 'no-store' });
      if (!res.ok) return;
      const version = (await res.text()).trim();
      const s = getState();
      if (s.knownVersion === null) {
        dispatch({ type: 'SET_VERSION', version });
      } else if (version !== s.knownVersion && s.phase !== KioskPhase.GREETING) {
        window.location.reload();
      }
    } catch { /* network error — skip */ }
  }

  checkVersion();
  setInterval(checkVersion, 60_000);
}
