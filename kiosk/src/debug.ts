// Send debug messages to backend for remote diagnosis.
// View all sessions: GET /api/chat/debug

const SESSION_ID = Math.random().toString(36).slice(2, 10);
const ua = navigator.userAgent.slice(0, 80);

export function debugLog(msg: string): void {
  console.log(`[kiosk] ${msg}`);
  // Fire-and-forget POST to backend
  fetch('/api/chat/debug', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session: SESSION_ID, ua, msg }),
  }).catch(() => {});
}
