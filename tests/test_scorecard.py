"""Verification scorecard tests — the math G1/G2 are read from."""
import math

import numpy as np
import pytest

from tbay_fishcast.verification.scorecard import contingency, temperature_error


def test_mae_bias_rmse_basic():
    model = [10.0, 12.0, 14.0]
    truth = [11.0, 11.0, 14.0]
    s = temperature_error(model, truth)
    assert s.n == 3
    assert s.mae == pytest.approx((1 + 1 + 0) / 3)
    assert s.bias == pytest.approx((-1 + 1 + 0) / 3)
    assert s.rmse == pytest.approx(math.sqrt((1 + 1 + 0) / 3))


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
