"""Tests for the spawning-run phenology loader (reads the committed events_calendar.yaml).

These pin the WINDOW LOGIC and the map→calendar species mapping against the committed calendar, so
a marker can never claim a run that the calendar doesn't authorize (the same 'truthful markers'
discipline the regs gate enforces for legality)."""
from __future__ import annotations

from datetime import date

from tbay_fishcast.features import run_calendar as rc


def test_no_runs_in_early_summer():
    """Mid-June: no salmon/steelhead run window is open — markers must read inactive."""
    assert rc.active_calendar_species(date(2026, 6, 15)) == set()
    st = rc.marker_status(date(2026, 6, 15), ["salmon", "steelhead"])
    assert st["active"] is False and st["runs"] == []


def test_chinook_staging_opens_late_august():
    """chinook_staging window is 08-25 → 09-15: closed on 08-24, open on 08-25."""
    assert "chinook" not in rc.active_calendar_species(date(2026, 8, 24))
    assert "chinook" in rc.active_calendar_species(date(2026, 8, 25))


def test_salmon_marker_lights_up_in_september():
    """A salmon/steelhead mouth is active during the September Pacific-salmon staging."""
    st = rc.marker_status(date(2026, 9, 1), ["salmon", "steelhead"])
    assert st["active"] is True
    assert "salmon" in st["species_active"]
    assert any(r["species"] == "chinook" for r in st["runs"])


def test_freeze_up_end_is_bounded():
    """fall_steelhead ends 'freeze_up' — resolved to a deterministic early-December date, so the
    window is open in mid-November and closed by mid-December."""
    assert "steelhead" in rc.active_calendar_species(date(2026, 11, 15))
    assert "steelhead" not in rc.active_calendar_species(date(2026, 12, 15))


def test_upwelling_event_excluded_from_runs():
    """The condition-triggered upwelling_event (lake_trout) is NOT a calendar run — it must not
    surface as an active run window (it's handled by the phase banner, not the run markers)."""
    # Its window (06-15 → 09-30) would otherwise match many summer dates.
    for d in (date(2026, 7, 1), date(2026, 9, 1)):
        assert "lake_trout" not in rc.active_calendar_species(d)


def test_dipnet_season_excluded():
    """Whitefish dip-net season is not rod-and-reel angling and must not drive a run marker."""
    assert "lake_whitefish" not in rc.active_calendar_species(date(2026, 11, 1))


def test_determinism_and_species_mapping():
    """salmon → {chinook,coho,pink}; steelhead → steelhead; stable across calls."""
    a = rc.marker_status(date(2026, 9, 10), ["salmon"])
    b = rc.marker_status(date(2026, 9, 10), ["salmon"])
    assert a == b
    assert a["active"] is True and a["species_active"] == ["salmon"]
