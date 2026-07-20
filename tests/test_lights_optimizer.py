"""Unit tests for scripts/lights_optimizer.py (v2 — comfort-first, provenance).

The decision engine reads InfluxDB via a handful of module-level helper
functions; tests monkeypatch those to drive `evaluate_light` deterministically
and capture the resulting publishes / decision-log rows. (scripts/ is put on
the path by tests/conftest.py.)
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import lights_optimizer as lo

TZ = ZoneInfo("Europe/Helsinki")


def _local(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


# ── Coverage / config invariants ──────────────────────────────────────────────
def test_every_light_index_is_categorized_or_special():
    from light_labels import LIGHT_LABELS
    covered = set(lo.CATEGORY_OF) | set(lo.SPECIAL_IDX)
    assert covered == set(LIGHT_LABELS), covered.symmetric_difference(set(LIGHT_LABELS))


def test_every_category_has_a_behaviour():
    for cat in set(lo.CATEGORY_OF.values()):
        assert cat in lo.CATS


def test_comfort_first_invariants():
    assert lo.CATS["living"].daylight_off is False
    assert lo.CATS["office"].overnight_off is False
    assert lo.CATS["theater"].overnight_off is False
    assert lo.CATS["office"].daylight_off is False


# ── Overnight window ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("h,m,expected", [
    (0, 0, False), (0, 30, True), (3, 0, True), (5, 59, True),
    (6, 0, False), (12, 0, False), (23, 0, False),
])
def test_overnight_window(h, m, expected):
    assert lo.in_overnight_window(_local(2026, 1, 15, h, m)) is expected


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


# ── co2_signal_class ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("recent,base,expected", [
    (700, 500, "ELEVATED"),
    (560, 500, "ELEVATED"),
    (430, 600, "DROPPED"),
    (500, 650, "DROPPED"),
    (520, 515, "BASELINE"),
    (None, 500, "UNKNOWN"),
])
def test_co2_signal_class(monkeypatch, recent, base, expected):
    calls = {"n": 0}

    def fake_query(flux):
        calls["n"] += 1
        val = recent if calls["n"] == 1 else base
        if val is None:
            return []
        return [type("R", (), {"get_value": lambda self, v=val: v})()]

    lo._memo.clear()
    monkeypatch.setattr(lo, "_query", fake_query)
    assert lo.co2_signal_class() == expected


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


# ── evaluate_light decision engine ────────────────────────────────────────────
@pytest.fixture
def harness(monkeypatch):
    published: list[tuple] = []
    decisions: list[tuple] = []
    state = {
        "since": datetime.now(timezone.utc) - timedelta(hours=2),
        "origin": "wall",
        "presence": None,        # default per-room presence (None|True|False)
        "presence_rooms": {},    # per-room override: {room: True|False|None}
        "co2": "BASELINE",
        "dwell": False,
    }
    monkeypatch.setattr(lo, "fetch_last_transition", lambda idx: (True, state["since"]))
    monkeypatch.setattr(lo, "classify_origin", lambda idx, is_on, since: state["origin"])
    monkeypatch.setattr(lo, "_presence_for_room_uncached",
                        lambda room: state["presence_rooms"].get(room, state["presence"]))
    monkeypatch.setattr(lo, "_co2_signal_class_uncached", lambda: state["co2"])
    monkeypatch.setattr(lo, "within_min_dwell", lambda idx: state["dwell"])
    monkeypatch.setattr(lo, "publish_state",
                        lambda idx, on, reason: (published.append((idx, on, reason)) or True))
    monkeypatch.setattr(lo, "log_decision",
                        lambda idx, decision, reason, category="", manual_locked=False, on_dur=None:
                        decisions.append((idx, decision, reason)))
    lo._memo.clear()
    lo._dismissed_date.clear()
    return {"published": published, "decisions": decisions, "state": state}


def _eval(idx, is_on, now, away=False, dark=True):
    sr = _local(now.year, now.month, now.day, 6, 0)
    ss = _local(now.year, now.month, now.day, 21, 0)
    lo.evaluate_light(idx, is_on, now, sr, ss, is_dark=dark, away=away)


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
    harness["state"]["since"] = datetime.now(timezone.utc) - timedelta(minutes=30)
    _eval(54, True, _local(2026, 1, 15, 17, 0), dark=False)
    assert (54, False, "vacancy_off") in harness["published"]


def test_window_daylight_off(harness):
    _eval(46, True, _local(2026, 6, 15, 13, 0), dark=False)
    assert (46, False, "daylight_off") in harness["published"]


def test_toilet_duration_cap(harness):
    harness["state"]["since"] = datetime.now(timezone.utc) - timedelta(minutes=90)
    _eval(44, True, _local(2026, 1, 15, 14, 0))
    assert (44, False, "duration_cap") in harness["published"]


def test_whole_house_away_turns_off_living(harness):
    _eval(54, True, _local(2026, 1, 15, 14, 0), away=True)
    assert (54, False, "away_off") in harness["published"]


def test_living_auto_on_when_dark_and_occupied(harness):
    harness["state"]["presence"] = True
    _eval(54, False, _local(2026, 1, 15, 18, 0), dark=True)
    assert (54, True, "auto_on_comfort") in harness["published"]


def test_no_auto_on_when_not_dark(harness):
    harness["state"]["presence"] = True
    _eval(54, False, _local(2026, 6, 15, 13, 0), dark=False)
    assert harness["published"] == []


def test_dismissed_today_suppresses_auto_on(harness):
    harness["state"]["presence"] = True
    lo._dismissed_date[54] = _local(2026, 1, 15, 18, 0).date()
    _eval(54, False, _local(2026, 1, 15, 18, 0), dark=True)
    assert harness["published"] == []
    assert harness["decisions"][-1][1:] == ("hold", "dismissed_today")


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
    # mmWave present + dark, but theater must not relight (movie mood is manual).
    harness["state"]["presence_rooms"] = {"theater": True}
    _eval(49, False, _local(2026, 1, 15, 20, 0), dark=True)
    assert harness["published"] == []


def test_living_room_fp300_vacancy_does_not_kill_kitchen(harness):
    # FP300 (living_room) reads vacant, but the kitchen (idx 40, room=living_core,
    # no sensor) must NOT be turned off by it.
    harness["state"]["presence_rooms"] = {"living_room": False}  # living_core → None
    harness["state"]["since"] = datetime.now(timezone.utc) - timedelta(minutes=30)
    _eval(40, True, _local(2026, 1, 15, 14, 0))
    assert harness["published"] == []
    # ...while the living-room ceiling (54, room=living_room) IS turned off.
    _eval(54, True, _local(2026, 1, 15, 14, 0))
    assert (54, False, "vacancy_off") in harness["published"]


def test_motion_auto_on_suppressed_when_not_dark(harness):
    harness["state"]["presence_rooms"] = {"wc_down": True}
    _eval(44, False, _local(2026, 6, 15, 13, 0), dark=False)
    assert harness["published"] == []
