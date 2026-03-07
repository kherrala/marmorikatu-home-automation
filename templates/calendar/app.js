// == Sunrise/sunset theme switcher (Tampere 61.50N, 23.76E) ==================
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
  function updateTheme() {
    var now = new Date();
    var start = new Date(now.getFullYear(), 0, 1);
    var doy = Math.floor((now - start) / 86400000) + 1;
    var sunrise = getSunHour(doy, true);
    var sunset = getSunHour(doy, false);
    var h = now.getHours() + now.getMinutes() / 60;
    document.documentElement.setAttribute('data-theme', (h < sunrise || h >= sunset) ? 'dark' : 'light');
  }
  updateTheme();
  setInterval(updateTheme, 60000);
})();

const WEEKDAYS = ['sunnuntai','maanantai','tiistai','keskiviikko','torstai','perjantai','lauantai'];
const GRID_START = 6;  // 06:00
const GRID_END = 23;   // 23:00
const GRID_HOURS = GRID_END - GRID_START;
const DEFAULT_VIEW_START = 9;
const DEFAULT_VIEW_END = 15;

let dayOffset = 0;  // 0 = today+tomorrow, 1 = tomorrow+day-after, etc.

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

function todayStr() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}

function tomorrowStr() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}

function dateOffsetStr(offset) {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}

function isGarbage(ev) {
  return ev.type === 'garbage';
}

function isSchool(ev) {
  return ev.type === 'school';
}

function eventTypeClass(ev) {
  if (isGarbage(ev)) return ' garbage';
  if (isSchool(ev)) return ' school';
  return '';
}

function computeViewWindow(events1, events2) {
  var earliest = DEFAULT_VIEW_START;
  var latest = DEFAULT_VIEW_END;
  var allTimed = events1.concat(events2).filter(function(e) { return !e.allDay; });
  for (var i = 0; i < allTimed.length; i++) {
    var s = new Date(allTimed[i].start);
    var sh = s.getHours();
    if (sh < earliest) earliest = sh;
    if (allTimed[i].end) {
      var e = new Date(allTimed[i].end);
      var eh = e.getHours() + (e.getMinutes() > 0 ? 1 : 0);
      if (eh > latest) latest = eh;
    }
  }
  earliest = Math.max(GRID_START, earliest);
  latest = Math.min(GRID_END, latest);
  if (latest <= earliest) latest = earliest + 1;
  return { start: earliest, end: latest, hours: latest - earliest };
}

function timeToGridPercent(isoStr) {
  const d = new Date(isoStr);
  const hours = d.getHours() + d.getMinutes() / 60;
  const clamped = Math.max(GRID_START, Math.min(GRID_END, hours));
  return ((clamped - GRID_START) / GRID_HOURS) * 100;
}

function detectOverlaps(events) {
  // Sort by start time
  const sorted = events.slice().sort((a, b) => a.start.localeCompare(b.start));
  const result = sorted.map(ev => ({ ...ev, overlapIndex: 0 }));

  for (let i = 0; i < result.length; i++) {
    for (let j = 0; j < i; j++) {
      // Check if event j overlaps with event i
      if (result[j].end > result[i].start) {
        result[i].overlapIndex = 1;
        if (result[j].overlapIndex === 0) result[j].overlapIndex = 0;
        break;
      }
    }
  }
  return result;
}

function renderDayColumn(dateStr, events, labelText, labelCls, viewWindow) {
  const d = new Date(dateStr + 'T00:00:00');
  const weekday = WEEKDAYS[d.getDay()];

  const allDay = events.filter(e => e.allDay);
  const timed = events.filter(e => !e.allDay);
  const timedWithOverlap = detectOverlaps(timed);

  let html = '<div class="day-col" role="region" aria-label="' + labelText + ' ' + weekday + ' ' + formatDate(dateStr) + '">';

  // Header
  html += '<div class="day-col-header">';
  html += '<span class="day-col-label ' + labelCls + '">' + labelText + '</span>';
  html += '<span class="day-col-weekday">' + weekday.charAt(0).toUpperCase() + weekday.slice(1) + '</span>';
  html += '<span class="day-col-date">' + formatDate(dateStr) + '</span>';
  html += '</div>';

  // All-day pills
  html += '<div class="allday-zone">';
  for (const ev of allDay) {
    const gc = eventTypeClass(ev);
    html += '<span class="allday-pill' + gc + '">' + ev.summary + '</span>';
  }
  html += '</div>';

  // Time grid (scrollable container → inner tall div)
  var vw = viewWindow || { start: DEFAULT_VIEW_START, end: DEFAULT_VIEW_END, hours: DEFAULT_VIEW_END - DEFAULT_VIEW_START };
  var innerHeightPct = (GRID_HOURS / vw.hours) * 100;
  html += '<div class="time-grid" data-date="' + dateStr + '" data-view-start="' + vw.start + '">';
  html += '<div class="time-grid-inner" style="height:' + innerHeightPct + '%">';

  // Hour lines
  for (let h = GRID_START; h <= GRID_END; h++) {
    const pct = ((h - GRID_START) / GRID_HOURS) * 100;
    html += '<div class="hour-row" style="top:' + pct + '%">';
    html += '<span class="hour-label">' + String(h).padStart(2, '0') + '</span>';
    html += '</div>';
  }

  // Timed events
  for (const ev of timedWithOverlap) {
    const top = timeToGridPercent(ev.start);
    const endPct = ev.end ? timeToGridPercent(ev.end) : top + (100 / GRID_HOURS); // default 1h
    let height = endPct - top;
    const minH = 1.5 / 68 * 100; // ~1.5vh relative to grid
    if (height < minH) height = minH;

    const gc = eventTypeClass(ev);
    const overlap = ev.overlapIndex > 0 ? ' overlap-' + ev.overlapIndex : (timedWithOverlap.some(o => o !== ev && o.overlapIndex > 0 && o.start < ev.end && o.end > ev.start) ? ' overlap-0' : '');

    html += '<div class="grid-event' + gc + overlap + '" style="top:' + top + '%;height:' + height + '%">';
    html += '<div class="grid-event-summary">' + ev.summary + '</div>';
    html += '<div class="grid-event-time">' + formatTime(ev.start);
    if (ev.end) html += '–' + formatTime(ev.end);
    html += '</div>';
    html += '</div>';
  }

  html += '</div>'; // time-grid-inner
  html += '</div>'; // time-grid
  html += '</div>'; // day-col

  return html;
}

function renderAgenda(events) {
  if (events.length === 0) return '';

  // Group by date
  const groups = {};
  for (const ev of events) {
    if (!groups[ev.date]) groups[ev.date] = [];
    groups[ev.date].push(ev);
  }

  const dates = Object.keys(groups).sort();
  let html = '<div class="day-col" role="region" aria-label="Tulevat tapahtumat">';
  html += '<div class="day-col-header">';
  html += '<span class="day-col-label other">TULOSSA</span>';
  html += '</div>';
  html += '<div class="agenda-section">';
  html += '<div class="agenda">';

  dates.forEach((dateStr, gi) => {
    const d = new Date(dateStr + 'T00:00:00');
    const weekday = WEEKDAYS[d.getDay()];
    const label = dayLabel(dateStr);

    html += '<div class="day-group" style="animation-delay:' + (gi * 0.08) + 's">';
    html += '<div class="day-header">';
    if (label.text) {
      html += '<span class="day-label ' + label.cls + '">' + label.text + '</span>';
    }
    html += '<span class="day-name">' + weekday.charAt(0).toUpperCase() + weekday.slice(1) + '</span>';
    html += '<time class="day-date" datetime="' + dateStr + '">' + formatDate(dateStr) + '</time>';
    html += '</div>';

    html += '<div class="events-list">';
    groups[dateStr].forEach((ev, ei) => {
      const gc = eventTypeClass(ev);
      const isAllDay = ev.allDay;
      html += '<div class="event-card' + (isAllDay ? ' event-allday-card' : '') + gc + '" style="animation-delay:' + (gi * 0.08 + ei * 0.04) + 's">';

      html += '<div class="event-time">';
      if (isAllDay) {
        html += '<div class="event-allday">Koko päivä</div>';
      } else {
        html += '<div class="event-time-text">' + formatTime(ev.start) + '</div>';
        if (ev.end) {
          html += '<div class="event-time-end">' + formatTime(ev.end) + '</div>';
        }
      }
      html += '</div>';

      html += '<div class="event-divider"></div>';

      html += '<div class="event-details">';
      html += '<div class="event-summary">' + ev.summary + '</div>';
      if (ev.location) {
        html += '<div class="event-location">&#128205; ' + ev.location + '</div>';
      }
      html += '</div>';

      html += '</div>';
    });
    html += '</div>';
    html += '</div>';
  });

  html += '</div>'; // agenda
  html += '</div>'; // agenda-section
  html += '</div>'; // day-col
  return html;
}

function dayColumnLabel(dateStr) {
  const today = todayStr();
  const tomorrow = tomorrowStr();
  if (dateStr === today) return { text: 'TÄNÄÄN', cls: 'today' };
  if (dateStr === tomorrow) return { text: 'HUOMENNA', cls: 'tomorrow' };
  return { text: '', cls: 'other' };
}

function render(data) {
  if (!data || !data.events) return;
  const app = document.getElementById('app');
  const events = data.events;

  if (events.length === 0) {
    app.className = 'container';
    app.innerHTML = '<div class="header">' +
      '<span class="header-icon">&#128197;</span>' +
      '<span class="header-label">Perheen kalenteri</span>' +
      '</div>' +
      '<div class="empty-state">' +
      '<div class="empty-icon">&#128198;</div>' +
      '<div class="empty-text">Ei tulevia tapahtumia</div>' +
      '</div>';
    return;
  }

  const col1Date = dateOffsetStr(dayOffset);
  const col2Date = dateOffsetStr(dayOffset + 1);
  const col1Label = dayColumnLabel(col1Date);
  const col2Label = dayColumnLabel(col2Date);
  const col1Events = events.filter(e => e.date === col1Date);
  const col2Events = events.filter(e => e.date === col2Date);
  const restEvents = events.filter(e => e.date > col2Date);

  // Max offset: don't go beyond available events
  const lastDate = events.length > 0 ? events[events.length - 1].date : col2Date;
  const maxOffset = Math.max(0, Math.floor((new Date(lastDate + 'T00:00:00') - new Date(todayStr() + 'T00:00:00')) / 86400000) - 1);

  let html = '<div class="header">' +
    '<span class="header-icon">&#128197;</span>' +
    '<span class="header-label">Perheen kalenteri</span>' +
    '<span class="day-nav">' +
    '<button class="nav-btn nav-prev' + (dayOffset === 0 ? ' disabled' : '') + '" onclick="navigateDays(-1)" aria-label="Edelliset päivät">&#9664;</button>' +
    '<button class="nav-btn nav-next' + (dayOffset >= maxOffset ? ' disabled' : '') + '" onclick="navigateDays(1)" aria-label="Seuraavat päivät">&#9654;</button>' +
    '</span>' +
    '</div>';

  // Compute shared view window from both columns' events
  const viewWindow = computeViewWindow(col1Events, col2Events);

  // Day columns
  html += '<div class="day-columns">';
  html += renderDayColumn(col1Date, col1Events, col1Label.text, col1Label.cls, viewWindow);
  html += renderDayColumn(col2Date, col2Events, col2Label.text, col2Label.cls, viewWindow);
  html += renderAgenda(restEvents);
  html += '</div>';

  app.className = 'container';
  app.innerHTML = html;

  // Update timestamp
  const ts = document.getElementById('update-time');
  const now = new Date();
  ts.textContent = 'Päivitetty ' + now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');

  // Scroll time grids to VIEW_START
  scrollGridsToView();

  // Start now-line updates
  updateNowLine();
}

function scrollGridsToView() {
  document.querySelectorAll('.time-grid').forEach(function(grid) {
    var inner = grid.querySelector('.time-grid-inner');
    if (!inner) return;
    var viewStart = parseInt(grid.getAttribute('data-view-start') || DEFAULT_VIEW_START);
    var scrollPct = (viewStart - GRID_START) / GRID_HOURS;
    grid.scrollTop = inner.offsetHeight * scrollPct;
  });
}

function navigateDays(delta) {
  dayOffset = Math.max(0, dayOffset + delta);
  if (lastData) render(lastData);
}

function updateNowLine() {
  // Remove existing now-lines
  document.querySelectorAll('.now-line').forEach(el => el.remove());

  const today = todayStr();
  const grid = document.querySelector('.time-grid[data-date="' + today + '"]');
  if (!grid) return;

  const now = new Date();
  const hours = now.getHours() + now.getMinutes() / 60;
  if (hours < GRID_START || hours > GRID_END) return;

  const pct = ((hours - GRID_START) / GRID_HOURS) * 100;
  const line = document.createElement('div');
  line.className = 'now-line';
  line.style.top = pct + '%';
  grid.appendChild(line);
}

let lastData = null;

async function refresh() {
  try {
    const resp = await fetch('api/calendar?days=30');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    lastData = data;
    dayOffset = 0;
    render(data);
  } catch (e) {
    console.error('Calendar fetch error:', e);
    if (lastData) render(lastData);
  }
}

refresh();
setInterval(refresh, 5 * 60 * 1000);
setInterval(updateNowLine, 60 * 1000);
