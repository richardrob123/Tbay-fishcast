"""ADR-061 — the run triggers the calendar asserts, measured against this bay's own record.

The point of these tests is not the statistics (those live in the calib artifact); it is that the
PRODUCT must not narrate a mechanism nobody has measured. A rising river is an observation. "The
freshet pulls staging fish in" is a forecast, and this system had been shipping it as prose.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tbay_fishcast.features import river_flow as rf

ROOT = Path(__file__).resolve().parents[1]
CALIB = ROOT / "data" / "calib" / "run_trigger_skill.json"


def _rising():
    t = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
    return [(t - timedelta(days=3), 0.20), (t - timedelta(days=1), 0.40), (t, 0.60)]


def test_a_rising_river_is_still_reported_as_rising():
    """The measurement retires a CLAIM, not the observation. The hydrograph is real."""
    r = rf.classify(_rising())
    assert r.state == "rising" and r.freshet is True
    assert "rising" in r.note


def test_the_rising_note_does_not_promise_fish():
    """THE FIX. The shipped string said "freshet pulls staging fish in" — a causal claim about
    fish behaviour that ADR-061 could not find in 20 dated pink report days (flow on the report
    day ran 0.84x/0.93x the trailing week, i.e. slightly BELOW normal water)."""
    note = rf.classify(_rising()).note.lower()
    for banned in ("pulls staging fish", "pulls fish", "fish in"):
        assert banned not in note, f"unmeasured fish-response claim in the shipped note: {note!r}"
    assert "unmeasured" in note, "the note must say the run-trigger link is unmeasured"


def test_the_dataclass_comment_does_not_call_freshet_a_run_trigger():
    src = (ROOT / "src" / "tbay_fishcast" / "features" / "river_flow.py").read_text()
    line = next(ln for ln in src.splitlines() if ln.strip().startswith("freshet: bool"))
    assert "the staging/run trigger" not in line


def test_the_measurement_is_recorded_with_its_confound():
    """A null this confounded is only safe to act on while the confound travels with it."""
    if not CALIB.exists():
        pytest.skip("run_trigger_skill.json has not been built in this checkout")
    d = json.loads(CALIB.read_text())
    assert d["n_report_days"] >= 10
    assert "effort" in d["confound"].lower() and "iNaturalist" in d["confound"]
    # the thermal control has no effort confound, so it is the one allowed to stand alone
    t = d["thermal_control"]
    if t.get("p_null_tighter_or_equal") is not None:
        assert t["detected"] == (t["p_null_tighter_or_equal"] < 0.05)
    # and the headline may not claim support the controls did not give
    if not d["rain_trigger_supported"] and not d["thermal_trigger_supported"]:
        assert "NEITHER" in d["verdict"]


def test_report_days_are_deduplicated_to_the_day():
    """2023-09-14 carries six iNaturalist records that are ONE observer's afternoon. Counting the
    day six times would let a single outing set the median of every statistic."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from analyze_run_triggers import report_days
    rows = [{"species": "pink", "date_precision": "day", "date": "2023-09-14"}] * 6 + [
        {"species": "pink", "date_precision": "day", "date": "2023-09-15"},
        {"species": "pink", "date_precision": "year", "date": "2019-01-01"},   # undated -> dropped
        {"species": "pink", "date_precision": "day", "date": "2023-05-02"},    # out of season
        {"species": "lake_trout", "date_precision": "day", "date": "2023-09-14"},
    ]
    got = [d.isoformat() for d in report_days(rows)]
    assert got == ["2023-09-14", "2023-09-15"]
