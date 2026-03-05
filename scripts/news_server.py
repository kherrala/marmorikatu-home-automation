"""
News headlines widget server for kiosk carousel.
Fetches Finnish news from Yle RSS feeds (free, no API key), caches in memory,
serves a fullscreen styled news page.
"""

import asyncio
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("news")

# -- Config -------------------------------------------------------------------
FEEDS = os.environ.get(
    "NEWS_FEEDS",
    "https://yle.fi/rss/uutiset/tuoreimmat,https://yle.fi/rss/t/18-146831/fi",
).split(",")

FEED_LABELS = {
    "tuoreimmat": "Uutiset",
    "18-146831": "Pirkanmaa",
}

CACHE_TTL = int(os.environ.get("NEWS_CACHE_TTL", "900"))
PORT = int(os.environ.get("NEWS_PORT", "3021"))
MAX_ITEMS = 20

# -- Cache --------------------------------------------------------------------
_cache: dict = {"items": None, "ts": 0}


def _label_for_url(url: str) -> str:
    """Extract a human-friendly source label from the feed URL."""
    for key, label in FEED_LABELS.items():
        if key in url:
            return label
    return "Uutiset"


def _parse_rss(xml_text: str, source: str) -> list[dict]:
    """Parse RSS XML and return list of news items."""
    items = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            title = item.findtext("title", "").strip()
            description = item.findtext("description", "").strip()
            link = item.findtext("link", "").strip()
            pub_date_str = item.findtext("pubDate", "").strip()

            pub_date = None
            if pub_date_str:
                try:
                    pub_date = parsedate_to_datetime(pub_date_str)
                except Exception:
                    pass

            if title:
                items.append(
                    {
                        "title": title,
                        "description": description,
                        "link": link,
                        "pubDate": pub_date.isoformat() if pub_date else "",
                        "source": source,
                        "_sort_ts": pub_date.timestamp() if pub_date else 0,
                    }
                )
    except ET.ParseError as e:
        log.error("RSS parse error for %s: %s", source, e)
    return items


async def fetch_news() -> list[dict]:
    """Fetch from all RSS feeds, merge, deduplicate, return top items."""
    now = time.time()
    if _cache["items"] and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["items"]

    all_items: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            tasks = []
            for url in FEEDS:
                tasks.append(client.get(url.strip()))
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            for url, resp in zip(FEEDS, responses):
                if isinstance(resp, Exception):
                    log.error("Feed fetch failed for %s: %s", url, resp)
                    continue
                if resp.status_code != 200:
                    log.error("Feed HTTP %d for %s", resp.status_code, url)
                    continue
                source = _label_for_url(url)
                items = _parse_rss(resp.text, source)
                all_items.extend(items)
    except Exception as e:
        log.error("News fetch failed: %s", e)
        if _cache["items"]:
            log.info("Returning stale cached data")
            return _cache["items"]
        raise

    # Deduplicate by title
    seen: set[str] = set()
    unique: list[dict] = []
    for item in all_items:
        if item["title"] not in seen:
            seen.add(item["title"])
            unique.append(item)

    # Sort by publish date descending
    unique.sort(key=lambda x: x["_sort_ts"], reverse=True)

    # Strip internal sort key, keep top N
    result = [{k: v for k, v in item.items() if k != "_sort_ts"} for item in unique[:MAX_ITEMS]]

    _cache["items"] = result
    _cache["ts"] = now
    log.info("News feeds refreshed — %d items from %d feeds", len(result), len(FEEDS))
    return result


# -- Endpoints ----------------------------------------------------------------
async def api_news(request):
    items = await fetch_news()
    return JSONResponse(items)


async def health(request):
    return JSONResponse({"status": "ok"})


async def index(request):
    return HTMLResponse(NEWS_HTML)


# -- Inline HTML widget -------------------------------------------------------
NEWS_HTML = r"""<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Uutiset</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,200;0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,300&family=DM+Serif+Display&display=swap');

  *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }

  :root {
    --bg: linear-gradient(160deg, #0a0f1e 0%, #121a30 35%, #1a2540 65%, #1e2d48 100%);
    --glass: rgba(255,255,255,0.05);
    --glass-border: rgba(255,255,255,0.08);
    --glass-hover: rgba(255,255,255,0.09);
    --text: #edf2f7;
    --text-dim: rgba(237,242,247,0.6);
    --text-muted: rgba(237,242,247,0.35);
    --accent: #64b5f6;
    --accent-warm: #ffb74d;
    --pirkanmaa: #81c784;
  }

  html, body {
    width: 100vw; height: 100vh;
    overflow: hidden;
    font-family: 'DM Sans', sans-serif;
    color: var(--text);
    background: var(--bg);
  }

  /* -- Noise overlay -------------------------------------------------------- */
  body::before {
    content: '';
    position: fixed; inset: 0;
    z-index: 0;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
    background-size: 200px;
    opacity: 0.5;
    pointer-events: none;
  }

  /* -- Vignette -------------------------------------------------------------- */
  body::after {
    content: '';
    position: fixed; inset: 0;
    z-index: 0;
    background: radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.35) 100%);
    pointer-events: none;
  }

  /* -- Container ------------------------------------------------------------- */
  .container {
    width: 100%; height: 100%;
    display: grid;
    grid-template-columns: 1fr 1fr;
    padding: 4vh 3.5vw 3vh;
    gap: 2.5vw;
    position: relative;
    z-index: 2;
  }

  /* -- Featured (left) ------------------------------------------------------- */
  .featured {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 3vh;
  }

  .featured-header {
    display: flex;
    align-items: center;
    gap: 1.2vw;
  }

  .featured-icon {
    font-size: 3vh;
    opacity: 0.7;
  }

  .featured-label {
    font-size: 1.8vh;
    font-weight: 600;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--text-muted);
  }

  .featured-card {
    background: var(--glass);
    border: 1px solid var(--glass-border);
    border-radius: 2.5vh;
    padding: 5vh 4vw;
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    position: relative;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    gap: 2.5vh;
  }

  .featured-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.12) 30%, rgba(255,255,255,0.12) 70%, transparent);
  }

  /* Decorative gradient accent */
  .featured-card::after {
    content: '';
    position: absolute;
    top: 0; left: 0;
    width: 100%; height: 100%;
    background: linear-gradient(135deg, rgba(100,181,246,0.06) 0%, transparent 40%, rgba(255,183,77,0.04) 100%);
    pointer-events: none;
  }

  .featured-source {
    display: inline-flex;
    align-items: center;
    gap: 0.5vw;
    padding: 0.6vh 1.2vw;
    background: rgba(100,181,246,0.12);
    border: 1px solid rgba(100,181,246,0.2);
    border-radius: 10vh;
    font-size: 1.6vh;
    font-weight: 600;
    color: var(--accent);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    width: fit-content;
    z-index: 1;
  }

  .featured-source.pirkanmaa {
    background: rgba(129,199,132,0.12);
    border-color: rgba(129,199,132,0.2);
    color: var(--pirkanmaa);
  }

  .featured-title {
    font-family: 'DM Serif Display', serif;
    font-size: 5.5vh;
    font-weight: 400;
    line-height: 1.15;
    letter-spacing: -0.01em;
    background: linear-gradient(180deg, #fff 20%, rgba(255,255,255,0.75) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    z-index: 1;
  }

  .featured-desc {
    font-size: 2.4vh;
    font-weight: 300;
    line-height: 1.55;
    color: var(--text-dim);
    z-index: 1;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .featured-time {
    font-size: 1.8vh;
    font-weight: 500;
    color: var(--text-muted);
    z-index: 1;
  }

  /* -- Headlines list (right) ------------------------------------------------ */
  .headlines {
    display: flex;
    flex-direction: column;
    overflow: hidden;
    position: relative;
  }

  .headlines-header {
    font-size: 1.8vh;
    font-weight: 600;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 2vh;
    padding-bottom: 1vh;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    flex-shrink: 0;
  }

  .headlines-scroll {
    flex: 1;
    overflow: hidden;
    position: relative;
  }

  .headlines-track {
    display: flex;
    flex-direction: column;
    gap: 0;
    animation: scroll-up var(--scroll-dur, 60s) linear infinite;
  }

  .headlines-track:hover {
    animation-play-state: paused;
  }

  @keyframes scroll-up {
    0% { transform: translateY(0); }
    100% { transform: translateY(var(--scroll-dist, -50%)); }
  }

  .headline-item {
    padding: 2vh 1.5vw;
    border-radius: 1.5vh;
    transition: background 0.3s ease;
    cursor: default;
    flex-shrink: 0;
  }

  .headline-item:not(:last-child) {
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }

  .headline-meta {
    display: flex;
    align-items: center;
    gap: 1vw;
    margin-bottom: 0.8vh;
  }

  .headline-source {
    font-size: 1.4vh;
    font-weight: 600;
    padding: 0.3vh 0.8vw;
    border-radius: 10vh;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .headline-source.uutiset {
    background: rgba(100,181,246,0.1);
    color: var(--accent);
  }

  .headline-source.pirkanmaa {
    background: rgba(129,199,132,0.1);
    color: var(--pirkanmaa);
  }

  .headline-time {
    font-size: 1.4vh;
    color: var(--text-muted);
    font-weight: 500;
  }

  .headline-title {
    font-family: 'DM Serif Display', serif;
    font-size: 2.6vh;
    font-weight: 400;
    line-height: 1.3;
    color: var(--text);
    margin-bottom: 0.5vh;
  }

  .headline-desc {
    font-size: 1.8vh;
    font-weight: 300;
    line-height: 1.4;
    color: var(--text-muted);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  /* -- Fade edges on scroll area -------------------------------------------- */
  .headlines-scroll::before,
  .headlines-scroll::after {
    content: '';
    position: absolute;
    left: 0; right: 0;
    height: 6vh;
    z-index: 5;
    pointer-events: none;
  }
  .headlines-scroll::before {
    top: 0;
    background: linear-gradient(to bottom, rgba(10,15,30,0.9), transparent);
  }
  .headlines-scroll::after {
    bottom: 0;
    background: linear-gradient(to top, rgba(10,15,30,0.9), transparent);
  }

  /* -- Stagger fade-in ------------------------------------------------------ */
  .fade-in { animation: fadeIn 0.8s cubic-bezier(0.22, 1, 0.36, 1); }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .featured-card {
    animation: cardIn 0.7s cubic-bezier(0.22, 1, 0.36, 1) 0.1s backwards;
  }
  @keyframes cardIn {
    from { opacity: 0; transform: translateY(16px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .headline-item {
    animation: itemIn 0.5s cubic-bezier(0.22, 1, 0.36, 1) backwards;
  }
  @keyframes itemIn {
    from { opacity: 0; transform: translateX(12px); }
    to { opacity: 1; transform: translateX(0); }
  }

  /* -- Loading -------------------------------------------------------------- */
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

  /* -- Update timestamp ----------------------------------------------------- */
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
  Ladataan uutisia...
</div>

<div class="update-time" id="update-time"></div>

<script>
// == Relative time in Finnish ==
function relativeTime(isoStr) {
  if (!isoStr) return '';
  const now = Date.now();
  const then = new Date(isoStr).getTime();
  const diffMin = Math.round((now - then) / 60000);
  if (diffMin < 1) return 'juuri nyt';
  if (diffMin < 60) return diffMin + ' min sitten';
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return diffH + ' t sitten';
  const diffD = Math.floor(diffH / 24);
  return diffD + ' pv sitten';
}

// == Source CSS class ==
function sourceClass(source) {
  if (source === 'Pirkanmaa') return 'pirkanmaa';
  return 'uutiset';
}

// == Render ==
function render(items) {
  if (!items || items.length === 0) return;

  const app = document.getElementById('app');
  const featured = items[0];
  const rest = items.slice(1);

  // Build headline items (duplicate for seamless scroll)
  const buildItems = (list) => list.map((item, i) => `
    <div class="headline-item" style="animation-delay: ${0.05 * (i + 1)}s">
      <div class="headline-meta">
        <span class="headline-source ${sourceClass(item.source)}">${item.source}</span>
        <span class="headline-time">${relativeTime(item.pubDate)}</span>
      </div>
      <div class="headline-title">${item.title}</div>
      ${item.description ? `<div class="headline-desc">${item.description}</div>` : ''}
    </div>
  `).join('');

  const headlineItems = buildItems(rest);
  // Duplicate for seamless infinite scroll
  const headlineItemsDup = buildItems(rest);

  app.className = 'container fade-in';
  app.innerHTML = `
    <div class="featured">
      <div class="featured-header">
        <span class="featured-icon">&#128240;</span>
        <span class="featured-label">Tuoreimmat uutiset</span>
      </div>
      <div class="featured-card">
        <span class="featured-source ${sourceClass(featured.source)}">${featured.source}</span>
        <div class="featured-title">${featured.title}</div>
        ${featured.description ? `<div class="featured-desc">${featured.description}</div>` : ''}
        <div class="featured-time">${relativeTime(featured.pubDate)}</div>
      </div>
    </div>
    <div class="headlines">
      <div class="headlines-header">Lisää uutisia</div>
      <div class="headlines-scroll">
        <div class="headlines-track" id="track">
          ${headlineItems}
          ${headlineItemsDup}
        </div>
      </div>
    </div>
  `;

  // Set scroll animation distance to exactly half (one copy)
  requestAnimationFrame(() => {
    const track = document.getElementById('track');
    if (track) {
      const halfHeight = track.scrollHeight / 2;
      track.style.setProperty('--scroll-dist', `-${halfHeight}px`);
      // Duration: ~4 seconds per item
      const dur = Math.max(30, rest.length * 4);
      track.style.setProperty('--scroll-dur', `${dur}s`);
    }
  });

  // Update timestamp
  const ts = document.getElementById('update-time');
  const now = new Date();
  ts.textContent = 'Päivitetty ' + now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
}

// == Fetch and render ==
let lastData = null;

async function refresh() {
  try {
    const resp = await fetch('api/news');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    lastData = data;
    render(data);
  } catch (e) {
    console.error('News fetch error:', e);
    if (lastData) render(lastData);
  }
}

// Initial load
refresh();
// Refresh every 5 min
setInterval(refresh, 5 * 60 * 1000);
</script>
</body>
</html>"""

# -- App ----------------------------------------------------------------------
app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/news", api_news),
        Route("/health", health),
    ],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
