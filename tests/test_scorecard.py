"""Verification scorecard tests — the math G1/G2 are read from."""
import math

import numpy as np
import pytest

from tbay_fishcast.verification.scorecard import (
    anomaly_correlation,
    band_coverage,
    contingency,
    leave_one_out_mae,
    temperature_error,
)


def test_mae_bias_rmse_basic():
    model = [10.0, 12.0, 14.0]
    truth = [11.0, 11.0, 14.0]
    s = temperature_error(model, truth)
    assert s.n == 3
    assert s.mae == pytest.approx((1 + 1 + 0) / 3)
    assert s.bias == pytest.approx((-1 + 1 + 0) / 3)
    assert s.rmse == pytest.approx(math.sqrt((1 + 1 + 0) / 3))
    assert s.median_abs == pytest.approx(1.0)
    assert s.p90_abs == pytest.approx(1.0, abs=0.2)


def test_pearson_r_perfect_correlation():
    s = temperature_error([1.0, 2.0, 3.0, 4.0], [2.0, 3.0, 4.0, 5.0])  # truth = model+1
    assert s.pearson_r == pytest.approx(1.0)
    assert s.bias == pytest.approx(-1.0)


def test_pearson_r_nan_without_variance():
    s = temperature_error([5.0, 5.0, 5.0], [1.0, 2.0, 3.0])  # model constant
    assert math.isnan(s.pearson_r)


def test_nan_pairs_dropped():
    s = temperature_error([10.0, np.nan, 14.0], [11.0, 5.0, 14.0])
    assert s.n == 2


def test_depth_caveat_carried():
    s = temperature_error([10.0], [10.0])
    assert "6 m" in s.depth_caveat  # never presented as a clean 6 m verification


def test_empty_is_nan_not_crash():
    s = temperature_error([], [])
    assert s.n == 0 and math.isnan(s.mae)


def test_contingency_pod_far():
    all_days = set(range(10))
    observed = {1, 2, 3, 4}
    forecast = {2, 3, 4, 5, 6}
    c = contingency(forecast, observed, all_days)
    assert c.hits == 3        # 2,3,4
    assert c.misses == 1      # 1
    assert c.false_alarms == 2  # 5,6
    assert c.pod == pytest.approx(3 / 4)
    assert c.far == pytest.approx(2 / 5)


def test_contingency_no_events_pod_nan():
    c = contingency(set(), set(), set(range(5)))
    assert math.isnan(c.pod) and math.isnan(c.far)
    assert c.correct_neg == 5


def test_anomaly_correlation_ignores_shared_trend():
    # two series with the SAME linear trend but opposite wiggles -> negative anomaly r
    x = np.arange(10, dtype=float)
    wig = np.array([0, 1, 0, -1, 0, 1, 0, -1, 0, 1], dtype=float)
    model = 2 * x + wig
    truth = 2 * x - wig          # identical trend, opposite anomalies
    assert anomaly_correlation(model, truth) < -0.5


def test_anomaly_correlation_perfect_wiggle_match():
    x = np.arange(12, dtype=float)
    wig = np.sin(x)
    assert anomaly_correlation(3 * x + wig, 5 + 3 * x + wig) == pytest.approx(1.0, abs=1e-6)


def test_leave_one_out_mae_uses_others_bias():
    # three groups each warm-biased by ~ +3; held-out MAE should be small (~0)
    groups = {
        "a": ([13, 14, 15], [10, 11, 12]),   # bias +3
        "b": ([14, 15, 16], [11, 12, 13]),   # bias +3
        "c": ([12, 13, 14], [9, 10, 11]),    # bias +3
    }
    out = leave_one_out_mae(groups)
    assert set(out) == {"a", "b", "c"}
    for v in out.values():
        assert v == pytest.approx(0.0, abs=1e-9)   # others' bias (+3) perfectly corrects


def test_leave_one_out_penalizes_odd_group_out():
    groups = {
        "a": ([13, 14], [10, 11]),   # +3
        "b": ([14, 15], [11, 12]),   # +3
        "odd": ([20, 21], [10, 11]), # +10 — very different bias
    }
    out = leave_one_out_mae(groups)
    assert out["odd"] > 5.0          # corrected by +3 from others, still ~7 off


def test_band_coverage_counts_inside():
    model = [15.0, 15.0, 15.0, 15.0]
    truth = [13.0, 12.0, 10.0, 9.0]  # corrected band = [model-4, model-1] = [11,14]
    # 13 in, 12 in, 10 out, 9 out -> 0.5
    assert band_coverage(model, truth, lo_bias=1.0, hi_bias=4.0) == pytest.approx(0.5)
