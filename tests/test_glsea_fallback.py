"""GLSEA surface-anchor fallback — walk back to the most recent available day.

GLSEA lags ~1 day and gaps on cloud; dropping the anchor collapses the corrected
isotherm to the surface, so the fallback is load-bearing. Mocked (no network).
"""
from datetime import date

from tbay_fishcast.ingest import glsea
from tbay_fishcast.ingest import SourceUnavailable
from tbay_fishcast.ingest.glsea import SstPixel


def _px(day):
    return SstPixel(sst_c=18.0, pixel_lat=48.5, pixel_lon=-89.0, dist_km=0.5,
                    day=day, dataset=glsea.DATASET_TRUTH)


def test_falls_back_to_previous_day(monkeypatch):
    """Today 404s (not posted yet); yesterday resolves and is returned."""
    calls = []

    def fake(lat, lon, day, **kw):
        calls.append(day)
        if day == "2026-08-05":
            raise SourceUnavailable("404 not posted")
        return _px(day)

    monkeypatch.setattr(glsea, "fetch_sst", fake)
    px = glsea.fetch_recent_sst(48.5, -89.0, date(2026, 8, 5))
    assert px is not None and px.day == "2026-08-04"     # used yesterday
    assert calls[:2] == ["2026-08-05", "2026-08-04"]     # tried today first


def test_uses_requested_day_when_present(monkeypatch):
    monkeypatch.setattr(glsea, "fetch_sst", lambda lat, lon, day, **kw: _px(day))
    px = glsea.fetch_recent_sst(48.5, -89.0, "2026-08-05")
    assert px.day == "2026-08-05"


def test_returns_none_when_whole_window_missing(monkeypatch):
    def fake(lat, lon, day, **kw):
        raise SourceUnavailable("404")

    monkeypatch.setattr(glsea, "fetch_sst", fake)
    assert glsea.fetch_recent_sst(48.5, -89.0, date(2026, 8, 5), max_back=3) is None
