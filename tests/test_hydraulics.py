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
