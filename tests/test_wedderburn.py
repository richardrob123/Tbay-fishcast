"""Wedderburn-number physics tests (PLAN task 4)."""
import math

import pytest

from tbay_fishcast.features.wedderburn import (
    friction_velocity_water, upwelling_favorable, wedderburn_number,
)


def test_calm_wind_is_infinite_W():
    assert wedderburn_number(0.0, 10.0) == math.inf


def test_stronger_wind_lowers_W():
    w_light = wedderburn_number(3.0, 10.0)
    w_strong = wedderburn_number(9.0, 10.0)
    assert w_strong < w_light


def test_shallower_mixed_layer_lowers_W():
    # W ~ h^2, so a shallower epilimnion is more easily overturned
    assert wedderburn_number(6.0, 5.0) < wedderburn_number(6.0, 15.0)


def test_favorable_classification_monotone_in_wind():
    # at fixed mixed layer, there is a wind speed above which it flips favorable
    ml = 10.0
    assert upwelling_favorable(40.0, ml) is True    # gale -> favorable
    assert upwelling_favorable(2.0, ml) is False     # near calm -> not


def test_friction_velocity_positive_and_scales():
    assert friction_velocity_water(10.0) > friction_velocity_water(5.0) > 0


def test_zero_mixed_layer_rejected():
    with pytest.raises(ValueError):
        wedderburn_number(5.0, 0.0)
