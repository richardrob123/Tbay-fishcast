"""Tests for lidar-derived river reach structure (ADR-045).

Every case here is a bug that actually shipped during the build, caught by the datum check rather
than by reasoning. The safety property is DIRECTIONAL, as everywhere else in this project: the
layer claims to be MEASURED, so the failure that matters is silently profiling the wrong water and
presenting it with the same confidence as the right water.
"""
from __future__ import annotations

import math

from tbay_fishcast.features import river_profile as rp


def _line(pts):
    return [(round(a, 6), round(b, 6)) for a, b in pts]


def test_chain_respects_way_order_not_proximity():
    """A nearest-neighbour walk over flattened points wanders up tributaries; chaining on shared
    endpoints keeps the river's own topology."""
    w1 = _line([(48.50, -89.22), (48.49, -89.21), (48.48, -89.20)])
    w2 = _line([(48.48, -89.20), (48.47, -89.19)])
    out = rp.chain_ways([w2, w1])
    # chain_ways is direction-AGNOSTIC by contract: it returns the connected run, and the script
    # orients it downstream afterwards using elevation (the only reliable signal for flow
    # direction). So assert the sequence up to reversal.
    assert len(out) == 4, "the shared joint node must not be duplicated"
    assert out in (w1 + w2[1:], (w1 + w2[1:])[::-1])


def test_chain_handles_ways_stored_in_reverse():
    """OSM way DIRECTION is not meaningful — plenty of waterway segments point upstream. The first
    implementation only matched head-to-tail and silently truncated those rivers."""
    w1 = _line([(48.50, -89.22), (48.49, -89.21)])
    w2 = _line([(48.48, -89.20), (48.49, -89.21)])      # reversed relative to flow
    out = rp.chain_ways([w1, w2])
    assert len(out) == 3, "a reversed way must still attach"
    assert {out[0], out[-1]} == {(48.50, -89.22), (48.48, -89.20)}


def test_chain_prefers_the_branch_that_reaches_the_mouth():
    """THE BUG THIS EXISTS FOR: at a confluence the greedy walk takes whichever branch it meets
    first, and picking the LONGEST chain then runs up a tributary past the headwaters. The Current
    River came out 19.1 km ending 84 m above lake level. Given a mouth, only chains reaching it
    are eligible."""
    #        main stem (short, reaches the mouth)      tributary (long, goes inland)
    stem = _line([(48.46, -89.19), (48.455, -89.187), (48.451, -89.1842)])
    trib = _line([(48.46, -89.19), (48.50, -89.25), (48.55, -89.32), (48.60, -89.40)])
    out = rp.chain_ways([stem, trib], mouth=(48.4510, -89.1842))
    assert out[-1] == (48.451, -89.1842) or out[0] == (48.451, -89.1842), \
        "the selected chain must touch the known mouth"


def test_chain_falls_back_when_no_branch_reaches_the_mouth():
    """A wrong mouth coordinate must not yield an empty profile — fall back and let the datum
    check flag it loudly, rather than silently producing nothing."""
    w = _line([(48.50, -89.22), (48.49, -89.21)])
    out = rp.chain_ways([w], mouth=(47.0, -90.0))
    assert out, "must fall back to the longest chain rather than return nothing"


def test_slope_sign_is_positive_downhill():
    dist = [0.0, 100.0, 200.0, 300.0]
    z = [110.0, 109.0, 108.0, 107.0]           # dropping 1 m per 100 m = 10 m/km
    s = rp.reach_slope(dist, z, window_m=400.0)
    assert all(abs(v - 10.0) < 0.01 for v in s if math.isfinite(v))


def test_edges_are_measured_percentiles_not_constants():
    """No arbitrary thresholds (standing project rule): doubling every slope must double the
    edges, because they are percentiles of the measured distribution."""
    base = [float(i) for i in range(1000)]
    e1 = rp.calibrate(base)
    e2 = rp.calibrate([2 * v for v in base])
    for k in ("flat", "steep", "barrier"):
        assert abs(e2[k] - 2 * e1[k]) < 1e-6


def test_calibration_refuses_thin_evidence():
    try:
        rp.calibrate([1.0, 2.0, 3.0])
    except ValueError as e:
        assert "too thin" in str(e)
    else:
        raise AssertionError("must refuse to calibrate on a handful of samples")


def test_short_reaches_are_dropped():
    """A reach shorter than a couple of channel widths is bed noise, not a pool."""
    dist = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
    z = [100.0] * 10
    ll = [(48.4, -89.2)] * 10
    cls = ["pool"] * 3 + ["rapid"] * 1 + ["pool"] * 6      # the single rapid is 0 m long
    out = rp.to_reaches(dist, z, ll, cls, min_len_m=25.0)
    assert all(r.cls == "pool" for r in out)


def test_holding_water_below_barrier_is_identified():
    """For migratory fish the barrier is the destination, not an exclusion — the pool below it is
    the fishing water, and that link is what the product actually surfaces."""
    ll = (48.4, -89.2)
    reaches = [
        rp.Reach("riffle", 0, 100, 210, 209, 10, *ll),
        rp.Reach("barrier", 100, 140, 209, 200, 225, *ll),
        rp.Reach("pool", 140, 400, 200, 199.9, 0.4, *ll),
    ]
    link = rp.holding_water_below(reaches)
    assert link == {1: 2}


def test_holding_water_ignores_a_distant_pool():
    ll = (48.4, -89.2)
    reaches = [
        rp.Reach("barrier", 0, 40, 209, 200, 225, *ll),
        rp.Reach("riffle", 40, 900, 200, 195, 5.6, *ll),
        rp.Reach("pool", 900, 1200, 195, 194.9, 0.3, *ll),
    ]
    assert rp.holding_water_below(reaches, max_gap_m=150.0) == {}


def test_densify_produces_even_spacing():
    pts = [(48.40, -89.20), (48.41, -89.20)]
    out, dist = rp.densify(pts, 50.0)
    gaps = [b - a for a, b in zip(dist, dist[1:])]
    assert all(abs(g - 50.0) < 1e-6 for g in gaps)
    assert len(out) == len(dist)
