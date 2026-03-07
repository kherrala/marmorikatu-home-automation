"""
Family calendar widget server for kiosk carousel.
Fetches events from a public Google Calendar iCal feed, expands recurring events,
caches in memory, serves a fullscreen styled calendar agenda page.
"""

import logging
import os
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("calendar")

# -- Config -------------------------------------------------------------------
ICAL_URL = os.environ.get("CALENDAR_ICAL_URL", "")
CACHE_TTL = int(os.environ.get("CALENDAR_CACHE_TTL", "900"))
PORT = int(os.environ.get("CALENDAR_PORT", "3022"))
DAYS_AHEAD = int(os.environ.get("CALENDAR_DAYS_AHEAD", "7"))
TZ = ZoneInfo("Europe/Helsinki")

WEEKDAYS_FI = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai", "sunnuntai"]

# -- Cache --------------------------------------------------------------------
_cache: dict = {"events": None, "ts": 0}


def _parse_events(cal_text: str, days: int = 14) -> list[dict]:
    """Parse iCal text and expand recurring events for the next N days."""
    cal = icalendar.Calendar.from_ical(cal_text)
    now = datetime.now(TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)

    raw_events = recurring_ical_events.of(cal).between(start, end)

    events = []
    for ev in raw_events:
        summary = str(ev.get("SUMMARY", ""))
        if not summary:
            continue

        dt_start = ev.get("DTSTART").dt
        dt_end = ev.get("DTEND").dt if ev.get("DTEND") else None
        location = str(ev.get("LOCATION", "")) if ev.get("LOCATION") else ""

        all_day = not isinstance(dt_start, datetime)

        if all_day:
            event_date = dt_start.isoformat()
            start_str = dt_start.isoformat()
            end_str = dt_end.isoformat() if dt_end else ""
        else:
            if dt_start.tzinfo is None:
                dt_start = dt_start.replace(tzinfo=TZ)
            else:
                dt_start = dt_start.astimezone(TZ)
            event_date = dt_start.date().isoformat()
            start_str = dt_start.isoformat()

            if dt_end:
                if dt_end.tzinfo is None:
                    dt_end = dt_end.replace(tzinfo=TZ)
                else:
                    dt_end = dt_end.astimezone(TZ)
                end_str = dt_end.isoformat()
            else:
                end_str = ""

        events.append({
            "summary": summary,
            "start": start_str,
            "end": end_str,
            "allDay": all_day,
            "location": location,
            "date": event_date,
        })

    # Sort by date then by allDay (all-day first), then by start time
    events.sort(key=lambda e: (e["date"], not e["allDay"], e["start"]))
    return events


async def fetch_events() -> list[dict]:
    """Fetch iCal feed, parse, cache. Falls back to stale cache on error."""
    now = time.time()
    if _cache["events"] and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["events"]

    if not ICAL_URL:
        log.warning("CALENDAR_ICAL_URL not set")
        return []

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(ICAL_URL)
            resp.raise_for_status()
            events = _parse_events(resp.text, days=14)
    except Exception as e:
        log.error("Calendar fetch failed: %s", e)
        if _cache["events"]:
            log.info("Returning stale cached data")
            return _cache["events"]
        raise

    _cache["events"] = events
    _cache["ts"] = now
    log.info("Calendar refreshed — %d events", len(events))
    return events


def _filter_events(events: list[dict], days: int) -> list[dict]:
    """Filter cached events to the requested number of days ahead."""
    today = date.today()
    cutoff = (today + timedelta(days=days)).isoformat()
    today_str = today.isoformat()
    return [e for e in events if today_str <= e["date"] < cutoff]


# -- Endpoints ----------------------------------------------------------------
async def api_calendar(request: Request):
    days = int(request.query_params.get("days", str(DAYS_AHEAD)))
    days = max(1, min(days, 14))
    events = await fetch_events()
    filtered = _filter_events(events, days)
    return JSONResponse({
        "events": filtered,
        "generated": datetime.now(TZ).isoformat(),
    })


async def health(request):
    return JSONResponse({"status": "ok"})


async def index(request):
    return HTMLResponse(CALENDAR_HTML)


# -- Inline HTML widget -------------------------------------------------------
CALENDAR_HTML = r"""<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kalenteri</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,200;0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,300&family=DM+Serif+Display&display=swap');

  *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }

  :root {
    --bg: linear-gradient(160deg, #0a0f1e 0%, #121a30 35%, #1a2540 65%, #1e2d48 100%);
    --glass: rgba(255,255,255,0.05);
    --glass-border: rgba(255,255,255,0.08);
    --text: #edf2f7;
    --text-dim: rgba(237,242,247,0.6);
    --text-muted: rgba(237,242,247,0.35);
    --accent: #64b5f6;
    --accent-warm: #ffb74d;
    --today: #81c784;
    --tomorrow: #64b5f6;
  }

  html, body {
    width: 100vw; height: 100vh;
    overflow: hidden;
    font-family: 'DM Sans', sans-serif;
    color: var(--text);
    background: var(--bg);
  }

  body::before {
    content: '';
    position: fixed; inset: 0;
    z-index: 0;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
    background-size: 200px;
    opacity: 0.5;
    pointer-events: none;
  }

  body::after {
    content: '';
    position: fixed; inset: 0;
    z-index: 0;
    background: radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.35) 100%);
    pointer-events: none;
  }

  .container {
    width: 100%; height: 100%;
    display: flex;
    flex-direction: column;
    padding: 4vh 5vw 3vh;
    position: relative;
    z-index: 2;
  }

  .header {
    display: flex;
    align-items: center;
    gap: 1.2vw;
    margin-bottom: 3vh;
    flex-shrink: 0;
  }

  .header-icon { font-size: 3.5vh; opacity: 0.7; }

  .header-label {
    font-size: 1.8vh;
    font-weight: 600;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--text-muted);
  }

  .agenda {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 3vh;
    scrollbar-width: none;
  }
  .agenda::-webkit-scrollbar { display: none; }

  .day-group { animation: fadeIn 0.8s cubic-bezier(0.22, 1, 0.36, 1) backwards; }

  .day-header {
    display: flex;
    align-items: baseline;
    gap: 1.5vw;
    margin-bottom: 1.5vh;
    padding-bottom: 1vh;
    border-bottom: 1px solid rgba(255,255,255,0.06);
  }

  .day-label {
    font-size: 1.6vh;
    font-weight: 600;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    padding: 0.4vh 1vw;
    border-radius: 10vh;
  }

  .day-label.today {
    background: rgba(129,199,132,0.15);
    color: var(--today);
  }

  .day-label.tomorrow {
    background: rgba(100,181,246,0.12);
    color: var(--tomorrow);
  }

  .day-label.other {
    color: var(--text-muted);
  }

  .day-name {
    font-family: 'DM Serif Display', serif;
    font-size: 3.5vh;
    font-weight: 400;
    background: linear-gradient(180deg, #fff 20%, rgba(255,255,255,0.75) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }

  .day-date {
    font-size: 2.2vh;
    font-weight: 300;
    color: var(--text-dim);
  }

  .events-list {
    display: flex;
    flex-direction: column;
    gap: 1vh;
  }

  .event-card {
    background: var(--glass);
    border: 1px solid var(--glass-border);
    border-radius: 1.8vh;
    padding: 2vh 2.5vw;
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    display: flex;
    align-items: center;
    gap: 2vw;
    position: relative;
    overflow: hidden;
    animation: cardIn 0.5s cubic-bezier(0.22, 1, 0.36, 1) backwards;
  }

  .event-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.08) 30%, rgba(255,255,255,0.08) 70%, transparent);
  }

  .event-time {
    min-width: 8vw;
    text-align: right;
    flex-shrink: 0;
  }

  .event-time-text {
    font-size: 2.4vh;
    font-weight: 500;
    color: var(--accent);
    font-variant-numeric: tabular-nums;
  }

  .event-time-end {
    font-size: 1.6vh;
    font-weight: 300;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  .event-allday {
    font-size: 1.6vh;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--accent-warm);
  }

  .event-divider {
    width: 3px;
    height: 100%;
    min-height: 4vh;
    border-radius: 2px;
    background: var(--accent);
    opacity: 0.4;
    flex-shrink: 0;
  }

  .event-allday-card .event-divider {
    background: var(--accent-warm);
  }

  .event-details { flex: 1; min-width: 0; }

  .event-summary {
    font-family: 'DM Serif Display', serif;
    font-size: 2.8vh;
    font-weight: 400;
    line-height: 1.25;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .event-location {
    font-size: 1.7vh;
    font-weight: 300;
    color: var(--text-dim);
    margin-top: 0.3vh;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 60vh;
    gap: 2vh;
  }

  .empty-icon { font-size: 8vh; opacity: 0.3; }

  .empty-text {
    font-size: 2.8vh;
    font-weight: 300;
    color: var(--text-dim);
  }

  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes cardIn {
    from { opacity: 0; transform: translateX(12px); }
    to { opacity: 1; transform: translateX(0); }
  }

  .loading {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    font-size: 2.8vh;
    font-weight: 300;
    color: var(--text-dim);
    gap: 2vh;
  }
  .loading-spinner {
    width: 3.5vh; height: 3.5vh;
    border: 2px solid rgba(255,255,255,0.08);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.9s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .update-time {
    position: fixed;
    bottom: 1.5vh;
    right: 2vw;
    font-size: 1.6vh;
    font-weight: 500;
    color: var(--text-muted);
    letter-spacing: 0.05em;
    z-index: 10;
  }
</style>
</head>
<body>

<div id="app" class="loading">
  <div class="loading-spinner"></div>
  Ladataan kalenteria...
</div>

<div class="update-time" id="update-time"></div>

<script>
const WEEKDAYS = ['sunnuntai','maanantai','tiistai','keskiviikko','torstai','perjantai','lauantai'];

function formatDate(dateStr) {
  const d = new Date(dateStr);
  return d.getDate() + '.' + (d.getMonth() + 1) + '.';
}

function formatTime(isoStr) {
  const d = new Date(isoStr);
  return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
}

function dayLabel(dateStr) {
  const today = new Date();
  today.setHours(0,0,0,0);
  const tomorrow = new Date(today);
  tomorrow.setDate(tomorrow.getDate() + 1);

  const d = new Date(dateStr + 'T00:00:00');
  if (d.getTime() === today.getTime()) return { text: 'TÄNÄÄN', cls: 'today' };
  if (d.getTime() === tomorrow.getTime()) return { text: 'HUOMENNA', cls: 'tomorrow' };
  return { text: '', cls: 'other' };
}

function render(data) {
  if (!data || !data.events) return;
  const app = document.getElementById('app');
  const events = data.events;

  if (events.length === 0) {
    app.className = 'container';
    app.innerHTML = `
      <div class="header">
        <span class="header-icon">&#128197;</span>
        <span class="header-label">Perheen kalenteri</span>
      </div>
      <div class="empty-state">
        <div class="empty-icon">&#128198;</div>
        <div class="empty-text">Ei tulevia tapahtumia</div>
      </div>
    `;
    return;
  }

  // Group by date
  const groups = {};
  for (const ev of events) {
    if (!groups[ev.date]) groups[ev.date] = [];
    groups[ev.date].push(ev);
  }

  const dates = Object.keys(groups).sort();
  let html = `
    <div class="header">
      <span class="header-icon">&#128197;</span>
      <span class="header-label">Perheen kalenteri</span>
    </div>
    <div class="agenda">
  `;

  dates.forEach((dateStr, gi) => {
    const d = new Date(dateStr + 'T00:00:00');
    const weekday = WEEKDAYS[d.getDay()];
    const label = dayLabel(dateStr);

    html += `<div class="day-group" style="animation-delay: ${gi * 0.1}s">`;
    html += `<div class="day-header">`;
    if (label.text) {
      html += `<span class="day-label ${label.cls}">${label.text}</span>`;
    }
    html += `<span class="day-name">${weekday.charAt(0).toUpperCase() + weekday.slice(1)}</span>`;
    html += `<span class="day-date">${formatDate(dateStr)}</span>`;
    html += `</div>`;

    html += `<div class="events-list">`;
    groups[dateStr].forEach((ev, ei) => {
      const isAllDay = ev.allDay;
      html += `<div class="event-card ${isAllDay ? 'event-allday-card' : ''}" style="animation-delay: ${gi * 0.1 + ei * 0.05}s">`;

      html += `<div class="event-time">`;
      if (isAllDay) {
        html += `<div class="event-allday">Koko päivä</div>`;
      } else {
        html += `<div class="event-time-text">${formatTime(ev.start)}</div>`;
        if (ev.end) {
          html += `<div class="event-time-end">${formatTime(ev.end)}</div>`;
        }
      }
      html += `</div>`;

      html += `<div class="event-divider"></div>`;

      html += `<div class="event-details">`;
      html += `<div class="event-summary">${ev.summary}</div>`;
      if (ev.location) {
        html += `<div class="event-location">&#128205; ${ev.location}</div>`;
      }
      html += `</div>`;

      html += `</div>`;
    });
    html += `</div>`;
    html += `</div>`;
  });

  html += `</div>`;
  app.className = 'container fade-in';
  app.innerHTML = html;

  const ts = document.getElementById('update-time');
  const now = new Date();
  ts.textContent = 'Päivitetty ' + now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
}

let lastData = null;

async function refresh() {
  try {
    const resp = await fetch('api/calendar');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    lastData = data;
    render(data);
  } catch (e) {
    console.error('Calendar fetch error:', e);
    if (lastData) render(lastData);
  }
}

refresh();
setInterval(refresh, 5 * 60 * 1000);
</script>
</body>
</html>"""

# -- App ----------------------------------------------------------------------
app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/calendar", api_calendar),
        Route("/health", health),
    ],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
