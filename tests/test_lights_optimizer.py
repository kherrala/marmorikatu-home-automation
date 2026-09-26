"""Unit tests for scripts/lights_optimizer.py (v2 — comfort-first, provenance).

The decision engine reads InfluxDB via a handful of module-level helper
functions; tests monkeypatch those to drive `evaluate_light` deterministically
and capture the resulting publishes / decision-log rows. (scripts/ is put on
the path by tests/conftest.py.)
"""
import time
import re
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import lights_optimizer as lo

TZ = ZoneInfo("Europe/Helsinki")


def _local(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


# ── Coverage / config invariants ──────────────────────────────────────────────
def test_every_light_index_is_categorized_or_special():
    from light_labels import LIGHT_LABELS
    # Every labeled PLC output is either categorized, handled by a special block,
    # or explicitly disconnected (no physical light).
    covered = set(lo.CATEGORY_OF) | set(lo.SPECIAL_IDX) | set(lo.DISCONNECTED_IDX)
    assert covered == set(LIGHT_LABELS), covered.symmetric_difference(set(LIGHT_LABELS))


def test_disconnected_lights_are_not_categorized():
    # A disconnected output must never be evaluated — keep it out of CATEGORY_OF.
    assert not (lo.DISCONNECTED_IDX & set(lo.CATEGORY_OF))


def test_every_category_has_a_behaviour():
    for cat in set(lo.CATEGORY_OF.values()):
        assert cat in lo.CATS


def test_comfort_first_invariants():
    assert lo.CATS["living"].daylight_off is False
    assert lo.CATS["office"].overnight_off is False
    assert lo.CATS["secondary"].overnight_off is True
    assert lo.CATS["office"].daylight_off is False


# ── Overnight window ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("h,m,expected", [
    (0, 0, False), (0, 30, True), (3, 0, True), (5, 59, True),
    (6, 0, False), (12, 0, False), (23, 0, False),
])
def test_overnight_window(h, m, expected):
    assert lo.in_overnight_window(_local(2026, 1, 15, h, m)) is expected


@pytest.mark.parametrize("h,m,expected", [
    (19, 59, False), (20, 0, True), (23, 59, True), (0, 0, True),
    (5, 59, True), (6, 0, False), (12, 0, False),
])
def test_evening_cutoff_window_continues_across_midnight(h, m, expected):
    assert lo.in_overnight_window(_local(2026, 9, 26, h, m), dtime(20)) is expected


def test_evening_cutoff_keeps_same_start_after_midnight():
    cutoff = _local(2026, 9, 26, 20)
    assert lo.overnight_start_dt(_local(2026, 9, 26, 23), dtime(20)) == cutoff
    assert lo.overnight_start_dt(_local(2026, 9, 27, 2), dtime(20)) == cutoff


# ── Porch (idx 47) — optimizer is the sole controller ─────────────────────────
@pytest.fixture
def porch(monkeypatch):
    pub, dec = [], []
    st = {"until": 0.0, "origin": "unknown"}   # detection hold + who lit it
    monkeypatch.setattr(lo, "light_override_until", lambda idx: st["until"])
    monkeypatch.setattr(lo, "fetch_last_transition",
                        lambda idx: (True, datetime.now(timezone.utc)))
    monkeypatch.setattr(lo, "classify_origin", lambda idx, is_on, since: st["origin"])
    monkeypatch.setattr(lo, "publish_state",
                        lambda idx, on, reason: (pub.append((idx, on, reason)) or True))
    monkeypatch.setattr(lo, "log_decision",
                        lambda idx, decision, reason, category="", manual_locked=False, on_dur=None:
                        dec.append((decision, reason)))
    return {"pub": pub, "dec": dec, "st": st}


def _sun(now):
    return _local(now.year, now.month, now.day, 6, 0), _local(now.year, now.month, now.day, 21, 0)


def test_porch_no_auto_on_at_dusk(porch):
    # Dark evening, porch off, no detection hold → optimizer must NOT turn it on.
    now = _local(2026, 1, 15, 19, 30)
    lo.run_porch(now, {47: False}, *_sun(now))
    assert porch["pub"] == []


def test_porch_detection_lights_it(porch):
    now = _local(2026, 1, 15, 23, 0)
    porch["st"]["until"] = now.timestamp() + 300   # active detection hold
    lo.run_porch(now, {47: False}, *_sun(now))
    assert (47, True, "porch_detection") in porch["pub"]


def test_porch_detection_ended_turns_off_our_light(porch):
    # Hold expired, porch on, and WE lit it (origin=optimizer) → turn off.
    now = _local(2026, 1, 15, 23, 10)
    porch["st"]["until"] = 0.0
    porch["st"]["origin"] = "optimizer"
    lo.run_porch(now, {47: True}, *_sun(now))
    assert (47, False, "porch_detection_ended") in porch["pub"]


def test_porch_manual_on_left_alone_at_night(porch):
    # Porch on at night, no hold, a human lit it → never turned off.
    now = _local(2026, 1, 15, 22, 0)
    porch["st"]["origin"] = "wall"
    lo.run_porch(now, {47: True}, *_sun(now))
    assert porch["pub"] == []


def test_porch_manual_off_during_detection_respected(porch):
    # Detection hold active but the user turned it off → don't re-light it.
    now = _local(2026, 1, 15, 23, 0)
    porch["st"]["until"] = now.timestamp() + 300
    porch["st"]["origin"] = "human"
    lo.run_porch(now, {47: False}, *_sun(now))
    assert porch["pub"] == []
    assert porch["dec"][-1] == ("hold", "detection_dismissed")


def test_porch_daylight_off_if_left_on(porch):
    now = _local(2026, 6, 15, 13, 0)   # midday, manual on
    porch["st"]["origin"] = "wall"
    lo.run_porch(now, {47: True}, *_sun(now))
    assert (47, False, "daylight_off") in porch["pub"]


# ── classify_origin ───────────────────────────────────────────────────────────
def test_classify_origin_optimizer(monkeypatch):
    since = datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(lo, "fetch_recent_commands",
                        lambda idx, lookback_min=180: [(True, "optimizer", since - timedelta(seconds=12))])
    assert lo.classify_origin(40, True, since) == "optimizer"


def test_classify_origin_mobile_is_human(monkeypatch):
    since = datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(lo, "fetch_recent_commands",
                        lambda idx, lookback_min=180: [(True, "mobile", since - timedelta(seconds=10))])
    assert lo.classify_origin(40, True, since) == "human"


def test_classify_origin_wall_when_no_breadcrumb(monkeypatch):
    since = datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(lo, "fetch_recent_commands", lambda idx, lookback_min=180: [])
    assert lo.classify_origin(40, True, since) == "wall"


def test_command_provenance_uses_latest_across_source_tables(monkeypatch):
    from influxdb_client.client.flux_table import FluxRecord
    since = datetime.now(timezone.utc)
    # Influx sorts inside each source table, not across source tables.
    rows = [FluxRecord(0, {"_time": since - timedelta(seconds=5), "_value": 1,
                           "source": "mobile"}),
            FluxRecord(1, {"_time": since - timedelta(seconds=20), "_value": 1,
                           "source": "optimizer"})]
    monkeypatch.setattr(lo, "_query", lambda _: rows)
    assert lo.classify_origin(54, True, since) == "human"


# ── evaluate_light decision engine ────────────────────────────────────────────
@pytest.fixture
def harness(monkeypatch):
    published: list[tuple] = []
    decisions: list[tuple] = []
    state = {
        "since": _local(2026, 1, 14, 12),
        "origin": "wall",
        "presence": None,        # default per-room presence (None|True|False)
        "presence_rooms": {},    # per-room override: {room: True|False|None}
        "co2": "BASELINE",
        "dwell": False,
        "lux": {},               # per-room measured illuminance: {room: lux}
    }
    monkeypatch.setattr(lo, "fetch_last_transition", lambda idx: (True, state["since"]))
    monkeypatch.setattr(lo, "classify_origin", lambda idx, is_on, since: state["origin"])
    monkeypatch.setattr(lo, "_presence_for_room_uncached",
                        lambda room: state["presence_rooms"].get(room, state["presence"]))
    monkeypatch.setattr(lo, "_room_illuminance_uncached",
                        lambda room: state["lux"].get(room))
    def fake_query(flux):
        if '"co2"' in flux:
            value = 700 if state["co2"] == "ELEVATED" else 430
            return [type("R", (), {"get_value": lambda self: value})()]
        raise AssertionError(f"Unexpected unmocked Flux query: {flux}")
    monkeypatch.setattr(lo, "_query", fake_query)
    monkeypatch.setattr(lo, "within_min_dwell", lambda idx: state["dwell"])
    monkeypatch.setattr(lo, "publish_state",
                        lambda idx, on, reason: (published.append((idx, on, reason)) or True))
    monkeypatch.setattr(lo, "log_decision",
                        lambda idx, decision, reason, category="", manual_locked=False, on_dur=None:
                        decisions.append((idx, decision, reason)))
    monkeypatch.setattr(lo.time, "sleep", lambda _: None)
    lo._memo.clear()
    lo._dismissed.clear()
    monkeypatch.setattr(lo, "_dismissal_seen", {})
    monkeypatch.setattr(lo, "_last_publish_ts", {})
    return {"published": published, "decisions": decisions, "state": state}


def _eval(idx, is_on, now, dark=True):
    sr = _local(now.year, now.month, now.day, 6, 0)
    ss = _local(now.year, now.month, now.day, 21, 0)
    lo.evaluate_light(idx, is_on, now, sr, ss, is_dark=dark)


def test_living_manual_on_held_during_awake_hours(harness):
    harness["state"]["origin"] = "wall"
    _eval(54, True, _local(2026, 1, 15, 19, 0))
    assert harness["published"] == []
    assert harness["decisions"][-1][1] == "hold"


def test_living_never_daylight_off(harness):
    _eval(54, True, _local(2026, 6, 15, 13, 0), dark=False)
    assert harness["published"] == []


def test_living_not_vacancy_off_on_co2_dropped(harness):
    # REGRESSION: CO2 "dropped" with no real presence must NOT turn the living
    # room off (CO2 only drives auto-ON). presence=None, co2=DROPPED → HOLD.
    harness["state"]["presence"] = None
    harness["state"]["co2"] = "DROPPED"
    _eval(54, True, _local(2026, 1, 15, 17, 0), dark=False)
    assert harness["published"] == []
    assert harness["decisions"][-1][1] == "hold"


def test_living_vacancy_off_only_on_real_presence(harness):
    # Real mmWave presence=False (Presence Service) DOES allow vacancy-off.
    harness["state"]["presence"] = False
    harness["state"]["since"] = _local(2026, 1, 15, 13, 30)
    _eval(54, True, _local(2026, 1, 15, 17, 0), dark=False)
    assert (54, False, "vacancy_off") in harness["published"]


def test_window_daylight_off(harness):
    _eval(46, True, _local(2026, 6, 15, 13, 0), dark=False)
    assert (46, False, "daylight_off") in harness["published"]


def test_sensorless_utility_duration_cap(harness):
    harness["state"]["since"] = _local(2026, 1, 15, 12, 30)
    _eval(61, True, _local(2026, 1, 15, 14, 0))
    assert (61, False, "duration_cap") in harness["published"]


def test_living_auto_on_when_dark_and_occupied(harness):
    harness["state"]["presence"] = True
    _eval(54, False, _local(2026, 1, 15, 18, 0), dark=True)
    assert (54, True, "auto_on_comfort") in harness["published"]


def test_no_auto_on_when_not_dark(harness):
    harness["state"]["presence"] = True
    _eval(54, False, _local(2026, 6, 15, 13, 0), dark=False)
    assert harness["published"] == []


def test_dismissed_session_suppresses_auto_on(harness):
    # A live session dismissal blocks re-auto-on even while present + dark.
    harness["state"]["presence"] = True
    lo._dismissed.add(54)
    _eval(54, False, _local(2026, 1, 15, 18, 0), dark=True)
    assert harness["published"] == []
    assert harness["decisions"][-1][1:] == ("hold", "dismissed_session")


# ── Dismissal model: session-scoped, edge-triggered, vacancy-cleared ──────────
def test_fresh_manual_off_registers_dismissal(harness, monkeypatch):
    # The user just turned OFF a light we auto-on'd (fresh off, optimizer was last
    # to command ON, off attributed to a human) → a session dismissal is armed.
    harness["state"]["origin"] = "wall"          # off attributed to a human
    harness["state"]["since"] = datetime.now(timezone.utc) - timedelta(seconds=5)
    monkeypatch.setattr(lo, "fetch_recent_commands",
                        lambda idx, lookback_min=30: [(True, "optimizer", None)])
    lo.detect_dismissals({54: False})
    assert 54 in lo._dismissed


def test_stale_manual_off_does_not_rearm_on_return(harness, monkeypatch):
    # REGRESSION (the whole reason for the rewrite): walking back into a room you
    # darkened hours ago must NOT re-arm suppression. The off transition is old, so
    # even though it's still the last transition, no dismissal is registered.
    harness["state"]["origin"] = "wall"
    harness["state"]["since"] = datetime.now(timezone.utc) - timedelta(hours=3)
    monkeypatch.setattr(lo, "fetch_recent_commands",
                        lambda idx, lookback_min=30: [(True, "optimizer", None)])
    lo.detect_dismissals({54: False})
    assert 54 not in lo._dismissed


def test_dismissal_cleared_when_room_goes_vacant(harness):
    # Leaving the room (presence False) drops the dismissal → a fresh arrival can
    # auto-on again.
    lo._dismissed.add(54)
    harness["state"]["presence"] = False
    lo.maintain_dismissals()
    assert 54 not in lo._dismissed


def test_dismissal_held_while_room_still_occupied(harness):
    # Still present → dismissal persists (we don't re-light what they just turned off).
    lo._dismissed.add(54)
    harness["state"]["presence"] = True
    lo.maintain_dismissals()
    assert 54 in lo._dismissed


def test_unknown_presence_cannot_clear_a_manual_off(harness):
    # A sensor outage is not a new visit and cannot silently undo manual OFF.
    lo._dismissed.add(54)
    harness["state"]["presence"] = None
    lo.maintain_dismissals()
    assert 54 in lo._dismissed


def test_occupied_dismissal_does_not_expire_and_relight_room(harness):
    lo._dismissed.add(54)
    harness["state"]["presence"] = True
    lo.maintain_dismissals()
    _eval(54, False, _local(2026, 9, 25, 2, 0))
    assert harness["published"] == []


def test_living_dismissal_uses_the_same_occupancy_zone_as_auto_on(harness):
    lo._dismissed.add(54)
    harness["state"]["presence_rooms"] = {"kitchen": True, "living_room": False}
    lo.maintain_dismissals()
    _eval(54, False, _local(2026, 9, 25, 2, 0))
    assert harness["published"] == []


def test_mobile_off_is_respected_even_when_it_is_the_last_command(harness, monkeypatch):
    harness["state"]["origin"] = "human"
    harness["state"]["since"] = datetime.now(timezone.utc) - timedelta(seconds=5)
    harness["state"]["presence"] = True
    monkeypatch.setattr(lo, "fetch_recent_commands", lambda idx: [
        (True, "optimizer", harness["state"]["since"] - timedelta(minutes=5)),
        (False, "mobile", harness["state"]["since"]),
    ])
    now = _local(2026, 9, 25, 2, 0)
    lo.detect_dismissals({54: False})
    _eval(54, False, now)
    assert harness["published"] == []


def test_cleared_dismissal_does_not_rearm_from_the_same_off_edge(harness):
    harness["state"]["since"] = datetime.now(timezone.utc) - timedelta(seconds=5)
    harness["state"]["presence"] = False
    now = _local(2026, 9, 25, 2, 0)
    lo.detect_dismissals({44: False})
    lo.maintain_dismissals()
    lo.detect_dismissals({44: False})  # still within DISMISSAL_FRESH_S
    harness["state"]["presence"] = True
    lo._memo.clear()
    _eval(44, False, now)
    assert (44, True, "auto_on_comfort") in harness["published"]


def test_off_snapshot_while_our_on_is_in_flight_is_not_a_dismissal(harness):
    harness["state"]["since"] = datetime.now(timezone.utc) - timedelta(seconds=20)
    lo._last_publish_ts[44] = time.time() - 5
    lo.detect_dismissals({44: False})
    assert 44 not in lo._dismissed


def test_compose_dwell_allows_reentry_after_plc_round_trip(harness, monkeypatch):
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
    dwell = float(re.search(r"MIN_DWELL_SECONDS=(\d+)", compose).group(1))
    monkeypatch.setattr(lo, "MIN_DWELL_SECONDS", dwell)
    # Use the actual guard (the harness normally bypasses the clock).
    monkeypatch.setattr(lo, "within_min_dwell", _real_within_min_dwell)
    lo._last_publish_ts[44] = time.time() - 35
    harness["state"]["presence_rooms"] = {"wc_down": True}
    _eval(44, False, _local(2026, 9, 25, 2, 0))
    assert (44, True, "auto_on_comfort") in harness["published"]


_real_within_min_dwell = lo.within_min_dwell


def test_min_dwell_holds(harness):
    harness["state"]["dwell"] = True
    _eval(54, True, _local(2026, 1, 15, 14, 0))
    assert harness["published"] == []
    assert harness["decisions"][-1][1:] == ("hold", "min_dwell_hold")


def test_overnight_off_forgotten_light(harness):
    harness["state"]["since"] = datetime(2026, 1, 15, 22, 0, tzinfo=timezone.utc)
    _eval(46, True, _local(2026, 1, 16, 3, 0))
    assert (46, False, "overnight_off") in harness["published"]


def test_overnight_protects_light_switched_on_during_window(harness):
    now = _local(2026, 1, 16, 3, 5)
    harness["state"]["since"] = _local(2026, 1, 16, 3, 0).astimezone(timezone.utc)
    _eval(46, True, now)
    assert harness["published"] == []


def test_occupied_room_not_overnight_culled(harness):
    # A presence-wired room (living core) that reads occupied is never culled,
    # even if it was on since before the overnight window began.
    harness["state"]["since"] = datetime(2026, 1, 15, 22, 0, tzinfo=timezone.utc)
    harness["state"]["presence"] = True
    _eval(54, True, _local(2026, 1, 16, 3, 0))
    assert harness["published"] == []


# ── Per-room presence + motion auto-on (Zigbee Presence Engine) ───────────────
def test_toilet_motion_auto_on_when_dark(harness):
    harness["state"]["presence_rooms"] = {"wc_down": True}   # PIR sees motion
    _eval(44, False, _local(2026, 1, 15, 18, 0), dark=True)
    assert (44, True, "auto_on_comfort") in harness["published"]


def test_hall_motion_auto_on_when_dark(harness):
    harness["state"]["presence_rooms"] = {"hall_down": True}
    _eval(35, False, _local(2026, 1, 15, 18, 0), dark=True)
    assert (35, True, "auto_on_comfort") in harness["published"]


def test_theater_never_auto_on_even_with_presence(harness):
    # Even a stray theater presence reading cannot activate the manual basement.
    harness["state"]["presence_rooms"] = {"theater": True}
    _eval(49, False, _local(2026, 1, 15, 20, 0), dark=True)
    assert harness["published"] == []


def test_open_plan_one_sensor_holds_the_whole_zone(harness):
    # Kitchen occupied but living FP300 vacant: BOTH the kitchen ceiling (40) and the
    # living ceiling (54) stay on — it's one open-plan zone, held if either half sees
    # someone. (The old per-room logic wrongly culled 54 here.)
    harness["state"]["co2"] = "BASELINE"
    harness["state"]["presence_rooms"] = {"living_room": False, "kitchen": True}
    harness["state"]["since"] = _local(2026, 1, 15, 13, 30)
    _eval(40, True, _local(2026, 1, 15, 14, 0))
    _eval(54, True, _local(2026, 1, 15, 14, 0))
    assert harness["published"] == []


def test_open_plan_culled_only_when_whole_zone_empty(harness):
    # Both halves vacant AND CO₂ not elevated → the open-plan lights cull.
    harness["state"]["co2"] = "BASELINE"
    harness["state"]["presence_rooms"] = {"living_room": False, "kitchen": False}
    harness["state"]["since"] = _local(2026, 1, 15, 13, 30)
    _eval(40, True, _local(2026, 1, 15, 14, 0))
    _eval(54, True, _local(2026, 1, 15, 14, 0))
    assert (40, False, "vacancy_off") in harness["published"]
    assert (54, False, "vacancy_off") in harness["published"]


def test_open_plan_vacancy_is_not_overridden_by_co2(harness):
    # Both installed sensors confirm vacancy: elevated CO₂ must not hold lights.
    harness["state"]["co2"] = "ELEVATED"
    harness["state"]["presence_rooms"] = {"living_room": False, "kitchen": False}
    harness["state"]["since"] = _local(2026, 1, 15, 13, 30)
    _eval(40, True, _local(2026, 1, 15, 14, 0))
    assert (40, False, "vacancy_off") in harness["published"]


@pytest.mark.parametrize("readings", [
    {"kitchen": False, "living_room": False},
    {"kitchen": None, "living_room": None},
    {"kitchen": False, "living_room": None},
])
def test_co2_alone_never_switches_on_an_empty_or_unknown_room(harness, readings):
    harness["state"]["presence_rooms"] = readings
    # Replay CO2 crossing the 580 ppm threshold repeatedly during the night.
    for co2 in ("ELEVATED", "BASELINE", "ELEVATED", "BASELINE"):
        lo._memo.clear()
        harness["state"]["co2"] = co2
        _eval(54, False, _local(2026, 9, 25, 2, 12))
    assert harness["published"] == []


@pytest.mark.parametrize("hour", [18, 2])
def test_open_plan_missing_sensor_cannot_confirm_vacancy(harness, hour):
    harness["state"]["presence_rooms"] = {"kitchen": False, "living_room": None}
    harness["state"]["since"] = _local(2026, 9, 24, 20, 0)
    _eval(54, True, _local(2026, 9, 25, hour, 0))
    assert harness["published"] == []


@pytest.mark.parametrize("idx,room", [(44, "wc_down"), (6, "khh"),
                                         (26, "hall_up"), (54, "living_room")])
def test_occupied_room_is_not_cut_off_at_night(harness, idx, room):
    harness["state"]["presence_rooms"] = {room: True}
    _eval(idx, True, _local(2026, 9, 25, 2, 0))
    assert harness["published"] == []


def test_nighttime_lamp_brightness_cannot_switch_its_own_light_off(harness):
    harness["state"]["presence_rooms"] = {"living_room": True}
    harness["state"]["origin"] = "optimizer"
    harness["state"]["lux"] = {"living_room": 300}
    _eval(54, True, _local(2026, 9, 25, 2, 0), dark=True)
    assert harness["published"] == []


def test_open_plan_auto_on_from_kitchen_then_off_when_empty(harness):
    harness["state"]["co2"] = "BASELINE"
    harness["state"]["presence_rooms"] = {"kitchen": True}
    _eval(40, False, _local(2026, 1, 15, 18, 0), dark=True)          # kitchen occupied + dark
    assert (40, True, "auto_on_comfort") in harness["published"]
    harness["published"].clear()
    lo._memo.clear()                                                # new tick: re-read presence
    harness["state"]["presence_rooms"] = {"kitchen": False, "living_room": False}
    harness["state"]["since"] = _local(2026, 1, 15, 13, 30)
    _eval(40, True, _local(2026, 1, 15, 18, 0), dark=True)
    assert (40, False, "vacancy_off") in harness["published"]


def test_motion_auto_on_suppressed_when_not_dark(harness):
    # A WINDOWED room light (hall_down 35) with no lux reading is dark-gated.
    harness["state"]["presence_rooms"] = {"hall_down": True}
    _eval(35, False, _local(2026, 6, 15, 13, 0), dark=False)
    assert harness["published"] == []


def test_windowless_wc_auto_ons_in_daylight(harness):
    # Downstairs WC lights are windowless; basement WC remains entirely manual.
    assert {44, 45} <= lo.WINDOWLESS_LIGHTS
    assert 52 not in lo.WINDOWLESS_LIGHTS
    harness["state"]["presence_rooms"] = {"wc_down": True}
    _eval(44, False, _local(2026, 6, 15, 13, 0), dark=False)
    assert (44, True, "auto_on_comfort") in harness["published"]


def test_autokatos_varasto_not_linked_to_indoor_khh_sensor(harness):
    # REGRESSION: 61 "Varasto" is the DETACHED autokatos (carport) storage — it must
    # NOT be driven by the indoor KHH PIR. It's manual-on utility: no room link, not
    # windowless-auto-on, and KHH occupancy never turns it on.
    assert lo.LIGHT_ROOM.get(61) is None
    assert 61 not in lo.WINDOWLESS_LIGHTS
    assert lo.CATEGORY_OF[61] == "utility"
    assert lo.CATS["utility"].auto_on is False
    harness["state"]["presence_rooms"] = {"khh": True}   # someone in the utility room
    _eval(61, False, _local(2026, 1, 15, 18, 0), dark=True)
    assert harness["published"] == []                    # carport store stays off


def test_aula_led_not_auto_on_by_hall_sensor(harness):
    # Only the aula kattovalo (26) auto-ons from the upstairs-hall PIR; the aula LED
    # (3) is manual-on secondary, fully unlinked from the sensor.
    assert lo.CATEGORY_OF[3] == "secondary"
    assert lo.CATS["secondary"].auto_on is False
    assert lo.LIGHT_ROOM.get(3) is None
    harness["state"]["presence_rooms"] = {"hall_up": True}
    _eval(3, False, _local(2026, 1, 15, 18, 0), dark=True)
    assert harness["published"] == []


def test_aula_kattovalo_auto_ons_by_hall_sensor(harness):
    assert lo.CATEGORY_OF[26] == "circulation"
    assert lo.LIGHT_ROOM[26] == "hall_up"
    harness["state"]["presence_rooms"] = {"hall_up": True}
    _eval(26, False, _local(2026, 1, 15, 18, 0), dark=True)
    assert (26, True, "auto_on_comfort") in harness["published"]


def test_khh_ceiling_does_not_auto_on(harness):
    # Only the KHH LED (6) auto-ons; the ceiling (56) is secondary (manual-on).
    assert lo.CATEGORY_OF[56] == "secondary"
    assert lo.CATS["secondary"].auto_on is False
    harness["state"]["presence_rooms"] = {"khh": True}
    _eval(56, False, _local(2026, 1, 15, 2, 0), dark=True)  # dark + occupied
    assert harness["published"] == []


def test_khh_led_auto_ons_when_dark(harness):
    harness["state"]["presence_rooms"] = {"khh": True}
    _eval(6, False, _local(2026, 1, 15, 2, 0), dark=True)
    assert (6, True, "auto_on_comfort") in harness["published"]


@pytest.mark.parametrize("lux", [17, 18, 39])
def test_khh_morning_motion_lights_dim_room_after_sunrise(harness, lux):
    # Actual morning readings were 17–18 lux (up to 39 with lamps on). The
    # old 12-lux override suppressed auto-on once the sun crossed 8 degrees.
    harness["state"]["presence_rooms"] = {"khh": True}
    harness["state"]["lux"] = {"khh": lux}
    _eval(6, False, _local(2026, 9, 26, 8, 34), dark=False)
    assert (6, True, "auto_on_comfort") in harness["published"]


def test_khh_genuinely_bright_room_stays_off(harness):
    harness["state"]["presence_rooms"] = {"khh": True}
    harness["state"]["lux"] = {"khh": 60}
    _eval(6, False, _local(2026, 9, 26, 10), dark=False)
    assert harness["published"] == []


@pytest.mark.parametrize("idx", [6, 26, 35, 44, 54])
@pytest.mark.parametrize("hour", [2, 14])
def test_sensor_room_unknown_cannot_be_cut_by_a_timer(harness, idx, hour):
    harness["state"]["presence"] = None
    harness["state"]["since"] = _local(2026, 9, 24, 20)
    _eval(idx, True, _local(2026, 9, 26, hour))
    assert harness["published"] == []


def test_portaikko_not_driven_by_hall_down_sensor():
    # The hall_down PIR is nowhere near the portaikko (42) — must stay unmapped.
    assert lo.LIGHT_ROOM.get(42) != "hall_down"


def test_bright_enough_culls_our_auto_on(harness):
    # A light WE auto-on'd, room now above ROOM_BRIGHT_LUX (sun out) → turn off.
    harness["state"]["origin"] = "optimizer"
    harness["state"]["presence_rooms"] = {"living_room": True}
    harness["state"]["lux"] = {"living_room": lo.ROOM_BRIGHT_LUX["living_room"] + 50}
    _eval(54, True, _local(2026, 6, 15, 15, 0), dark=False)
    assert (54, False, "bright_enough") in harness["published"]


def test_bright_enough_respects_manual_on(harness):
    # Same brightness, but a human turned it on → held, not culled.
    harness["state"]["origin"] = "wall"
    harness["state"]["presence_rooms"] = {"living_room": True}
    harness["state"]["lux"] = {"living_room": lo.ROOM_BRIGHT_LUX["living_room"] + 50}
    _eval(54, True, _local(2026, 6, 15, 15, 0), dark=False)
    assert (54, False, "bright_enough") not in harness["published"]


def test_bright_enough_only_listed_rooms(harness):
    # KHH is not in ROOM_BRIGHT_LUX (its light dominates the sensor) → no bright cull.
    assert "khh" not in lo.ROOM_BRIGHT_LUX
    harness["state"]["origin"] = "optimizer"
    harness["state"]["presence_rooms"] = {"khh": True}
    harness["state"]["lux"] = {"khh": 500}
    _eval(6, True, _local(2026, 6, 15, 15, 0), dark=False)
    assert (6, False, "bright_enough") not in harness["published"]


def test_windowed_khh_light_stays_daylight_gated(harness):
    # KHH room light (6) has a window → stays suppressed in daylight, same PIR.
    assert 6 not in lo.WINDOWLESS_LIGHTS
    harness["state"]["presence_rooms"] = {"khh": True}
    _eval(6, False, _local(2026, 6, 15, 13, 0), dark=False)
    assert harness["published"] == []


def test_measured_dim_room_auto_ons_before_dusk(harness):
    # Overcast afternoon: sun up (dark=False) but the living-room sensor reads
    # below DARK_LUX_THRESHOLD and someone is present → auto-on.
    harness["state"]["presence_rooms"] = {"living_room": True}
    harness["state"]["lux"] = {"living_room": lo.ROOM_DARK_LUX["living_room"] - 30}
    _eval(54, False, _local(2026, 6, 15, 15, 0), dark=False)
    assert (54, True, "auto_on_comfort") in harness["published"]


def test_bright_room_stays_off_in_daylight(harness):
    # Same room, but bright (above its per-room threshold) → no auto-on.
    harness["state"]["presence_rooms"] = {"living_room": True}
    harness["state"]["lux"] = {"living_room": lo.ROOM_DARK_LUX["living_room"] + 100}
    _eval(54, False, _local(2026, 6, 15, 15, 0), dark=False)
    assert harness["published"] == []


def test_daylight_shutoff_does_not_relight_until_the_dark_threshold():
    is_on = False
    decisions = []
    for lux in (60, 120, 250, 150, 90, 60):
        decision, reason = lo.decide_room_light(
            is_on=is_on, occupied=True, auto_on=True, dim=lux < 80,
            bright=lux > 200, manual_on=False, dismissed=False, on_minutes=10)
        decisions.append((decision, reason))
        if decision in ("on", "off"):
            is_on = decision == "on"
    assert decisions == [
        ("on", "auto_on_comfort"), ("hold", "no_off_rule"),
        ("off", "bright_enough"), ("hold", "not_dark"),
        ("hold", "not_dark"), ("on", "auto_on_comfort"),
    ]


@pytest.mark.parametrize("occupied,bright", [(False, False), (True, True)])
def test_fresh_on_is_protected_while_presence_and_light_readings_catch_up(occupied, bright):
    assert lo.decide_room_light(
        is_on=True, occupied=occupied, auto_on=True, dim=False, bright=bright,
        manual_on=False, dismissed=False, on_minutes=0.5)[0] == "hold"


def test_living_manual_secondary_shares_kitchen_occupancy(harness):
    harness["state"]["presence_rooms"] = {"kitchen": True, "living_room": False}
    _eval(5, True, _local(2026, 9, 26, 14))
    assert harness["published"] == []


@pytest.mark.parametrize("idx", [54, 19, 26, 6, 44, 45, 29, 34])
@pytest.mark.parametrize("origin", ["wall", "human"])
def test_manual_on_is_not_reversed_by_a_vacant_sensor_after_two_minutes(harness, idx, origin):
    # A person corrects an automatic OFF, but the sensor still misses them.
    # The generic 90-second initial ON floor must not be the only protection.
    now = _local(2026, 9, 26, 14)
    harness["state"].update(since=now - timedelta(minutes=2), origin=origin,
                            presence=False, lux={"living_room": 500})
    _eval(idx, True, now, dark=False)
    assert harness["published"] == []
    assert harness["decisions"][-1][1:] == ("hold", "manual_hold")


@pytest.mark.parametrize("elapsed_minutes,occupied,expected", [
    (9.99, False, "hold"), (10, False, "off"), (11, False, "off"),
    (11, True, "hold"), (11, None, "hold"),
])
def test_manual_toilet_on_has_ten_minute_minimum_then_uses_occupancy(
        harness, elapsed_minutes, occupied, expected):
    now = _local(2026, 9, 26, 14)
    harness["state"].update(since=now - timedelta(minutes=elapsed_minutes),
                            origin="wall", presence=occupied)
    _eval(44, True, now)
    assert harness["decisions"][-1][1] == expected
    assert harness["published"] == ([(44, False, "vacancy_off")] if expected == "off" else [])


def test_automatic_toilet_on_does_not_get_the_manual_override(harness):
    now = _local(2026, 9, 26, 14)
    harness["state"].update(since=now - timedelta(minutes=2), origin="optimizer",
                            presence=False)
    _eval(44, True, now)
    assert harness["published"] == [(44, False, "vacancy_off")]


@pytest.mark.parametrize("idx", [idx for idx, (_, floor) in lo.LIGHT_LABELS.items() if floor == 0])
@pytest.mark.parametrize("hour,is_on", [(14, False), (14, True), (19, True), (20, True),
                                      (23, True), (2, True)])
def test_basement_uses_only_overnight_shutoff_not_upstairs_sensors(harness, monkeypatch, idx, hour, is_on):
    # Long basement workdays must not inherit upstairs motion or time limits.
    assert idx not in lo.LIGHT_ROOM
    harness["state"].update(since=_local(2026, 9, 24, 8), presence=False,
                            lux={"living_room": 500})
    def no_presence_query(_):
        raise AssertionError("Basement must not consult room sensors")
    monkeypatch.setattr(lo, "presence_for_light", no_presence_query)
    _eval(idx, is_on, _local(2026, 9, 26, hour), dark=hour != 14)
    cutoff_active = hour < 6 or (hour >= 20 and idx in (49, 50, 53))
    expected = [(idx, False, "overnight_off")] if cutoff_active and is_on else []
    assert harness["published"] == expected


@pytest.mark.parametrize("idx", [49, 50, 53])
def test_basement_light_turned_back_on_after_overnight_off_stays_on(harness, idx):
    # The user rejects the overnight OFF to continue working. Do not repeat it.
    harness["state"].update(since=_local(2026, 9, 26, 20, 5), origin="wall",
                            presence=False)
    for day, hour in ((26, 21), (26, 23), (27, 1), (27, 3), (27, 5)):
        _eval(idx, True, _local(2026, 9, day, hour))
    assert harness["published"] == []
    # If forgotten the following day, the next evening's cutoff still applies.
    _eval(idx, True, _local(2026, 9, 27, 20))
    assert harness["published"] == [(idx, False, "overnight_off")]


@pytest.mark.parametrize("idx", [51, 52])
def test_billiard_and_basement_wc_keep_later_cutoff_and_respect_manual_return(harness, idx):
    harness["state"].update(since=_local(2026, 9, 26, 18), origin="wall", presence=False)
    for now in (_local(2026, 9, 26, 20), _local(2026, 9, 26, 23),
                _local(2026, 9, 27, 0, 29)):
        _eval(idx, True, now)
    assert harness["published"] == []
    _eval(idx, True, _local(2026, 9, 27, 0, 30))
    assert harness["published"] == [(idx, False, "overnight_off")]
    harness["published"].clear()
    harness["state"]["since"] = _local(2026, 9, 27, 0, 35)
    for hour in (1, 3, 5):
        _eval(idx, True, _local(2026, 9, 27, hour))
    assert harness["published"] == []


@pytest.mark.parametrize("idx", [2, 3, 7])
def test_basement_evening_cutoff_does_not_apply_to_other_floors(harness, idx):
    _eval(idx, True, _local(2026, 9, 26, 20))
    assert harness["published"] == []
