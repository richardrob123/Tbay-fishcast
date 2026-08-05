"""2-D reachability tests (pure)."""
import numpy as np

from tbay_fishcast.features.reachability import reachability, shore_distance_m


def _grid():
    # 20x20, land on the left 5 cols (NaN), water elsewhere deepening to the right
    g = np.full((20, 20), np.nan)
    for c in range(5, 20):
        g[:, c] = (c - 5) * 1.0   # depth 0..14 m going offshore
    return g


def test_shore_distance_zero_at_edge_grows_offshore():
    g = _grid()
    d = shore_distance_m(g, res_ground_m=10.0)
    # first water column is adjacent to land -> ~10 m; deep column farther
    assert d[10, 5] <= 15.0
    assert d[10, 19] > d[10, 5]


def test_reachable_when_isotherm_shallow_and_near_shore():
    g = _grid()
    d = shore_distance_m(g, 10.0)
    # isotherm at 2 m: bottom reaches 2 m at col 7 (~20 m from shore) -> within 75 m
    ok, closest, area = reachability(g, d, iso_depth_m=2.0, cast_range_m=75.0, res_ground_m=10.0)
    assert ok and closest is not None and closest <= 75.0 and area > 0


def test_not_reachable_when_isotherm_deep_offshore():
    g = _grid()
    d = shore_distance_m(g, 10.0)
    # isotherm at 12 m: bottom only reaches 12 m far offshore (col 17, ~120 m) -> out of 75 m
    ok, closest, area = reachability(g, d, iso_depth_m=12.0, cast_range_m=75.0, res_ground_m=10.0)
    assert not ok and closest is None and area == 0.0


def test_absent_isotherm_not_reachable():
    g = _grid()
    d = shore_distance_m(g, 10.0)
    ok, closest, area = reachability(g, d, iso_depth_m=None, cast_range_m=75.0, res_ground_m=10.0)
    assert not ok and closest is None


def test_shoal_off_to_the_side_is_found():
    # a 1-D transect straight offshore would miss a shallow shoal placed off-axis;
    # the 2-D test must find it. Deep water everywhere (15 m) except a shallow patch.
    g = np.full((20, 20), 15.0)
    g[:, 0] = np.nan  # shore on the left
    g[2:5, 3:6] = 3.0  # a shallow shoal near shore, off the centre row
    d = shore_distance_m(g, 10.0)
    ok, closest, area = reachability(g, d, iso_depth_m=12.0, cast_range_m=75.0, res_ground_m=10.0)
    assert ok  # deep (>=12 m) cold bottom exists within cast range
