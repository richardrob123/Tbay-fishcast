"""Tests for the temperature-skill layer (ADR-049).

The thing being guarded here is not arithmetic — it is the set of ways a skill number can look
solid and mean nothing. Each test names the specific way.
"""
from __future__ import annotations

import math

from tbay_fishcast.features import thermal_skill as ts


def _pairs(n_days, f_err, b_err, per_day=10, jitter=0.15, seed=3):
    """Days with a little spread — a perfectly constant error has no bootstrap variance at all,
    which the degenerate guard correctly refuses to build an interval from."""
    import random
    rng = random.Random(seed)
    return [(f"d{d:03d}", f_err * (1 + jitter * rng.gauss(0, 1)),
             b_err * (1 + jitter * rng.gauss(0, 1)))
            for d in range(n_days) for _ in range(per_day)]


def test_a_clear_win_is_reported_as_a_win():
    r = ts.block_bootstrap_ratio(_pairs(60, 0.5, 2.0), block_days=1)
    assert r["beats"] and r["hi"] < 1.0
    assert abs(r["ratio"] - 0.25) < 0.05


def test_a_clear_loss_is_reported_as_a_loss():
    r = ts.block_bootstrap_ratio(_pairs(60, 3.0, 1.0), block_days=1)
    assert not r["beats"] and r["lo"] > 1.0
    assert r["verdict"] == "NO BETTER than the baseline"


def test_repeating_one_day_does_not_manufacture_confidence():
    """THE INFLATED-N TRAP. Five leads x ten depths on one day is 50 rows and ONE independent
    observation. A row-level bootstrap would return a tight interval around a single day's luck;
    resampling days must leave the interval wide."""
    import random
    rng = random.Random(7)
    many_rows_one_day = [("d000", rng.gauss(1.0, 1.0), rng.gauss(1.05, 1.0))
                         for _ in range(500)]
    r = ts.block_bootstrap_ratio(many_rows_one_day, block_days=1)
    assert r["n"] == 500 and r["n_days"] == 1
    assert not r["beats"], "one day cannot establish skill however many rows it carries"


def test_an_ambiguous_sample_is_inconclusive_rather_than_a_verdict():
    """The demotion rule must not fire on noise: a ratio near 1.0 has to say so."""
    import random
    rng = random.Random(11)
    pairs = [(f"d{d:03d}", abs(rng.gauss(0, 1)), abs(rng.gauss(0, 1)))
             for d in range(12) for _ in range(5)]
    r = ts.block_bootstrap_ratio(pairs, block_days=1)
    assert r["verdict"] == "inconclusive"
    assert r["lo"] < 1.0 < r["hi"]


def test_a_zero_error_baseline_does_not_divide_by_zero():
    """Lead 0 scored against persistence-from-the-same-instant has a zero-error baseline."""
    r = ts.block_bootstrap_ratio(_pairs(30, 2.0, 0.0, jitter=0.0), block_days=1)
    assert r["ratio"] is None and not r["beats"]


def test_block_length_is_measured_from_the_data():
    """A correlated error series must yield a block longer than one day; white noise must not."""
    corr = {f"d{i:03d}": [math.sin(i / 6.0)] for i in range(60)}
    white = {f"d{i:03d}": [(-1) ** i * 1.0] for i in range(60)}
    assert ts.decorrelation_days(corr) > 1
    assert ts.decorrelation_days(white) == 1


def test_the_baseline_taken_is_the_harder_one():
    """Scoring against whichever reference happens to be WORSE is how skill is manufactured."""
    assert ts.best_baseline(0.5, 2.0) == (0.5, "persistence")
    assert ts.best_baseline(2.0, 0.5) == (0.5, "climatology")
    assert ts.best_baseline(None, 2.0) == (2.0, "climatology")
    assert ts.best_baseline(1.0, None) == (1.0, "persistence")
    # sign must not decide which is "harder" — a -3 C baseline error is worse than +1 C
    assert ts.best_baseline(-3.0, 1.0) == (1.0, "climatology")


def test_depth_band_scales_inversely_with_stratification():
    """THE WHOLE POINT OF SCORING IN DEGREES. The same 1 C error is a tight band across a sharp
    thermocline and a useless one in a nearly mixed column — a single pooled metres figure
    describes neither."""
    sharp, _ = ts.depth_sigma_from_gradient(1.0, 2.0, max_m=25.0)
    soft, _ = ts.depth_sigma_from_gradient(1.0, 0.2, max_m=25.0)
    assert abs(sharp - 0.5) < 1e-9
    assert abs(soft - 5.0) < 1e-9
    assert soft > sharp


def test_a_mixed_column_yields_no_band_rather_than_a_huge_one():
    v, why = ts.depth_sigma_from_gradient(1.0, 0.0, max_m=25.0)
    assert v is None and "not constrained" in why
    v, why = ts.depth_sigma_from_gradient(1.0, 0.01, max_m=25.0)
    assert v is None and "not constrained" in why, "100 m band on a 25 m cast is not information"
    v, why = ts.depth_sigma_from_gradient(None, 1.0)
    assert v is None and "no measured" in why


def test_local_gradient_is_read_off_the_bracketing_layers():
    z = [0.0, 5.0, 10.0, 20.0]
    t = [18.0, 17.0, 8.0, 6.0]
    assert abs(ts.local_gradient(z, t, 7.0) - 1.8) < 1e-9      # (17-8)/5
    assert abs(ts.local_gradient(z, t, 15.0) - 0.2) < 1e-9     # (8-6)/10
    assert ts.local_gradient(z, t, 50.0) is None, "outside the profile is not extrapolated"


def test_sigma_table_separates_leads_and_depths():
    rows = ([{"err_c": 1.0, "lead_h": 24, "depth_m": 7.0} for _ in range(30)]
            + [{"err_c": 4.0, "lead_h": 24, "depth_m": 35.0} for _ in range(30)])
    s = ts.sigma_by_lead_depth(rows)
    vals = {(v["lead_h"], v["depth_lo"]): v["rmse_c"] for v in s.values()}
    assert abs(vals[(24, 5)] - 1.0) < 1e-9
    assert abs(vals[(24, 30)] - 4.0) < 1e-9


def test_bootstrap_is_deterministic():
    """A verdict that changes between runs cannot be audited."""
    a = ts.block_bootstrap_ratio(_pairs(30, 1.0, 2.0), block_days=3)
    b = ts.block_bootstrap_ratio(_pairs(30, 1.0, 2.0), block_days=3)
    assert a == b


def test_one_block_yields_no_interval_rather_than_false_certainty():
    """THE DEGENERATE BOOTSTRAP, and it was a live bug: with a single block every resample is the
    same sample, so the interval collapsed to a point and a point below 1.0 was reported as a
    proven win — 500 rows carrying one day of information."""
    r = ts.block_bootstrap_ratio(_pairs(2, 0.5, 2.0, per_day=250), block_days=10)
    assert r["n"] == 500 and r["n_blocks"] == 1
    assert r["lo"] is None and r["hi"] is None and not r["beats"]
    assert "insufficient independent blocks" in r["verdict"]


def test_interval_says_whether_it_is_resolved_or_approximate():
    """A 2.5% tail needs ~40 blocks to be resolved rather than approximated; the bracket must not
    imply a precision the block count cannot support."""
    coarse = ts.block_bootstrap_ratio(_pairs(60, 1.0, 2.0), block_days=10)
    fine = ts.block_bootstrap_ratio(_pairs(120, 1.0, 2.0), block_days=1)
    assert coarse["interval_quality"] == "approximate"
    assert fine["interval_quality"] == "resolved"


def test_sign_test_is_robust_where_the_bootstrap_may_not_be():
    """A systematic bias can stay correlated longer than any block we can estimate. The day-level
    sign test assumes nothing about magnitude or correlation — only which side won each day."""
    pairs = [(f"d{d:03d}", 3.0, 1.0) for d in range(118) for _ in range(9)]
    for d in range(4):                                   # four days the forecast wins
        pairs = [p for p in pairs if p[0] != f"d{d:03d}"]
        pairs += [(f"d{d:03d}", 1.0, 3.0) for _ in range(9)]
    s = ts.sign_test_days(pairs)
    assert s["wins"] == 4 and s["losses"] == 114
    assert s["p_two_sided"] < 1e-20
    assert s["win_rate"] < 0.05
