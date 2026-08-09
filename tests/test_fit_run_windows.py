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


def test_two_stage_bar_computes_but_does_not_ship_thin_fits():
    """A fit between the compute bar and the APPLY bar must be visible but not shipped.
    Origin: the first live run proposed a 20-day fall-steelhead shift on 12 photo sightings."""
    dates = []
    for yr in (2021, 2022, 2023, 2024, 2025):          # 5 years x 3 = 15 reports
        dates += [f"{yr}-09-05", f"{yr}-09-10", f"{yr}-09-15"]
    f = frw.fit_entry(CHINOOK_ENTRY, _rows("chinook", dates))
    assert f is not None and f["n"] == 15
    assert f["applied"] is False, "15 reports must not ship a window change"
    # clearing the apply bar flips it
    for yr in (2021, 2022, 2023, 2024, 2025):
        dates += [f"{yr}-09-02", f"{yr}-09-12"]        # -> 25 reports over 5 years
    f2 = frw.fit_entry(CHINOOK_ENTRY, _rows("chinook", dates))
    assert f2["n"] == 25 and f2["applied"] is True


def test_analog_rows_never_fit_local_windows():
    """The ledger carries out-of-area covariates (MN DNR Knife River trap, ~200 km southwest
    across the lake). They must NEVER fit Thunder Bay's windows, or a window would claim to be
    'measured locally' while describing Minnesota's fish."""
    local = [f"{yr}-09-{d:02d}" for yr in (2021, 2022, 2023, 2024) for d in (5, 8, 11)]
    analog = [f"{yr}-08-{d:02d}" for yr in (2015, 2016, 2017, 2018, 2019, 2020)
              for d in (1, 3, 5, 7, 9)]        # 30 rows, much earlier - would drag the window
    rows = _rows("chinook", local)
    rows += [{**r, "analog": True} for r in _rows("chinook", analog)]
    f = frw.fit_entry(CHINOOK_ENTRY, rows)
    assert f is not None
    assert f["n"] == len(local), "analog rows must be excluded from n entirely"
    assert f["applied"] is False, "12 local reports alone must not ship a window"


def test_offshore_survey_rows_never_fit_shore_windows():
    """USGS research-vessel trawls are open-lake tows with trawl gear — long-term abundance
    context, not shore run observations. They must not fit a shore-fishing tool's windows."""
    shore = [f"{yr}-09-{d:02d}" for yr in (2021, 2022, 2023, 2024) for d in (5, 8, 11)]
    off = [f"{yr}-08-{d:02d}" for yr in (2014, 2015, 2016, 2017, 2018) for d in (1, 3, 5, 7)]
    rows = _rows("chinook", shore)
    rows += [{**r, "offshore_survey": True} for r in _rows("chinook", off)]
    f = frw.fit_entry(CHINOOK_ENTRY, rows)
    assert f is not None and f["n"] == len(shore)
