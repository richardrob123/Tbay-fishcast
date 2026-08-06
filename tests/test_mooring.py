"""GLERL mooring offshore prior — half-month keying + committed-climatology serving.

The NetCDF read is network/offline; the load-bearing pure logic is the half-month period
key, the nearest-period fallback, and that the committed climatology parses and serves a
physically ordered summer thermocline (deepens through the season).
"""
import json
from datetime import date
from pathlib import Path

from tbay_fishcast.ingest import mooring

CLIM = Path(__file__).resolve().parents[1] / "knowledge" / "mooring_superior_climatology.json"


def test_period_of_halves():
    assert mooring.period_of(date(2026, 8, 6)) == "08a"
    assert mooring.period_of(date(2026, 8, 15)) == "08a"
    assert mooring.period_of(date(2026, 8, 16)) == "08b"
    assert mooring.period_of(date(2026, 12, 31)) == "12b"


def test_committed_climatology_is_physical():
    clim = json.loads(CLIM.read_text())
    p = clim["periods"]
    # stratified season present and thermocline deepens Aug -> late Sep
    for k in ("08a", "08b", "09a", "09b"):
        assert k in p
    depths = [p[k]["iso12_depth_m"] for k in ("08a", "08b", "09a", "09b")]
    assert all(d is not None for d in depths)
    assert depths == sorted(depths)          # monotonically deepening
    # an all-cold winter column has no 12 C isotherm
    assert p["02a"]["iso12_depth_m"] is None


def test_prior_for_day_and_fallback():
    clim = json.loads(CLIM.read_text())
    aug = mooring.prior_for_day(date(2026, 8, 6), clim)
    assert aug.period == "08a" and aug.iso12_depth_m == 7.16
    # a period the 2-yr record may not sample still resolves via nearest-period fallback
    got = mooring.prior_for_day(date(2026, 8, 20), clim)
    assert got is not None and got.iso12_depth_m is not None
