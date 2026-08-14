"""Land-to-lake wind exposure: METAR conventions, fit forms, circular direction (ADR-056)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tbay_fishcast.features import wind_exposure as we
from tbay_fishcast.ingest import asos_archive as asos


def _obs(minute_offsets, speed=10.0, direction=270.0):
    base = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    return [asos.WindObs(base + timedelta(minutes=m), speed, direction) for m in minute_offsets]


# --- METAR conventions --------------------------------------------------------------------------

def test_calm_is_not_a_northerly():
    """METAR 00000KT arrives as direction 0 with speed 0. Verified against 3426 live records:
    direction 0 occurs only at speed 0, and north is reported as 360."""
    calm = asos.parse_row({"valid": "2026-07-01 12:00", "drct": "0", "sknt": "0"})
    assert calm.calm and calm.dir_deg is None and calm.speed_kn == 0.0
    north = asos.parse_row({"valid": "2026-07-01 12:00", "drct": "360", "sknt": "8"})
    assert not north.calm and north.dir_deg == 360.0
    assert asos.parse_row({"valid": "2026-07-01 12:00", "drct": "", "sknt": "8"}) is None
    assert asos.parse_row({"valid": "2026-07-01 12:00", "drct": "999", "sknt": "8"}) is None


def test_hourly_takes_the_nearest_observation_within_tolerance():
    """SPECIs sit between the hourly METARs, so the series is irregular. The hour gets the
    closest real observation — never an interpolated one."""
    obs = _obs([-3, +8])                      # 11:57 and 12:08
    h = asos.hourly(obs)
    assert set(h) == {datetime(2026, 7, 1, 12, tzinfo=timezone.utc)}
    assert h[datetime(2026, 7, 1, 12, tzinfo=timezone.utc)].time.minute == 57


def test_an_hour_with_no_close_observation_is_absent_not_filled():
    assert asos.hourly(_obs([+25])) == {}      # 12:25 is 25 min from either hour


# --- The fits -----------------------------------------------------------------------------------

def test_multiplicative_fit_is_forced_through_the_origin():
    """Calm on land is calm on the water. An intercept free to float would let the fit buy
    accuracy in the light-wind bulk by predicting a breeze when there is none."""
    f = we.fit([(x, 1.5 * x) for x in range(1, 60)], form="multiplicative")
    assert f.offset == 0.0
    assert abs(f.scale - 1.5) < 1e-6
    assert f.apply(0.0) == 0.0


def test_a_fit_never_returns_a_negative_wind():
    f = we.fit([(x, x - 8.0) for x in range(1, 60)], form="affine")
    assert f.apply(0.0) == 0.0


def test_fit_refuses_a_thin_sample():
    assert we.fit([(1.0, 2.0)] * 10, form="affine") is None


def test_score_reports_the_uncorrected_reading_alongside():
    """Without the uncorrected column there is no way to see that a correction bought nothing —
    which is exactly what happened in the strong-wind tail on the real data."""
    train = [(x, 4.5 + 0.83 * x) for x in range(1, 60)]
    s = we.score(we.fit(train, form="affine"), train)
    assert s["corrected_mae_kn"] < s["uncorrected_mae_kn"]
    assert "uncorrected_bias_kn" in s


# --- Direction, on the circle -------------------------------------------------------------------

def test_direction_offset_wraps_the_short_way():
    """350 and 10 differ by 20, not 340. Arithmetic differencing would turn a season of
    northerlies into a mean offset near 180."""
    out = we.circular_offset_deg([(350.0, 10.0)] * 40)
    assert abs(out["mean_offset_deg"] - 20.0) < 1e-6
    assert out["concentration"] > 0.99


def test_direction_offset_reports_low_concentration_when_there_is_no_preferred_offset():
    pairs = [(0.0, float(d)) for d in range(0, 360, 9)] * 2
    out = we.circular_offset_deg(pairs)
    assert out["concentration"] < 0.05


def test_direction_offset_refuses_a_thin_sample():
    assert we.circular_offset_deg([(10.0, 30.0)] * 5) is None
