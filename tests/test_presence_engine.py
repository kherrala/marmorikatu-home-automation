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


# ── _next_occupancy: per-type occupancy state transitions ─────────────────────
def test_mmwave_holds_and_emits_on_edges():
    # Rising edge: vacant → occupied, emit once.
    assert pe._next_occupancy("mmwave", False, True) == (True, True)
    # Already occupied, another positive report: stays occupied, no re-emit.
    assert pe._next_occupancy("mmwave", True, True) == (True, False)
    # Explicit falling edge: occupied → vacant, emit.
    assert pe._next_occupancy("mmwave", True, False) == (False, True)
    # Falling edge while already vacant: no change, no emit.
    assert pe._next_occupancy("mmwave", False, False) == (False, False)


def test_pir_latches_on_motion_and_ignores_false():
    # Motion latches occupancy.
    assert pe._next_occupancy("pir", False, True) == (True, True)
    # PIR "false" is the gap between re-triggers — ignored; the linger clears it.
    assert pe._next_occupancy("pir", True, False) == (True, False)


def test_battery_only_report_never_changes_occupancy():
    # pos=None (no occupancy field) must not flip either sensor type.
    assert pe._next_occupancy("mmwave", True, None) == (True, False)
    assert pe._next_occupancy("pir", True, None) == (True, False)
    assert pe._next_occupancy("mmwave", False, None) == (False, False)
