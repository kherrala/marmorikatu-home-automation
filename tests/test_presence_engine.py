"""Unit tests for scripts/presence_engine.py normalization helpers.

(scripts/ is on the path via tests/conftest.py.)
"""
import json

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


# ── _vacancy_params: per-type confirm/failsafe wiring ─────────────────────────
def test_vacancy_params_pir_holds_until_false_then_linger():
    # PIR: linger_s is the post-falling-edge grace; last_positive guard is the long
    # stuck-sensor failsafe, NOT a presence cap.
    confirm_s, failsafe_s = pe._vacancy_params({"type": "pir", "linger_s": 90})
    assert confirm_s == 90.0
    assert failsafe_s == pe.PIR_DEAD_SENSOR_FAILSAFE_S
    assert failsafe_s >= 3600  # comfortably above any real continuous occupancy


def test_vacancy_params_mmwave_short_confirm_long_linger_failsafe():
    confirm_s, failsafe_s = pe._vacancy_params({"type": "mmwave", "linger_s": 7200})
    assert confirm_s == pe.FALLING_CONFIRM_S
    assert failsafe_s == 7200.0


def test_pir_continuous_presence_not_truncated_at_linger():
    # THE BUG this fix targets: a SNZB-03P holds `occupancy:true` from a single
    # rising edge and never re-reports it. 11 min later, still present (no device
    # `false` → pending stays 0). With PIR params, linger_s is the post-`false`
    # grace, so last_positive uses the long failsafe → the room must NOT clear.
    # (The old model passed linger_s as the last_positive timeout and wrongly
    # cleared a continuously-present person at 90 s.)
    confirm_s, failsafe_s = pe._vacancy_params({"type": "pir", "linger_s": 90})
    now = 100_000.0
    held_11_min = now - 11 * 60
    assert pe._tick_vacancy(True, 0.0, held_11_min, now - 5,
                            now, confirm_s, failsafe_s, pe.HEARTBEAT_S) != "clear"


def test_pir_clears_grace_after_device_false():
    # After the device's explicit `false` (pending armed), the room clears once the
    # linger_s grace elapses — even though last_positive is far inside the failsafe.
    confirm_s, failsafe_s = pe._vacancy_params({"type": "pir", "linger_s": 90})
    now = 100_000.0
    assert pe._tick_vacancy(True, now - 91, now - 200, now - 5,
                            now, confirm_s, failsafe_s, pe.HEARTBEAT_S) == "clear"
    # ...but not before the grace is up.
    assert pe._tick_vacancy(True, now - 30, now - 200, now - 5,
                            now, confirm_s, failsafe_s, pe.HEARTBEAT_S) != "clear"


# ── on_message: a PIR `false` is an authoritative falling edge, not noise ──────
class _FakeMsg:
    def __init__(self, friendly, payload):
        self.topic = f"{pe.Z2M_BASE}/{friendly}"
        self.payload = json.dumps(payload).encode()


def _setup_pir_room(monkeypatch):
    monkeypatch.setattr(pe, "_devices", {"snzb_test": "hall_test"})
    monkeypatch.setattr(pe, "_rooms",
                        {"hall_test": {"type": "pir", "linger_s": 90, "confidence": 0.85}})
    monkeypatch.setattr(pe, "_state", {})
    monkeypatch.setattr(pe, "emit_room", lambda room: None)   # no MQTT/Influx
    monkeypatch.setattr(pe, "touch_health", lambda: None)


def test_pir_false_arms_pending_falling_edge(monkeypatch):
    _setup_pir_room(monkeypatch)
    pe.on_message(None, None, _FakeMsg("snzb_test", {"occupancy": True}))
    st = pe._state["hall_test"]
    assert st["occupied"] is True and st["pending_vacant_since"] == 0.0
    # The device's explicit `false`: room stays HELD occupied, but a falling edge is
    # now armed (the tick clears it after the grace). Old model ignored PIR `false`.
    pe.on_message(None, None, _FakeMsg("snzb_test", {"occupancy": False}))
    assert st["occupied"] is True
    assert st["pending_vacant_since"] > 0.0
    assert st["sensor_occupied"] is False


def test_pir_redetect_cancels_pending(monkeypatch):
    _setup_pir_room(monkeypatch)
    pe.on_message(None, None, _FakeMsg("snzb_test", {"occupancy": True}))
    pe.on_message(None, None, _FakeMsg("snzb_test", {"occupancy": False}))
    assert pe._state["hall_test"]["pending_vacant_since"] > 0.0
    # A re-detect within the grace cancels the pending vacancy.
    pe.on_message(None, None, _FakeMsg("snzb_test", {"occupancy": True}))
    assert pe._state["hall_test"]["pending_vacant_since"] == 0.0
