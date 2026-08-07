"""Authoritative water-mask integration — hermetic parts only.

The Overpass fetch itself is network (and uses curl, which the socket guard can't block),
so these cover the pure logic: land_shore_distance driven by an explicit land_mask, and
that water_mask fails cleanly (WaterMaskError) on bad input so corrected_fields can fall back.
"""
import numpy as np
import pytest

from tbay_fishcast.features.overlay import land_shore_distance
from tbay_fishcast.ingest.watermask import WaterMaskError, water_mask


def test_land_shore_distance_from_explicit_mask():
    # water column on the left, land block on the right; distance is measured from land.
    depth = np.full((10, 10), 5.0)          # all "water" depth-wise
    land = np.zeros((10, 10), dtype=bool)
    land[:, 7:] = True                       # right three columns are authoritative land
    dist, shore = land_shore_distance(depth, 10.0, land_mask=land, mainland_only=False)
    assert shore[:, 7:].all() and not shore[:, :7].any()
    # a water pixel adjacent to land is ~one cell (10 m) away; far side is farther
    assert dist[0, 6] < dist[0, 0]
    assert dist[5, 6] == pytest.approx(10.0, abs=1e-6)   # mid-row: one cell from land


def test_land_mask_drops_small_specks():
    depth = np.full((20, 20), 5.0)
    land = np.zeros((20, 20), dtype=bool)
    land[5:11, 5:11] = True                   # 36 px block (away from border) — kept
    land[15, 15] = True                       # 1 px speck — dropped (< min_land_px)
    _dist, shore = land_shore_distance(depth, 5.0, land_mask=land,
                                       mainland_only=False, min_land_px=12)
    assert shore[5:11, 5:11].all()
    assert not shore[15, 15]


def test_water_mask_bad_shape_raises():
    with pytest.raises(WaterMaskError):
        water_mask((0.0, 0.0, 1.0, 1.0), (0, 0))
