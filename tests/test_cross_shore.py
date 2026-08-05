"""Cross-shore transect tests — the product logic."""
import pytest

from tbay_fishcast.features.cross_shore import (
    depth_to_distance, evaluate, isotherm_depth,
)

# a stratified summer column: warm at surface, cold at depth
DEPTHS = [0, 2, 4, 6, 8, 10, 15, 20]
STRAT = [20, 19, 16, 13, 11, 10, 8, 7]   # crosses 12C around 7 m
# bathymetry: gentle then steeper (distance m -> depth m)
BDIST = [0, 25, 50, 75, 150, 400]
BDEPTH = [0, 2, 4, 6, 9, 16]


def test_isotherm_depth_interpolates():
    z = isotherm_depth(DEPTHS, STRAT, 12.0)
    assert 6.0 < z < 8.0  # 12C sits between the 13C@6m and 11C@8m layers


def test_isotherm_absent_when_column_all_warm():
    assert isotherm_depth([0, 5, 10], [20, 18, 16], 12.0) is None  # never gets to 12


def test_depth_to_distance_interpolates():
    d = depth_to_distance(BDIST, BDEPTH, 6.0)
    assert d == pytest.approx(75.0)  # 6 m bottom at 75 m per the bathy table


def test_depth_to_distance_none_when_too_deep():
    assert depth_to_distance(BDIST, BDEPTH, 30.0) is None  # bottom never reaches 30 m


def test_evaluate_out_of_range_normal_day():
    # 12C at ~7 m -> bottom reaches 7 m somewhere past 75 m -> out of cast range
    r = evaluate(DEPTHS, STRAT, BDIST, BDEPTH, target_c=12.0, cast_range_m=75.0)
    assert r.isotherm_depth_m == pytest.approx(7.0, abs=0.5)
    assert not r.in_cast_range


def test_evaluate_in_range_during_upwelling():
    # upwelling shoals the cold water: 12C now at ~3 m -> bottom reaches 3 m at ~37 m -> IN range
    upwelled = [12, 11.5, 11, 10, 9, 8, 7, 6]
    r = evaluate(DEPTHS, upwelled, BDIST, BDEPTH, target_c=12.0, cast_range_m=75.0)
    assert r.isotherm_depth_m < 3.0
    assert r.in_cast_range
    assert "IN cast range" in r.note


def test_evaluate_flags_band_absent():
    warm = [22, 21, 20, 19, 18, 17, 16, 15]  # never 12C
    r = evaluate(DEPTHS, warm, BDIST, BDEPTH, target_c=12.0, cast_range_m=75.0)
    assert not r.in_cast_range and r.isotherm_depth_m is None
