"""Unit tests for scripts/lights_optimizer.py.

Covers pure helpers (in_after_midnight_window, porch_target_state),
the CO2 classifier (co2_signal_class) with the InfluxDB query
function monkeypatched, and the CO2 auto-on → confirm → user-off
dismissal state machine in check_and_control() with all I/O
monkeypatched out.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import lights_optimizer as lo


HELSINKI = ZoneInfo("Europe/Helsinki")


def _local(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=HELSINKI)


# ── in_after_midnight_window ──────────────────────────────────────────────────

@pytest.mark.parametrize("h,m,expected", [
    (0, 0,  False),   # before 00:30 lower bound
    (0, 29, False),
    (0, 30, True),    # inclusive lower bound
    (2, 0,  True),    # middle of window
    (4, 59, True),    # just inside upper bound (default AFTER_MIDNIGHT_END_HOUR=5)
    (5, 0,  False),   # exclusive upper bound
    (12, 0, False),
    (23, 59, False),
])
def test_in_after_midnight_window_boundaries(h, m, expected):
    now = _local(2026, 5, 17, h, m)
    assert lo.in_after_midnight_window(now) is expected


def test_in_after_midnight_window_respects_end_hour(monkeypatch):
    """If AFTER_MIDNIGHT_END_HOUR is bumped to 7, 06:00 should be inside."""
    monkeypatch.setattr(lo, "AFTER_MIDNIGHT_END_HOUR", 7)
    assert lo.in_after_midnight_window(_local(2026, 5, 17, 6, 0)) is True
    assert lo.in_after_midnight_window(_local(2026, 5, 17, 7, 0)) is False


# ── porch_target_state ───────────────────────────────────────────────────────

def test_porch_off_during_bright_daylight(monkeypatch):
    """Mid-afternoon with sun well above threshold → porch stays off."""
    monkeypatch.setattr(lo, "PORCH_OFF_HOUR", 22)
    assert lo.porch_target_state(_local(2026, 5, 19, 15, 0), sun_elev_deg=35.0) is False


def test_porch_on_when_evening_dusk(monkeypatch):
    """Sun has dropped below 8° in the evening → porch on."""
    monkeypatch.setattr(lo, "PORCH_OFF_HOUR", 22)
    # 19:30 winter dusk: sun at 5°
    assert lo.porch_target_state(_local(2026, 1, 15, 19, 30), sun_elev_deg=5.0) is True


def test_porch_off_after_porch_off_hour_cap(monkeypatch):
    """Even fully dark, past PORCH_OFF_HOUR the porch turns off."""
    monkeypatch.setattr(lo, "PORCH_OFF_HOUR", 22)
    assert lo.porch_target_state(_local(2026, 1, 15, 22, 30), sun_elev_deg=-10.0) is False


def test_porch_off_at_predawn_twilight(monkeypatch):
    """Sun still below threshold at 04:00 but it's morning — porch stays off
    (no flap-on at pre-dawn twilight)."""
    monkeypatch.setattr(lo, "PORCH_OFF_HOUR", 22)
    assert lo.porch_target_state(_local(2026, 1, 15, 4, 0), sun_elev_deg=-12.0) is False


def test_porch_skipped_on_midsummer_when_never_dark(monkeypatch):
    """Midsummer evening: sun still at 10° at 22:00 → porch stays off."""
    monkeypatch.setattr(lo, "PORCH_OFF_HOUR", 23)
    assert lo.porch_target_state(_local(2026, 6, 21, 22, 0), sun_elev_deg=10.0) is False


def test_porch_threshold_boundary(monkeypatch):
    """Boundary: 8° threshold, < (strict) — 8.0 is NOT dark, 7.99 is."""
    monkeypatch.setattr(lo, "PORCH_OFF_HOUR", 22)
    monkeypatch.setattr(lo, "SUN_DARK_ELEVATION_DEG", 8.0)
    assert lo.porch_target_state(_local(2026, 4, 15, 20, 0), sun_elev_deg=8.0) is False
    assert lo.porch_target_state(_local(2026, 4, 15, 20, 0), sun_elev_deg=7.99) is True


def test_porch_wrap_around_off_hour_past_midnight(monkeypatch):
    """PORCH_OFF_HOUR=26 (= 02:00 next day) — 01:00 with sun still below
    threshold remains on; 02:30 turns off."""
    monkeypatch.setattr(lo, "PORCH_OFF_HOUR", 26)
    assert lo.porch_target_state(_local(2026, 1, 15, 1, 0), sun_elev_deg=-15.0) is True
    assert lo.porch_target_state(_local(2026, 1, 15, 2, 30), sun_elev_deg=-15.0) is False


# ── co2_signal_class ─────────────────────────────────────────────────────────

class _FakeRow:
    """Mimics what _query() returns: an object with a .get_value() method."""
    def __init__(self, v):
        self._v = v

    def get_value(self):
        return self._v


def _stub_query(recent, baseline):
    """Build a _query replacement that returns recent vs baseline based on
    the literal range header in the Flux query string."""
    def fake(flux):
        if "range(start: -5m)" in flux:
            return [_FakeRow(recent)] if recent is not None else []
        if "range(start: -2h, stop: -1h)" in flux:
            return [_FakeRow(baseline)] if baseline is not None else []
        return []
    return fake


@pytest.mark.parametrize("recent,baseline,expected", [
    # Absolute fallbacks — fire regardless of baseline
    (600, 500,  "ELEVATED"),   # recent >= 580 absolute
    (580, 500,  "ELEVATED"),   # boundary inclusive
    (400, 1000, "DROPPED"),    # recent <= 450 absolute (wins over delta math)
    (450, 1000, "DROPPED"),    # boundary inclusive

    # Delta classification — recent must be inside (450, 580) to reach the delta branch
    (475, 458,  "BASELINE"),   # delta +17 < 20
    (475, 455,  "ELEVATED"),   # delta +20 hits threshold (>= 20)
    (455, 555,  "DROPPED"),    # delta -100 hits threshold (<= -100, recent > 450 absolute)
    (475, 460,  "BASELINE"),   # delta +15 < 20

    # Baseline missing — only absolute fallback works
    (600, None, "ELEVATED"),
    (400, None, "DROPPED"),
    (500, None, "BASELINE"),

    # Recent missing → UNKNOWN
    (None, 500, "UNKNOWN"),
])
def test_co2_signal_class(monkeypatch, recent, baseline, expected):
    monkeypatch.setattr(lo, "_query", _stub_query(recent, baseline))
    assert lo.co2_signal_class() == expected


# ── CO2 dismissal state machine (integration-style) ──────────────────────────

@pytest.fixture
def co2_harness(monkeypatch):
    """Stub out every I/O the optimizer touches per-tick so we can drive
    check_and_control() through state transitions and watch the resulting
    publishes / decisions in memory."""

    # Reset module-level CO2 state between tests
    monkeypatch.setattr(lo, "_co2_auto_on_at", {})
    monkeypatch.setattr(lo, "_co2_auto_on_confirmed", {})
    monkeypatch.setattr(lo, "_co2_dismissed_date", {})
    monkeypatch.setattr(lo, "_co2_after_midnight_quenched", {})

    # Quiet the rest of the tick: no occupancy, no other lights, no sauna,
    # no porch logic firing on real data.
    monkeypatch.setattr(lo, "switch_pressed_recently", lambda m: False)
    monkeypatch.setattr(lo, "light_turned_on_recently", lambda m: False)
    monkeypatch.setattr(lo, "co2_recently_elevated", lambda m: False)
    monkeypatch.setattr(lo, "fetch_sauna_temp_recent", lambda: None)
    monkeypatch.setattr(lo, "sauna_session_ended_minutes_ago", lambda: None)
    monkeypatch.setattr(lo, "fetch_last_zero_to_one", lambda idx: None)
    monkeypatch.setattr(lo, "light_override_until", lambda idx: 0.0)

    # Capture all publishes + log_decisions for assertion
    published = []
    decisions = []

    def fake_publish(idx, on, reason):
        published.append((idx, on, reason))
        return True

    def fake_log(idx, decision, reason, on_dur=None, category=""):
        decisions.append((idx, decision, reason, category))

    monkeypatch.setattr(lo, "publish_state", fake_publish)
    monkeypatch.setattr(lo, "log_decision", fake_log)

    # Deterministic sun: pretend it's deep night, well below dark threshold.
    monkeypatch.setattr(lo, "todays_sun",
                        lambda now: (now.replace(hour=6, minute=0, second=0, microsecond=0),
                                     now.replace(hour=21, minute=0, second=0, microsecond=0)))

    import lights_optimizer as _lo
    # Patch the astral sun_elevation reference inside the module
    monkeypatch.setattr(_lo, "sun_elevation", lambda observer, dateandtime: -10.0)

    # Mutable holders so the test can drive the simulation
    state = {"now": _local(2026, 5, 17, 21, 0),
             "kitchen_on": False,
             "livingroom_on": False,
             "co2": "BASELINE"}

    def fake_states():
        return {
            lo.CO2_AUTO_KITCHEN_IDX:    (state["kitchen_on"], state["now"]),
            lo.CO2_AUTO_LIVINGROOM_IDX: (state["livingroom_on"], state["now"]),
            47: (False, state["now"]),  # porch off, no schedule action needed
        }

    monkeypatch.setattr(lo, "fetch_current_light_states", fake_states)
    monkeypatch.setattr(lo, "co2_signal_class", lambda: state["co2"])

    # Pretend `now` is always state["now"]
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            n = state["now"]
            return n if tz is None else n.astimezone(tz)

    monkeypatch.setattr(lo, "datetime", _FixedDatetime)

    return state, published, decisions


def _co2_pubs(published):
    """Filter to only CO2-managed-light publishes — the porch (idx 47)
    is owned by the schedule block and adds noise."""
    return [p for p in published if p[0] in lo.CO2_AUTO_MANAGED]


def test_co2_auto_on_when_dark_and_elevated(co2_harness):
    state, published, _ = co2_harness
    state["co2"] = "ELEVATED"

    lo.check_and_control()

    on_publishes = [p for p in _co2_pubs(published) if p[1] is True]
    assert (lo.CO2_AUTO_KITCHEN_IDX, True, "co2_occupancy") in on_publishes
    assert (lo.CO2_AUTO_LIVINGROOM_IDX, True, "co2_occupancy") in on_publishes
    # Internal tracking is now set so we can transition to confirm/dismiss
    assert lo.CO2_AUTO_KITCHEN_IDX in lo._co2_auto_on_at


def test_co2_no_auto_on_when_dismissed_today(co2_harness):
    state, published, decisions = co2_harness
    state["co2"] = "ELEVATED"
    today = state["now"].date()
    lo._co2_dismissed_date[lo.CO2_AUTO_KITCHEN_IDX] = today
    lo._co2_dismissed_date[lo.CO2_AUTO_LIVINGROOM_IDX] = today

    lo.check_and_control()

    assert not [p for p in _co2_pubs(published) if p[1] is True], \
        "Should not auto-on a dismissed light"
    reasons = {(idx, reason) for idx, _, reason, _ in decisions}
    assert (lo.CO2_AUTO_KITCHEN_IDX, "dismissed_today") in reasons


def test_co2_user_dismissal_marks_date(co2_harness):
    """Auto-on → relay confirms → user turns off → dismissal flag set
    → next tick suppressed."""
    state, published, decisions = co2_harness

    # Tick 1: dark + elevated → auto-on published
    state["co2"] = "ELEVATED"
    lo.check_and_control()
    assert lo.CO2_AUTO_KITCHEN_IDX in lo._co2_auto_on_at
    assert not lo._co2_auto_on_confirmed.get(lo.CO2_AUTO_KITCHEN_IDX)

    # Tick 2: relay confirms (light reads back as on)
    state["now"] += timedelta(minutes=1)
    state["kitchen_on"] = True
    state["livingroom_on"] = True
    lo.check_and_control()
    assert lo._co2_auto_on_confirmed[lo.CO2_AUTO_KITCHEN_IDX] is True

    # Tick 3: user flips it off (still elevated CO2)
    state["now"] += timedelta(minutes=1)
    state["kitchen_on"] = False
    state["livingroom_on"] = False
    published.clear()
    decisions.clear()
    lo.check_and_control()

    today = state["now"].date()
    assert lo._co2_dismissed_date.get(lo.CO2_AUTO_KITCHEN_IDX) == today
    assert lo._co2_dismissed_date.get(lo.CO2_AUTO_LIVINGROOM_IDX) == today
    # The dismissal tick itself must NOT re-publish ON for the CO2 lights
    assert not [p for p in _co2_pubs(published) if p[1] is True]

    # Tick 4: still elevated, still dark — dismissal must hold
    state["now"] += timedelta(minutes=1)
    published.clear()
    decisions.clear()
    lo.check_and_control()
    assert not [p for p in _co2_pubs(published) if p[1] is True]
    reasons = {(idx, reason) for idx, _, reason, _ in decisions}
    assert (lo.CO2_AUTO_KITCHEN_IDX, "dismissed_today") in reasons


def test_co2_publish_not_confirmed_within_grace_retries(co2_harness):
    """If the relay never confirms within _CO2_PUBLISH_GRACE_SECONDS, the
    optimizer must NOT mark the light dismissed — instead the stale attempt
    is dropped and a fresh ON is published in the same tick."""
    state, published, _ = co2_harness

    # Tick 1: auto-on, but relay stays off (publish silently failed)
    state["co2"] = "ELEVATED"
    lo.check_and_control()
    first_attempt_t = lo._co2_auto_on_at[lo.CO2_AUTO_KITCHEN_IDX]
    assert not lo._co2_auto_on_confirmed.get(lo.CO2_AUTO_KITCHEN_IDX)

    # Tick 2: jump past grace window — light still off, no confirmation.
    # The optimizer clears the stale attempt AND immediately retries the
    # ON publish; what we care about is that dismissal was NOT recorded.
    state["now"] += timedelta(seconds=int(lo._CO2_PUBLISH_GRACE_SECONDS) + 5)
    published.clear()
    lo.check_and_control()

    # No dismissal recorded — eligible to retry next tick
    assert lo.CO2_AUTO_KITCHEN_IDX not in lo._co2_dismissed_date
    # Fresh retry published on this tick
    on_publishes = [p for p in _co2_pubs(published) if p[1] is True]
    assert (lo.CO2_AUTO_KITCHEN_IDX, True, "co2_occupancy") in on_publishes
    # Attempt timestamp refreshed (not the original)
    assert lo._co2_auto_on_at[lo.CO2_AUTO_KITCHEN_IDX] > first_attempt_t


def test_co2_after_midnight_turns_off_running_light(co2_harness):
    """02:00 local, kitchen light on → after_midnight rule auto-offs it."""
    state, published, decisions = co2_harness
    state["now"] = _local(2026, 5, 17, 2, 0)
    state["kitchen_on"] = True
    state["livingroom_on"] = True
    state["co2"] = "BASELINE"
    # These are OUR auto-ons that lingered past midnight (they carry an
    # auto_on_t); the after-midnight rule only offs lights we switched on.
    lo._co2_auto_on_at[lo.CO2_AUTO_KITCHEN_IDX] = _local(2026, 5, 17, 1, 30)
    lo._co2_auto_on_at[lo.CO2_AUTO_LIVINGROOM_IDX] = _local(2026, 5, 17, 1, 30)

    lo.check_and_control()

    offs = [p for p in published if p[1] is False]
    assert (lo.CO2_AUTO_KITCHEN_IDX, False, "co2_auto_after_midnight") in offs
    assert (lo.CO2_AUTO_LIVINGROOM_IDX, False, "co2_auto_after_midnight") in offs


def test_co2_after_midnight_quench_prevents_reflapping(co2_harness):
    """Regression for the 00:00-02:00 ON/OFF storm: after_midnight off
    must mark a same-night quench so the very next tick — with CO₂ still
    elevated and the room still dark — does NOT re-fire ON."""
    state, published, decisions = co2_harness
    today = _local(2026, 5, 23, 0, 30).date()

    # Tick 1: deep night, kitchen+livingroom on, CO₂ still elevated
    # (someone awake). after_midnight rule fires OFF on both.
    state["now"] = _local(2026, 5, 23, 0, 30)
    state["kitchen_on"] = True
    state["livingroom_on"] = True
    state["co2"] = "ELEVATED"
    # Our own auto-ons (carry auto_on_t) so the after-midnight rule offs them.
    lo._co2_auto_on_at[lo.CO2_AUTO_KITCHEN_IDX] = _local(2026, 5, 23, 0, 15)
    lo._co2_auto_on_at[lo.CO2_AUTO_LIVINGROOM_IDX] = _local(2026, 5, 23, 0, 15)
    lo.check_and_control()

    offs = [p for p in _co2_pubs(published) if p[1] is False]
    assert (lo.CO2_AUTO_KITCHEN_IDX, False, "co2_auto_after_midnight") in offs
    assert (lo.CO2_AUTO_LIVINGROOM_IDX, False, "co2_auto_after_midnight") in offs
    assert lo._co2_after_midnight_quenched.get(lo.CO2_AUTO_KITCHEN_IDX) == today
    assert lo._co2_after_midnight_quenched.get(lo.CO2_AUTO_LIVINGROOM_IDX) == today

    # Tick 2: relay confirmed the off, CO₂ still elevated, still dark,
    # still in the after-midnight window. The quench must block re-fire.
    state["now"] += timedelta(minutes=1)
    state["kitchen_on"] = False
    state["livingroom_on"] = False
    published.clear()
    decisions.clear()
    lo.check_and_control()

    assert not [p for p in _co2_pubs(published) if p[1] is True], \
        "after_midnight quench must block immediate re-fire"
    reasons = {(idx, reason) for idx, _, reason, _ in decisions}
    assert (lo.CO2_AUTO_KITCHEN_IDX, "after_midnight_quenched") in reasons
    assert (lo.CO2_AUTO_LIVINGROOM_IDX, "after_midnight_quenched") in reasons


def test_co2_after_midnight_quench_releases_outside_window(co2_harness):
    """Quench is scoped to the after-midnight window only — once the clock
    passes AFTER_MIDNIGHT_END_HOUR, normal CO₂ auto-on resumes the same day."""
    state, published, _ = co2_harness

    # Tick 1: 00:45 → after_midnight off + quench set
    state["now"] = _local(2026, 5, 23, 0, 45)
    state["kitchen_on"] = True
    state["livingroom_on"] = True
    state["co2"] = "ELEVATED"
    # Our own auto-ons (carry auto_on_t) so the after-midnight rule offs them.
    lo._co2_auto_on_at[lo.CO2_AUTO_KITCHEN_IDX] = _local(2026, 5, 23, 0, 30)
    lo._co2_auto_on_at[lo.CO2_AUTO_LIVINGROOM_IDX] = _local(2026, 5, 23, 0, 30)
    lo.check_and_control()

    today = state["now"].date()
    assert lo._co2_after_midnight_quenched.get(lo.CO2_AUTO_KITCHEN_IDX) == today

    # Tick 2: jump to 05:30 (past AFTER_MIDNIGHT_END_HOUR=5) — quench should
    # no longer gate, so auto-on fires again on still-elevated CO₂.
    state["now"] = _local(2026, 5, 23, 5, 30)
    state["kitchen_on"] = False
    state["livingroom_on"] = False
    published.clear()
    lo.check_and_control()

    on_publishes = [p for p in _co2_pubs(published) if p[1] is True]
    assert (lo.CO2_AUTO_KITCHEN_IDX, True, "co2_occupancy") in on_publishes
    assert (lo.CO2_AUTO_LIVINGROOM_IDX, True, "co2_occupancy") in on_publishes


def test_co2_publish_failure_does_not_record_auto_on(co2_harness, monkeypatch):
    """If `publish_state` returns False (MQTT broker unreachable), the
    optimizer must not pretend the light came on: no `_co2_auto_on_at`
    entry, and the decision log should reflect the failure so analytics
    don't double-count phantom auto-ons."""
    state, published, decisions = co2_harness

    def fake_publish_fail(idx, on, reason):
        published.append((idx, on, reason))
        return False
    monkeypatch.setattr(lo, "publish_state", fake_publish_fail)

    state["co2"] = "ELEVATED"
    lo.check_and_control()

    assert lo.CO2_AUTO_KITCHEN_IDX not in lo._co2_auto_on_at
    reasons = {(idx, reason) for idx, _, reason, _ in decisions}
    assert (lo.CO2_AUTO_KITCHEN_IDX, "mqtt_publish_failed") in reasons


def test_co2_manual_on_not_auto_offed_when_dropped(co2_harness):
    """Regression: a user who manually switches on a CO₂-managed ceiling
    light (no `_co2_auto_on_at` record) must NOT get it killed on the next
    tick just because indoor CO₂ reads low. The min-on protection only ever
    applied to our own auto-ons; a manual on has no auto_on_t and used to
    fall straight through to `co2_no_occupancy` off."""
    state, published, decisions = co2_harness

    # User manually flips the living-room ceiling on; CO₂ is low (they just
    # walked into a well-ventilated room). No auto_on_t exists for it.
    state["livingroom_on"] = True
    state["co2"] = "DROPPED"
    assert lo.CO2_AUTO_LIVINGROOM_IDX not in lo._co2_auto_on_at

    lo.check_and_control()

    # It must be held, not turned off.
    offs = [p for p in _co2_pubs(published) if p[1] is False]
    assert (lo.CO2_AUTO_LIVINGROOM_IDX, False, "co2_no_occupancy") not in offs
    reasons = {(idx, reason) for idx, _, reason, _ in decisions}
    assert (lo.CO2_AUTO_LIVINGROOM_IDX, "manual_on") in reasons


def test_co2_manual_on_survives_after_midnight(co2_harness):
    """Regression: a light the USER switched on after midnight (no auto_on_t)
    must NOT be killed by the after-midnight rule, nor set a quench. Only our
    own lingering auto-ons get the after-midnight off."""
    state, published, decisions = co2_harness
    state["now"] = _local(2026, 5, 17, 1, 0)   # inside the after-midnight window
    state["livingroom_on"] = True
    state["co2"] = "ELEVATED"
    assert lo.CO2_AUTO_LIVINGROOM_IDX not in lo._co2_auto_on_at

    lo.check_and_control()

    offs = [p for p in _co2_pubs(published) if p[1] is False]
    assert (lo.CO2_AUTO_LIVINGROOM_IDX, False, "co2_auto_after_midnight") not in offs
    reasons = {(idx, reason) for idx, _, reason, _ in decisions}
    assert (lo.CO2_AUTO_LIVINGROOM_IDX, "manual_on") in reasons
    # No quench set → it won't be blocked from staying on.
    assert lo.CO2_AUTO_LIVINGROOM_IDX not in lo._co2_after_midnight_quenched


def test_post_sauna_skips_freshly_pressed_light(monkeypatch):
    """A bathroom press inside the post-sauna lookback window must NOT
    be auto-offed by the cooldown rule — these lights are manual_only
    precisely so wall-clock timers can't cut a fresh shower short."""
    monkeypatch.setattr(lo, "switch_pressed_recently", lambda m: False)
    monkeypatch.setattr(lo, "light_turned_on_recently", lambda m: False)
    monkeypatch.setattr(lo, "co2_recently_elevated", lambda m: False)
    monkeypatch.setattr(lo, "fetch_sauna_temp_recent", lambda: None)
    monkeypatch.setattr(lo, "co2_signal_class", lambda: "BASELINE")
    monkeypatch.setattr(lo, "light_override_until", lambda idx: 0.0)
    monkeypatch.setattr(lo, "todays_sun",
                        lambda now: (now.replace(hour=6, minute=0, second=0, microsecond=0),
                                     now.replace(hour=21, minute=0, second=0, microsecond=0)))
    import lights_optimizer as _lo
    monkeypatch.setattr(_lo, "sun_elevation", lambda observer, dateandtime: 30.0)

    fresh_press_t = datetime.now(timezone.utc) - timedelta(minutes=2)
    monkeypatch.setattr(lo, "fetch_last_zero_to_one",
                        lambda idx: fresh_press_t if idx in lo.SAUNA_AFTER_LIGHTS else None)
    monkeypatch.setattr(lo, "sauna_session_ended_minutes_ago", lambda: 90.0)

    states_now = datetime.now(HELSINKI)
    def fake_states():
        out = {idx: (True, states_now) for idx in lo.SAUNA_AFTER_LIGHTS}
        out[47] = (False, states_now)
        return out
    monkeypatch.setattr(lo, "fetch_current_light_states", fake_states)

    published = []
    decisions = []
    monkeypatch.setattr(lo, "publish_state",
                        lambda idx, on, reason: (published.append((idx, on, reason)), True)[1])
    monkeypatch.setattr(lo, "log_decision",
                        lambda idx, decision, reason, on_dur=None, category="":
                            decisions.append((idx, decision, reason, category)))

    lo.check_and_control()

    post_sauna_offs = [p for p in published
                       if p[0] in lo.SAUNA_AFTER_LIGHTS and p[1] is False]
    assert post_sauna_offs == [], \
        f"manual press should suppress post-sauna auto-off, got: {post_sauna_offs}"
    reasons = {(idx, reason) for idx, _, reason, _ in decisions}
    for idx_after in lo.SAUNA_AFTER_LIGHTS:
        assert (idx_after, "post_sauna_manual_grace") in reasons
