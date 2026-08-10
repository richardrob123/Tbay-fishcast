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


def test_width_gradient_signs_constriction_and_expansion():
    """A narrowing must read negative and a widening positive — the sign is what distinguishes
    a scour-producing constriction from a slack-water expansion."""
    d = [0.0, 100.0, 200.0]
    assert rp.width_gradient([30.0, 20.0, 10.0], d)[1] < 0      # narrowing
    assert rp.width_gradient([10.0, 20.0, 30.0], d)[1] > 0      # widening
    # centred difference: a gap AT the station is irrelevant (its neighbours carry it), but a
    # gap in a NEIGHBOUR must yield None rather than a fabricated gradient
    assert rp.width_gradient([20.0, None, 20.0], d)[1] == 0.0
    assert rp.width_gradient([None, 20.0, 30.0], d)[1] is None


def test_curvature_sign_identifies_the_outer_bank():
    """The scour pool sits on the OUTER bank, so the sign has to be right or the tool sends an
    angler to the shallow side of the bend."""
    left = [(48.400, -89.200), (48.401, -89.200), (48.402, -89.2005),
            (48.4025, -89.2015), (48.4028, -89.2030)]
    c = rp.curvature(left)
    mid = [v for v in c if v is not None]
    assert mid, "curvature must be defined away from the ends"
    assert rp.outer_bank(mid[0]) in ("left", "right")
    # a straight line has no meaningful bend, so no outer bank
    straight = [(48.40 + 0.001 * i, -89.20) for i in range(6)]
    assert all(rp.outer_bank(v) is None for v in rp.curvature(straight) if v is not None)


def test_stream_power_makes_a_big_flat_river_comparable_to_a_small_steep_one():
    """The whole reason omega replaced raw slope: the Kam (wide, low gradient, high Q) and McVicar
    (narrow, steep, low Q) are 4.5x apart in slope and must land close in stream power."""
    kam = rp.unit_stream_power(2.0, 20.0, 116.0)
    mcvicar = rp.unit_stream_power(9.0, 0.3, 6.0)
    assert 0.5 < kam / mcvicar < 2.0, f"omega should reconcile them, got {kam:.1f} vs {mcvicar:.1f}"


def test_stream_power_rejects_nonphysical_input():
    assert rp.unit_stream_power(5.0, 1.0, 0.0) is None
    assert rp.unit_stream_power(5.0, None, 10.0) is None
    assert rp.unit_stream_power(-5.0, 1.0, 10.0) == 0.0     # negative slope is survey noise


def test_barrier_is_species_specific():
    """A step that passes steelhead can still stop brook trout — which is exactly why the
    upstream limit has to be reported per species rather than once per river."""
    assert not rp.barrier_for(1.0, 10.0, "steelhead")
    assert rp.barrier_for(1.0, 10.0, "brook_trout")
    assert rp.barrier_for(3.0, 10.0, "steelhead")


def test_upstream_limit_walks_up_from_the_mouth():
    ll = (48.4, -89.2)
    reaches = [
        rp.Reach("riffle", 0, 100, 210, 209, 10, *ll),
        rp.Reach("barrier", 100, 110, 209, 206, 300, *ll),     # 3 m step
        rp.Reach("pool", 110, 400, 206, 205.9, 0.3, *ll),
    ]
    assert rp.upstream_limit(reaches, "steelhead") == 1
    assert rp.upstream_limit(reaches, "lake_trout") == 1
    flat = [rp.Reach("pool", 0, 400, 200, 199.9, 0.25, *ll)]
    assert rp.upstream_limit(flat, "steelhead") is None


def test_bend_radius_uses_a_width_scaled_span():
    """Measuring curvature at the scale of the channel itself reads digitising noise, not
    planform — that produced R/w of 0.30, a river doubling back inside a third of its own width.

    NOTE the test had to be written correctly to mean anything: on a PERFECT arc the circumradius
    is span-invariant by definition, so a clean curve proves nothing. The claim is specifically
    about noisy lines, so this jitters a straight centreline and asserts that a short span is
    fooled into a tight radius while a wide span is not.
    """
    import math as _m
    jitter = [(48.40 + 0.0002 * i, -89.20 + (0.00004 if i % 2 else -0.00004)) for i in range(40)]
    tight = rp.bend_radius(jitter, 20, span_m=60.0)
    wide = rp.bend_radius(jitter, 20, span_m=200.0)
    assert tight is not None and wide is not None
    assert wide > tight * 3, (
        f"a wide span must see through the jitter: short={tight:.0f} m wide={wide:.0f} m")


def test_bend_radius_is_stable_against_station_parity():
    """THE DEFECT THIS REPLACES. The three-point circumradius picked whichever stations the span
    walk landed on, so on a line with alternating jitter it returned None / 380 m / None / 3399 m
    purely by parity. A radius that depends on which index you started from is not a measurement,
    so the fit now uses every point in the span."""
    jitter = [(48.40 + 0.0002 * i, -89.20 + (0.00004 if i % 2 else -0.00004)) for i in range(40)]
    vals = [rp.bend_radius(jitter, 20, span_m=sp) for sp in (60.0, 120.0, 200.0, 400.0)]
    assert all(v is not None for v in vals), "no span may collapse to None by parity"
    assert vals == sorted(vals), "a wider span must see through jitter, monotonically"


def test_straight_river_yields_no_bends_where_a_percentile_would_invent_them():
    """The reason R/w replaced a curvature percentile: a percentile ALWAYS returns its quantile,
    so a dead-straight river would still be handed 10% 'bends'."""
    straight = [(48.40 + 0.0002 * i, -89.20) for i in range(40)]
    assert rp.bend_seams(straight, [28.0] * 40) == []


def test_bends_must_persist_to_count():
    """22 of the Kam's 73 first-pass bends were single stations with zero extent — curvature
    spikes that did not survive one measurement span. A bend has to hold across stations AND
    cover a real fraction of a channel width."""
    import math as _m
    arc = [(48.40 + 0.0009 * _m.sin(t / 8), -89.20 + 0.0009 * (1 - _m.cos(t / 8)))
           for t in range(40)]
    kept = rp.bend_seams(arc, [28.0] * 40)
    assert all(b["n_stations"] >= rp.MIN_BEND_STATIONS for b in kept)
    assert all(b["extent_m"] >= 0.5 * 28.0 - 1e-6 for b in kept)


def test_backwater_stations_are_suppressed():
    """In a drowned mouth the reach-smoothed width balloons, so offsetting by half of it throws
    the marker into open water instead of onto a bank."""
    import math as _m
    arc = [(48.40 + 0.0009 * _m.sin(t / 8), -89.20 + 0.0009 * (1 - _m.cos(t / 8)))
           for t in range(40)]
    assert rp.bend_seams(arc, [28.0] * 40) != []
    assert rp.bend_seams(arc, [28.0] * 40, suppress=[True] * 40) == []


# ---------------------------------------------------------------------------
# ADR-047: local vs reach width. The bug this guards against was visible on the
# live map — bend seams drawn 60-110 m off a 10 m stream, in the woods.
# ---------------------------------------------------------------------------

def test_local_width_fills_the_sampling_stride_but_not_saturation():
    """measure_widths runs on a stride, so most stations have no measurement; those holes are an
    artefact of sampling and interpolate. A LONGER run of None is saturation — no bank was found,
    so there is no channel — and inventing a width there is what threw seams into the woods."""
    from tbay_fishcast.ingest import hrdem

    dist = [20.0 * i for i in range(21)]                     # 21 stations, 20 m apart
    w = [None] * 21
    for i in (0, 5, 10):                                     # measured every 100 m
        w[i] = 10.0
    w[15] = 20.0
    # stations 16..20 unmeasured past the last sample; 11..14 fill; a synthetic saturated
    # stretch is made by removing the sample at 10
    out = hrdem.local_width(w, dist, max_gap_m=200.0)
    assert out[0] == 10.0 and out[5] == 10.0
    assert out[2] == 10.0, "inside the stride, interpolated"
    assert 10.0 < out[12] < 20.0, "linear between 10 m and 20 m samples"
    assert out[20] is None, "past the last measurement we do not extrapolate"

    sat = list(w)
    sat[5] = sat[10] = None                                  # 400 m with no bank found
    out2 = hrdem.local_width(sat, dist, max_gap_m=200.0)
    assert out2[5] is None and out2[7] is None, "saturation must not be filled"


def test_local_width_tracks_the_stride_instead_of_a_constant():
    """The gap limit is twice the MEASURED median sampling interval, so changing the stride does
    not quietly turn saturation into interpolation."""
    from tbay_fishcast.ingest import hrdem

    dist = [10.0 * i for i in range(41)]
    w = [None] * 41
    for i in range(0, 41, 2):                                # dense sampling: every 20 m
        w[i] = 12.0
    w[20] = w[22] = w[24] = None                             # a 60 m hole = saturation at this stride
    out = hrdem.local_width(w, dist, max_gap_m=40.0)
    assert out[1] == 12.0, "one-station holes still fill"
    assert out[22] is None, "a hole three times the sampling interval is not a sampling hole"


def test_reach_and_local_width_disagree_where_the_bug_lived():
    """The Neebing case, reduced: a 10 m stream whose 1 km window contains a 150 m confluence.
    reach_width answers 'what is this reach like' (right for the slope window); local_width
    answers 'how wide is the channel HERE' (right for a bank offset). Using the first for the
    second is a 15x positional error."""
    from tbay_fishcast.ingest import hrdem

    # a narrow throat inside a wide reach — the Neebing near its confluence, and the Current
    # either side of Boulevard Lake
    dist = [20.0 * i for i in range(51)]
    w = [None] * 51
    for i in range(0, 51, 5):
        w[i] = 150.0
    w[25] = 10.0
    local = hrdem.local_width(w, dist, max_gap_m=200.0)
    reach = hrdem.reach_width(w, dist, smooth_m=1000.0)
    assert local[25] == 10.0, "the measurement at the throat is 10 m and must survive"
    assert reach[25] == 150.0, "the reach median is the wide water either side"
    assert reach[25] / local[25] == 15.0, (
        "offsetting a bend seam by half of the WRONG one of these is a 75 m positional error "
        "on a 10 m stream")


def test_width_requires_banks_on_both_sides():
    """ADR-047: the flat-tolerance test alone finds 'everything within 0.60 m of the water', which
    is the channel where the stream is incised and the whole FIELD where it is not. On the Neebing
    that measured a 12 m stream as 152 m wide, and the bend seams drew in the woods."""
    from tbay_fishcast.ingest import hrdem

    base = 280.0
    incised = [283.0] * 40 + [280.0] * 6 + [283.0] * 40
    assert hrdem._bank_confined(incised, 40, 45, base)

    # a bank that only marginally clears the wet tolerance is not a bank — it is noise on flat
    # ground, and 1x the tolerance is cleared by construction at every edge
    marginal = [280.9] * 40 + [280.0] * 6 + [280.9] * 40
    assert not hrdem._bank_confined(marginal, 40, 45, base)

    # BOTH sides, not either: a stream at the toe of a valley wall has one good bank and one
    # floodplain, and it is the floodplain side that runs away with the width
    one_sided = [280.4] * 40 + [280.0] * 6 + [286.0] * 40
    assert not hrdem._bank_confined(one_sided, 40, 45, base)

    # the bank must be reached CLOSE to the water; a rise 100 m away is a valley, not a bank
    far = [280.2] * 20 + [280.3] * 20 + [280.0] * 6 + [280.3] * 20 + [286.0] * 20
    assert not hrdem._bank_confined(far, 40, 45, base)
