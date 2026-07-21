"""Unit tests for scripts/presence_engine.py normalization helpers.

(scripts/ is on the path via tests/conftest.py.)
"""
import presence_engine as pe


# ── _positive: is this a positive occupancy/motion signal? ────────────────────
def test_positive_occupancy_bool():
    assert pe._positive({"occupancy": True}) is True
    assert pe._positive({"occupancy": False}) is False


def test_positive_presence_key():
    assert pe._positive({"presence": True}) is True
    assert pe._positive({"presence": False}) is False


def test_positive_string_and_int_forms():
    assert pe._positive({"occupancy": "true"}) is True
    assert pe._positive({"occupancy": "detected"}) is True
    assert pe._positive({"occupancy": 1}) is True
    assert pe._positive({"occupancy": 0}) is False


def test_positive_none_when_no_occupancy_field():
    # A battery/illuminance-only report carries no occupancy signal.
    assert pe._positive({"battery": 96, "illuminance": 20}) is None


# ── _num: first present numeric field ─────────────────────────────────────────
def test_num_prefers_first_key():
    assert pe._num({"illuminance_lux": 40, "illuminance": 5},
                   "illuminance_lux", "illuminance") == 40


def test_num_falls_through():
    assert pe._num({"illuminance": 5}, "illuminance_lux", "illuminance") == 5


def test_num_none_when_absent_or_nonnumeric():
    assert pe._num({}, "battery") is None
    assert pe._num({"battery": "low"}, "battery") is None


# ── config type defaults ──────────────────────────────────────────────────────
def test_type_defaults_present():
    assert pe.TYPE_DEFAULTS["mmwave"]["confidence"] >= 0.9
    # mmWave is held-until-falling-edge, so its linger is a long dead-sensor
    # failsafe that must exceed the PIR bridge timer (inverted from the old
    # short-mmwave-linger model that flapped the FP300 to vacant).
    assert pe.TYPE_DEFAULTS["mmwave"]["linger_s"] >= pe.TYPE_DEFAULTS["pir"]["linger_s"]


# ── _tick_vacancy: per-tick maintenance decision ──────────────────────────────
# Signature: (occupied, pending_since, last_positive, last_emit, now,
#             confirm_s, linger_s, heartbeat_s)
NOW = 10_000.0
CONFIRM, LINGER, HEARTBEAT = 60.0, 900.0, 60.0


def test_confirmed_falling_edge_clears():
    # Occupied, a falling edge armed CONFIRM+ ago with no re-detect → clear.
    assert pe._tick_vacancy(True, NOW - 61, NOW - 61, NOW - 5,
                            NOW, CONFIRM, LINGER, HEARTBEAT) == "clear"


def test_pending_not_yet_confirmed_does_not_clear():
    # Falling edge armed only 30s ago (< CONFIRM) → not yet vacant.
    assert pe._tick_vacancy(True, NOW - 30, NOW - 30, NOW - 5,
                            NOW, CONFIRM, LINGER, HEARTBEAT) is None


def test_cancelled_pending_holds_via_heartbeat():
    # pending_since reset to 0 (a re-detect happened): stays occupied; only a
    # heartbeat is due since last_emit is stale.
    assert pe._tick_vacancy(True, 0.0, NOW - 5, NOW - 61,
                            NOW, CONFIRM, LINGER, HEARTBEAT) == "heartbeat"


def test_dead_sensor_failsafe_clears_after_linger():
    # No explicit false ever arrived, but no positive for > linger_s → clear.
    assert pe._tick_vacancy(True, 0.0, NOW - 901, NOW - 5,
                            NOW, CONFIRM, LINGER, HEARTBEAT) == "clear"


def test_heartbeat_when_fresh_but_emit_stale():
    assert pe._tick_vacancy(True, 0.0, NOW - 10, NOW - 61,
                            NOW, CONFIRM, LINGER, HEARTBEAT) == "heartbeat"


def test_nothing_to_do_when_fresh():
    assert pe._tick_vacancy(True, 0.0, NOW - 10, NOW - 5,
                            NOW, CONFIRM, LINGER, HEARTBEAT) is None
    # Vacant room with a recent emit: nothing to do.
    assert pe._tick_vacancy(False, 0.0, NOW - 10, NOW - 5,
                            NOW, CONFIRM, LINGER, HEARTBEAT) is None
