"""Unit tests for the barometric directional-prior classifier (pure, synthetic series, no network)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tbay_fishcast.features import barometric


def _series(start_p, slope_per_h, n=12, step_h=1):
    """Synthetic hourly pressure series ending at issue time, with a constant slope (hPa/h)."""
    t0 = datetime(2026, 8, 7, 6, tzinfo=timezone.utc)
    times, pres = [], []
    for i in range(n):
        times.append((t0 + timedelta(hours=i * step_h)).isoformat())
        pres.append(start_p + slope_per_h * i * step_h)
    return times, pres


AT = datetime(2026, 8, 7, 6 + 11, tzinfo=timezone.utc)  # last sample of a 12-pt hourly series


def test_falling_pressure_is_improving():
    t, p = _series(1015.0, -0.6)      # −0.6 hPa/h ≈ −1.8/3h
    b = barometric.classify(t, p, AT)
    assert b.state == "falling" and b.prior == "improving"
    assert b.trend_hpa_3h < -1.0


def test_rising_pressure_is_slowing():
    t, p = _series(1005.0, +0.7)
    b = barometric.classify(t, p, AT)
    assert b.state == "rising" and b.prior == "slowing"
    assert b.trend_hpa_3h > 1.0


def test_steady_pressure_is_neutral():
    t, p = _series(1013.0, +0.1)      # +0.3/3h, within the steady band
    b = barometric.classify(t, p, AT)
    assert b.state == "steady" and b.prior == "neutral"


def test_insufficient_data_is_unknown():
    b = barometric.classify(["2026-08-07T12:00"], [1012.0], AT)
    assert b.state == "unknown" and b.level_hpa is None


def test_level_is_nearest_to_issue():
    t, p = _series(1000.0, +1.0)      # rises 1 hPa/h; value at issue (i=11) = 1011
    b = barometric.classify(t, p, AT)
    assert b.level_hpa == 1011.0


def test_dict_is_json_safe():
    t, p = _series(1015.0, -0.6)
    d = barometric.classify(t, p, AT).as_dict()
    assert set(d) == {"level_hpa", "trend_hpa_3h", "state", "prior", "note", "tier"}


def test_short_baseline_span_is_unknown_trend():
    """Stress-test 2026-08: two samples minutes apart extrapolated noise x(3h/dt) into a huge
    fake trend. A baseline shorter than half the tendency window must yield trend=unknown."""
    t0 = datetime(2026, 8, 7, 12, tzinfo=timezone.utc)
    b = barometric.classify([(t0 - timedelta(minutes=2)).isoformat(), t0.isoformat()],
                            [1013.0, 1012.9], t0)
    assert b.state == "unknown" and b.trend_hpa_3h is None
    assert b.level_hpa == 1012.9


def test_nan_pressure_filtered():
    t0 = datetime(2026, 8, 7, 12, tzinfo=timezone.utc)
    times = [(t0 - timedelta(hours=h)).isoformat() for h in range(11, -1, -1)]
    pres = [1013.0] * 11 + [float("nan")]
    b = barometric.classify(times, pres, t0)
    assert "nan" not in b.note.lower()
