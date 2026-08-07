"""Combined-suitability primitives — graded, no binary cliffs (the model's accuracy core).
Hermetic: pure functions, synthetic inputs.
"""
import numpy as np
import pytest

from tbay_fishcast.features import suitability as su
from tbay_fishcast.features import upwelling


def test_upwelling_favorability_is_graded_not_binary():
    # monotone increasing, bounded (0,1), ~0.5 at the s50 centre
    assert su.upwelling_favorability(0) < 0.05
    assert su.upwelling_favorability(su.FAVOR_S50_KN) == pytest.approx(0.5, abs=1e-6)
    assert su.upwelling_favorability(25) > 0.95
    xs = np.array([2, 6, 10, 13, 16, 20], dtype=float)
    ys = su.upwelling_favorability(xs)
    assert np.all(np.diff(ys) > 0)                # strictly increasing
    # the key point: a persistent moderate wind is NOT zero
    assert su.upwelling_favorability(10) > 0.15


def test_thermal_suitability_plateau_and_taper():
    # lake trout range 6-12, optimal 6-10
    r, o = (6.0, 12.0), (6.0, 10.0)
    assert su.thermal_suitability(8.0, r, o) == 1.0     # inside optimal
    assert su.thermal_suitability(6.0, r, o) == 1.0     # cold edge == optimal cold edge -> plateau
    assert su.thermal_suitability(11.0, r, o) == pytest.approx(0.5, abs=1e-6)  # warm margin midpoint
    assert su.thermal_suitability(12.0, r, o) == pytest.approx(0.0, abs=1e-6)  # range warm edge
    assert su.thermal_suitability(13.0, r, o) == 0.0    # outside range
    assert su.thermal_suitability(4.0, r, o) == 0.0     # below range


def test_thermal_suitability_cold_and_warm_margins():
    # a range where optimal is strictly interior on both sides
    r, o = (8.0, 16.0), (12.0, 14.0)
    assert su.thermal_suitability(13.0, r, o) == 1.0
    assert su.thermal_suitability(10.0, r, o) == pytest.approx(0.5, abs=1e-6)  # cold margin mid
    assert su.thermal_suitability(15.0, r, o) == pytest.approx(0.5, abs=1e-6)  # warm margin mid
    assert su.thermal_suitability(8.0, r, o) == 0.0
    assert su.thermal_suitability(16.0, r, o) == 0.0


def test_thermal_suitability_array_and_nan():
    r, o = (6.0, 12.0), (6.0, 10.0)
    out = su.thermal_suitability(np.array([np.nan, 8.0, 12.0, 20.0]), r, o)
    assert out[0] == 0.0 and out[1] == 1.0 and out[2] == pytest.approx(0.0) and out[3] == 0.0


def test_thermal_front_gradient_finds_edges():
    # a field with a sharp step in the middle -> high gradient at the step, ~0 on the flats
    field = np.array([[6., 6., 6., 12., 12., 12.]] * 6)
    g = su.thermal_front_gradient(field, res_m=10.0)
    # the interior flat columns have ~0 gradient; the step column has a large gradient
    assert g[3, 0] == pytest.approx(0.0, abs=1e-9)          # flat interior
    assert g[3, 3] > g[3, 0]                                 # at/near the step it's larger
    assert np.nanmax(g) == pytest.approx((12. - 6.) / (2 * 10.0), rel=0.3)  # ~Δ/Δx scale
    # units are per-metre: same step over 100 m spacing gives 10x smaller gradient
    g2 = su.thermal_front_gradient(field, res_m=100.0)
    assert np.nanmax(g2) < np.nanmax(g)


def test_ensemble_favorability_graded_vs_binary():
    # west wind at 10 kt for a whole day, one member: binary prob (>=13) = 0, favorability > 0
    time = [f"2026-08-07T{h:02d}:00" for h in range(24)]
    members = [{"speed_kn": [10.0] * 24, "dir_deg": [270.0] * 24}]
    prob = upwelling.upwelling_probability(time, members)          # binary, >=13 kt
    fav = upwelling.ensemble_favorability(time, members)           # continuous
    from datetime import date
    d = date(2026, 8, 7)
    assert prob[d] == 0.0                    # no member reaches the 13 kt sustained bar
    assert 0.1 < fav[d] < 0.5                # but graded favorability registers the moderate blow
    # east wind (out of sector) -> zero favorability regardless of speed
    east = [{"speed_kn": [20.0] * 24, "dir_deg": [90.0] * 24}]
    assert upwelling.ensemble_favorability(time, east)[d] == 0.0
