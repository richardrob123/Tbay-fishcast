"""Tests for fitting run windows from the ledger (ADR-042).

The safety property here is EVIDENCE DISCIPLINE: a window may only move when enough local dated
reports across enough years justify it, the two steelhead runs must never merge, and a thin
ledger must leave the authored literature dates untouched."""
from __future__ import annotations

from tbay_fishcast.knowledge import fit_run_windows as frw


def _rows(species, dates, precision="day"):
    return [{"kind": "catch", "species": species, "date": d, "date_precision": precision}
            for d in dates]


CHINOOK_ENTRY = {"id": "chinook_staging", "species": "chinook",
                 "window": {"start": "08-25", "end": "09-15"}}
SPRING_STEEL = {"id": "spring_steelhead", "species": "steelhead",
                "window": {"start": "04-10", "end": "05-20"}}
FALL_STEEL = {"id": "fall_steelhead", "species": "steelhead",
              "window": {"start": "10-01", "end": "freeze_up"}}


def test_thin_evidence_never_moves_a_window():
    """4 reports in 1 year is not phenology — the authored window must stand."""
    rows = _rows("chinook", ["2024-09-01", "2024-09-03", "2024-09-05", "2024-09-07"])
    assert frw.fit_entry(CHINOOK_ENTRY, rows) is None


def test_fit_emitted_once_evidence_bar_is_met():
    dates = []
    for yr in (2022, 2023, 2024, 2025):
        dates += [f"{yr}-08-28", f"{yr}-09-01", f"{yr}-09-04", f"{yr}-09-08"]
    f = frw.fit_entry(CHINOOK_ENTRY, _rows("chinook", dates))
    assert f is not None
    assert f["n"] == 16 and f["n_years"] == 4
    assert f["authored_start"] == "08-25" and f["authored_end"] == "09-15"
    # the fitted window sits inside the reports' bulk, and is reported with its shift
    assert f["start"] >= "08-25" and f["end"] <= "09-15"
    assert isinstance(f["shift_days"], int)
    assert "effort-biased" in f["method"]


def test_spring_and_fall_steelhead_never_merge():
    """The failure mode this design exists to prevent: a naive pooled percentile over all
    steelhead reports would produce one April-to-December window. Anchoring each fit to its own
    authored window keeps the two runs separate."""
    spring, fall = [], []
    for yr in (2022, 2023, 2024, 2025):
        spring += [f"{yr}-04-18", f"{yr}-04-25", f"{yr}-05-02", f"{yr}-05-10"]
        fall += [f"{yr}-10-08", f"{yr}-10-18", f"{yr}-10-28", f"{yr}-11-06"]
    rows = _rows("steelhead", spring + fall)
    fs = frw.fit_entry(SPRING_STEEL, rows)
    ff = frw.fit_entry(FALL_STEEL, rows)
    assert fs and ff
    assert fs["start"][:2] == "04" and fs["end"][:2] in ("04", "05")
    assert ff["start"][:2] in ("10", "11") and ff["end"][:2] in ("10", "11", "12")
    assert fs["n"] == 16 and ff["n"] == 16      # each run saw only its own reports


def test_multi_year_requirement_blocks_one_big_season():
    """40 reports from a single season is a big year, not a measured window."""
    dates = [f"2024-09-{d:02d}" for d in range(1, 15)] * 3
    assert frw.fit_entry(CHINOOK_ENTRY, _rows("chinook", dates)) is None


def test_imprecise_dates_are_excluded_from_timing():
    """month/year-precision rows cannot time a run and must not count toward the bar."""
    dates = []
    for yr in (2022, 2023, 2024, 2025):
        dates += [f"{yr}-09-01", f"{yr}-09-05", f"{yr}-09-09", f"{yr}-09-12"]
    rows = _rows("chinook", dates, precision="month")
    assert frw.fit_entry(CHINOOK_ENTRY, rows) is None


def test_non_migratory_species_are_not_fitted():
    dates = [f"{yr}-07-{d:02d}" for yr in (2022, 2023, 2024) for d in (5, 10, 15, 20, 25)]
    entry = {"id": "laker", "species": "lake_trout", "window": {"start": "06-01", "end": "09-30"}}
    assert frw.fit_entry(entry, _rows("lake_trout", dates)) is None


def test_calendar_uses_fitted_window_when_present(tmp_path, monkeypatch):
    """End-to-end: a fits file changes what the calendar reports, and stamps provenance."""
    import json
    from datetime import date
    from tbay_fishcast.features import run_calendar as rc
    fits = {"windows": {"chinook_staging": {
        "start": "09-02", "end": "09-20", "n": 40, "n_years": 5, "shift_days": 8}}}
    p = tmp_path / "fitted.json"
    p.write_text(json.dumps(fits))
    monkeypatch.setattr(rc, "_FITTED", p)
    rc._load_entries.cache_clear()
    try:
        # authored window opened 08-25; the fitted one opens 09-02, so 08-26 is now CLOSED
        assert "chinook" not in rc.active_calendar_species(date(2026, 8, 26))
        assert "chinook" in rc.active_calendar_species(date(2026, 9, 10))
        st = rc.marker_status(date(2026, 9, 10), ["salmon"])
        run = next(r for r in st["runs"] if r["id"] == "chinook_staging")
        assert run["fitted"]["n"] == 40 and run["fitted"]["authored"] == ["08-25", "09-15"]
        assert run["tier"] == "DATA(local reports)"
    finally:
        rc._load_entries.cache_clear()
