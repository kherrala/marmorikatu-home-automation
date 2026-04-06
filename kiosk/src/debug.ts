// In-memory debug log for remote diagnosis of mobile sessions.
// Access via: window.__kioskDebug in browser console, or POST to /api/chat/debug

const MAX_ENTRIES = 200;
const entries: Array<{ time: string; msg: string }> = [];

export function debugLog(msg: string): void {
  const time = new Date().toISOString().slice(11, 23); // HH:MM:SS.mmm
  entries.push({ time, msg });
  if (entries.length > MAX_ENTRIES) entries.shift();
  console.log(`[kiosk] ${msg}`);
}

export function getDebugLog(): Array<{ time: string; msg: string }> {
  return entries;
}

// Expose globally for console access
(window as any).__kioskDebug = {
  log: () => entries,
  clear: () => { entries.length = 0; },
};
