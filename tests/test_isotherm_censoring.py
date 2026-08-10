"""Censored isotherm observations must never be logged as measurements (ADR-048).

This is the test for a bug that produced no error, no warning and no anomalous-looking output for
a week: 30 of the first 35 forecast-gate rows recorded obs_iso_m = 3.00, which was not an observed
isotherm depth but the depth of the shallowest thermistor on a chain whose whole column was colder
than the 12 C target. The map showed a +/- 1.4 m "measured" band computed from those rows.
"""
from __future__ import annotations

from tbay_fishcast.features.cross_shore import isotherm_crossing, isotherm_depth

# The real LLO1 (Duluth) profile at 2026-08-09T12:00Z, from GLOS ERDDAP. Its whole 3-38 m column
# is 4.2-6.6 C in August, so the 12 C isotherm is somewhere above the top sensor — unobservable
# by this chain.
LLO1_Z = [3.0, 6.0, 8.0, 13.0, 18.0, 23.0, 28.0, 33.0, 38.0]
LLO1_T = [6.62, 5.89, 5.16, 4.94, 4.71, 4.59, 4.48, 4.36, 4.20]
# The real 45216 (Ontonagon) profile the same day: 17.9-19.1 C over 2-12 m, entirely too warm.
ONT_Z = [2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0]
ONT_T = [19.07, 19.06, 19.03, 19.06, 19.02, 18.70, 17.94]


def test_a_column_colder_than_the_target_is_reported_as_censored():
    """THE BUG. isotherm_depth returns 3.0 here — the top sensor — and that is deliberate for the
    map ('the cold water reaches the surface'). What was missing is anything SAYING so."""
    depth, status = isotherm_crossing(LLO1_Z, LLO1_T, 12.0)
    assert status == "above_top"
    assert depth == 3.0 == LLO1_Z[0], "the returned value is the top sensor, not an isotherm"
    assert isotherm_depth(LLO1_Z, LLO1_T, 12.0) == 3.0, "map behaviour is unchanged"


def test_a_column_warmer_than_the_target_is_also_censored():
    """The asymmetry that hid the bug for a week: this direction already returned None and was
    skipped, so only the too-COLD direction leaked into the log as a number."""
    depth, status = isotherm_crossing(ONT_Z, ONT_T, 12.0)
    assert status == "below_bottom" and depth is None


def test_a_real_crossing_is_reported_as_a_measurement():
    z = [0.0, 5.0, 10.0, 15.0]
    t = [18.0, 15.0, 11.0, 8.0]
    depth, status = isotherm_crossing(z, t, 12.0)
    assert status == "crossing"
    assert abs(depth - 8.75) < 1e-9, "linear between 15 C at 5 m and 11 C at 10 m"


def test_a_sensor_exactly_on_target_is_a_crossing_not_a_bound():
    z = [0.0, 5.0, 10.0]
    t = [18.0, 12.0, 8.0]
    assert isotherm_crossing(z, t, 12.0) == (5.0, "crossing")


def test_status_and_depth_never_disagree():
    """A caller that keeps only status == 'crossing' must never be handed a None depth, and a
    censored status must never be handed a depth it could mistake for a measurement."""
    for z, t in ((LLO1_Z, LLO1_T), (ONT_Z, ONT_T), ([0.0, 5.0], [18.0, 8.0]), ([], [])):
        d, s = isotherm_crossing(z, t, 12.0)
        assert s in ("crossing", "above_top", "below_bottom")
        if s == "crossing":
            assert d is not None
        if s == "below_bottom":
            assert d is None


def test_isotherm_depth_still_matches_isotherm_crossing():
    """isotherm_depth is now a thin wrapper; if the two ever diverge, the map and the gate would
    be reading different physics from the same profile."""
    for z, t in ((LLO1_Z, LLO1_T), (ONT_Z, ONT_T), ([0.0, 5.0, 10.0], [18.0, 15.0, 8.0])):
        for target in (4.0, 8.0, 12.0, 20.0):
            for colder in (True, False):
                assert isotherm_depth(z, t, target, colder) == \
                    isotherm_crossing(z, t, target, colder)[0]


# --- the analyzer's quarantine of rows written BEFORE the guard existed -------------------

def _run_analyzer(tmp_path, rows, monkeypatch):
    import csv
    import importlib
    import json
    mod = importlib.import_module("scripts.analyze_forecast_error") if False else None
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "scripts"))
    import analyze_forecast_error as afe
    log = tmp_path / "gate.csv"
    out = tmp_path / "out.json"
    cols = ["valid_date", "chain", "lead_h", "issue_date", "obs_iso_m", "fcst_corr_iso_m",
            "abs_err_m", "persist_iso_m", "persist_abs_err_m", "obs_status"]
    with log.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    monkeypatch.setattr(afe, "LOG", log)
    monkeypatch.setattr(afe, "OUT", out)
    monkeypatch.setattr(afe, "ROOT", tmp_path)
    afe.main()
    return json.loads(out.read_text())


def _row(day, chain, lead, obs, err, status=""):
    return {"valid_date": day, "chain": chain, "lead_h": str(lead), "issue_date": day,
            "obs_iso_m": obs, "fcst_corr_iso_m": "2.5", "abs_err_m": str(err),
            "persist_iso_m": "2.0", "persist_abs_err_m": "1.0", "obs_status": status}


def test_a_chain_pinned_across_several_days_is_quarantined(tmp_path, monkeypatch):
    """LLO1's signature: the same observed isotherm depth, to the centimetre, on every day."""
    rows = [_row(f"2026-08-0{d}", "llo1", L, "3.0", 0.4)
            for d in range(4, 10) for L in (24, 48, 72, 96, 120)]
    res = _run_analyzer(tmp_path, rows, monkeypatch)
    assert res["quarantined_chains"] == ["llo1"]
    assert res["n"] == 0
    assert res["pooled_mae_m"] is None, "no band may be published from quarantined rows"


def test_one_logged_day_is_not_evidence_of_pinning(tmp_path, monkeypatch):
    """The first version of the rule tested constancy across ROWS, so a chain with a single valid
    date — whose five lead rows necessarily share one observation — was quarantined for having a
    constant reference. That is the rule reaching past its evidence."""
    rows = [_row("2026-08-04", "45216", L, "8.25", 3.5) for L in (24, 48, 72, 96, 120)]
    res = _run_analyzer(tmp_path, rows, monkeypatch)
    assert res["quarantined_chains"] == []
    assert res["n"] == 5


def test_rows_written_after_the_guard_are_never_quarantined(tmp_path, monkeypatch):
    """obs_status == 'crossing' is set by the gate only for a genuine crossing, so a chain that
    legitimately sits at one depth for days must not be thrown away for being stable."""
    rows = [_row(f"2026-08-0{d}", "llo1", L, "3.0", 0.4, status="crossing")
            for d in range(4, 10) for L in (24, 48, 72, 96, 120)]
    res = _run_analyzer(tmp_path, rows, monkeypatch)
    assert res["quarantined_chains"] == []
    assert res["n"] == 30


def test_the_band_is_withheld_below_the_sample_bar(tmp_path, monkeypatch):
    rows = [_row("2026-08-04", "45216", L, "8.25", 3.5) for L in (24, 48, 72, 96, 120)]
    res = _run_analyzer(tmp_path, rows, monkeypatch)
    assert res["pooled_mae_m"] is None and res["pooled_p90_m"] is None
    assert "5 usable rows" in res["band_blocked_reason"]


def test_skill_is_withheld_when_the_reference_does_not_move(tmp_path, monkeypatch):
    """THE ADR-006 GUARD. A ratio below 1.0 against a constant reference is not skill, and would
    read as a passed demotion bar."""
    rows = [_row(f"2026-08-{d:02d}", "c1", L, "3.0", 0.4, status="crossing")
            for d in range(1, 8) for L in (24, 48, 72, 96, 120)]
    res = _run_analyzer(tmp_path, rows, monkeypatch)
    assert res["n_effective_chains"] == 0
    assert res["skill_vs_persistence"] is None
    assert "does not move" in res["skill_blocked_reason"]


def test_skill_is_reported_once_the_reference_moves(tmp_path, monkeypatch):
    rows = []
    for i, d in enumerate(range(1, 8)):
        for L in (24, 48, 72, 96, 120):
            rows.append(_row(f"2026-08-{d:02d}", "c1", L, str(3.0 + i), 0.4, status="crossing"))
    res = _run_analyzer(tmp_path, rows, monkeypatch)
    assert res["n_effective_chains"] == 1
    assert res["skill_blocked_reason"] is None
    s = res["skill_vs_persistence"]
    assert s and s["n_pairs"] == 35
    assert s["per_lead"]["24"]["beats_persistence"] is True   # 0.4 < 1.0
