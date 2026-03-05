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
    f"sunrise,sunset,precipitation_probability_max"
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
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@200;300;400;500;600&display=swap');

  *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }

  :root {
    --bg-day: linear-gradient(135deg, #1a2a6c 0%, #2d5f8a 40%, #4a90b8 100%);
    --bg-night: linear-gradient(135deg, #0a0e27 0%, #151d3b 40%, #1a2744 100%);
    --bg-overcast: linear-gradient(135deg, #2c3e50 0%, #3d566e 40%, #4a6274 100%);
    --bg-rain: linear-gradient(135deg, #1a1f3a 0%, #2d3a52 40%, #3a4a60 100%);
    --bg-snow: linear-gradient(135deg, #2a3040 0%, #3d4a5c 40%, #5a6a7a 100%);
    --glass: rgba(255,255,255,0.08);
    --glass-border: rgba(255,255,255,0.12);
    --text: #fff;
    --text-dim: rgba(255,255,255,0.6);
    --text-muted: rgba(255,255,255,0.4);
  }

  html, body {
    width: 100vw; height: 100vh;
    overflow: hidden;
    font-family: 'Inter', sans-serif;
    color: var(--text);
    background: var(--bg-day);
    transition: background 2s ease;
  }

  .container {
    width: 100%; height: 100%;
    display: grid;
    grid-template-columns: 1fr 1fr;
    padding: 3vh 3vw 2.5vh;
    gap: 3vw;
    position: relative;
    z-index: 2;
  }

  .forecast {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 2.5vh;
  }

  /* -- Animated background scene -------------------------------------------- */
  #scene {
    position: fixed; inset: 0;
    z-index: 1;
    overflow: hidden;
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
    width: 18vh; height: 18vh;
    position: relative;
    margin-bottom: 1vh;
  }

  .current-temp {
    font-size: 16vh;
    font-weight: 200;
    line-height: 1;
    letter-spacing: -0.02em;
    text-shadow: 0 2px 30px rgba(0,0,0,0.3);
  }

  .current-desc {
    font-size: 3.5vh;
    font-weight: 300;
    opacity: 0.9;
    text-transform: capitalize;
  }

  .current-details {
    display: flex;
    gap: 3vw;
    font-size: 2.8vh;
    font-weight: 300;
    color: var(--text-dim);
    margin-top: 0.5vh;
    flex-wrap: wrap;
    justify-content: center;
  }

  .current-details span {
    display: flex;
    align-items: center;
    gap: 0.8vw;
  }

  .detail-label {
    font-size: 2.4vh;
    color: var(--text-muted);
    margin-right: 0.3vw;
  }

  /* -- Hourly forecast ----------------------------------------------------- */
  .section {
    background: var(--glass);
    border: 1px solid var(--glass-border);
    border-radius: 2vh;
    padding: 2.5vh 3vw;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
  }

  .section-title {
    font-size: 2.2vh;
    font-weight: 600;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 2vh;
  }

  .hourly-row {
    display: flex;
    flex-direction: column;
    gap: 1.2vh;
  }

  .hourly-item {
    display: flex;
    align-items: center;
    gap: 2vw;
  }

  .hourly-time {
    font-size: 2.6vh;
    font-weight: 400;
    color: var(--text-dim);
    width: 7ch;
    flex-shrink: 0;
  }

  .hourly-icon {
    width: 5vh; height: 5vh;
    position: relative;
    flex-shrink: 0;
  }

  .hourly-temp {
    font-size: 3vh;
    font-weight: 300;
    width: 5ch;
    text-align: right;
    flex-shrink: 0;
  }

  .hourly-precip {
    font-size: 2vh;
    color: var(--text-muted);
    margin-left: auto;
  }

  /* -- Daily forecast ------------------------------------------------------ */
  .daily-row {
    display: flex;
    flex-direction: column;
    gap: 1.2vh;
  }

  .daily-item {
    display: flex;
    align-items: center;
    gap: 2vw;
  }

  .daily-day {
    font-size: 2.8vh;
    font-weight: 500;
    width: 4ch;
    flex-shrink: 0;
  }

  .daily-icon {
    width: 5vh; height: 5vh;
    position: relative;
    flex-shrink: 0;
  }

  .daily-temps {
    font-size: 3vh;
    font-weight: 300;
  }

  .daily-temps .lo {
    color: var(--text-dim);
  }

  .daily-precip {
    font-size: 2vh;
    color: var(--text-muted);
    margin-left: auto;
  }

  /* == Weather icon animations ============================================= */

  /* --- Sun --- */
  .wi-sun {
    width: 100%; height: 100%;
    position: relative;
  }
  .wi-sun .core {
    position: absolute;
    inset: 22%;
    border-radius: 50%;
    background: radial-gradient(circle, #ffd54f 0%, #ffb300 100%);
    box-shadow: 0 0 40px rgba(255,213,79,0.6), 0 0 80px rgba(255,179,0,0.3);
    animation: sun-pulse 3s ease-in-out infinite;
  }
  .wi-sun .ray {
    position: absolute;
    top: 50%; left: 50%;
    width: 3px; height: 30%;
    background: linear-gradient(to top, rgba(255,213,79,0.8), transparent);
    transform-origin: bottom center;
    border-radius: 2px;
    animation: ray-rotate 12s linear infinite;
  }
  @keyframes sun-pulse {
    0%, 100% { transform: scale(1); box-shadow: 0 0 40px rgba(255,213,79,0.6), 0 0 80px rgba(255,179,0,0.3); }
    50% { transform: scale(1.08); box-shadow: 0 0 60px rgba(255,213,79,0.8), 0 0 120px rgba(255,179,0,0.4); }
  }
  @keyframes ray-rotate {
    from { transform: rotate(var(--r)) translateY(-130%); }
    to { transform: rotate(calc(var(--r) + 360deg)) translateY(-130%); }
  }

  /* --- Moon --- */
  .wi-moon {
    width: 100%; height: 100%;
    position: relative;
  }
  .wi-moon .crescent {
    position: absolute;
    inset: 15%;
    border-radius: 50%;
    background: radial-gradient(circle at 35% 40%, #e8e4d4 0%, #d4cfb8 60%, #c8c0a0 100%);
    box-shadow: 0 0 30px rgba(232,228,212,0.4), 0 0 60px rgba(232,228,212,0.15);
    animation: moon-glow 4s ease-in-out infinite;
  }
  .wi-moon .crescent-shadow {
    position: absolute;
    top: 10%; right: 15%;
    width: 50%; height: 60%;
    border-radius: 50%;
    background: var(--bg-night);
    filter: blur(2px);
  }
  .wi-moon .star {
    position: absolute;
    width: var(--s, 3px); height: var(--s, 3px);
    background: #fff;
    border-radius: 50%;
    animation: star-twinkle var(--dur, 3s) ease-in-out infinite;
    animation-delay: var(--delay, 0s);
    top: var(--ty); left: var(--tx);
  }
  @keyframes moon-glow {
    0%, 100% { box-shadow: 0 0 30px rgba(232,228,212,0.4), 0 0 60px rgba(232,228,212,0.15); }
    50% { box-shadow: 0 0 40px rgba(232,228,212,0.55), 0 0 80px rgba(232,228,212,0.25); }
  }
  @keyframes star-twinkle {
    0%, 100% { opacity: 0.4; transform: scale(1); }
    50% { opacity: 1; transform: scale(1.3); }
  }

  /* --- Partly cloudy night (moon + cloud) --- */
  .wi-partly-cloudy-night {
    width: 100%; height: 100%;
    position: relative;
  }
  .wi-partly-cloudy-night .wi-moon {
    position: absolute;
    top: -5%; right: 0;
    width: 60%; height: 60%;
    z-index: 2;
  }
  .wi-partly-cloudy-night .wi-cloud {
    position: absolute;
    bottom: 0; left: 0;
    width: 85%; height: 50%;
    animation: cloud-pass 10s ease-in-out infinite alternate;
    z-index: 1;
  }

  /* --- Cloud --- */
  .wi-cloud {
    width: 100%; height: 100%;
    position: relative;
  }
  .cloud-body {
    position: absolute;
    border-radius: 50%;
    background: #c4c9d4;
    box-shadow: inset -3px -3px 8px rgba(0,0,0,0.1);
  }
  .cloud-body.c1 { width: 55%; height: 50%; bottom: 25%; left: 20%; }
  .cloud-body.c2 { width: 40%; height: 40%; bottom: 30%; left: 40%; }
  .cloud-body.c3 { width: 70%; height: 35%; bottom: 20%; left: 15%; border-radius: 20px; }
  .cloud-dark .cloud-body { background: #8a95a8; }

  .wi-cloud-drift {
    animation: cloud-drift 8s ease-in-out infinite alternate;
  }
  @keyframes cloud-drift {
    0% { transform: translateX(-3%); }
    100% { transform: translateX(3%); }
  }

  /* --- Partly cloudy (sun + cloud) --- */
  .wi-partly-cloudy {
    width: 100%; height: 100%;
    position: relative;
  }
  .wi-partly-cloudy .wi-sun {
    position: absolute;
    top: 0; left: 0;
    width: 65%; height: 65%;
  }
  .wi-partly-cloudy .wi-cloud {
    position: absolute;
    bottom: 5%; right: 0;
    width: 75%; height: 60%;
    animation: cloud-pass 10s ease-in-out infinite alternate;
  }
  @keyframes cloud-pass {
    0% { transform: translateX(-5%); }
    100% { transform: translateX(5%); }
  }

  /* --- Rain drops --- */
  .wi-rain {
    width: 100%; height: 100%;
    position: relative;
    overflow: hidden;
  }
  .wi-rain .wi-cloud { position: absolute; top: 0; left: 5%; width: 90%; height: 55%; }
  .raindrop {
    position: absolute;
    width: 2px;
    height: 12px;
    background: linear-gradient(to bottom, transparent, rgba(120,180,255,0.8));
    border-radius: 0 0 2px 2px;
    animation: rain-fall var(--dur) linear infinite;
    animation-delay: var(--delay);
    top: 55%;
    left: var(--x);
  }
  @keyframes rain-fall {
    0% { transform: translateY(0); opacity: 0.8; }
    100% { transform: translateY(250%); opacity: 0; }
  }

  /* --- Snow flakes --- */
  .wi-snow {
    width: 100%; height: 100%;
    position: relative;
    overflow: hidden;
  }
  .wi-snow .wi-cloud { position: absolute; top: 0; left: 5%; width: 90%; height: 55%; }
  .snowflake {
    position: absolute;
    width: 5px; height: 5px;
    background: #fff;
    border-radius: 50%;
    opacity: 0.9;
    top: 55%;
    left: var(--x);
    animation: snow-fall var(--dur) linear infinite;
    animation-delay: var(--delay);
  }
  @keyframes snow-fall {
    0% { transform: translateY(0) translateX(0) rotate(0deg); opacity: 0.9; }
    50% { transform: translateY(120%) translateX(8px) rotate(180deg); opacity: 0.7; }
    100% { transform: translateY(250%) translateX(-3px) rotate(360deg); opacity: 0; }
  }

  /* --- Drizzle --- */
  .wi-drizzle {
    width: 100%; height: 100%;
    position: relative;
    overflow: hidden;
  }
  .wi-drizzle .wi-cloud { position: absolute; top: 0; left: 5%; width: 90%; height: 55%; }
  .drizzle-drop {
    position: absolute;
    width: 1.5px;
    height: 7px;
    background: linear-gradient(to bottom, transparent, rgba(150,200,255,0.5));
    border-radius: 0 0 1px 1px;
    animation: rain-fall var(--dur) linear infinite;
    animation-delay: var(--delay);
    top: 55%;
    left: var(--x);
  }

  /* --- Fog --- */
  .wi-fog {
    width: 100%; height: 100%;
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 8%;
    padding: 10%;
  }
  .fog-bar {
    height: 4px;
    border-radius: 3px;
    background: rgba(200,210,220,0.5);
    animation: fog-breathe var(--dur) ease-in-out infinite alternate;
    animation-delay: var(--delay);
  }
  @keyframes fog-breathe {
    0% { opacity: 0.3; transform: translateX(-5%) scaleX(0.9); }
    100% { opacity: 0.7; transform: translateX(5%) scaleX(1.05); }
  }

  /* --- Thunderstorm --- */
  .wi-thunder {
    width: 100%; height: 100%;
    position: relative;
    overflow: hidden;
  }
  .wi-thunder .wi-cloud { position: absolute; top: 0; left: 5%; width: 90%; height: 55%; }
  .wi-thunder .wi-cloud .cloud-body { background: #6a7590; }
  .lightning-bolt {
    position: absolute;
    top: 45%; left: 45%;
    width: 15%; height: 45%;
    background: none;
    z-index: 3;
    animation: lightning-flash 4s ease-in-out infinite;
    animation-delay: var(--delay, 0s);
  }
  .lightning-bolt::before {
    content: '';
    position: absolute;
    inset: 0;
    clip-path: polygon(50% 0%, 30% 45%, 55% 45%, 35% 100%, 75% 38%, 50% 38%);
    background: linear-gradient(to bottom, #fff8e1, #ffd54f);
    filter: drop-shadow(0 0 8px rgba(255,213,79,0.8));
  }
  @keyframes lightning-flash {
    0%, 88%, 100% { opacity: 0; }
    90% { opacity: 1; }
    92% { opacity: 0.2; }
    94% { opacity: 0.9; }
    96% { opacity: 0; }
  }

  /* -- Background scene particles ------------------------------------------ */
  .scene-rain {
    position: absolute;
    width: 1px; height: 25px;
    background: linear-gradient(to bottom, transparent, rgba(120,180,255,0.3));
    animation: scene-rain-fall var(--dur) linear infinite;
    animation-delay: var(--delay);
    top: -30px;
    left: var(--x);
  }
  @keyframes scene-rain-fall {
    to { transform: translateY(110vh); }
  }

  .scene-snow {
    position: absolute;
    width: var(--size, 4px); height: var(--size, 4px);
    background: rgba(255,255,255,0.6);
    border-radius: 50%;
    animation: scene-snow-fall var(--dur) linear infinite;
    animation-delay: var(--delay);
    top: -10px;
    left: var(--x);
  }
  @keyframes scene-snow-fall {
    0% { transform: translateY(0) translateX(0); }
    25% { transform: translateY(27vh) translateX(20px); }
    50% { transform: translateY(55vh) translateX(-15px); }
    75% { transform: translateY(82vh) translateX(10px); }
    100% { transform: translateY(110vh) translateX(-5px); }
  }

  .scene-cloud {
    position: absolute;
    width: var(--w, 200px); height: var(--h, 60px);
    background: rgba(180,190,210,0.08);
    border-radius: 50%;
    filter: blur(10px);
    animation: scene-cloud-drift var(--dur) linear infinite;
    top: var(--y);
    left: -250px;
  }
  @keyframes scene-cloud-drift {
    to { left: calc(100vw + 250px); }
  }

  .scene-star {
    position: absolute;
    width: var(--s, 2px); height: var(--s, 2px);
    background: #fff;
    border-radius: 50%;
    animation: star-twinkle var(--dur) ease-in-out infinite;
    animation-delay: var(--delay);
    top: var(--y);
    left: var(--x);
  }

  /* -- Fade-in animation for data refresh --------------------------------- */
  .fade-in {
    animation: fadeIn 0.6s ease-out;
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(5px); }
    to { opacity: 1; transform: translateY(0); }
  }

  /* -- Location label ------------------------------------------------------- */
  .location {
    font-size: 2.5vh;
    font-weight: 400;
    color: var(--text-muted);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  /* -- Loading state -------------------------------------------------------- */
  .loading {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    font-size: 3vh;
    font-weight: 300;
    color: var(--text-dim);
  }
  .loading-spinner {
    width: 4vh; height: 4vh;
    border: 3px solid var(--glass-border);
    border-top-color: var(--text);
    border-radius: 50%;
    animation: spin 1s linear infinite;
    margin-right: 2vh;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* -- Update timestamp ----------------------------------------------------- */
  .update-time {
    position: fixed;
    bottom: 1.5vh;
    right: 2vw;
    font-size: 1.8vh;
    color: var(--text-muted);
    z-index: 10;
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
// == WMO codes → Finnish ==
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
const FI_DAYS_LONG = ['sunnuntai','maanantai','tiistai','keskiviikko','torstai','perjantai','lauantai'];

// == WMO → icon group ==
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

// == Moon HTML helper ==
function moonHTML(cls) {
  const stars = Array.from({length:5}, (_,i) => {
    const tx = [5,75,85,15,60][i], ty = [10,5,45,55,20][i];
    const s = 2 + Math.random()*2;
    return `<div class="star" style="--tx:${tx}%;--ty:${ty}%;--s:${s}px;--dur:${2+Math.random()*3}s;--delay:${Math.random()*2}s"></div>`;
  }).join('');
  return `<div class="${cls} wi-moon">
    <div class="crescent"></div><div class="crescent-shadow"></div>${stars}
  </div>`;
}

// == Build icon HTML ==
function iconHTML(code, sizeClass, night) {
  const g = wmoGroup(code);
  const cls = sizeClass || '';
  const isNight = night != null ? night : window._isNight || false;
  switch (g) {
    case 'clear':
      if (isNight) return moonHTML(cls);
      return `<div class="${cls} wi-sun">
        <div class="core"></div>
        ${Array.from({length:8}, (_,i) => `<div class="ray" style="--r:${i*45}deg"></div>`).join('')}
      </div>`;
    case 'partly-cloudy':
      if (isNight) return `<div class="${cls} wi-partly-cloudy-night">
        ${moonHTML('')}
        <div class="wi-cloud wi-cloud-drift">
          <div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div>
        </div>
      </div>`;
      return `<div class="${cls} wi-partly-cloudy">
        <div class="wi-sun"><div class="core"></div>
          ${Array.from({length:6}, (_,i) => `<div class="ray" style="--r:${i*60}deg"></div>`).join('')}
        </div>
        <div class="wi-cloud wi-cloud-drift">
          <div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div>
        </div>
      </div>`;
    case 'cloudy':
      return `<div class="${cls} wi-cloud wi-cloud-drift cloud-dark">
        <div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div>
      </div>`;
    case 'fog':
      return `<div class="${cls} wi-fog">
        ${Array.from({length:4}, (_,i) => `<div class="fog-bar" style="--dur:${3+i*0.7}s;--delay:${i*0.4}s;width:${85-i*12}%"></div>`).join('')}
      </div>`;
    case 'drizzle':
      return `<div class="${cls} wi-drizzle">
        <div class="wi-cloud"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div>
        ${Array.from({length:5}, (_,i) => `<div class="drizzle-drop" style="--x:${20+i*14}%;--dur:${1.8+Math.random()*0.6}s;--delay:${Math.random()*1.5}s"></div>`).join('')}
      </div>`;
    case 'rain':
      return `<div class="${cls} wi-rain">
        <div class="wi-cloud"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div>
        ${Array.from({length:8}, (_,i) => `<div class="raindrop" style="--x:${12+i*10}%;--dur:${0.7+Math.random()*0.5}s;--delay:${Math.random()*1}s"></div>`).join('')}
      </div>`;
    case 'snow':
      return `<div class="${cls} wi-snow">
        <div class="wi-cloud"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div>
        ${Array.from({length:7}, (_,i) => `<div class="snowflake" style="--x:${10+i*12}%;--dur:${2.5+Math.random()*1.5}s;--delay:${Math.random()*2}s"></div>`).join('')}
      </div>`;
    case 'thunder':
      return `<div class="${cls} wi-thunder">
        <div class="wi-cloud"><div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div></div>
        <div class="lightning-bolt" style="--delay:0s"></div>
        <div class="lightning-bolt" style="--delay:2.2s;left:55%;width:10%"></div>
        ${Array.from({length:6}, (_,i) => `<div class="raindrop" style="--x:${15+i*12}%;--dur:${0.8+Math.random()*0.4}s;--delay:${Math.random()*1}s"></div>`).join('')}
      </div>`;
    default:
      return `<div class="${cls} wi-cloud wi-cloud-drift">
        <div class="cloud-body c1"></div><div class="cloud-body c2"></div><div class="cloud-body c3"></div>
      </div>`;
  }
}

// == Background scene ==
function setScene(code) {
  const scene = document.getElementById('scene');
  scene.innerHTML = '';
  const g = wmoGroup(code);
  const body = document.body;

  // Determine if night time
  let isNight = false;
  if (window._sunrise && window._sunset) {
    const now = new Date();
    const sr = new Date(window._sunrise);
    const ss = new Date(window._sunset);
    isNight = (now < sr || now > ss);
  }
  window._isNight = isNight;

  // Set background gradient
  if (isNight) body.style.background = 'var(--bg-night)';
  else if (g === 'rain' || g === 'thunder') body.style.background = 'var(--bg-rain)';
  else if (g === 'snow') body.style.background = 'var(--bg-snow)';
  else if (g === 'cloudy' || g === 'fog' || g === 'drizzle') body.style.background = 'var(--bg-overcast)';
  else body.style.background = 'var(--bg-day)';

  // Add scene particles
  if (g === 'rain' || g === 'thunder') {
    for (let i = 0; i < 60; i++) {
      const el = document.createElement('div');
      el.className = 'scene-rain';
      el.style.cssText = `--x:${Math.random()*100}vw;--dur:${0.6+Math.random()*0.4}s;--delay:${Math.random()*2}s`;
      scene.appendChild(el);
    }
  } else if (g === 'snow') {
    for (let i = 0; i < 50; i++) {
      const el = document.createElement('div');
      el.className = 'scene-snow';
      el.style.cssText = `--x:${Math.random()*100}vw;--dur:${6+Math.random()*8}s;--delay:${Math.random()*10}s;--size:${2+Math.random()*5}px`;
      scene.appendChild(el);
    }
  } else if (g === 'drizzle') {
    for (let i = 0; i < 30; i++) {
      const el = document.createElement('div');
      el.className = 'scene-rain';
      el.style.cssText = `--x:${Math.random()*100}vw;--dur:${1.2+Math.random()*0.8}s;--delay:${Math.random()*3}s;opacity:0.3`;
      scene.appendChild(el);
    }
  }

  // Add twinkling stars at night
  if (isNight) {
    for (let i = 0; i < 40; i++) {
      const el = document.createElement('div');
      el.className = 'scene-star';
      el.style.cssText = `--x:${Math.random()*100}vw;--y:${Math.random()*60}vh;--s:${1+Math.random()*2.5}px;--dur:${2+Math.random()*4}s;--delay:${Math.random()*5}s`;
      scene.appendChild(el);
    }
  }

  // Always add a few drifting clouds for non-clear
  if (g !== 'clear') {
    for (let i = 0; i < 4; i++) {
      const el = document.createElement('div');
      el.className = 'scene-cloud';
      el.style.cssText = `--y:${5+i*20}%;--w:${150+Math.random()*200}px;--h:${40+Math.random()*40}px;--dur:${40+Math.random()*30}s;animation-delay:${-Math.random()*40}s`;
      scene.appendChild(el);
    }
  }
}

// == Wind direction → Finnish compass ==
function windDir(deg) {
  const dirs = ['P','PKO','KO','IKO','I','IKA','KA','LKA','L','LLU','LU','PLU'];
  return dirs[Math.round(deg / 30) % 12] || '';
}

// == Render weather data ==
function render(data) {
  const app = document.getElementById('app');
  const c = data.current;

  // Store sunrise/sunset for scene
  if (data.daily && data.daily.sunrise) window._sunrise = data.daily.sunrise[0];
  if (data.daily && data.daily.sunset) window._sunset = data.daily.sunset[0];

  // Scene background
  setScene(c.weather_code);

  // Current weather
  const tempStr = Math.round(c.temperature_2m) + '°';
  const feelsLike = Math.round(c.apparent_temperature);
  const desc = WMO_FI[c.weather_code] || 'Tuntematon';
  const humidity = Math.round(c.relative_humidity_2m);
  const wind = Math.round(c.wind_speed_10m);

  // Find today's high/low
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
    for (let i = startIdx; i < Math.min(startIdx + 6, times.length); i++) {
      const t = new Date(times[i]);
      const h = t.getHours().toString().padStart(2, '0') + ':00';
      const temp = Math.round(data.hourly.temperature_2m[i]);
      const code = data.hourly.weather_code[i];
      const precip = data.hourly.precipitation_probability ? data.hourly.precipitation_probability[i] : null;
      const hourNight = sr && ss ? (t < sr || t > ss) : false;
      hourlyHTML += `
        <div class="hourly-item">
          <span class="hourly-time">${h}</span>
          <div class="hourly-icon">${iconHTML(code, '', hourNight)}</div>
          <span class="hourly-temp">${temp}°</span>
          ${precip != null && precip > 0 ? `<span class="hourly-precip">${precip}%</span>` : ''}
        </div>`;
    }
  }

  // Daily: next 4 days (skip today)
  let dailyHTML = '';
  if (data.daily) {
    const dLen = Math.min(5, data.daily.time.length);
    for (let i = 1; i < dLen; i++) {
      const d = new Date(data.daily.time[i]);
      const dayName = FI_DAYS[d.getDay()];
      const code = data.daily.weather_code[i];
      const dhi = Math.round(data.daily.temperature_2m_max[i]);
      const dlo = Math.round(data.daily.temperature_2m_min[i]);
      const precip = data.daily.precipitation_probability_max ? data.daily.precipitation_probability_max[i] : null;
      dailyHTML += `
        <div class="daily-item">
          <span class="daily-day">${dayName}</span>
          <div class="daily-icon">${iconHTML(code)}</div>
          <span class="daily-temps">${dhi}° <span class="lo">/ ${dlo}°</span></span>
          ${precip != null && precip > 0 ? `<span class="daily-precip">${precip}%</span>` : ''}
        </div>`;
    }
  }

  app.className = 'container fade-in';
  app.innerHTML = `
    <div class="current">
      <span class="location">Tampere</span>
      <div class="current-icon">${iconHTML(c.weather_code)}</div>
      <div class="current-temp">${tempStr}</div>
      <div class="current-desc">${desc}</div>
      <div class="current-details">
        <span><span class="detail-label">Tuntuu</span>${feelsLike}°</span>
        <span><span class="detail-label">↑</span>${hi}° <span class="detail-label">↓</span>${lo}°</span>
      </div>
      <div class="current-details">
        <span><span class="detail-label">💧</span>${humidity}%</span>
        <span><span class="detail-label">💨</span>${wind} m/s</span>
      </div>
    </div>
    <div class="forecast">
      <div class="section">
        <div class="section-title">Seuraavat tunnit</div>
        <div class="hourly-row">${hourlyHTML}</div>
      </div>
      <div class="section">
        <div class="section-title">Ennuste</div>
        <div class="daily-row">${dailyHTML}</div>
      </div>
    </div>
  `;

  // Update timestamp
  const ts = document.getElementById('update-time');
  const now = new Date();
  ts.textContent = 'Päivitetty ' + now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
}

// == Fetch and render ==
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
        Route("/api/weather", api_weather),
        Route("/health", health),
    ],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
