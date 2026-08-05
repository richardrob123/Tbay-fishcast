"""NONNA bathymetry — pure geometry tests (no network).

Synthetic depth grids stand in for a fetched patch so the transect logic is
exercised deterministically: snap-to-water, offshore-direction detection, and
the shore-anchored depth-vs-distance walk.
"""
import math

import numpy as np
import pytest

from tbay_fishcast.ingest.nonna import (
    depths_from_raw,
    ground_res_m,
    nearest_water_px,
    offshore_bearing,
    to_mercator,
    walk_profile,
)


def _ramp_grid(n=40):
    """Land on the west (low col), water deepening toward the east (high col).
    Shoreline near col 10; depth grows 0.5 m per column offshore."""
    g = np.full((n, n), np.nan)
    for c in range(n):
        depth = (c - 10) * 0.5
        if depth > 0:
            g[:, c] = depth
    return g


def test_depths_from_raw_flips_sign_and_masks():
    raw = np.array([[-16.7, -2.0, 0.5, 3.4e38]])
    d = depths_from_raw(raw)
    assert d[0, 0] == 16.7 and d[0, 1] == 2.0
    assert math.isnan(d[0, 2])   # +0.5 = drying/land, not water
    assert math.isnan(d[0, 3])   # nodata fill


def test_nearest_water_snaps_from_land():
    g = _ramp_grid()
    # a pin on land (col 3) snaps to the nearest wet column (col 11, first depth>0)
    r, c = nearest_water_px(g, 20, 3)
    assert c == 11 and np.isfinite(g[r, c])


def test_offshore_bearing_points_east_down_the_ramp():
    g = _ramp_grid()
    brg = offshore_bearing(g, 20, 15)
    # east is bearing 90; deepening is eastward, so detected bearing hugs 90
    assert abs((brg - 90 + 180) % 360 - 180) <= 20


def test_offshore_bearing_respects_hint():
    g = _ramp_grid()
    # hand it a southeast hint; the deepening is due east, within tolerance -> ~east/SE
    brg = offshore_bearing(g, 20, 15, hint_deg=110, hint_tol_deg=60)
    assert brg is not None
    assert abs((brg - 90 + 180) % 360 - 180) <= 60


def test_walk_profile_shore_anchored_and_monotone():
    g = _ramp_grid()
    dists, depths = walk_profile(g, 20, 15, 90.0, res_ground_m=10.0)
    assert dists[0] == 0.0 and depths[0] == 0.0     # shore origin
    assert all(b >= a for a, b in zip(dists, dists[1:]))   # distance increases
    # depth increases offshore along the ramp
    assert depths[-1] > depths[1]
    # distances are real ground metres (step_px=1 * 10 m)
    assert dists[1] == 10.0


def test_ground_res_shrinks_with_latitude():
    # mercator metres are inflated; ground res at 48.4N is ~0.66x the mercator pixel
    assert ground_res_m(19.1, 48.4) == pytest.approx(19.1 * math.cos(math.radians(48.4)), rel=1e-6)


def test_to_mercator_roundtrip_sign():
    x, y = to_mercator(48.4, -89.2)
    assert x < 0 and y > 0   # western hemisphere, northern latitude
