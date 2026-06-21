#!/usr/bin/env python3
"""
Spot-price tier classifier (analytics-only).

Classifies upcoming 15-minute electricity price slots into
CHEAP / NORMAL / EXPENSIVE / PRE_HEAT using historical seasonal
percentiles + a relative-spread fallback + a pre-heat lookahead, then
writes the classification to InfluxDB measurement `heating_optimizer`
for dashboards and the MCP server.

This service used to also command the Thermia heat pump (setpoint d50,
EVU, reduction_t d59, elect_boiler_steps_max d81 via MQTT register
writes). Those writes are retired:

  - The persistent registers (d50/d59/d81) wear the unit's flash on
    every cycle. Frequent writes degrade the controller hardware.
  - EVU only triggers a reduction_t-based target shift on this unit;
    it does not block the compressor.
  - The publisher's INDR_T bias (`indoor_temp_publisher.py`) drives
    price-aware heat suppression as a runtime sensor input — no flash
    write, smoother (continuous interpolation across season-aware
    cheap/expensive percentiles), and demand-aware (per-room PID
    counter-bias).

This service is now read-only. It produces:

Inputs (read from InfluxDB):
  - electricity.price_with_tax  — 15-min spot price forecast + history
  - ruuvi outdoor temperature   — for cold-weather pre-heat clamps

Outputs (written to InfluxDB measurement `heating_optimizer`):
  - tier         — current slot classification (CHEAP/NORMAL/
                   EXPENSIVE/PRE_HEAT)
  - price        — current slot's spot price (c/kWh, with tax)
  - outdoor_temp — outdoor temperature at classification time
"""

import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from health import touch_health

# ── Configuration ─────────────────────────────────────────────────────────────
INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "wago-secret-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")

# Liveness: exit non-zero after this many consecutive failed classifications so
# the container crash-loops visibly instead of looping forever. See health.py.
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("MAX_CONSECUTIVE_FAILURES", "5"))

PRICE_PERCENTILE_CHEAP = float(os.environ.get("PRICE_PERCENTILE_CHEAP", "25"))
PRICE_PERCENTILE_EXPENSIVE = float(os.environ.get("PRICE_PERCENTILE_EXPENSIVE", "75"))

# Seasonal price comparison: thresholds are computed from the same season's
# historical data. Heating season has higher prices, non-heating lower.
# Transition around mid-March and mid-October.
HEATING_SEASON_START_MONTH = 10   # October
HEATING_SEASON_START_DAY = 15
HEATING_SEASON_END_MONTH = 3      # mid-March
HEATING_SEASON_END_DAY = 15

# Safety clamps for computed percentile thresholds (c/kWh).
# Prevents nonsensical classification when history is skewed.
MIN_CHEAP_THRESHOLD = float(os.environ.get("MIN_CHEAP_THRESHOLD", "3.0"))
MAX_CHEAP_THRESHOLD = float(os.environ.get("MAX_CHEAP_THRESHOLD", "6.0"))
MIN_EXPENSIVE_THRESHOLD = float(os.environ.get("MIN_EXPENSIVE_THRESHOLD", "5.0"))
MAX_EXPENSIVE_THRESHOLD = float(os.environ.get("MAX_EXPENSIVE_THRESHOLD", "15.0"))

PRE_HEAT_HOURS = int(os.environ.get("PRE_HEAT_HOURS", "2"))
PRE_HEAT_SLOTS = PRE_HEAT_HOURS * 4  # quarter-hour slots

# Maximum consecutive EXPENSIVE slots — longer runs reclassify back to
# NORMAL so we don't flag the entire afternoon as "do nothing", which
# would be unhelpful when the price profile is uniformly elevated.
# (Historically named MAX_EVU_HOURS because this cap controlled the EVU
# duty cycle. EVU is no longer commanded; the cap still shapes the tier
# string written for dashboards/MCP.)
MAX_EXPENSIVE_HOURS = int(os.environ.get(
    "MAX_EXPENSIVE_HOURS", os.environ.get("MAX_EVU_HOURS", "3")))
MAX_EXPENSIVE_SLOTS = MAX_EXPENSIVE_HOURS * 4

# Minimum price spread (max - min in forecast window) required to activate
# the relative pre-heat fallback when no historically-expensive slots exist.
MIN_RELATIVE_SPREAD = float(os.environ.get("MIN_RELATIVE_SPREAD", "2.0"))

# Flicker reduction: minimum contiguous EXPENSIVE slots to keep (shorter →
# NORMAL). Avoids reclassifying tier on isolated 15-min price spikes that
# wouldn't justify a behavioural change anyway.
MIN_EXPENSIVE_SLOTS = int(os.environ.get("MIN_EXPENSIVE_SLOTS", "2"))

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
running = True


def signal_handler(sig, frame):
    global running
    log.info("Shutdown requested")
    running = False


# ── InfluxDB queries ─────────────────────────────────────────────────────────

def fetch_price_forecast(query_api):
    """Fetch electricity prices from now to +36h.
    Returns list of (datetime_utc, price_c_per_kwh) sorted by time."""
    flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h, stop: 36h)
  |> filter(fn: (r) => r._measurement == "electricity" and r._field == "price_with_tax")
  |> group()
  |> sort(columns: ["_time"])
"""
    try:
        tables = query_api.query(flux, org=INFLUXDB_ORG)
        prices = []
        for table in tables:
            for record in table.records:
                prices.append((record.get_time(), record.get_value()))
        return prices
    except Exception as e:
        log.error(f"Failed to fetch prices: {e}")
        return []


def is_heating_season(dt=None):
    """Check if a date falls in the heating season (Oct 15 – Mar 15)."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    m, d = dt.month, dt.day
    if m >= HEATING_SEASON_START_MONTH:
        return m > HEATING_SEASON_START_MONTH or d >= HEATING_SEASON_START_DAY
    if m <= HEATING_SEASON_END_MONTH:
        return m < HEATING_SEASON_END_MONTH or d <= HEATING_SEASON_END_DAY
    return False


def fetch_historical_prices(query_api):
    """Fetch electricity prices from the same season across available history.
    Compares winter prices to winter, summer to summer, so thresholds reflect
    seasonal norms rather than annual averages.
    Returns sorted list of price values (c/kWh) for percentile calculation."""
    heating = is_heating_season()
    season_name = "heating" if heating else "non-heating"

    if heating:
        months = [10, 11, 12, 1, 2, 3]
    else:
        months = [3, 4, 5, 6, 7, 8, 9, 10]

    month_filter = " or ".join(f'm == {m}' for m in months)

    flux = f"""
import "date"
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -730d)
  |> filter(fn: (r) => r._measurement == "electricity" and r._field == "price_with_tax")
  |> filter(fn: (r) => {{
       m = date.month(t: r._time)
       return {month_filter}
     }})
  |> group()
"""
    try:
        tables = query_api.query(flux, org=INFLUXDB_ORG)
        values = []
        for table in tables:
            for record in table.records:
                values.append(record.get_value())
        values.sort()
        log.info(f"Season: {season_name} (months {months}), {len(values)} historical price points")
        return values
    except Exception as e:
        log.error(f"Failed to fetch historical prices: {e}")
        return []


def fetch_outdoor_temperature(query_api):
    """Fetch latest outdoor temperature from Ruuvi sensor."""
    flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "ruuvi" and r._field == "temperature")
  |> filter(fn: (r) => r.sensor_name == "Ulkolämpötila" or r.sensor_name == "Ulkolampotila")
  |> last()
"""
    try:
        tables = query_api.query(flux, org=INFLUXDB_ORG)
        for table in tables:
            for record in table.records:
                return record.get_value()
    except Exception as e:
        log.error(f"Failed to fetch outdoor temp: {e}")
    return None


# ── Price classification ─────────────────────────────────────────────────────

CHEAP = "CHEAP"
NORMAL = "NORMAL"
EXPENSIVE = "EXPENSIVE"
PRE_HEAT = "PRE_HEAT"


def percentile(values, pct):
    """Linear-interpolation percentile of a pre-sorted list."""
    if not values:
        return 0
    k = (len(values) - 1) * pct / 100.0
    f = int(k)
    c = f + 1
    if c >= len(values):
        return values[f]
    return values[f] + (k - f) * (values[c] - values[f])


def classify_prices(prices, p_cheap, p_expensive):
    """Classify price entries into quarter-hour slots using the given thresholds.
    Returns list of (slot_start_utc, tier, price) tuples."""
    if not prices:
        return []

    classified = []
    for ts, price in prices:
        if price <= p_cheap:
            tier = CHEAP
        elif price >= p_expensive:
            tier = EXPENSIVE
        else:
            tier = NORMAL
        classified.append((ts, tier, price))

    return classified


def apply_relative_fallback(classified, p_cheap):
    """When the forecast has no historically-expensive slots but prices
    still vary meaningfully, mark the relatively most expensive slots
    (within-window P75) as EXPENSIVE so the publisher's price-bias still
    sees a meaningful gradient. Pre-heat lookahead then applies before
    those relatively-expensive blocks.

    Only activates when:
      - No EXPENSIVE slots in the forecast (absolute approach is dormant)
      - Max-min price spread >= MIN_RELATIVE_SPREAD c/kWh
      - Within-window P75 is above the absolute CHEAP threshold (no point
        load-shifting when all prices are objectively cheap)"""
    if any(tier == EXPENSIVE for _, tier, _ in classified):
        return classified

    prices_only = sorted(price for _, _, price in classified)
    spread = prices_only[-1] - prices_only[0]

    if spread < MIN_RELATIVE_SPREAD:
        log.info(f"Relative fallback: price spread {spread:.2f} c/kWh < {MIN_RELATIVE_SPREAD} c/kWh — no action")
        return classified

    p75 = percentile(prices_only, 75)

    if p75 <= p_cheap:
        log.info(f"Relative fallback: within-window P75 {p75:.2f} c/kWh ≤ absolute cheap threshold "
                 f"{p_cheap:.2f} c/kWh — all prices objectively cheap, no action")
        return classified

    log.info(f"Relative fallback active: spread {spread:.2f} c/kWh, "
             f"within-window P75 = {p75:.2f} c/kWh → expensive above this")

    result = []
    for ts, tier, price in classified:
        if price >= p75:
            result.append((ts, EXPENSIVE, price))
        else:
            result.append((ts, tier, price))
    return result


def filter_short_expensive_blocks(classified):
    """Downgrade EXPENSIVE blocks shorter than MIN_EXPENSIVE_SLOTS to NORMAL."""
    if MIN_EXPENSIVE_SLOTS <= 1:
        return classified

    result = list(classified)
    i = 0
    while i < len(result):
        if result[i][1] != EXPENSIVE:
            i += 1
            continue
        block_start = i
        while i < len(result) and result[i][1] == EXPENSIVE:
            i += 1
        block_len = i - block_start
        if block_len < MIN_EXPENSIVE_SLOTS:
            for j in range(block_start, i):
                ts, _, price = result[j]
                result[j] = (ts, NORMAL, price)
            log.info(f"Filtered short EXPENSIVE block: {block_len} slot(s) at "
                     f"{result[block_start][0].strftime('%H:%M')} → NORMAL")

    return result


def apply_pre_heat_and_long_block_cap(classified):
    """Apply two schedule adjustments:
      1. Long-block cap: if a contiguous EXPENSIVE run exceeds
         MAX_EXPENSIVE_SLOTS, the tail is reclassified to NORMAL — long
         expensive runs reflect a uniformly-elevated price floor where
         tier-driven suppression is no longer meaningful.
      2. Pre-heat lookahead: the PRE_HEAT_SLOTS leading up to each
         EXPENSIVE block are promoted from CHEAP/NORMAL to PRE_HEAT, so
         the publisher (or anyone reading the tier) can boost heat
         banking before the run begins."""
    result = list(classified)

    if MAX_EXPENSIVE_SLOTS > 0:
        consecutive = 0
        for i, (ts, tier, price) in enumerate(result):
            if tier == EXPENSIVE:
                consecutive += 1
                if consecutive > MAX_EXPENSIVE_SLOTS:
                    result[i] = (ts, NORMAL, price)
            else:
                consecutive = 0

    for i, (ts, tier, price) in enumerate(result):
        if tier == EXPENSIVE:
            for j in range(max(0, i - PRE_HEAT_SLOTS), i):
                if result[j][1] in (CHEAP, NORMAL):
                    result[j] = (result[j][0], PRE_HEAT, result[j][2])

    return result


def current_slot(schedule):
    """Return (tier, price) for the slot containing now, or
    (NORMAL, None) if the schedule is empty."""
    now_utc = datetime.now(timezone.utc)
    tier = NORMAL
    price = None
    for ts, t, p in schedule:
        slot_time = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        if slot_time <= now_utc:
            tier = t
            price = p
        else:
            break
    return tier, price


# ── Decision logging ─────────────────────────────────────────────────────────

def log_decision(write_api, tier, price, outdoor_temp):
    """Write classification result to InfluxDB."""
    point = Point("heating_optimizer").field("tier", tier)
    if price is not None:
        point = point.field("price", price)
    if outdoor_temp is not None:
        point = point.field("outdoor_temp", outdoor_temp)
    point = point.time(datetime.now(timezone.utc), WritePrecision.S)
    try:
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)
    except Exception as e:
        log.error(f"Failed to log decision: {e}")


# ── Control loop ─────────────────────────────────────────────────────────────

def check_and_classify(query_api, write_api):
    """Fetch prices, classify, write current tier."""
    prices = fetch_price_forecast(query_api)
    if len(prices) < 12:
        log.warning(f"Insufficient price data ({len(prices)} points, need ≥12) — assuming NORMAL")
        return

    log.info(f"Fetched {len(prices)} forecast price points")

    historical = fetch_historical_prices(query_api)
    if len(historical) < 96:
        log.warning(f"Insufficient historical data ({len(historical)} points) — using forecast percentiles as fallback")
        historical = sorted(p for _, p in prices)

    p_cheap_raw = percentile(historical, PRICE_PERCENTILE_CHEAP)
    p_expensive_raw = percentile(historical, PRICE_PERCENTILE_EXPENSIVE)
    p_cheap = max(MIN_CHEAP_THRESHOLD, min(MAX_CHEAP_THRESHOLD, p_cheap_raw))
    p_expensive = max(MIN_EXPENSIVE_THRESHOLD, min(MAX_EXPENSIVE_THRESHOLD, p_expensive_raw))
    if p_cheap >= p_expensive:
        p_cheap = p_expensive * 0.6
    log.info(f"Price thresholds (from {len(historical)} historical points): "
             f"cheap ≤ {p_cheap:.2f} (raw P{PRICE_PERCENTILE_CHEAP:.0f}={p_cheap_raw:.2f}), "
             f"expensive ≥ {p_expensive:.2f} (raw P{PRICE_PERCENTILE_EXPENSIVE:.0f}={p_expensive_raw:.2f}) c/kWh")

    outdoor_temp = fetch_outdoor_temperature(query_api)
    if outdoor_temp is not None:
        log.info(f"Outdoor temperature: {outdoor_temp:.1f}°C")
    else:
        log.warning("Outdoor temperature unavailable")

    classified = classify_prices(prices, p_cheap, p_expensive)
    schedule = apply_relative_fallback(classified, p_cheap)
    schedule = filter_short_expensive_blocks(schedule)
    schedule = apply_pre_heat_and_long_block_cap(schedule)

    tier_counts = {CHEAP: 0, NORMAL: 0, EXPENSIVE: 0, PRE_HEAT: 0}
    for _, tier, _ in schedule:
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    log.info(f"Schedule: {tier_counts[CHEAP]} cheap, {tier_counts[NORMAL]} normal, "
             f"{tier_counts[EXPENSIVE]} expensive, {tier_counts[PRE_HEAT]} pre-heat slots")

    tier, price = current_slot(schedule)
    if price is not None:
        log.info(f"Current slot: tier={tier}, price={price:.2f} c/kWh")
    else:
        log.info(f"Current slot: tier={tier}")

    log_decision(write_api, tier, price, outdoor_temp)


# ── Main ─────────────────────────────────────────────────────────────────────

def sleep_interruptible(seconds):
    end = time.monotonic() + seconds
    while running and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


def seconds_until_next_price_boundary(buffer_secs=5):
    """Seconds until the next 15-minute price slot boundary plus a small buffer."""
    now = datetime.now(timezone.utc)
    slot_start = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    next_slot = slot_start + timedelta(minutes=15)
    return (next_slot - now).total_seconds() + buffer_secs


def main():
    log.info("=" * 60)
    log.info("Spot-price tier classifier (analytics-only)")
    log.info("=" * 60)
    log.info("Mode:        read-only — writes only `heating_optimizer.tier|price|outdoor_temp`")
    log.info("             Heat-pump control is via INDR_T bias in indoor_temp_publisher.")
    season = "heating" if is_heating_season() else "non-heating"
    log.info(f"Percentiles: cheap ≤ P{PRICE_PERCENTILE_CHEAP:.0f}, expensive ≥ P{PRICE_PERCENTILE_EXPENSIVE:.0f} (seasonal, currently {season})")
    log.info(f"Seasons:     heating Oct {HEATING_SEASON_START_DAY}–Mar {HEATING_SEASON_END_DAY}, non-heating Mar {HEATING_SEASON_END_DAY}–Oct {HEATING_SEASON_START_DAY}")
    log.info(f"Clamps:      cheap [{MIN_CHEAP_THRESHOLD}–{MAX_CHEAP_THRESHOLD}], expensive [{MIN_EXPENSIVE_THRESHOLD}–{MAX_EXPENSIVE_THRESHOLD}] c/kWh")
    log.info(f"Pre-heat:    {PRE_HEAT_HOURS}h ({PRE_HEAT_SLOTS} slots) before EXPENSIVE")
    log.info(f"Long block:  cap at {MAX_EXPENSIVE_HOURS}h ({MAX_EXPENSIVE_SLOTS} slots) — tail reclassified to NORMAL")
    log.info(f"Min block:   {MIN_EXPENSIVE_SLOTS} slots ({MIN_EXPENSIVE_SLOTS * 15}min) — shorter EXPENSIVE blocks downgraded")
    log.info(f"Rel spread:  {MIN_RELATIVE_SPREAD} c/kWh min for relative fallback")
    log.info(f"Interval:    aligned to 15-min price slot boundaries (+5s buffer)")
    log.info("-" * 60)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    influx_client = InfluxDBClient(
        url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG
    )
    try:
        log.info(f"InfluxDB: {influx_client.health().status}")
    except Exception as e:
        log.warning(f"InfluxDB health check: {e}")

    query_api = influx_client.query_api()
    write_api = influx_client.write_api(write_options=SYNCHRONOUS)

    consecutive_failures = 0
    try:
        check_and_classify(query_api, write_api)
        touch_health()
    except Exception as e:
        consecutive_failures += 1
        log.exception("check_and_classify failed: %s", e)

    while running:
        secs = seconds_until_next_price_boundary()
        log.info(f"Sleeping {secs:.0f}s until next price slot boundary")
        sleep_interruptible(secs)
        if running:
            try:
                check_and_classify(query_api, write_api)
                consecutive_failures = 0
                touch_health()
            except Exception as e:
                consecutive_failures += 1
                log.exception("check_and_classify failed (%d/%d consecutive): %s",
                              consecutive_failures, MAX_CONSECUTIVE_FAILURES, e)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log.critical("%d consecutive failures — exiting non-zero for restart/visibility",
                                 consecutive_failures)
                    influx_client.close()
                    sys.exit(1)

    influx_client.close()
    log.info("Shutdown complete")


if __name__ == "__main__":
    main()
