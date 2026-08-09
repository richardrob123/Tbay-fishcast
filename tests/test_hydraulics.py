"""Tests for the 1-D hydraulic layer (ADR-045).

The safety property is the same one that runs through this project: the layer is T3 DERIVED, and
its single unmeasured input (Manning's n) propagates a 2.5x spread into the Froude number. So the
failures that matter are (a) claiming a flow regime the uncertainty does not support, and
(b) reporting a number at all where the physics does not apply.
"""
from __future__ import annotations

import math

from tbay_fishcast.features import hydraulics as hy


def test_depth_and_velocity_are_physically_sensible():
    """A riffle should be ankle-to-knee deep and quick; a pool deeper and slower."""
    riffle = hy.solve(10.0, 28.0, 8.0)
    pool = hy.solve(10.0, 28.0, 0.5)
    assert 0.15 < riffle.depth_m < 0.6
    assert 0.4 < pool.depth_m < 1.5
    assert pool.depth_m > riffle.depth_m
    assert pool.velocity_ms < riffle.velocity_ms


def test_froude_crosses_one_between_riffle_and_rapid():
    """Fr = 1 is a PHYSICAL transition, and it must land between fast-riffle and rapid rather
    than somewhere convenient. This is what lets the classifier avoid a chosen threshold."""
    assert hy.solve(10.0, 28.0, 0.5).froude < 0.3        # pool: tranquil
    assert hy.solve(10.0, 28.0, 8.0).froude < 1.0        # riffle: still subcritical
    assert hy.solve(10.0, 28.0, 40.0).froude > 1.0       # rapid: supercritical


def test_supercritical_is_only_claimed_when_the_whole_band_clears_one():
    """THE CENTRAL HONESTY TEST. n is unmeasured and drives a 2.5x spread in Fr, so a mid-estimate
    just above 1 does NOT justify calling a reach broken water."""
    s = hy.solve(10.0, 28.0, 40.0)
    assert s.froude > 1.0, "mid estimate is supercritical"
    assert s.froude_lo < 1.0 < s.froude_hi, "but the band straddles critical"
    assert s.state == "transitional", "so the regime must be reported as unresolved"
    # a genuinely violent chute clears the band and may be claimed
    steep = hy.solve(10.0, 28.0, 400.0)
    assert steep.froude_lo > 1.0 and steep.state == "supercritical"


def test_band_ordering_follows_the_physics_of_n():
    """A rougher bed (higher n) gives deeper, slower flow and therefore a LOWER Froude number.
    Getting this backwards would invert every regime call."""
    s = hy.solve(10.0, 28.0, 8.0)
    assert s.froude_lo < s.froude < s.froude_hi
    assert hy.manning_depth(10, 28, 8.0, hy.N_HI) > hy.manning_depth(10, 28, 8.0, hy.N_LO)


def test_backwater_is_detected_and_reports_no_numbers():
    """Near the mouth the lake sets depth, not slope. Manning must not be applied there, and the
    tolerance comes from our own measured datum error rather than a round number."""
    s = hy.solve(20.0, 100.0, 0.02, z_m=183.0, lake_datum_m=183.2)
    assert s.backwater and s.state == "backwater"
    assert s.depth_m is None and s.froude is None, "no fabricated depth where physics doesn't apply"
    up = hy.solve(20.0, 100.0, 2.0, z_m=210.0, lake_datum_m=183.2)
    assert not up.backwater and up.depth_m is not None


def test_non_physical_inputs_yield_no_state_rather_than_a_number():
    for bad in (hy.solve(10, 28, 0.0), hy.solve(10, 0.0, 8.0),
                hy.solve(0.0, 28, 8.0), hy.solve(None, 28, 8.0)):
        assert bad.depth_m is None and bad.state == "unknown"


def test_zero_slope_does_not_divide_by_zero():
    assert hy.manning_depth(10, 28, 0.0) is None
    assert hy.manning_depth(10, 28, -1.0) is None


def test_velocity_gradient_signs_deceleration_into_a_pool():
    d = [0.0, 50.0, 100.0]
    assert hy.velocity_gradient([1.5, 1.0, 0.5], d)[1] < 0     # slowing into a pool
    assert hy.velocity_gradient([0.5, 1.0, 1.5], d)[1] > 0     # accelerating into a chute
    assert hy.velocity_gradient([1.0, None, 1.0], d)[1] == 0.0
    assert hy.velocity_gradient([None, 1.0, 1.5], d)[1] is None


def test_seams_distinguish_kinds_rather_than_scoring_one_blob():
    """Deceleration, bends and constrictions are fished differently, so they must stay separate
    labels — and a bend's SIDE has to survive, or the tool sends someone to the shallow bank."""
    tags = hy.find_seams([-0.05], [0.6], [-1.2],
                         dv_edge=0.01, curv_edge=0.4, dwds_edge=0.6)[0]
    assert "seam_decel" in tags
    assert "seam_bend_right" in tags
    assert "seam_constriction" in tags
    quiet = hy.find_seams([0.0001], [0.01], [0.01],
                          dv_edge=0.01, curv_edge=0.4, dwds_edge=0.6)[0]
    assert quiet == []


def test_froude_is_dimensionless_and_comparable_across_rivers():
    """The failure this replaces: raw slope made the Kam and McVicar incomparable. Fr must put a
    big flat river and a small steep creek on one scale without any regional calibration."""
    kam = hy.solve(21.0, 114.0, 1.0)
    mcvicar = hy.solve(0.3, 8.0, 9.0)
    assert kam.froude is not None and mcvicar.froude is not None
    assert 0.2 < kam.froude / mcvicar.froude < 5.0


def test_implausible_depth_is_refused_rather_than_reported():
    """THE CURRENT RIVER CASE. Station 02AB014 gauges a tributary at 0.08 m3/s, and Manning duly
    returns a 0.02 m depth for a 28 m channel. A 2 cm 'river' is a wet road, and reporting it as a
    measurement would launder a bad gauge into a confident number."""
    bad = hy.solve(0.084, 28.0, 8.0)
    assert bad.depth_m is None and bad.state == "unknown"
    assert "wrong gauge" in bad.note
    good = hy.solve(10.0, 28.0, 8.0)
    assert good.depth_m is not None


def test_width_rescales_with_discharge():
    """THE SEASON MISMATCH. Widths are the WETTED width on the lidar date; the Current runs at
    2.3% of that flow in August, and pairing a freshet width with a low-flow discharge produced a
    3.5 cm depth on a 28 m river."""
    aug = hy.width_at_flow(28.0, 0.27, 11.80)
    assert 8.0 < aug < 14.0, f"August width should be roughly a third of May's, got {aug:.1f}"
    assert hy.width_at_flow(28.0, 11.80, 11.80) == 28.0, "at the reference flow, width is unchanged"
    assert hy.width_at_flow(28.0, 0.0, 11.8) is None
    assert hy.width_at_flow(28.0, 1.0, 0.0) is None


def test_rescaled_width_makes_the_current_river_plausible_again():
    w = hy.width_at_flow(28.0, 0.27, 11.80)
    s = hy.solve(0.27, w, 8.0)
    assert s.depth_m is not None, "with the right width it must no longer be refused"
    assert 0.02 < s.depth_m < 0.5 and 0.1 < s.velocity_ms < 1.5


def test_scour_pools_are_flagged_as_lower_bounds():
    """Manning solves NORMAL depth; a pool is cut below the uniform-flow profile with its depth
    set by the downstream riffle. Reporting a normal depth as the pool's would overstate what we
    know about the one quantity a fishing tool most wants."""
    pool = hy.solve(0.27, 10.5, 0.5, mean_slope_m_km=8.7)
    assert "LOWER BOUND" in pool.note
    riffle = hy.solve(0.27, 10.5, 8.0, mean_slope_m_km=8.7)
    assert "LOWER BOUND" not in riffle.note


def test_species_seam_preference_is_ordinal_and_differs_by_species():
    """Steelhead exploit the largest gradients; salmon favour holding depth. A single universal
    'strength' ramp would be wrong for three of the four species."""
    assert hy.species_seam_signal("steelhead")["primary"] == "velocity_gradient"
    assert hy.species_seam_signal("salmon")["primary"] == "pool_depth"
    assert hy.species_seam_signal("brook_trout")["primary"] == "bend"
    assert hy.species_seam_signal("lake_trout")["primary"] == "none"


def test_unmodelled_species_gets_no_ranking_rather_than_a_default():
    assert hy.species_seam_signal("walleye")["primary"] == "none"


def test_seam_ramp_is_measured_and_nested():
    vals = [float(i) for i in range(500)]
    r = hy.seam_ramp_bands(vals)
    assert r["edges"] == sorted(r["edges"]), "a nested ramp must have monotonic edges"
    assert hy.seam_band(vals[-1], r) == len(r["qs"]), "the maximum reaches the top band"
    assert hy.seam_band(0.0, r) == 0
    doubled = hy.seam_ramp_bands([2 * v for v in vals])
    assert all(abs(b - 2 * a) < 1e-6 for a, b in zip(r["edges"], doubled["edges"])), \
        "edges are percentiles of the measured distribution, not constants"


def test_seam_ramp_refuses_thin_evidence():
    try:
        hy.seam_ramp_bands([1.0, 2.0, 3.0])
    except ValueError as e:
        assert "too thin" in str(e)
    else:
        raise AssertionError("must refuse to build a ramp from a handful of values")
