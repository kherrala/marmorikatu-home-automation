"""
Family calendar widget server for kiosk carousel.
Fetches events from a public Google Calendar iCal feed and PJHOY garbage collection
schedule, expands recurring events, caches in memory, serves a fullscreen styled
calendar agenda page.
"""

import asyncio
import json as json_mod
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
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
DAYS_AHEAD = int(os.environ.get("CALENDAR_DAYS_AHEAD", "90"))
TZ = ZoneInfo("Europe/Helsinki")

WEEKDAYS_FI = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai", "sunnuntai"]

# -- PJHOY config ------------------------------------------------------------
PJHOY_USERNAME = os.environ.get("PJHOY_USERNAME", "")
PJHOY_PASSWORD = os.environ.get("PJHOY_PASSWORD", "")
PJHOY_CUSTOMER_NUMBERS = [n for n in os.environ.get("PJHOY_CUSTOMER_NUMBERS", "").split(",") if n]
PJHOY_CACHE_FILE = Path(os.environ.get("PJHOY_CACHE_FILE", "/app/cache/pjhoy.json"))
PJHOY_CACHE_TTL = 86400  # 24 hours
PJHOY_BASE_URL = "https://extranet.pjhoy.fi/pirkka/"
PJHOY_DAYS = 90

PRODUCT_GROUPS: dict[str, str] = {
    "SEK": "\U0001f5d1\ufe0f Sekajäte tyhjennys",
    "BIO": "\U0001f343 Biojäte tyhjennys",
    "KK": "\U0001f4e6 Kartonki tyhjennys",
    "MU": "\U0001f504 Muovi tyhjennys",
    "PP": "\U0001f4c4 Paperi tyhjennys",
    "ME": "\U0001f527 Metalli tyhjennys",
    "LA": "\U0001f943 Lasi tyhjennys",
    "VU": "\u2623\ufe0f Vaarallinen jäte tyhjennys",
}

# -- Cache --------------------------------------------------------------------
_cache: dict = {"events": None, "ts": 0}
_pjhoy_cache: dict = {"events": None, "ts": 0}


def _load_pjhoy_disk_cache() -> list[dict] | None:
    """Load PJHOY cache from disk if it exists."""
    try:
        if PJHOY_CACHE_FILE.exists():
            data = json_mod.loads(PJHOY_CACHE_FILE.read_text())
            age = time.time() - data.get("ts", 0)
            events = data.get("events", [])
            if age < PJHOY_CACHE_TTL:
                _pjhoy_cache["events"] = events
                _pjhoy_cache["ts"] = data["ts"]
                log.info("PJHOY loaded from disk cache (%d events, %.0fh old)", len(events), age / 3600)
            return events
    except Exception as e:
        log.warning("PJHOY disk cache load failed: %s", e)
    return None


def _save_pjhoy_disk_cache(events: list[dict]) -> None:
    """Persist PJHOY events to disk."""
    try:
        PJHOY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PJHOY_CACHE_FILE.write_text(json_mod.dumps({"events": events, "ts": time.time()}))
    except Exception as e:
        log.warning("PJHOY disk cache save failed: %s", e)


def _extrapolate_dates(next_date: date, interval_weeks: int, days: int = PJHOY_DAYS) -> list[date]:
    """Generate all pickup dates within a window from next_date (forward and backward)."""
    today = date.today()
    window_start = today
    window_end = today + timedelta(days=days)
    interval = timedelta(weeks=interval_weeks)
    dates = []

    # Forward from next_date
    d = next_date
    while d <= window_end:
        if d >= window_start:
            dates.append(d)
        d += interval

    # Backward from next_date
    d = next_date - interval
    while d >= window_start:
        dates.append(d)
        d -= interval

    return sorted(set(dates))


async def fetch_pjhoy_events() -> list[dict]:
    """Fetch garbage collection schedule from PJHOY extranet."""
    now = time.time()
    if _pjhoy_cache["events"] is not None and (now - _pjhoy_cache["ts"]) < PJHOY_CACHE_TTL:
        return _pjhoy_cache["events"]

    if not PJHOY_USERNAME:
        return []

    async def _do_fetch(client: httpx.AsyncClient) -> list[dict]:
        # Step 1: Get session cookie
        await client.get(PJHOY_BASE_URL)
        # Step 2: Login (don't follow redirect — capture cookies from 302)
        login_resp = await client.post(
            f"{PJHOY_BASE_URL}j_acegi_security_check?target=2",
            data={"j_username": PJHOY_USERNAME, "j_password": PJHOY_PASSWORD, "remember-me": "false"},
            follow_redirects=False,
        )
        redirect_location = login_resp.headers.get("location", "")
        if "login_error" in redirect_location:
            raise RuntimeError(f"PJHOY login failed (redirect to {redirect_location})")
        # Step 3: Fetch services — construct full customer numbers from username prefix + suffixes
        parts = PJHOY_USERNAME.split("-")
        prefix = f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else PJHOY_USERNAME
        if PJHOY_CUSTOMER_NUMBERS:
            full_numbers = [f"{prefix}-{suffix}" for suffix in PJHOY_CUSTOMER_NUMBERS]
        else:
            full_numbers = [PJHOY_USERNAME]
        # Use list of tuples for repeated customerNumbers[] param
        params = [("customerNumbers[]", n) for n in full_numbers]
        resp = await client.get(
            f"{PJHOY_BASE_URL}secure/get_services_by_customer_numbers.do", params=params
        )
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "json" not in ct:
            raise ValueError(f"Expected JSON, got {ct} — session may have expired")
        return resp.json()

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            try:
                services = await _do_fetch(client)
            except ValueError as ve:
                log.info("PJHOY session expired, retrying login: %s", ve)
                services = await _do_fetch(client)
    except Exception as e:
        log.error("PJHOY fetch failed: %s", e)
        if _pjhoy_cache["events"] is not None:
            log.info("Returning stale PJHOY cache")
            return _pjhoy_cache["events"]
        # Try disk cache as last resort
        disk = _load_pjhoy_disk_cache()
        return disk if disk else []

    events = []
    for svc in services:
        next_date_str = svc.get("ASTNextDate")
        if not next_date_str:
            continue
        try:
            next_date = date.fromisoformat(next_date_str)
        except ValueError:
            continue
        interval_str = svc.get("ASTVali", "")
        try:
            interval_weeks = int(interval_str)
        except (ValueError, TypeError):
            interval_weeks = 0
        if interval_weeks <= 0:
            interval_weeks = 4  # default fallback

        pg = svc.get("tariff", {}).get("productgroup", "")
        summary = PRODUCT_GROUPS.get(pg, f"\U0001f5d1\ufe0f {svc.get('ASTNimi', 'Jätehuolto')}")

        for d in _extrapolate_dates(next_date, interval_weeks):
            events.append({
                "summary": summary,
                "start": d.isoformat(),
                "end": "",
                "allDay": True,
                "location": "",
                "date": d.isoformat(),
                "type": "garbage",
            })

    _pjhoy_cache["events"] = events
    _pjhoy_cache["ts"] = time.time()
    _save_pjhoy_disk_cache(events)
    log.info("PJHOY refreshed — %d events from %d services", len(events), len(services))
    return events


# Load disk cache on import
_load_pjhoy_disk_cache()


def _parse_events(cal_text: str, days: int = 90) -> list[dict]:
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
            "type": "calendar",
        })

    # Sort by date then by allDay (all-day first), then by start time
    events.sort(key=lambda e: (e["date"], not e["allDay"], e["start"]))
    return events


async def _fetch_ical_events() -> list[dict]:
    """Fetch iCal feed and parse events."""
    if not ICAL_URL:
        log.warning("CALENDAR_ICAL_URL not set")
        return []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(ICAL_URL)
        resp.raise_for_status()
        return _parse_events(resp.text, days=90)


async def fetch_events() -> list[dict]:
    """Fetch iCal + PJHOY events, merge, cache. Falls back to stale cache on error."""
    now = time.time()
    if _cache["events"] and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["events"]

    try:
        ical_events, pjhoy_events = await asyncio.gather(
            _fetch_ical_events(),
            fetch_pjhoy_events(),
            return_exceptions=True,
        )
        if isinstance(ical_events, BaseException):
            log.error("iCal fetch failed: %s", ical_events)
            ical_events = []
        if isinstance(pjhoy_events, BaseException):
            log.error("PJHOY fetch failed: %s", pjhoy_events)
            pjhoy_events = []
        events = ical_events + pjhoy_events
        events.sort(key=lambda e: (e["date"], not e["allDay"], e["start"]))
    except Exception as e:
        log.error("Calendar fetch failed: %s", e)
        if _cache["events"]:
            log.info("Returning stale cached data")
            return _cache["events"]
        raise

    _cache["events"] = events
    _cache["ts"] = now
    log.info("Calendar refreshed — %d events (%d iCal, %d PJHOY)",
             len(events),
             len(ical_events) if not isinstance(ical_events, BaseException) else 0,
             len(pjhoy_events) if not isinstance(pjhoy_events, BaseException) else 0)
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
    days = max(1, min(days, 90))
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


_TEMPLATE_DIR = Path(os.environ.get(
    "CALENDAR_TEMPLATE_DIR",
    Path(__file__).resolve().parent.parent / "templates" / "calendar"
))


def _load_template() -> str:
    html = (_TEMPLATE_DIR / "index.html").read_text()
    css = (_TEMPLATE_DIR / "style.css").read_text()
    js = (_TEMPLATE_DIR / "app.js").read_text()
    return html.replace("/* __CSS__ */", css).replace("/* __JS__ */", js)


CALENDAR_HTML = _load_template()

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
