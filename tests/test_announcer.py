"""Unit tests for scripts/announcer.py multi-light grouping helpers.

(scripts/ is on the path via tests/conftest.py.)
"""
from datetime import datetime, timezone

import announcer as a


def _row(idx, name, decision="off", reason="vacancy_off"):
    return {"decision": decision, "reason": reason, "light_name": name,
            "light_id": str(idx), "ts": datetime.now(timezone.utc)}


# ── _join_fi: Finnish list join ───────────────────────────────────────────────
def test_join_fi_forms():
    assert a._join_fi(["Sauna"]) == "Sauna"
    assert a._join_fi(["Sauna", "Kylpyhuone"]) == "Sauna ja Kylpyhuone"
    assert a._join_fi(["A", "B", "C"]) == "A, B ja C"


def test_join_fi_drops_empty_and_defaults():
    assert a._join_fi([None, "A", ""]) == "A"
    assert a._join_fi([]) == "Valot"


# ── _group_key: what merges vs stays single ───────────────────────────────────
def test_group_key_groupable_reasons():
    assert a._group_key(_row(54, "x", "off", "vacancy_off")) == ("off", "vacancy_off")
    assert a._group_key(_row(1, "x", "off", "post_sauna_cleanup")) == ("off", "post_sauna")
    assert a._group_key(_row(54, "x", "on", "auto_on_comfort")) == ("on", "auto_on_comfort")


def test_group_key_single_light_specials_not_grouped():
    assert a._group_key(_row(47, "x", "on", "porch_detection")) is None
    assert a._group_key(_row(4, "x", "on", "sauna_heated")) is None
    assert a._group_key(_row(4, "x", "off", "sauna_cooled")) is None
    # non-actionable decisions never group
    assert a._group_key(_row(54, "x", "hold", "no_off_rule")) is None


# ── _format_lights_group: merged announcement text ────────────────────────────
def test_post_sauna_group_merges_and_sorts_by_id():
    rows = [_row(39, "Tekninen tila", "off", "post_sauna"),
            _row(1, "Kylpyhuone alakerta", "off", "post_sauna"),
            _row(38, "Sauna siivousvalo", "off", "post_sauna")]
    ev = a._format_lights_group(rows)
    assert ev.text == ("Kylpyhuone alakerta, Sauna siivousvalo ja Tekninen tila "
                       "sammutettiin saunavuoron päätteeksi.")
    assert ev.key == "lights_opt_post_sauna_grp:1-38-39"
    assert ev.kind == "lights_opt_post_sauna"


def test_vacancy_group_uses_passive_verb():
    rows = [_row(54, "Olohuone kattovalo", "off", "vacancy_off"),
            _row(19, "Ruokailu", "off", "vacancy_off")]
    ev = a._format_lights_group(rows)
    assert ev.text == "Ruokailu ja Olohuone kattovalo sammutettiin — huone on tyhjä."


def test_auto_on_group_uses_plural_verb():
    rows = [_row(54, "Olohuone kattovalo", "on", "auto_on_comfort"),
            _row(55, "Olohuone kattovalo 2", "on", "auto_on_comfort")]
    ev = a._format_lights_group(rows)
    assert "syttyivät automaattisesti" in ev.text
    assert ev.text.startswith("Olohuone kattovalo ja Olohuone kattovalo 2")


# ── _raw_light_group_event: group simultaneous manual/wall flips ──────────────
def _ts(sec):
    from datetime import datetime, timezone, timedelta
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=sec)


def test_raw_single_light_uses_singular_verb():
    ev = a._raw_light_group_event([(1, _ts(0))], turned_on=False)
    assert ev.text == "Kylpyhuone alakerta sammui."
    ev_on = a._raw_light_group_event([(1, _ts(0))], turned_on=True)
    assert ev_on.text == "Kylpyhuone alakerta syttyi."


def test_raw_multi_light_groups_with_plural_verb():
    # idx 1 Kylpyhuone alakerta, 38 Sauna siivousvalo, 4 Saunan laude ledi
    ev = a._raw_light_group_event([(38, _ts(0)), (1, _ts(0)), (4, _ts(0))],
                                  turned_on=False)
    # sorted by idx: 1, 4, 38
    assert ev.text.endswith("sammuivat.")
    assert ev.text.startswith("Kylpyhuone alakerta, Saunan laude ledi ja Sauna siivousvalo")
    assert ev.key == "light_off:1-4-38"


def test_raw_group_empty_is_none():
    assert a._raw_light_group_event([], turned_on=False) is None


# ── _alarm_should_emit: repeat-while-critical vs rising-edge ───────────────────
def test_critical_alarm_repeats_while_active():
    # prio 0 emits every time it's active (cooldown paces the repeat)…
    assert a._alarm_should_emit(0, active=True, prev_active=True) is True
    assert a._alarm_should_emit(0, active=True, prev_active=False) is True
    # …and never when inactive.
    assert a._alarm_should_emit(0, active=False, prev_active=True) is False


def test_warn_alarm_fires_once_on_rising_edge():
    assert a._alarm_should_emit(1, active=True, prev_active=False) is True   # rising
    assert a._alarm_should_emit(1, active=True, prev_active=True) is False   # still on
    assert a._alarm_should_emit(1, active=False, prev_active=True) is False  # cleared


# ── _battery_low: temperature-compensated CR2477 threshold ────────────────────
def test_battery_low_temp_compensated():
    assert a._battery_low(2.45, 22.0) is True    # room temp, < 2.5
    assert a._battery_low(2.55, 22.0) is False    # room temp, healthy
    assert a._battery_low(2.35, -18.0) is False   # freezer: sag is normal (thr 2.3)
    assert a._battery_low(2.25, -18.0) is True     # freezer: genuinely low
    assert a._battery_low(2.05, -25.0) is False   # deep cold: thr 2.0
    assert a._battery_low(None, 20.0) is False    # no voltage → not low


# ── _iv_boost_transition: MVHR humidity-boost enter/leave ─────────────────────
def test_iv_boost_transition():
    assert a._iv_boost_transition(1, 2, boost_mode=2) == "on"    # normal → boost
    assert a._iv_boost_transition(2, 1, boost_mode=2) == "off"   # boost → normal
    assert a._iv_boost_transition(1, 1, boost_mode=2) is None    # no change
    assert a._iv_boost_transition(2, 2, boost_mode=2) is None    # still boosting
    assert a._iv_boost_transition(None, 2, boost_mode=2) is None  # first reading, no edge
    # a change that doesn't cross the boost value is not a boost transition
    assert a._iv_boost_transition(1, 3, boost_mode=2) is None
