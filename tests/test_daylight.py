"""Property + reference tests for the deterministic dawn/dusk solar module.

No fixtures, no network — pure astronomy, so these assert against physical invariants and against
independently-computed reference values (NOAA solar calculator geometry). Thunder Bay sits at the
far-western edge of the Eastern time zone (89.25°W), so solar noon is ~14:03 EDT and sunsets are
genuinely late — the tests pin the astronomy, not a naive "sunset ≈ 8 pm" intuition."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tbay_fishcast.features import daylight

# Thunder Bay reference point (Kaministiquia mouth-ish; the windows are basin-wide, not per-station).
LAT, LON = 48.38, -89.25


def _minutes(a, b) -> float:
    return (b - a).total_seconds() / 60.0


def test_event_ordering():
    """Civil dawn precedes sunrise precedes sunset precedes civil dusk, every season."""
    for d in (date(2026, 3, 20), date(2026, 6, 21), date(2026, 8, 7), date(2026, 12, 21)):
        lw = daylight.compute(d, LAT, LON)
        assert lw.civil_dawn < lw.sunrise < lw.sunset < lw.civil_dusk, d


def test_symmetry_about_solar_noon():
    """Sunrise and sunset are (very nearly) symmetric about solar noon; likewise dawn/dusk.
    Declination drifts slightly across a day, so allow a couple of minutes."""
    lw = daylight.compute(date(2026, 8, 7), LAT, LON)
    noon = lw.sunrise + (lw.sunset - lw.sunrise) / 2
    dawn_gap = _minutes(lw.civil_dawn, noon)
    dusk_gap = _minutes(noon, lw.civil_dusk)
    assert abs(dawn_gap - dusk_gap) < 3.0, (dawn_gap, dusk_gap)


def test_daylength_seasonal_ordering():
    """Summer-solstice day is long, winter-solstice day is short, equinox near 12 h — the basic
    check that declination enters with the right sign."""
    def daylen(d):
        lw = daylight.compute(d, LAT, LON)
        return _minutes(lw.sunrise, lw.sunset)
    jun = daylen(date(2026, 6, 21))
    dec = daylen(date(2026, 12, 21))
    equ = daylen(date(2026, 9, 22))
    assert jun > 15.5 * 60          # ~16 h at 48°N
    assert dec < 9 * 60             # ~8.3 h
    assert 11.5 * 60 < equ < 12.5 * 60


def test_reference_values_thunder_bay_aug7():
    """Independently-computed reference (NOAA geometry) for 2026-08-07 at (48.38, -89.25):
    solar noon 18:03 UTC, sunrise ~10:40 UTC, sunset ~01:24 UTC(+1). Assert within 3 min."""
    lw = daylight.compute(date(2026, 8, 7), LAT, LON)
    assert lw.sunrise.strftime("%Y-%m-%dT%H:%M")[11:] in _within("10:40", 3)
    # sunset rolls into the next UTC day — the module must return the correct absolute instant
    assert lw.sunset.day == 8 and lw.sunset.hour == 1, lw.sunset
    assert _minutes(lw.sunrise, lw.sunset) == pytest.approx(884, abs=4)  # ~14 h 44 m daylength


def _within(hhmm: str, tol_min: int):
    """Set of HH:MM strings within ±tol_min of hhmm — for a tolerant string membership check."""
    h, m = (int(x) for x in hhmm.split(":"))
    base = h * 60 + m
    out = set()
    for delta in range(-tol_min, tol_min + 1):
        t = (base + delta) % 1440
        out.add(f"{t // 60:02d}:{t % 60:02d}")
    return out


def test_prime_windows_bracket_the_transition():
    """Morning prime starts at civil dawn and ends after sunrise; evening prime ends at civil
    dusk and starts before sunset. These are the fishable low-light windows."""
    lw = daylight.compute(date(2026, 8, 7), LAT, LON)
    am = lw.morning_prime
    pm = lw.evening_prime
    assert am[0] == lw.civil_dawn and am[1] > lw.sunrise
    assert pm[1] == lw.civil_dusk and pm[0] < lw.sunset
    assert am[1] - lw.sunrise == timedelta(minutes=45)
    assert lw.sunset - pm[0] == timedelta(minutes=45)


def test_determinism():
    """Same inputs → identical output (no clock/RNG in the data path, ADR-001)."""
    a = daylight.compute(date(2026, 8, 7), LAT, LON)
    b = daylight.compute(date(2026, 8, 7), LAT, LON)
    assert a == b
