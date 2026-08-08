"""Unit tests for river-discharge plume-trend classification (pure, synthetic series, no network)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tbay_fishcast.features import river_flow


def _series(q_start, q_end, days=4, step_h=6):
    """Linear discharge ramp from q_start (oldest) to q_end (newest) over `days`."""
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    n = int(days * 24 / step_h) + 1
    out = []
    for i in range(n):
        frac = i / (n - 1)
        out.append((t0 + timedelta(hours=i * step_h), q_start + (q_end - q_start) * frac))
    return out


def test_rising_river_is_freshet():
    rf = river_flow.classify(_series(5.0, 12.0))     # +140%
    assert rf.state == "rising" and rf.freshet is True
    assert rf.trend_pct > 12
    assert rf.q_cms == 12.0


def test_falling_river_is_not_freshet():
    rf = river_flow.classify(_series(12.0, 5.0))
    assert rf.state == "falling" and rf.freshet is False
    assert rf.trend_pct < -12


def test_steady_river():
    rf = river_flow.classify(_series(10.0, 10.3))    # +3%
    assert rf.state == "steady" and rf.freshet is False


def test_short_record_is_unknown_trend_but_reports_level():
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rf = river_flow.classify([(t0, 8.0), (t0 + timedelta(hours=2), 8.1)])  # <0.75 d span
    assert rf.state == "unknown" and rf.q_cms == 8.1 and rf.trend_pct is None


def test_empty_is_unknown():
    rf = river_flow.classify([])
    assert rf.state == "unknown" and rf.q_cms is None


def test_unordered_input_handled():
    s = _series(4.0, 9.0)
    import random  # noqa: PLC0415 - deterministic shuffle via fixed seed
    r = random.Random(0)
    shuffled = s[:]; r.shuffle(shuffled)
    assert river_flow.classify(shuffled).as_dict() == river_flow.classify(s).as_dict()


def test_small_river_level_formatting():
    """A sub-10 m³/s river keeps 2 decimals in the note; a big one rounds to integer."""
    assert "0.08" in river_flow.classify(_series(0.10, 0.08)).note
    assert "20 m" in river_flow.classify(_series(21.0, 20.0)).note


def test_nan_discharge_is_unknown_never_steady():
    """MED-1 (stress-test 2026-08): a NaN sample survived every comparison into a confident
    'steady' with 'nan m³/s' in the UI note. NaN must be dropped; if nothing valid remains,
    state is 'unknown' — never a guess."""
    t0 = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    rf = river_flow.classify([(t0 - timedelta(days=3), 5.0), (t0, float("nan"))])
    assert rf.state in ("unknown", "steady") and (rf.q_cms is None or rf.q_cms == 5.0)
    assert "nan" not in rf.note.lower()
    rf2 = river_flow.classify([(t0, float("nan"))])
    assert rf2.state == "unknown" and rf2.q_cms is None


def test_negative_discharge_rejected():
    """MED-2: ECCC gauges emit negative artifacts (ice/backwater); a physically impossible flow
    must never be confidently classified."""
    t0 = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    rf = river_flow.classify([(t0 - timedelta(days=3), 10.0), (t0, -5.0)])
    assert (rf.q_cms is None) or (rf.q_cms >= 0.0)
    assert "-5" not in rf.note
