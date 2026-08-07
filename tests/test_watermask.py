"""Authoritative water-mask integration — hermetic parts only.

The Overpass fetch itself is network (and uses curl, which the socket guard can't block),
so these cover the pure logic: land_shore_distance driven by an explicit land_mask, and
that water_mask fails cleanly (WaterMaskError) on bad input so corrected_fields can fall back.
"""
import numpy as np
import pytest

from tbay_fishcast.features.overlay import land_shore_distance
from tbay_fishcast.ingest import watermask as wm
from tbay_fishcast.ingest.watermask import WaterMaskError, iter_frozen, water_mask


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


# ---- Frozen (committed) masks: the shoreline must be deterministic, not re-fetched live ----

def test_frozen_masks_present_and_sane():
    """Every committed mask loads, is a plausible shoreline (not all-land/all-water), and
    carries its bounds. If this fails the runner will silently re-fetch Overpass and the
    coast can flicker between builds (the Silver-tip bug)."""
    entries = list(iter_frozen())
    assert len(entries) >= 5, f"expected the frozen coast masks to be committed, found {len(entries)}"
    for bounds, (h, w), mask in entries:
        assert mask.shape == (h, w) and mask.dtype == bool
        assert h >= 100 and w >= 100
        frac = mask.mean()
        assert 0.03 < frac < 0.99, f"implausible water fraction {frac:.2%} (all land/water?)"
        assert bounds is not None and len(bounds) == 4


def test_water_mask_prefers_frozen_without_network(monkeypatch):
    """water_mask() must return the COMMITTED mask before any Overpass call — so a build on
    a fresh runner (empty cache, flaky network) gets the identical verified shoreline."""
    entries = list(iter_frozen())
    if not entries:
        pytest.skip("no frozen masks committed")

    def _boom(*a, **k):
        raise AssertionError("water_mask hit the network despite a frozen mask being present")

    monkeypatch.setattr(wm, "_overpass", _boom)
    monkeypatch.setattr(wm, "_fetch_barrier_ways", _boom)
    for bounds, (h, w), mask in entries:
        got = water_mask(bounds, (h, w))
        assert np.array_equal(got, mask)


def test_frozen_survives_submetre_bound_drift(monkeypatch):
    """A8: the CHS WCS returns server-snapped GeoTIFF corners, so the patch bounds can drift
    by sub-metre amounts between builds. The committed mask must STILL resolve (content match)
    instead of falling through to a silent live-Overpass refetch — the ADR-020 regression."""
    entries = list(iter_frozen())
    if not entries:
        pytest.skip("no frozen masks committed")
    bounds, (h, w), mask = entries[0]

    def _boom(*a, **k):
        raise AssertionError("hit the network despite a frozen mask within drift tolerance")

    monkeypatch.setattr(wm, "_overpass", _boom)
    monkeypatch.setattr(wm, "_fetch_barrier_ways", _boom)

    # nudge every corner by a few dm — below tolerance: must resolve to the same mask
    drifted = tuple(v + d for v, d in zip(bounds, (0.4, -0.3, 0.2, -0.45)))
    got = water_mask(drifted, (h, w))
    assert np.array_equal(got, mask)
    # a perturbation well beyond tolerance is a genuinely different patch — must NOT match
    far = tuple(v + 50.0 for v in bounds)
    assert wm._frozen_get(far, h, w) is None
