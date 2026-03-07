const WEEKDAYS = ['sunnuntai','maanantai','tiistai','keskiviikko','torstai','perjantai','lauantai'];
const GRID_START = 6;  // 06:00
const GRID_END = 23;   // 23:00
const GRID_HOURS = GRID_END - GRID_START;

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

function isGarbage(ev) {
  return ev.type === 'garbage';
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

function renderDayColumn(dateStr, events, labelText, labelCls) {
  const d = new Date(dateStr + 'T00:00:00');
  const weekday = WEEKDAYS[d.getDay()];

  const allDay = events.filter(e => e.allDay);
  const timed = events.filter(e => !e.allDay);
  const timedWithOverlap = detectOverlaps(timed);

  let html = '<div class="day-col">';

  // Header
  html += '<div class="day-col-header">';
  html += '<span class="day-col-label ' + labelCls + '">' + labelText + '</span>';
  html += '<span class="day-col-weekday">' + weekday.charAt(0).toUpperCase() + weekday.slice(1) + '</span>';
  html += '<span class="day-col-date">' + formatDate(dateStr) + '</span>';
  html += '</div>';

  // All-day pills
  html += '<div class="allday-zone">';
  for (const ev of allDay) {
    const gc = isGarbage(ev) ? ' garbage' : '';
    html += '<span class="allday-pill' + gc + '">' + ev.summary + '</span>';
  }
  html += '</div>';

  // Time grid
  html += '<div class="time-grid" data-date="' + dateStr + '">';

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

    const gc = isGarbage(ev) ? ' garbage' : '';
    const overlap = ev.overlapIndex > 0 ? ' overlap-' + ev.overlapIndex : (timedWithOverlap.some(o => o !== ev && o.overlapIndex > 0 && o.start < ev.end && o.end > ev.start) ? ' overlap-0' : '');

    html += '<div class="grid-event' + gc + overlap + '" style="top:' + top + '%;height:' + height + '%">';
    html += '<div class="grid-event-summary">' + ev.summary + '</div>';
    html += '<div class="grid-event-time">' + formatTime(ev.start);
    if (ev.end) html += '–' + formatTime(ev.end);
    html += '</div>';
    html += '</div>';
  }

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
  let html = '<div class="agenda-section">';
  html += '<div class="agenda-section-label">Tulossa</div>';
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
    html += '<span class="day-date">' + formatDate(dateStr) + '</span>';
    html += '</div>';

    html += '<div class="events-list">';
    groups[dateStr].forEach((ev, ei) => {
      const gc = isGarbage(ev) ? ' garbage' : '';
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
  return html;
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

  const today = todayStr();
  const tomorrow = tomorrowStr();

  // Split events into day-view (today + tomorrow) and agenda (rest)
  const todayEvents = events.filter(e => e.date === today);
  const tomorrowEvents = events.filter(e => e.date === tomorrow);
  const restEvents = events.filter(e => e.date > tomorrow);

  let html = '<div class="header">' +
    '<span class="header-icon">&#128197;</span>' +
    '<span class="header-label">Perheen kalenteri</span>' +
    '</div>';

  // Day columns
  html += '<div class="day-columns">';
  html += renderDayColumn(today, todayEvents, 'TÄNÄÄN', 'today');
  html += renderDayColumn(tomorrow, tomorrowEvents, 'HUOMENNA', 'tomorrow');
  html += '</div>';

  // Agenda for remaining days
  html += renderAgenda(restEvents);

  app.className = 'container';
  app.innerHTML = html;

  // Update timestamp
  const ts = document.getElementById('update-time');
  const now = new Date();
  ts.textContent = 'Päivitetty ' + now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');

  // Start now-line updates
  updateNowLine();
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
    render(data);
  } catch (e) {
    console.error('Calendar fetch error:', e);
    if (lastData) render(lastData);
  }
}

refresh();
setInterval(refresh, 5 * 60 * 1000);
setInterval(updateNowLine, 60 * 1000);
