"""
Weather widget server for kiosk carousel.
Fetches forecast from Open-Meteo (free, no API key), caches in memory,
serves a fullscreen animated weather page.
"""

import asyncio
import json
import logging
import os
import time

import httpx
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("weather")

# -- Config -------------------------------------------------------------------
LAT = os.environ.get("WEATHER_LAT", "61.4978")
LON = os.environ.get("WEATHER_LON", "23.7610")
CACHE_TTL = int(os.environ.get("WEATHER_CACHE_TTL", "600"))
PORT = int(os.environ.get("WEATHER_PORT", "3020"))

OPEN_METEO_URL = (
    f"https://api.open-meteo.com/v1/forecast?"
    f"latitude={LAT}&longitude={LON}"
    f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
    f"weather_code,wind_speed_10m,wind_direction_10m"
    f"&hourly=temperature_2m,weather_code,precipitation_probability"
    f"&daily=weather_code,temperature_2m_max,temperature_2m_min,"
    f"sunrise,sunset,precipitation_probability_max,"
    f"precipitation_sum,wind_speed_10m_max,sunshine_duration"
    f"&timezone=Europe%2FHelsinki&forecast_days=5"
)

# -- Cache --------------------------------------------------------------------
_cache: dict = {"data": None, "ts": 0}


async def fetch_weather() -> dict:
    """Fetch from Open-Meteo, update cache."""
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(OPEN_METEO_URL)
            resp.raise_for_status()
            data = resp.json()
        _cache["data"] = data
        _cache["ts"] = now
        log.info("Weather data refreshed")
        return data
    except Exception as e:
        log.error("Open-Meteo fetch failed: %s", e)
        if _cache["data"]:
            log.info("Returning stale cached data")
            return _cache["data"]
        raise


# -- Endpoints ----------------------------------------------------------------
async def api_weather(request):
    data = await fetch_weather()
    return JSONResponse(data)


async def health(request):
    return JSONResponse({"status": "ok"})


async def index(request):
    return HTMLResponse(WEATHER_HTML)


# -- WMO code mappings (Finnish) ----------------------------------------------
WMO_FI = {
    0: "Selkeää",
    1: "Enimmäkseen selkeää",
    2: "Puolipilvistä",
    3: "Pilvistä",
    45: "Sumua",
    48: "Huurretta",
    51: "Kevyttä tihkua",
    53: "Tihkusadetta",
    55: "Tiheää tihkua",
    56: "Jäätävää tihkua",
    57: "Jäätävää tihkua",
    61: "Kevyttä sadetta",
    63: "Sadetta",
    65: "Voimakasta sadetta",
    66: "Jäätävää sadetta",
    67: "Voimakasta jäätävää sadetta",
    71: "Kevyttä lumisadetta",
    73: "Lumisadetta",
    75: "Voimakasta lumisadetta",
    77: "Lumijyväsiä",
    80: "Kevyitä sadekuuroja",
    81: "Sadekuuroja",
    82: "Voimakkaita sadekuuroja",
    85: "Lumikuuroja",
    86: "Voimakkaita lumikuuroja",
    95: "Ukkosmyrsky",
    96: "Ukkosta ja rakeita",
    99: "Voimakasta ukkosta ja rakeita",
}

# -- Inline HTML widget -------------------------------------------------------
WEATHER_HTML = r"""<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sää – Tampere</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,200;0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,300&family=DM+Serif+Display&display=swap');

  *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }

  :root {
    /* Backgrounds (set by JS via weather + day/night) */
    --bg-day: linear-gradient(160deg, #7ec8e3 0%, #5ba3d9 35%, #4a90c4 65%, #3d8cc4 100%);
    --bg-overcast: linear-gradient(160deg, #bcc4d0 0%, #a8b2c0 35%, #96a0b0 100%);
    --bg-rain: linear-gradient(160deg, #7a8698 0%, #6b7888 35%, #5c6a7c 100%);
    --bg-snow: linear-gradient(160deg, #b8c4d4 0%, #a8b4c4 35%, #98a4b4 100%);
    --bg-night: linear-gradient(160deg, #060b1a 0%, #0d1630 35%, #141f3d 65%, #1a2744 100%);
    --bg-night-rain: linear-gradient(160deg, #0e1525 0%, #1a2540 35%, #253350 100%);
    --bg-night-snow: linear-gradient(160deg, #1f2535 0%, #2d3548 35%, #404d62 100%);
    --bg-night-overcast: linear-gradient(160deg, #1e2d3d 0%, #2b3f52 35%, #3a5268 100%);
    /* Light theme defaults (day) */
    --glass: rgba(255,255,255,0.5);
    --glass-border: rgba(0,0,0,0.08);
    --text: #1a2332;
    --text-dim: rgba(26,35,50,0.6);
    --text-muted: rgba(26,35,50,0.4);
    --accent: #2979ff;
    --accent-warm: #e65100;
    --border-subtle: rgba(0,0,0,0.06);
    --highlight: rgba(0,0,0,0.04);
    --spinner-border: rgba(0,0,0,0.08);
    --noise-opacity: 0.2;
    --vignette: radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.08) 100%);
    --temp-start: #1a2332;
    --temp-end: rgba(26,35,50,0.75);
    --desc-color: rgba(26,35,50,0.7);
    --scene-cloud-bg: rgba(100,110,130,0.08);
  }

  html[data-theme="dark"] {
    --glass: rgba(255,255,255,0.06);
    --glass-border: rgba(255,255,255,0.10);
    --text: #edf2f7;
    --text-dim: rgba(237,242,247,0.55);
    --text-muted: rgba(237,242,247,0.35);
    --accent: #64b5f6;
    --accent-warm: #ffb74d;
    --border-subtle: rgba(255,255,255,0.05);
    --highlight: rgba(255,255,255,0.12);
    --spinner-border: rgba(255,255,255,0.08);
    --noise-opacity: 0.5;
    --vignette: radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.3) 100%);
    --temp-start: #fff;
    --temp-end: rgba(255,255,255,0.7);
    --desc-color: rgba(255,255,255,0.75);
    --scene-cloud-bg: rgba(180,190,210,0.06);
  }

  html, body {
    width: 100vw; height: 100vh;
    overflow: hidden;
    font-family: 'DM Sans', sans-serif;
    color: var(--text);
    background: var(--bg-day);
    transition: background 2.5s ease;
    letter-spacing: 0.02em;
  }

  .container {
    width: 100%; height: 100%;
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    padding: 3vh 2.5vw 2vh;
    gap: 2vw;
    position: relative;
    z-index: 2;
  }

  /* -- Animated background scene -------------------------------------------- */
  #scene {
    position: fixed; inset: 0;
    z-index: 1;
    overflow: hidden;
    pointer-events: none;
  }

  body::before {
    content: '';
    position: fixed; inset: 0;
    z-index: 0;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
    background-size: 200px;
    opacity: var(--noise-opacity);
    pointer-events: none;
  }

  body::after {
    content: '';
    position: fixed; inset: 0;
    z-index: 0;
    background: var(--vignette);
    pointer-events: none;
  }

  /* -- Current weather ----------------------------------------------------- */
  .current {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 1.5vh;
    text-align: center;
  }

  .current-icon {
    width: 16vh; height: 16vh;
    position: relative;
    margin-bottom: 0.5vh;
    filter: drop-shadow(0 4px 20px rgba(0,0,0,0.1));
  }

  .current-temp {
    font-family: 'DM Serif Display', serif;
    font-size: 14vh;
    font-weight: 400;
    line-height: 0.9;
    letter-spacing: -0.03em;
    background: linear-gradient(180deg, var(--temp-start) 20%, var(--temp-end) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }

  .current-desc {
    font-size: 3.5vh;
    font-weight: 300;
    font-style: italic;
    color: var(--desc-color);
    margin-top: 0.5vh;
  }

  .current-meta {
    display: flex;
    gap: 1.5vw;
    margin-top: 1.5vh;
    flex-wrap: wrap;
    justify-content: center;
  }

  .meta-chip {
    display: flex;
    align-items: center;
    gap: 0.5vw;
    padding: 1vh 1.2vw;
    background: var(--glass);
    border: 1px solid var(--glass-border);
    border-radius: 10vh;
    font-size: 2.5vh;
    font-weight: 400;
    color: var(--text-dim);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
  }

  .meta-chip .icon {
    font-size: 2vh;
    opacity: 0.7;
  }

  .meta-chip .val {
    color: var(--text);
    font-weight: 500;
  }

  /* -- Forecast sections --------------------------------------------------- */
  .section {
    background: var(--glass);
    border: 1px solid var(--glass-border);
    border-radius: 2.5vh;
    padding: 2.5vh 2vw 2vh;
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    position: relative;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .section::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--highlight) 30%, var(--highlight) 70%, transparent);
  }

  .section-title {
    font-size: 2.2vh;
    font-weight: 600;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 1.5vh;
    padding-bottom: 1vh;
    border-bottom: 1px solid var(--border-subtle);
    flex-shrink: 0;
  }

  .hourly-row {
    display: flex;
    flex-direction: column;
    gap: 0;
    flex: 1;
    justify-content: space-evenly;
  }

  .daily-row {
    display: grid;
    grid-template-rows: 1fr 1fr 1fr 1fr;
    gap: 1.2vh;
    flex: 1;
  }

  .hourly-item {
    display: flex;
    align-items: center;
    gap: 1.5vw;
    padding: 1.5vh 0.8vw;
    border-radius: 1.2vh;
    transition: background 0.3s ease;
  }

  .hourly-item:not(:last-child) {
    border-bottom: 1px solid var(--border-subtle);
  }

  .daily-item {
    display: grid;
    grid-template-columns: auto 1fr;
    grid-template-rows: auto auto auto;
    gap: 0.3vh 1.2vw;
    padding: 1.2vh 1.2vw;
    border-radius: 1.5vh;
    background: var(--highlight);
    align-items: center;
  }

  .hourly-time {
    font-size: 3vh;
    font-weight: 500;
    color: var(--text-dim);
    width: 6ch;
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
  }

  .hourly-icon, .daily-icon {
    width: 6vh; height: 6vh;
    position: relative;
    flex-shrink: 0;
  }

  .hourly-temp {
    font-family: 'DM Serif Display', serif;
    font-size: 3.8vh;
    font-weight: 400;
    width: 5ch;
    text-align: right;
    flex-shrink: 0;
  }

  .hourly-precip {
    font-size: 2.4vh;
    color: var(--accent);
    opacity: 0.8;
    margin-left: auto;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }

  .daily-header {
    grid-column: 1 / -1;
    display: flex;
    align-items: baseline;
    gap: 0.8vw;
  }

  .daily-day {
    font-size: 2.8vh;
    font-weight: 600;
    text-transform: capitalize;
  }

  .daily-date {
    font-size: 2.2vh;
    color: var(--text-muted);
    font-weight: 400;
  }

  .daily-temps {
    font-family: 'DM Serif Display', serif;
    font-size: 3.2vh;
    font-weight: 400;
    margin-left: auto;
  }

  .daily-temps .hi {
    color: var(--text);
  }

  .daily-temps .lo {
    color: var(--text-muted);
    font-size: 2.6vh;
  }

  .daily-desc {
    font-size: 2.2vh;
    font-weight: 400;
    color: var(--text-dim);
    font-style: italic;
  }

  .daily-details {
    grid-column: 1 / -1;
    display: flex;
    gap: 1.2vw;
    flex-wrap: wrap;
    align-items: center;
  }

  .daily-detail {
    font-size: 2vh;
    color: var(--text-dim);
    display: flex;
    align-items: center;
    gap: 0.3vw;
    font-variant-numeric: tabular-nums;
  }

  .daily-detail .val {
    color: var(--text);
    font-weight: 500;
  }

  /* == Weather icon animations ============================================= */

  .wi-sun { width: 100%; height: 100%; position: relative; }
  .wi-sun .core {
    position: absolute; inset: 22%; border-radius: 50%;
    background: radial-gradient(circle at 40% 40%, #ffe082 0%, #ffca28 40%, #ffb300 100%);
    box-shadow: 0 0 30px rgba(255,202,40,0.5), 0 0 60px rgba(255,179,0,0.25), inset 0 -4px 8px rgba(255,143,0,0.2);
    animation: sun-pulse 4s ease-in-out infinite;
  }
  .wi-sun .ray {
    position: absolute; top: 50%; left: 50%; width: 2.5px; height: 28%;
    background: linear-gradient(to top, rgba(255,213,79,0.7), transparent);
    transform-origin: bottom center; border-radius: 2px;
    animation: ray-rotate 15s linear infinite;
  }
  @keyframes sun-pulse {
    0%, 100% { transform: scale(1); box-shadow: 0 0 30px rgba(255,202,40,0.5), 0 0 60px rgba(255,179,0,0.25); }
    50% { transform: scale(1.06); box-shadow: 0 0 45px rgba(255,202,40,0.6), 0 0 90px rgba(255,179,0,0.3); }
  }
  @keyframes ray-rotate {
    from { transform: rotate(var(--r)) translateY(-135%); }
    to { transform: rotate(calc(var(--r) + 360deg)) translateY(-135%); }
  }

  .wi-moon { width: 100%; height: 100%; position: relative; }
  .wi-moon .crescent {
    position: absolute; inset: 15%; border-radius: 50%;
    background: radial-gradient(circle at 35% 35%, #f0ece0 0%, #ddd8c8 50%, #c8c0a8 100%);
    box-shadow: 0 0 25px rgba(240,236,224,0.3), 0 0 50px rgba(240,236,224,0.1);
    animation: moon-glow 5s ease-in-out infinite;
  }
  .wi-moon .crescent-shadow {
    position: absolute; top: 8%; right: 12%; width: 52%; height: 62%; border-radius: 50%;
    background: radial-gradient(circle, rgba(10,15,30,0.95) 30%, rgba(10,15,30,0.6) 100%);
    filter: blur(1.5px);
  }
  .wi-moon .star {
    position: absolute; width: var(--s, 3px); height: var(--s, 3px);
    background: #fff; border-radius: 50%;
    animation: star-twinkle var(--dur, 3s) ease-in-out infinite;
    animation-delay: var(--delay, 0s); top: var(--ty); left: var(--tx);
  }
  @keyframes moon-glow {
    0%, 100% { box-shadow: 0 0 25px rgba(240,236,224,0.3), 0 0 50px rgba(240,236,224,0.1); }
    50% { box-shadow: 0 0 35px rgba(240,236,224,0.45), 0 0 70px rgba(240,236,224,0.15); }
  }
  @keyframes star-twinkle {
    0%, 100% { opacity: 0.3; transform: scale(1); }
    50% { opacity: 1; transform: scale(1.4); }
  }

  .wi-partly-cloudy-night { width: 100%; height: 100%; position: relative; }
  .wi-partly-cloudy-night .wi-moon { position: absolute; top: -5%; right: 0; width: 55%; height: 55%; z-index: 2; }
  .wi-partly-cloudy-night > .wi-cloud { position: absolute; bottom: 5%; left: 0; width: 80%; height: 45%; animation: cloud-pass 12s ease-in-out infinite alternate; z-index: 3; }

  .wi-cloud { width: 100%; height: 100%; position: relative; }
  .cloud-body {
    position: absolute; border-radius: 50%;
    background: linear-gradient(135deg, #d0d5de 0%, #b8bfcc 100%);
    box-shadow: inset -2px -3px 6px rgba(0,0,0,0.08);
  }
  .cloud-body.c1 { width: 50%; height: 55%; bottom: 22%; left: 22%; }
  .cloud-body.c2 { width: 38%; height: 42%; bottom: 28%; left: 42%; }
  .cloud-body.c3 { width: 68%; height: 32%; bottom: 18%; left: 16%; border-radius: 50px; }
  .cloud-dark .cloud-body { background: linear-gradient(135deg, #8a95a8 0%, #6b7a8f 100%); }
  .wi-cloud-drift { animation: cloud-drift 10s ease-in-out infinite alternate; }
  @keyframes cloud-drift { 0% { transform: translateX(-2%); } 100% { transform: translateX(2%); } }

  .wi-partly-cloudy { width: 100%; height: 100%; position: relative; }
  .wi-partly-cloudy .wi-sun { position: absolute; top: 0; left: 0; width: 60%; height: 60%; }
  .wi-partly-cloudy .wi-cloud { position: absolute; bottom: 5%; right: 0; width: 72%; height: 55%; animation: cloud-pass 12s ease-in-out infinite alternate; }
  @keyframes cloud-pass { 0% { transform: translateX(-4%); } 100% { transform: translateX(4%); } }

  .wi-rain { width: 100%; height: 100%; position: relative; overflow: hidden; }
  .wi-rain .wi-cloud { position: absolute; top: 0; left: 5%; width: 90%; height: 50%; }
  .raindrop {
    position: absolute; width: 2px; height: 14px;
    background: linear-gradient(to bottom, transparent, rgba(100,181,246,0.8));
    border-radius: 0 0 2px 2px; animation: rain-fall var(--dur) linear infinite;
    animation-delay: var(--delay); top: 50%; left: var(--x);
  }
  @keyframes rain-fall { 0% { transform: translateY(0); opacity: 0.9; } 100% { transform: translateY(280%); opacity: 0; } }

  .wi-snow { width: 100%; height: 100%; position: relative; overflow: hidden; }
  .wi-snow .wi-cloud { position: absolute; top: 0; left: 5%; width: 90%; height: 50%; }
  .snowflake {
    position: absolute; width: 4px; height: 4px; background: #fff; border-radius: 50%;
    opacity: 0.85; top: 50%; left: var(--x);
    animation: snow-fall var(--dur) linear infinite; animation-delay: var(--delay);
    box-shadow: 0 0 3px rgba(255,255,255,0.3);
  }
  @keyframes snow-fall {
    0% { transform: translateY(0) translateX(0) rotate(0deg); opacity: 0.85; }
    50% { transform: translateY(130%) translateX(6px) rotate(180deg); opacity: 0.6; }
    100% { transform: translateY(280%) translateX(-4px) rotate(360deg); opacity: 0; }
  }

  .wi-drizzle { width: 100%; height: 100%; position: relative; overflow: hidden; }
  .wi-drizzle .wi-cloud { position: absolute; top: 0; left: 5%; width: 90%; height: 50%; }
  .drizzle-drop {
    position: absolute; width: 1.5px; height: 8px;
    background: linear-gradient(to bottom, transparent, rgba(100,181,246,0.45));
    border-radius: 0 0 1px 1px; animation: rain-fall var(--dur) linear infinite;
    animation-delay: var(--delay); top: 50%; left: var(--x);
  }

  .wi-fog { width: 100%; height: 100%; position: relative; display: flex; flex-direction: column; justify-content: center; gap: 10%; padding: 12%; }
  .fog-bar { height: 3px; border-radius: 3px; background: rgba(200,210,220,0.4); animation: fog-breathe var(--dur) ease-in-out infinite alternate; animation-delay: var(--delay); }
  @keyframes fog-breathe { 0% { opacity: 0.25; transform: translateX(-6%) scaleX(0.88); } 100% { opacity: 0.65; transform: translateX(6%) scaleX(1.06); } }

  .wi-thunder { width: 100%; height: 100%; position: relative; overflow: hidden; }
  .wi-thunder .wi-cloud { position: absolute; top: 0; left: 5%; width: 90%; height: 50%; }
  .wi-thunder .wi-cloud .cloud-body { background: linear-gradient(135deg, #6a7590 0%, #505e75 100%); }
  .lightning-bolt {
    position: absolute; top: 42%; left: 44%; width: 16%; height: 48%;
    background: none; z-index: 3;
    animation: lightning-flash 5s ease-in-out infinite; animation-delay: var(--delay, 0s);
  }
  .lightning-bolt::before {
    content: ''; position: absolute; inset: 0;
    clip-path: polygon(50% 0%, 30% 45%, 55% 45%, 35% 100%, 75% 38%, 50% 38%);
    background: linear-gradient(to bottom, #fff8e1, #ffd54f);
    filter: drop-shadow(0 0 10px rgba(255,213,79,0.9));
  }
  @keyframes lightning-flash {
    0%, 86%, 100% { opacity: 0; } 88% { opacity: 1; } 90% { opacity: 0.15; } 92% { opacity: 0.9; } 94% { opacity: 0; }
  }

  /* -- Background scene particles ------------------------------------------ */
  .scene-rain {
    position: absolute; width: 1px; height: 28px;
    background: linear-gradient(to bottom, transparent, rgba(100,181,246,0.25));
    animation: scene-rain-fall var(--dur) linear infinite;
    animation-delay: var(--delay); top: -30px; left: var(--x);
  }
  @keyframes scene-rain-fall { to { transform: translateY(110vh); } }

  .scene-snow {
    position: absolute; width: var(--size, 4px); height: var(--size, 4px);
    background: rgba(255,255,255,0.5); border-radius: 50%;
    animation: scene-snow-fall var(--dur) linear infinite;
    animation-delay: var(--delay); top: -10px; left: var(--x);
    box-shadow: 0 0 4px rgba(255,255,255,0.15);
  }
  @keyframes scene-snow-fall {
    0% { transform: translateY(0) translateX(0); } 25% { transform: translateY(27vh) translateX(15px); }
    50% { transform: translateY(55vh) translateX(-12px); } 75% { transform: translateY(82vh) translateX(8px); }
    100% { transform: translateY(110vh) translateX(-4px); }
  }

  .scene-cloud {
    position: absolute; width: var(--w, 200px); height: var(--h, 60px);
    background: var(--scene-cloud-bg); border-radius: 50%; filter: blur(12px);
    animation: scene-cloud-drift var(--dur) linear infinite; top: var(--y); left: -250px;
  }
  @keyframes scene-cloud-drift { to { left: calc(100vw + 250px); } }

  .scene-star {
    position: absolute; width: var(--s, 2px); height: var(--s, 2px);
    background: #fff; border-radius: 50%;
    animation: star-twinkle var(--dur) ease-in-out infinite;
    animation-delay: var(--delay); top: var(--y); left: var(--x);
  }

  /* -- Animations ---------------------------------------------------------- */
  .fade-in { animation: fadeIn 0.8s cubic-bezier(0.22, 1, 0.36, 1); }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
  .section { animation: sectionIn 0.6s cubic-bezier(0.22, 1, 0.36, 1) backwards; }
  .section:nth-child(2) { animation-delay: 0.15s; }
  .section:nth-child(3) { animation-delay: 0.3s; }
  @keyframes sectionIn { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }

  .location {
    font-size: 2.4vh;
    font-weight: 600;
    color: var(--text-muted);
    letter-spacing: 0.25em;
    text-transform: uppercase;
  }

  .loading {
    display: flex; align-items: center; justify-content: center;
    height: 100vh; font-size: 2.8vh; font-weight: 300;
    color: var(--text-dim); gap: 2vh;
  }
  .loading-spinner {
    width: 3.5vh; height: 3.5vh;
    border: 2px solid var(--spinner-border);
    border-top-color: var(--accent); border-radius: 50%;
    animation: spin 0.9s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .update-time {
    position: fixed; bottom: 1.5vh; right: 2vw;
    font-size: 1.6vh; font-weight: 500;
    color: var(--text-muted); letter-spacing: 0.05em; z-index: 10;
  }
</style>
</head>
<body>

<div id="scene"></div>

<div id="app" class="loading">
  <div class="loading-spinner"></div>
  Ladataan säätietoja...
</div>

<div class="update-time" id="update-time"></div>

<script>
// == Initial theme from NOAA calculation (before API data loads) ==
(function initTheme() {
  var LAT = 61.4978, LNG = 23.7610;
  var D2R = Math.PI / 180, R2D = 180 / Math.PI;
  function getSunHour(doy, rising) {
    var lngH = LNG / 15;
    var t = doy + ((rising ? 6 : 18) - lngH) / 24;
    var M = 0.9856 * t - 3.289;
    var L = ((M + 1.916 * Math.sin(M * D2R) + 0.020 * Math.sin(2 * M * D2R) + 282.634) % 360 + 360) % 360;
    var RA = ((R2D * Math.atan(0.91764 * Math.tan(L * D2R))) % 360 + 360) % 360;
    RA += Math.floor(L / 90) * 90 - Math.floor(RA / 90) * 90;
    RA /= 15;
    var sinDec = 0.39782 * Math.sin(L * D2R);
    var cosDec = Math.cos(Math.asin(sinDec));
    var cosH = (Math.cos(90.833 * D2R) - sinDec * Math.sin(LAT * D2R)) / (cosDec * Math.cos(LAT * D2R));
    if (cosH > 1) return rising ? 99 : -99;
    if (cosH < -1) return rising ? -99 : 99;
    var H = R2D * Math.acos(cosH);
    if (rising) H = 360 - H;
    H /= 15;
    var ut = ((H + RA - 0.06571 * t - 6.622 - lngH) % 24 + 24) % 24;
    return ut + (-new Date().getTimezoneOffset() / 60);
  }
  var now = new Date();
  var start = new Date(now.getFullYear(), 0, 1);
  var doy = Math.floor((now - start) / 86400000) + 1;
  var sunrise = getSunHour(doy, true);
  var sunset = getSunHour(doy, false);
  var h = now.getHours() + now.getMinutes() / 60;
  var isDark = h < sunrise || h >= sunset;
  document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
  window._isNight = isDark;
  document.body.style.background = isDark ? 'var(--bg-night)' : 'var(--bg-day)';
})();

// == WMO codes ==
const WMO_FI = {
  0:'Selkeää', 1:'Enimmäkseen selkeää', 2:'Puolipilvistä', 3:'Pilvistä',
  45:'Sumua', 48:'Huurretta',
  51:'Kevyttä tihkua', 53:'Tihkusadetta', 55:'Tiheää tihkua',
  56:'Jäätävää tihkua', 57:'Jäätävää tihkua',
  61:'Kevyttä sadetta', 63:'Sadetta', 65:'Voimakasta sadetta',
  66:'Jäätävää sadetta', 67:'Jäätävää sadetta',
  71:'Kevyttä lumisadetta', 73:'Lumisadetta', 75:'Voimakasta lumisadetta', 77:'Lumijyväsiä',
  80:'Kevyitä sadekuuroja', 81:'Sadekuuroja', 82:'Voimakkaita sadekuuroja',
  85:'Lumikuuroja', 86:'Lumikuuroja',
  95:'Ukkosmyrsky', 96:'Ukkosta ja rakeita', 99:'Ukkosta ja rakeita'
};

const FI_DAYS = ['su','ma','ti','ke','to','pe','la'];

function wmoGroup(code) {
  if (code === 0) return 'clear';
  if (code <= 2) return 'partly-cloudy';
  if (code === 3) return 'cloudy';
  if (code === 45 || code === 48) return 'fog';
  if (code >= 51 && code <= 57) return 'drizzle';
  if (code >= 61 && code <= 67) return 'rain';
  if (code >= 71 && code <= 77) return 'snow';
  if (code >= 80 && code <= 82) return 'rain';
  if (code >= 85 && code <= 86) return 'snow';
  if (code >= 95) return 'thunder';
  return 'cloudy';
}

function moonHTML(cls) {
  const stars = Array.from({length:5}, (_,i) => {
    const tx = [5,78,88,12,62][i], ty = [8,3,42,58,22][i];
    const s = 1.5 + Math.random()*2;
    return `<div class="star" style="--tx:${tx}%;--ty:${ty}%;--s:${s}px;--dur:${2.5+Math.random()*3}s;--delay:${Math.random()*2}s"></div>`;
  }).join('');
  return `<div class="${cls} wi-moon"><div class="crescent"></div><div class="crescent-shadow"></div>${stars}</div>`;
}

function iconHTML(code, sizeClass, night) {
  const g = wmoGroup(code);
  const cls = sizeClass || '';
  const isNight = night != null ? night : window._isNight || false;
  switch (g) {
    case 'clear':
      if (isNight) return moonHTML(cls);
      return `<div class="${cls} wi-sun"><div class="core"></div>${Array.from({length:8}, (_,i) => `<div class="ray" style="--r:${i*45}deg"></div>`).join('')}</div>`;
    case 'partly-cloudy':
      if (isNight) return `<div class="${cls} wi-partly-cloudy-night">${moonHTML('')}<div class="wi-cloud wi-cloud-drift"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div></div>`;
      return `<div class="${cls} wi-partly-cloudy"><div class="wi-sun"><div class="core"></div>${Array.from({length:6}, (_,i) => `<div class="ray" style="--r:${i*60}deg"></div>`).join('')}</div><div class="wi-cloud wi-cloud-drift"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div></div>`;
    case 'cloudy':
      return `<div class="${cls} wi-cloud wi-cloud-drift cloud-dark"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div>`;
    case 'fog':
      return `<div class="${cls} wi-fog">${Array.from({length:4}, (_,i) => `<div class="fog-bar" style="--dur:${3+i*0.8}s;--delay:${i*0.5}s;width:${88-i*14}%"></div>`).join('')}</div>`;
    case 'drizzle':
      return `<div class="${cls} wi-drizzle"><div class="wi-cloud"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div>${Array.from({length:5}, (_,i) => `<div class="drizzle-drop" style="--x:${18+i*15}%;--dur:${2+Math.random()*0.6}s;--delay:${Math.random()*1.5}s"></div>`).join('')}</div>`;
    case 'rain':
      return `<div class="${cls} wi-rain"><div class="wi-cloud"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div>${Array.from({length:8}, (_,i) => `<div class="raindrop" style="--x:${10+i*10}%;--dur:${0.6+Math.random()*0.4}s;--delay:${Math.random()*0.8}s"></div>`).join('')}</div>`;
    case 'snow':
      return `<div class="${cls} wi-snow"><div class="wi-cloud"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div>${Array.from({length:6}, (_,i) => `<div class="snowflake" style="--x:${12+i*13}%;--dur:${2.5+Math.random()*1.5}s;--delay:${Math.random()*2}s"></div>`).join('')}</div>`;
    case 'thunder':
      return `<div class="${cls} wi-thunder"><div class="wi-cloud"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div><div class="lightning-bolt" style="--delay:0s"></div><div class="lightning-bolt" style="--delay:2.5s;left:56%;width:11%"></div>${Array.from({length:6}, (_,i) => `<div class="raindrop" style="--x:${14+i*12}%;--dur:${0.7+Math.random()*0.4}s;--delay:${Math.random()*1}s"></div>`).join('')}</div>`;
    default:
      return `<div class="${cls} wi-cloud wi-cloud-drift"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div>`;
  }
}

function setScene(code) {
  const scene = document.getElementById('scene');
  scene.innerHTML = '';
  const g = wmoGroup(code);
  const body = document.body;

  let isNight = false;
  if (window._sunrise && window._sunset) {
    const now = new Date();
    isNight = (now < new Date(window._sunrise) || now > new Date(window._sunset));
  }
  window._isNight = isNight;
  document.documentElement.setAttribute('data-theme', isNight ? 'dark' : 'light');

  if (isNight) {
    if (g === 'rain' || g === 'thunder') body.style.background = 'var(--bg-night-rain)';
    else if (g === 'snow') body.style.background = 'var(--bg-night-snow)';
    else if (g === 'cloudy' || g === 'fog' || g === 'drizzle') body.style.background = 'var(--bg-night-overcast)';
    else body.style.background = 'var(--bg-night)';
  } else {
    if (g === 'rain' || g === 'thunder') body.style.background = 'var(--bg-rain)';
    else if (g === 'snow') body.style.background = 'var(--bg-snow)';
    else if (g === 'cloudy' || g === 'fog' || g === 'drizzle') body.style.background = 'var(--bg-overcast)';
    else body.style.background = 'var(--bg-day)';
  }

  if (g === 'rain' || g === 'thunder') {
    for (let i = 0; i < 50; i++) { const el = document.createElement('div'); el.className = 'scene-rain'; el.style.cssText = `--x:${Math.random()*100}vw;--dur:${0.7+Math.random()*0.5}s;--delay:${Math.random()*2}s`; scene.appendChild(el); }
  } else if (g === 'snow') {
    for (let i = 0; i < 40; i++) { const el = document.createElement('div'); el.className = 'scene-snow'; el.style.cssText = `--x:${Math.random()*100}vw;--dur:${7+Math.random()*8}s;--delay:${Math.random()*10}s;--size:${2+Math.random()*4}px`; scene.appendChild(el); }
  } else if (g === 'drizzle') {
    for (let i = 0; i < 25; i++) { const el = document.createElement('div'); el.className = 'scene-rain'; el.style.cssText = `--x:${Math.random()*100}vw;--dur:${1.3+Math.random()*0.8}s;--delay:${Math.random()*3}s;opacity:0.25`; scene.appendChild(el); }
  }
  if (isNight) {
    for (let i = 0; i < 50; i++) { const el = document.createElement('div'); el.className = 'scene-star'; el.style.cssText = `--x:${Math.random()*100}vw;--y:${Math.random()*65}vh;--s:${1+Math.random()*2}px;--dur:${2.5+Math.random()*4}s;--delay:${Math.random()*6}s`; scene.appendChild(el); }
  }
  if (g !== 'clear') {
    for (let i = 0; i < 3; i++) { const el = document.createElement('div'); el.className = 'scene-cloud'; el.style.cssText = `--y:${8+i*25}%;--w:${180+Math.random()*180}px;--h:${35+Math.random()*35}px;--dur:${45+Math.random()*25}s;animation-delay:${-Math.random()*45}s`; scene.appendChild(el); }
  }
}

function render(data) {
  const app = document.getElementById('app');
  const c = data.current;

  if (data.daily && data.daily.sunrise) window._sunrise = data.daily.sunrise[0];
  if (data.daily && data.daily.sunset) window._sunset = data.daily.sunset[0];

  setScene(c.weather_code);

  const tempStr = Math.round(c.temperature_2m) + '\u00b0';
  const feelsLike = Math.round(c.apparent_temperature);
  const desc = WMO_FI[c.weather_code] || 'Tuntematon';
  const humidity = Math.round(c.relative_humidity_2m);
  const wind = Math.round(c.wind_speed_10m);

  let hi = '', lo = '';
  if (data.daily) {
    hi = Math.round(data.daily.temperature_2m_max[0]);
    lo = Math.round(data.daily.temperature_2m_min[0]);
  }

  // Hourly: next 8 hours
  let hourlyHTML = '';
  if (data.hourly) {
    const now = new Date();
    const times = data.hourly.time;
    let startIdx = 0;
    for (let i = 0; i < times.length; i++) {
      if (new Date(times[i]) >= now) { startIdx = i; break; }
    }
    const sr = window._sunrise ? new Date(window._sunrise) : null;
    const ss = window._sunset ? new Date(window._sunset) : null;
    for (let i = startIdx; i < Math.min(startIdx + 8, times.length); i++) {
      const t = new Date(times[i]);
      const h = t.getHours().toString().padStart(2, '0') + ':00';
      const temp = Math.round(data.hourly.temperature_2m[i]);
      const code = data.hourly.weather_code[i];
      const precip = data.hourly.precipitation_probability ? data.hourly.precipitation_probability[i] : null;
      const hourNight = sr && ss ? (t < sr || t > ss) : false;
      hourlyHTML += `<div class="hourly-item"><span class="hourly-time">${h}</span><div class="hourly-icon">${iconHTML(code, '', hourNight)}</div><span class="hourly-temp">${temp}\u00b0</span>${precip != null && precip > 0 ? `<span class="hourly-precip">${precip}%</span>` : ''}</div>`;
    }
  }

  // Daily: next 4 days (skip today)
  let dailyHTML = '';
  if (data.daily) {
    const dLen = Math.min(5, data.daily.time.length);
    for (let i = 1; i < dLen; i++) {
      const d = new Date(data.daily.time[i] + 'T00:00:00');
      const dayName = FI_DAYS[d.getDay()];
      const dateStr = d.getDate() + '.' + (d.getMonth() + 1) + '.';
      const code = data.daily.weather_code[i];
      const desc = WMO_FI[code] || '';
      const dhi = Math.round(data.daily.temperature_2m_max[i]);
      const dlo = Math.round(data.daily.temperature_2m_min[i]);
      const precProb = data.daily.precipitation_probability_max ? data.daily.precipitation_probability_max[i] : null;
      const precSum = data.daily.precipitation_sum ? data.daily.precipitation_sum[i] : null;
      const wind = data.daily.wind_speed_10m_max ? Math.round(data.daily.wind_speed_10m_max[i]) : null;
      const sunshine = data.daily.sunshine_duration ? Math.round(data.daily.sunshine_duration[i] / 3600) : null;

      let detailsHTML = '';
      if (precProb != null && precProb > 0) {
        detailsHTML += `<span class="daily-detail">\ud83d\udca7 <span class="val">${precProb}%</span>`;
        if (precSum != null && precSum > 0) detailsHTML += ` ${precSum.toFixed(1)}mm`;
        detailsHTML += '</span>';
      }
      if (wind != null) detailsHTML += `<span class="daily-detail">\ud83c\udf2c\ufe0f <span class="val">${wind} m/s</span></span>`;
      if (sunshine != null && sunshine > 0) detailsHTML += `<span class="daily-detail">\u2600\ufe0f <span class="val">${sunshine}h</span></span>`;

      dailyHTML += `<div class="daily-item">` +
        `<div class="daily-header"><span class="daily-day">${dayName}</span><span class="daily-date">${dateStr}</span>` +
        `<span class="daily-temps"><span class="hi">${dhi}\u00b0</span> <span class="lo">${dlo}\u00b0</span></span></div>` +
        `<div class="daily-icon">${iconHTML(code)}</div>` +
        `<div class="daily-desc">${desc}</div>` +
        `<div class="daily-details">${detailsHTML}</div>` +
        `</div>`;
    }
  }

  app.className = 'container fade-in';
  app.innerHTML = `
    <div class="current">
      <span class="location">Tampere</span>
      <div class="current-icon">${iconHTML(c.weather_code)}</div>
      <div class="current-temp">${tempStr}</div>
      <div class="current-desc">${desc}</div>
      <div class="current-meta">
        <div class="meta-chip"><span class="icon">\u2728</span> Tuntuu <span class="val">${feelsLike}\u00b0</span></div>
        <div class="meta-chip"><span class="icon">\u2191</span><span class="val">${hi}\u00b0</span> <span class="icon">\u2193</span><span class="val">${lo}\u00b0</span></div>
      </div>
      <div class="current-meta">
        <div class="meta-chip"><span class="icon">\ud83d\udca7</span><span class="val">${humidity}%</span></div>
        <div class="meta-chip"><span class="icon">\ud83c\udf2c\ufe0f</span><span class="val">${wind} m/s</span></div>
      </div>
    </div>
    <div class="section">
      <div class="section-title">Seuraavat tunnit</div>
      <div class="hourly-row">${hourlyHTML}</div>
    </div>
    <div class="section">
      <div class="section-title">Ennuste</div>
      <div class="daily-row">${dailyHTML}</div>
    </div>
  `;

  const ts = document.getElementById('update-time');
  const now = new Date();
  ts.textContent = 'P\u00e4ivitetty ' + now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
}

let lastData = null;

async function refresh() {
  try {
    const resp = await fetch('api/weather');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    lastData = data;
    render(data);
  } catch (e) {
    console.error('Weather fetch error:', e);
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
        Route("/api/weather", api_weather),
        Route("/health", health),
    ],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
