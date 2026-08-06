"""Regression tests for AUDIT_ROUND3 confirmed defects — each test pins a fix.

Hermetic (no network): pure-function edges and config plumbing only.
"""
from datetime import date, datetime, timedelta, timezone

import numpy as np

from tbay_fishcast.config import load_config
from tbay_fishcast.features.cross_shore import isotherm_depth
from tbay_fishcast.features.forecast import ForecastPoint
from tbay_fishcast.features.reachability import reachable_mask
from tbay_fishcast.features.upwelling import member_favorable


def test_isotherm_bottom_equality_is_not_surface():
    # Column whose ONLY target-temp water is the exact bottom sensor: the isotherm is
    # at the bottom, not the surface (the old sign-product test fell through to the
    # whole-column-cold branch and reported maximal reachability).
    assert isotherm_depth([1, 5, 9], [16.0, 14.0, 12.0], 12.0) == 9.0
    # symmetric warm-water case
    assert isotherm_depth([1, 5, 9], [8.0, 10.0, 12.0], 12.0, colder_is_target=False) == 9.0
    # untouched behaviors: normal crossing, surface equality, whole-column cases
    assert isotherm_depth([1, 5, 9], [16.0, 13.0, 11.0], 12.0) == 7.0
    assert isotherm_depth([1, 5], [12.0, 10.0], 12.0) == 1.0
    assert isotherm_depth([1, 5], [10.0, 8.0], 12.0) == 1.0        # fully cold -> surface
    assert isotherm_depth([1, 5], [18.0, 15.0], 12.0) is None      # fully warm -> none


def test_reachable_mask_depth_cap_matches_map():
    # pins had NO depth cap while the map capped at 22 m (surfaces diverged)
    depth = np.array([[10.0, 30.0]])
    dist = np.array([[50.0, 50.0]])
    uncapped = reachable_mask(depth, dist, 5.0, 75.0)
    capped = reachable_mask(depth, dist, 5.0, 75.0, max_depth_m=22.0)
    assert uncapped.tolist() == [[True, True]]
    assert capped.tolist() == [[True, False]]


def test_product_config_single_source():
    cfg = load_config()
    assert cfg.product.target_c == 12.0
    assert cfg.product.cast_m == 75.0
    assert cfg.product.max_reach_depth_m == 22.0


def test_pooled_or_prior_flags_degraded(monkeypatch):
    from tbay_fishcast.features import bias_live
    monkeypatch.setattr(bias_live, "pooled_subsurface_bias",
                        lambda cfg, day: (0.0, 0.0, 0.0, [], 0))
    c, lo, hi, rows, n, src = bias_live.pooled_or_prior(None, date(2026, 8, 6))
    assert src == "frozen-prior" and (c, lo, hi) == bias_live.FROZEN_PRIOR and n == 0
    monkeypatch.setattr(bias_live, "pooled_subsurface_bias",
                        lambda cfg, day: (3.0, 1.0, 5.0, [], 9))
    assert bias_live.pooled_or_prior(None, date(2026, 8, 6))[5] == "live"


def test_upwelling_run_breaks_across_data_gap():
    # favorable hours either side of a 12 h data hole must NOT merge into one
    # "sustained" run (the gap-bridging inflated upwelling probability)
    t0 = datetime(2026, 8, 1, 0, tzinfo=timezone.utc)
    times = [t0 + timedelta(hours=h) for h in range(4)] \
        + [t0 + timedelta(hours=16 + h) for h in range(4)]
    d = [270.0] * 8          # west wind, favorable
    v = [20.0] * 8           # above threshold
    days = member_favorable(times, d, v, min_run_h=6.0)
    assert days == set()     # two 4 h runs, not one 8 h run


def test_imagery_masks_trees_but_keeps_teal_water():
    # A no-data strip: [surveyed | smooth dark TEAL water | mottled dark TREES].
    # Teal water is green-dominant too, so colour alone would eat it — texture must
    # keep smooth water as water and reclassify only the mottled canopy (AUDIT_ROUND3).
    from tbay_fishcast.features.overlay import imagery_unsurveyed_water
    H, W = 8, 24
    depth = np.full((H, W), np.nan)
    depth[:, 0] = 10.0                                   # surveyed water column
    rng = np.random.RandomState(0)
    rgb = np.zeros((H, W, 3), np.uint8)
    teal = slice(1, 12); trees = slice(12, 24)
    rgb[:, 0] = (10, 62, 52)                             # surveyed pixel: same smooth teal
    rgb[:, teal] = np.array([6, 63, 51], np.uint8)       # smooth dark teal water (σ≈0)
    # mottled dark canopy: green-dominant, high local texture
    canopy = np.stack([rng.randint(10, 40, (H, 12)), rng.randint(45, 95, (H, 12)),
                       rng.randint(20, 55, (H, 12))], axis=2).astype(np.uint8)
    rgb[:, trees] = canopy
    mask = imagery_unsurveyed_water(depth, rgb)
    # interior columns (skip the ±2 px texture-filter bleed at each material boundary)
    teal_kept = mask[:, 3:10].mean()
    trees_kept = mask[:, 15:23].mean()
    assert teal_kept > 0.8, f"teal water wrongly dropped ({teal_kept:.2f})"
    assert trees_kept < 0.2, f"canopy wrongly kept as water ({trees_kept:.2f})"


def test_forecast_point_band_defaults():
    p = ForecastPoint(datetime(2026, 8, 6, 12, tzinfo=timezone.utc), 0, 8.0, 40.0, True)
    assert p.reachable_certain is False and p.reachable_possible is False
