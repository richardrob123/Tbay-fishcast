"""Tests for tracing an ordered shoreline out of the frozen water masks (ADR-046).

What can go wrong here is geometric and silent: a line drawn on the wrong side of the bank, a
coast traced twice because two mask tiles overlap, or classification points that land in the lake
because the "shore" vertex sits exactly on the water's edge. Each has a test.
"""
from __future__ import annotations

import math

import numpy as np

from tbay_fishcast.features import shoreline as sl

# a 6-column strip: land on the left, water on the right, boundary between col 2 and col 3
BOUNDS = (-9930000.0, 6170000.0, -9930000.0 + 60.0, 6170000.0 + 60.0)   # 10 m pixels, 6x6


def _strip():
    m = np.zeros((6, 6), dtype=bool)
    m[:, 3:] = True          # True = water
    return m


def test_boundary_is_a_single_ordered_chain_of_the_right_length():
    ch = sl.trace_mask(_strip(), BOUNDS)
    assert len(ch) == 1, "a straight coast is one polyline, not six fragments"
    c = ch[0]
    assert len(c["pts"]) == len(c["bank"]) + 1 == 7
    lons = [p[0] for p in c["pts"]]
    lats = [p[1] for p in c["pts"]]
    assert max(lons) - min(lons) < 1e-9, "the traced line is vertical, like the coast it follows"
    assert lats == sorted(lats) or lats == sorted(lats, reverse=True), "vertices must be ordered"


def test_bank_samples_are_on_the_land_side():
    """If the classification point sits in the water there is no parcel under it and the whole
    coast reads `unknown` — the failure mode is a blank map, not an exception."""
    c = sl.trace_mask(_strip(), BOUNDS)[0]
    edge_lon = c["pts"][0][0]
    assert all(b[0] < edge_lon for b in c["bank"]), "land is west of the boundary here"
    assert all(nx < 0 for nx, _ny in c["normal"]), "the inward normal must point at the land"


def test_normal_flips_when_the_water_is_on_the_other_side():
    m = np.zeros((6, 6), dtype=bool)
    m[:, :3] = True                     # water on the LEFT now
    c = sl.trace_mask(m, BOUNDS)[0]
    assert all(nx > 0 for nx, _ny in c["normal"])
    assert all(b[0] > c["pts"][0][0] for b in c["bank"])


def test_segment_lengths_are_ground_metres_not_mercator_metres():
    """WebMercator metres are inflated by 1/cos(lat) — 1.51x at 48.4 N. Sampling 'every 60 m'
    without the correction silently samples every 90 m."""
    c = sl.trace_mask(_strip(), BOUNDS)[0]
    assert all(abs(s - 10.0 * math.cos(math.radians(48.4))) < 0.3 for s in c["seg_m"])


def test_an_island_traces_as_a_closed_ring():
    m = np.ones((7, 7), dtype=bool)
    m[2:5, 2:5] = False                  # a 3x3 island in open water
    ch = sl.trace_mask(m, BOUNDS[:2] + (BOUNDS[0] + 70.0, BOUNDS[1] + 70.0))
    assert len(ch) == 1
    pts = ch[0]["pts"]
    assert len(pts) == 13, "12 unit edges around a 3x3 block, closing on itself"
    assert pts[0] == pts[-1], "a ring must close"


def test_overlapping_tiles_are_not_traced_twice():
    """The frozen set is a pyramid: coarse masks sit entirely inside finer ones, and the 6 m tiles
    overlap by 13-40%. Tracing all of them would draw parts of the coast twice, at two
    resolutions, with two independent classifications fighting over the same pixel."""
    fine = (_strip(), BOUNDS)
    coarse = (_strip(), BOUNDS)          # identical extent
    both = sl.trace_all([_Fake("a", *fine), _Fake("b", *coarse)])
    assert len(both) == 1, "the second, fully covered tile must contribute nothing"


class _Fake:
    """Stands in for a mask file path so trace_all can be tested without touching disk."""
    def __init__(self, stem, mask, bounds):
        self.stem, self._m, self._b = stem, mask, bounds

    def __str__(self):
        return self.stem


_real_load = sl.load_mask


def setup_module(_mod):
    sl.load_mask = lambda p: (p._m, p._b) if isinstance(p, _Fake) else _real_load(p)


def teardown_module(_mod):
    sl.load_mask = _real_load


def test_sampling_covers_a_chain_at_the_requested_spacing():
    seg = [10.0] * 100                                  # 1 km of coast
    idx = sl.sample_positions(seg, 100.0)
    assert len(idx) == 10
    assert idx == sorted(idx) and idx[0] >= 0 and idx[-1] < len(seg)


def test_a_chain_shorter_than_one_sample_still_gets_classified():
    """A 40 m rock is not a reason to leave a hole in the line."""
    assert sl.sample_positions([10.0] * 4, 120.0) == [2]
    assert sl.sample_positions([], 120.0) == []


def test_runs_collapse_adjacent_equal_labels():
    assert sl.runs_from_labels(["a", "a", "b", "b", "b"]) == [(0, 2, "a"), (2, 5, "b")]
    assert sl.runs_from_labels([]) == []
    assert sl.runs_from_labels(["a"]) == [(0, 1, "a")]


def test_chain_nodes_are_contiguous():
    """A chain whose consecutive nodes do not share an edge would draw a line across the bay."""
    m = np.zeros((8, 8), dtype=bool)
    m[4:, :] = True
    m[2:4, 5:] = True                                   # an L-shaped coast, so orientation matters
    for c in sl.trace_mask(m, BOUNDS[:2] + (BOUNDS[0] + 80.0, BOUNDS[1] + 80.0)):
        pts = c["pts"]
        d = [math.hypot((a[0] - b[0]) * 74000, (a[1] - b[1]) * 111000)
             for a, b in zip(pts, pts[1:])]
        assert all(abs(x - d[0]) < 1.0 for x in d), "every step is one lattice edge"
