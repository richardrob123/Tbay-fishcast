"""Overlay-mask logic: cold/line/reachable + land-aware shore distance.

Synthetic fields (a sloping shore) so the invariants are exact and network-free.
These pin the two artifact fixes: no fake offshore 'shore', and a 12 C line that
survives a thin warm strip.
"""
import numpy as np

from tbay_fishcast.features.overlay import cold_line_reachable, land_shore_distance

RES = 10.0  # m/px


def _sloping_shore(rows=30, cols=30, land_cols=5, slope=2.0):
    """cols 0..land_cols-1 = land (NaN); water deepens 2 m per column after that."""
    depth = np.full((rows, cols), np.nan)
    for c in range(land_cols, cols):
        depth[:, c] = (c - (land_cols - 1)) * slope   # col land_cols -> 2 m, etc.
    return depth


def test_reachable_is_shallow_nearshore_only():
    depth = _sloping_shore()
    dist, land = land_shore_distance(depth, RES)
    iso = np.full_like(depth, 3.0)
    cold, line, reach = cold_line_reachable(depth, iso, dist, cast_m=75.0,
                                            max_reach_depth_m=22.0, min_reach_px=5)
    # land is the mainland block; nothing reachable on land or in the warm 2 m strip
    assert not reach[:, :5].any()
    assert not reach[:, 5].any()               # 2 m warm strip (below the isotherm target)
    # reachable pixels are cold, within a cast, and shallow
    rd = depth[reach]
    assert reach.any()
    assert rd.max() <= 22.0
    assert dist[reach].max() <= 75.0 + 1e-6


def test_no_reachable_in_deep_water():
    depth = _sloping_shore(cols=40)          # extends to ~70 m depth
    dist, _ = land_shore_distance(depth, RES)
    iso = np.full_like(depth, 3.0)
    _, _, reach = cold_line_reachable(depth, iso, dist, cast_m=75.0, max_reach_depth_m=22.0)
    assert depth[reach].max() <= 22.0        # never calls 30 m+ water shore-reachable


def test_line_marks_warm_cold_interface_even_thin():
    depth = _sloping_shore()
    dist, _ = land_shore_distance(depth, RES)
    iso = np.full_like(depth, 3.0)
    _, line, _ = cold_line_reachable(depth, iso, dist)
    # the 3 m crossing sits between col5 (2 m, warm) and col6 (4 m, cold): the line
    # marks that interface (thickened to a continuous stroke a few px wide)
    assert line[:, 6].all()
    assert not line[:, 12].any()             # not smeared far into deeper cold water


def test_tiny_nodata_speck_is_not_land():
    depth = _sloping_shore()
    depth[0, 6] = np.nan                      # a 1 px no-data hole beside shallow water
    _, land = land_shore_distance(depth, RES, min_land_px=12)
    assert not land[0, 6]                      # too small to be shore -> no fake island


def test_deep_warm_pinhole_makes_no_line():
    depth = _sloping_shore()
    depth[15, 20] = 1.0                        # a warm pinhole deep offshore (surrounded by cold)
    dist, _ = land_shore_distance(depth, RES)
    iso = np.full_like(depth, 3.0)
    _, line, _ = cold_line_reachable(depth, iso, dist)
    # the pinhole is warm but does not touch shore -> no line ring around it
    assert not line[14:17, 19:22].any()


def test_offshore_island_is_not_mainland_shore():
    """An interior island (real, borders shallow water, big enough) is dropped when
    mainland_only — it isn't walk-to shore, so cold water around it isn't 'reachable'."""
    depth = np.full((30, 30), 10.0)
    depth[:, :5] = np.nan                 # mainland block (touches the left border)
    depth[:, 5] = 1.0                     # shallow waterline so the mainland is 'shore'
    depth[12:19, 12:19] = 1.0             # a shallow patch mid-grid
    depth[13:18, 13:18] = np.nan          # a 5x5 island inside it, off the border
    _, shore_main = land_shore_distance(depth, RES, mainland_only=True)
    _, shore_all = land_shore_distance(depth, RES, mainland_only=False)
    assert shore_all[15, 15]              # island counts as land without the restriction
    assert not shore_main[15, 15]         # dropped as non-mainland
    assert shore_main[10, 2] and shore_all[10, 2]   # the mainland is kept either way


def test_speck_removal_drops_isolated_reachable():
    depth = _sloping_shore()
    dist, _ = land_shore_distance(depth, RES)
    iso = np.full_like(depth, 3.0)
    # force a lone reachable-looking pixel far offshore via a fake near-zero dist point
    dist2 = dist.copy()
    dist2[0, 25] = 0.0                          # 1 isolated in-range pixel in deep water
    _, _, reach = cold_line_reachable(depth, iso, dist2, min_reach_px=20)
    assert not reach[0, 25]                     # single speck removed
