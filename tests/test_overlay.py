"""Overlay-mask logic: cold/reachable + land-aware (mainland) shore distance.

Synthetic fields (a sloping shore) so the invariants are exact and network-free.
These pin the artifact fixes: no fake offshore 'shore', reachability only near the
mainland, castable-depth only. The 12 C line is a rendered contour (isobath_line_rgba)
verified visually, not asserted here.
"""
import numpy as np

from tbay_fishcast.features.overlay import (
    cold_reachable,
    isobath_line_features,
    isobath_line_rgba,
    land_shore_distance,
)

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
    cold, reach = cold_reachable(depth, iso, dist, cast_m=75.0,
                                 max_reach_depth_m=22.0, min_reach_px=5)
    assert not reach[:, :5].any()              # nothing reachable on land
    assert not reach[:, 5].any()               # nor in the 2 m warm strip (above target)
    rd = depth[reach]
    assert reach.any()
    assert rd.max() <= 22.0
    assert dist[reach].max() <= 75.0 + 1e-6


def test_no_reachable_in_deep_water():
    depth = _sloping_shore(cols=40)          # extends to ~70 m depth
    dist, _ = land_shore_distance(depth, RES)
    iso = np.full_like(depth, 3.0)
    _, reach = cold_reachable(depth, iso, dist, cast_m=75.0, max_reach_depth_m=22.0)
    assert depth[reach].max() <= 22.0        # never calls 30 m+ water shore-reachable


def test_tiny_nodata_speck_is_not_land():
    depth = _sloping_shore()
    depth[0, 6] = np.nan                      # a 1 px no-data hole beside shallow water
    _, land = land_shore_distance(depth, RES, min_land_px=12)
    assert not land[0, 6]                      # too small to be shore -> no fake island


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


def test_isobath_line_is_transparent_thin_and_placed():
    """The 12 C line renders as a THIN contour on a TRANSPARENT background at the
    iso crossing — not an opaque fill that would wipe the green it's drawn over."""
    depth = _sloping_shore(rows=60, cols=60)
    dist = np.zeros((60, 60))
    for c in range(60):
        dist[:, c] = abs(c - 4) * RES
    iso = np.full_like(depth, 3.0)
    buf = isobath_line_rgba(depth, iso, dist, line_band_m=1000.0)
    assert buf.shape == (60, 60, 4)
    drawn = buf[:, :, 3] > 40
    assert 0 < drawn.sum() < depth.size * 0.1        # a thin line, not a fill
    assert (buf[:, :, 3] == 0).sum() > depth.size * 0.5   # background transparent
    cols = np.where(drawn.any(axis=0))[0]
    assert 4 <= cols.min() and cols.max() <= 8       # at the 3 m crossing (col5->6)


def test_isobath_line_features_are_vector_paths_length_filtered():
    """The 12 C line comes back as lon/lat polylines (for a crisp vector layer), and
    short segments are dropped so tiny loops don't render as stray red specks."""
    depth = _sloping_shore(rows=40, cols=40)
    dist = np.zeros((40, 40))
    for c in range(40):
        dist[:, c] = abs(c - 4) * RES
    iso = np.full_like(depth, 3.0)
    bounds = (-9930000.0, 6190000.0, -9920000.0, 6200000.0)   # mercator, near Thunder Bay
    feats = isobath_line_features(depth, iso, dist, bounds, line_band_m=1000.0, min_len_m=50.0)
    assert len(feats) >= 1
    for path in feats:
        assert len(path) >= 2
        lon, lat = path[0]
        assert -90.5 < lon < -88.0 and 48.0 < lat < 49.0     # plausible lon/lat
    # an impossibly long minimum filters everything out
    assert isobath_line_features(depth, iso, dist, bounds, min_len_m=1e9) == []


def test_speck_removal_drops_isolated_reachable():
    depth = _sloping_shore()
    dist, _ = land_shore_distance(depth, RES)
    iso = np.full_like(depth, 3.0)
    dist2 = dist.copy()
    dist2[0, 25] = 0.0                          # 1 isolated in-range pixel in deep water
    _, reach = cold_reachable(depth, iso, dist2, min_reach_px=20)
    assert not reach[0, 25]                     # single speck removed
