"""Data-driven temperature correction + isotherm-band tests."""
import pytest

from tbay_fishcast.features.thermocline import (
    BiasModel,
    corrected_profile,
    isotherm_band,
    summarize_subsurface_bias,
)

# a raw LSOFS-like summer profile: warm surface, warm-biased subsurface
DEPTHS = [1, 2, 4, 6, 8, 10, 15]
RAW = [17.0, 16.5, 14.5, 12.9, 11.8, 11.0, 10.5]


def test_correction_surface_anchored():
    # surface bias -0.5 (LSOFS slightly cool) -> surface correction +0.5
    b = BiasModel(surface_bias_c=-0.5, subsurface_bias_c=3.0,
                  subsurface_bias_lo_c=2.0, subsurface_bias_hi_c=5.0)
    c = b.correction([0.0], "central")[0]
    assert c == pytest.approx(0.5)  # +0.5 at surface


def test_correction_subsurface_and_flat_below_zref():
    b = BiasModel(surface_bias_c=0.0, subsurface_bias_c=3.0,
                  subsurface_bias_lo_c=2.0, subsurface_bias_hi_c=5.0, z_ref_m=6.0)
    c6 = b.correction([6.0], "central")[0]
    c15 = b.correction([15.0], "central")[0]
    assert c6 == pytest.approx(-3.0)   # removes +3 warm bias at z_ref
    assert c15 == pytest.approx(-3.0)  # flat below z_ref


def test_corrected_profile_pulls_subsurface_down():
    b = BiasModel(surface_bias_c=0.0, subsurface_bias_c=3.0,
                  subsurface_bias_lo_c=2.0, subsurface_bias_hi_c=5.0)
    corr = corrected_profile(DEPTHS, RAW, b, "central")
    assert corr[0] == pytest.approx(RAW[0], abs=0.6)   # surface ~unchanged
    assert corr[-1] < RAW[-1]                           # deep pulled colder


def test_summarize_uses_median_and_spread():
    central, lo, hi = summarize_subsurface_bias([2.3, 5.4, 4.2])
    assert central == pytest.approx(4.2)   # median, robust to the 2.3/5.4 spread
    assert (lo, hi) == (2.3, 5.4)


def test_summarize_empty():
    assert summarize_subsurface_bias([]) == (0.0, 0.0, 0.0)


def test_isotherm_band_orders_shallow_to_deep():
    b = BiasModel(surface_bias_c=0.0, subsurface_bias_c=3.5,
                  subsurface_bias_lo_c=2.0, subsurface_bias_hi_c=5.0, n_buoys=3)
    band = isotherm_band(DEPTHS, RAW, b, 12.0)
    assert band["shallow"] is not None and band["deep"] is not None
    assert band["shallow"] <= band["central"] <= band["deep"]
    assert band["n_buoys"] == 3


def test_isotherm_band_warmer_bias_shoals():
    """A larger warm bias (hi) removes more heat -> shallower isotherm than lo."""
    b = BiasModel(surface_bias_c=0.0, subsurface_bias_c=3.5,
                  subsurface_bias_lo_c=2.0, subsurface_bias_hi_c=6.0)
    from tbay_fishcast.features.cross_shore import isotherm_depth
    z_lo = isotherm_depth(DEPTHS, corrected_profile(DEPTHS, RAW, b, "lo"), 12.0)
    z_hi = isotherm_depth(DEPTHS, corrected_profile(DEPTHS, RAW, b, "hi"), 12.0)
    assert z_hi < z_lo  # hi bias -> shallower
