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
