"""Bias-correction tests."""
import numpy as np
import pytest

from tbay_fishcast.features.calibration import BiasCorrection


def test_fit_recovers_offset():
    raw = np.array([10.0, 12.0, 14.0])
    obs = raw + 3.0  # obs consistently 3C warmer
    bc = BiasCorrection.fit(raw, obs)
    assert bc.offset_c == pytest.approx(3.0)
    assert bc.n_fit == 3


def test_apply_reduces_error_on_biased_series():
    raw = np.array([10.0, 11.0, 12.0, 13.0])
    obs = raw + 3.0
    bc = BiasCorrection.fit(raw, obs)
    before = np.mean(np.abs(raw - obs))
    after = np.mean(np.abs(bc.apply(raw) - obs))
    assert before == pytest.approx(3.0)
    assert after == pytest.approx(0.0)


def test_offset_does_not_change_anomalies():
    # a constant offset must leave deviations-from-mean (the timing signal) unchanged
    raw = np.array([10.0, 15.0, 8.0, 12.0])
    bc = BiasCorrection(offset_c=-3.0, n_fit=10)
    corr = bc.apply(raw)
    assert np.allclose(raw - raw.mean(), corr - corr.mean())


def test_nan_pairs_ignored():
    raw = np.array([10.0, np.nan, 14.0])
    obs = np.array([13.0, 20.0, 17.0])
    bc = BiasCorrection.fit(raw, obs)
    assert bc.n_fit == 2 and bc.offset_c == pytest.approx(3.0)


def test_empty_fit_raises():
    with pytest.raises(ValueError):
        BiasCorrection.fit([np.nan], [np.nan])
