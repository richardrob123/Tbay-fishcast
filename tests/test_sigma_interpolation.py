"""Property + golden tests for sigma-layer -> fixed-depth interpolation.

Pins CLAUDE rule 2's named properties:
  - interpolated temp is bounded by the two bracketing layer temps;
  - a target below the deepest layer clamps to the bottom-layer temp;
  - a target above the shallowest layer clamps to the surface-layer temp.
"""
import numpy as np
import pytest

from tbay_fishcast.features.sigma import interp_column, layer_depths

# LSOFS-style 20-layer sigma column (negative, surface -> bottom), uniform spacing.
SIGLAY20 = -(np.arange(20) + 0.5) / 20.0  # -0.025 .. -0.975


def _profile(surface, bottom, n=20):
    """A monotonic warm-surface/cold-bottom profile (August stratification)."""
    return np.linspace(surface, bottom, n)


def test_layer_depths_span_column():
    h = 20.0
    z = layer_depths(SIGLAY20, h, zeta=0.0)
    assert z[0] == pytest.approx(0.025 * h)     # surface-most
    assert z[-1] == pytest.approx(0.975 * h)    # bottom-most
    assert np.all(np.diff(z) > 0)               # strictly increasing depth


def test_positive_siglay_rejected():
    with pytest.raises(ValueError):
        layer_depths(np.array([0.1, -0.5]), 10.0)


def test_nonpositive_column_rejected():
    with pytest.raises(ValueError):
        layer_depths(SIGLAY20, h=0.0)


@pytest.mark.parametrize("target", [1.0, 3.3, 6.0, 9.9, 15.0, 19.0])
def test_interp_bounded_by_bracketing_layers(target):
    """PROPERTY: interpolated value lies within the bracketing layers' temps."""
    h = 20.0
    tp = _profile(18.0, 4.0)
    z = layer_depths(SIGLAY20, h)
    res = interp_column(tp, SIGLAY20, h, [target])
    # find bracketing layer temps
    lo = np.searchsorted(z, target) - 1
    lo = np.clip(lo, 0, len(z) - 2)
    t_lo, t_hi = sorted((tp[lo], tp[lo + 1]))
    assert t_lo - 1e-9 <= res.temp_c[0] <= t_hi + 1e-9


def test_deep_target_clamps_to_bottom():
    """PROPERTY: target deeper than the water column -> bottom-layer temp, clamped."""
    h = 12.0
    tp = _profile(17.0, 5.0)
    res = interp_column(tp, SIGLAY20, h, [100.0])  # 100 m in a 12 m column
    assert res.clamped[0]
    assert res.temp_c[0] == pytest.approx(tp[-1])  # bottom-most layer temp


def test_shallow_target_clamps_to_surface():
    """PROPERTY: target above the shallowest layer -> surface-layer temp, clamped."""
    h = 12.0
    tp = _profile(17.0, 5.0)
    res = interp_column(tp, SIGLAY20, h, [0.0])
    assert res.clamped[0]
    assert res.temp_c[0] == pytest.approx(tp[0])


def test_monotonic_profile_monotonic_output():
    """PROPERTY: on a monotone profile, deeper targets are never warmer."""
    h = 25.0
    tp = _profile(20.0, 3.0)
    res = interp_column(tp, SIGLAY20, h, [2, 6, 10, 15, 20])
    assert np.all(np.diff(res.temp_c) <= 1e-9)


def test_shape_mismatch_rejected():
    with pytest.raises(ValueError):
        interp_column(np.zeros(19), SIGLAY20, 10.0, [5.0])


def test_isothermal_returns_constant():
    h = 18.0
    tp = np.full(20, 9.3)
    res = interp_column(tp, SIGLAY20, h, [2, 6, 10])
    assert res.temp_c == pytest.approx([9.3, 9.3, 9.3])
    assert not res.clamped.any()  # all within span
