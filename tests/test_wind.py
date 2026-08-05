"""Wind-run feature tests (independent upwelling driver)."""
from datetime import datetime, timedelta, timezone

import numpy as np

from tbay_fishcast.features.wind import (
    favorable_wind_run, in_sector, wind_consistent,
)

BASE = datetime(2025, 8, 1, 0, 0, tzinfo=timezone.utc)


def _t(n):
    return [BASE + timedelta(hours=i) for i in range(n)]


def test_in_sector_west_quadrant():
    assert in_sector([270])[0]          # due west
    assert in_sector([230])[0] and in_sector([310])[0]
    assert not in_sector([180])[0]      # south
    assert not in_sector([90])[0]       # east


def test_in_sector_wraps_past_360():
    m = in_sector([350, 5, 180], sector=(340, 20))
    assert m[0] and m[1] and not m[2]


def test_favorable_run_positive_for_west_wind():
    n = 30
    run = favorable_wind_run(_t(n), [15.0] * n, [270.0] * n, window_h=24)
    assert run[-1] > 0
    # ~15 kt over 24 h ≈ 360 kt·h
    assert 300 < run[-1] < 400


def test_no_favorable_run_for_east_wind():
    n = 30
    run = favorable_wind_run(_t(n), [15.0] * n, [90.0] * n, window_h=24)
    assert np.all(run == 0)


def test_wind_consistent_true_when_favorable_precedes_onset():
    n = 48
    onset = BASE + timedelta(hours=40)
    assert wind_consistent(onset, _t(n), [14.0] * n, [270.0] * n,
                           lead_h=24, run_threshold=120)


def test_wind_consistent_false_for_calm():
    n = 48
    onset = BASE + timedelta(hours=40)
    assert not wind_consistent(onset, _t(n), [1.0] * n, [270.0] * n,
                               lead_h=24, run_threshold=120)
