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
    assert pe.TYPE_DEFAULTS["pir"]["linger_s"] >= pe.TYPE_DEFAULTS["mmwave"]["linger_s"]
