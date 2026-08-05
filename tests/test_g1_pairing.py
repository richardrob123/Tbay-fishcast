"""Unit tests for G1 daily-mean surface building, dedup, QC, and pairing."""
from datetime import date, datetime, timedelta, timezone

import pytest

from tbay_fishcast.verification.g1 import (
    SurfaceSample, build_daily_surface, pair_model_truth,
)

BASE = datetime(2025, 7, 1, 0, 0, tzinfo=timezone.utc)


def _samples(station, day_offset, hours, temp=15.0, issued_h=0):
    d = BASE + timedelta(days=day_offset)
    issued = d + timedelta(hours=issued_h)
    return [SurfaceSample(station, d + timedelta(hours=h), temp, issued) for h in hours]


def test_daily_mean_basic():
    s = _samples("marina", 0, range(24), temp=16.0)
    kept, dropped = build_daily_surface(s, min_hours=18)
    assert len(kept) == 1 and not dropped
    assert kept[0].n_hours == 24
    assert kept[0].mean_temp_c == 16.0
    assert kept[0].max_temp_c == 16.0 and kept[0].min_temp_c == 16.0


def test_daily_max_min_and_range():
    # temps vary 10..15 across the day
    d = BASE
    issued = d
    samples = [SurfaceSample("marina", d + timedelta(hours=h), 10.0 + h * 0.25, issued)
               for h in range(24)]
    kept, _ = build_daily_surface(samples, min_hours=18)
    assert kept[0].max_temp_c == pytest.approx(10.0 + 23 * 0.25)
    assert kept[0].min_temp_c == pytest.approx(10.0)
    assert kept[0].diurnal_range_c == pytest.approx(23 * 0.25)


def test_pair_selects_max_framing():
    d = BASE
    samples = [SurfaceSample("marina", d + timedelta(hours=h), 10.0 + h * 0.25, d)
               for h in range(24)]
    daily, _ = build_daily_surface(samples, min_hours=18)
    truth = {("marina", date(2025, 7, 1)): 20.0}
    mean_pair = pair_model_truth(daily, truth, "mean_temp_c")["marina"][0]
    max_pair = pair_model_truth(daily, truth, "max_temp_c")["marina"][0]
    assert max_pair[0] > mean_pair[0]  # max framing uses the warmer value


def test_thin_day_dropped_not_averaged():
    s = _samples("marina", 0, range(5))  # only 5 hours
    kept, dropped = build_daily_surface(s, min_hours=18)
    assert not kept
    assert dropped and dropped[0].n_hours == 5


def test_dedup_prefers_latest_issuance():
    vt = BASE + timedelta(hours=3)
    early = SurfaceSample("marina", vt, 10.0, issued=BASE)                     # older cycle
    late = SurfaceSample("marina", vt, 20.0, issued=BASE + timedelta(hours=6))  # newer cycle
    # pad to reach min_hours with unique hours
    pad = _samples("marina", 0, range(4, 4 + 20), temp=20.0)
    kept, _ = build_daily_surface([early, late, *pad], min_hours=18)
    # the deduped hour uses the LATE value (20), so the mean is exactly 20
    assert kept[0].mean_temp_c == 20.0


def test_implausible_values_filtered():
    good = _samples("marina", 0, range(20), temp=15.0)
    junk = [SurfaceSample("marina", BASE + timedelta(hours=20), 99.9, BASE),   # fill value
            SurfaceSample("marina", BASE + timedelta(hours=21), -50.0, BASE)]
    kept, _ = build_daily_surface(good + junk, min_hours=18)
    assert kept[0].n_hours == 20  # junk hours excluded
    assert kept[0].mean_temp_c == 15.0


def test_pairing_inner_join():
    daily = build_daily_surface(_samples("marina", 0, range(24), temp=16.0))[0]
    daily += build_daily_surface(_samples("marina", 1, range(24), temp=17.0))[0]
    truth = {("marina", date(2025, 7, 1)): 16.5}  # day 2 has no GLSEA
    paired = pair_model_truth(daily, truth)
    assert paired["marina"] == [(16.0, 16.5)]  # only the matched day
