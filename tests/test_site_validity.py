"""Tests for the site-validity guard (ADR-052).

This module exists because of a specific, expensive mistake: a conclusion about the product was
drawn from a mooring where the model was locally broken, and every check run on it looked for
bugs in OUR pipeline rather than asking whether the SITE could support the claim. These tests
pin the three outcomes that distinction has to keep separate.
"""
from __future__ import annotations

from tbay_fishcast.features import site_validity as sv


def _days(model, obs, sat, n=20):
    return [(model, obs, sat) for _ in range(n)]


def test_a_site_where_model_and_satellite_agree_can_ground_a_claim():
    v = sv.check(_days(18.2, 18.0, 18.1))
    assert v.usable and "can ground" in v.reason


def test_the_llo1_case_is_refused():
    """The real numbers: model 9.5 C, buoy 16.8 C, satellite 21.3 C. The satellite tracks the
    BUOY, so the model is locally wrong and the site cannot condemn the product elsewhere."""
    v = sv.check(_days(9.5, 16.8, 21.3))
    assert not v.usable
    assert "the model is locally wrong here" in v.reason
    assert v.model_vs_sat_c < -10


def test_a_bad_observation_is_named_as_such_not_blamed_on_the_model():
    """The other failure direction: if the in-situ reading is the one far from satellite, the
    measurement itself is untrustworthy — a different problem needing a different fix, and
    conflating the two sends you debugging the wrong thing."""
    v = sv.check(_days(18.0, 4.0, 18.2))
    assert not v.usable
    assert "OBSERVATION" in v.reason


def test_attribution_is_comparative_so_the_skin_effect_does_not_convict_the_buoy():
    """A satellite sees the top microns and a 1 m thermistor sees mixed water, so a 4-5 C gap on
    a calm sunny day is ordinary physics. An absolute bar blamed the OBSERVATION for that and let
    an 11.8 C model error pass — which is exactly backwards."""
    v = sv.check(_days(16.5, 17.9, 21.3))     # both off the skin temp, model slightly further
    assert not v.usable and "the model is locally wrong here" in v.reason


def test_a_thin_sample_cannot_judge_the_site_either_way():
    v = sv.check(_days(18.0, 18.0, 18.0, n=2))
    assert not v.usable and "cannot judge" in v.reason


def test_missing_satellite_days_are_not_counted():
    v = sv.check([(18.0, 18.0, None)] * 30)
    assert not v.usable and v.n_days == 0


def test_the_bar_is_for_gross_disagreement_not_imperfection():
    """A satellite analysis carries a few tenths of error and a 1 m sensor can sit a degree off a
    skin temperature on a calm day. Those must not disqualify a site."""
    assert sv.check(_days(18.0, 19.4, 17.9)).usable
    assert not sv.check(_days(18.0, 18.0, 14.0)).usable


def test_the_observation_arm_can_be_skipped():
    """Where only model-vs-satellite is available the check still runs, on that arm alone."""
    v = sv.check([(18.0, None, 18.1)] * 20)
    assert v.usable and v.obs_vs_sat_c is None


# --- reference validity (ADR-053) -------------------------------------------------------------

def test_a_smoothed_analysis_is_refused_as_a_skill_baseline():
    """THE SECOND WRONG CONCLUSION, in miniature. GLSEA's day-to-day change sd is 0.38 C where
    the water's is 1.93 C, so persisting it scored 0.295 C — its own smoothness — and nothing
    physical can beat that. The reference has to be checked before it is used, not after."""
    import math
    days = _days_span(40)
    water = {d: 10.0 + 3.0 * math.sin(i * 1.7) for i, d in enumerate(days)}     # lively
    smooth = {d: 10.0 + 3.0 * math.sin(i * 0.12) for i, d in enumerate(days)}   # relaxed
    v = sv.reference_variability(smooth, water)
    assert not v["usable_as_skill_baseline"]
    assert "SMOOTHER than the water" in v["reason"]
    assert v["variability_ratio"] < 0.5


def test_a_reference_that_tracks_the_water_is_accepted():
    import math
    days = _days_span(40)
    water = {d: 10.0 + 3.0 * math.sin(i * 1.7) for i, d in enumerate(days)}
    good = {d: v + 0.2 for d, v in water.items()}
    v = sv.reference_variability(good, water)
    assert v["usable_as_skill_baseline"] and v["variability_ratio"] > 0.9


def _days_span(n):
    import datetime as dt
    d0 = dt.date(2025, 6, 1)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def test_a_thin_overlap_cannot_characterise_the_reference():
    v = sv.reference_variability({"2025-06-01": 10.0}, {"2025-06-01": 10.0})
    assert not v["usable_as_skill_baseline"] and "unjudged" in v["reason"]


def test_a_thin_sample_cannot_CERTIFY_a_reference_either():
    """SEEN LIVE. A 16-day slice returned 'usable, ratio 1.10' for the same satellite product a
    99-day slice had measured at 5x too smooth. A variance ratio is wide at small n, and a guard
    that can be talked into approving on thin evidence is worse than no guard."""
    import math
    days = _days_span(16)
    water = {d: 10.0 + 3.0 * math.sin(i * 1.7) for i, d in enumerate(days)}
    smooth = {d: 10.0 + 3.0 * math.sin(i * 0.12) for i, d in enumerate(days)}
    v = sv.reference_variability(smooth, water)
    assert not v["usable_as_skill_baseline"], "16 days must not certify a reference"
    assert "unjudged" in v["reason"]


def test_a_deep_sensor_is_not_compared_against_a_skin_temperature():
    """MISATTRIBUTION CAUGHT LIVE. Fed a 6 m thermistor against satellite SST, the check blamed
    the BUOY for what was simply the thermocline. Below the mixed layer they are not the same
    quantity and the check must refuse."""
    v = sv.check([(18.0, 8.0, 18.2)] * 30, obs_depth_m=6.0)
    assert not v.usable and "thermocline" in v.reason
    ok = sv.check([(18.0, 17.6, 18.2)] * 30, obs_depth_m=1.0)
    assert ok.usable
