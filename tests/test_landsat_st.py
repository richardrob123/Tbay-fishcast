"""Landsat ST — pure conversion + QA water-bit logic (hermetic; the COG reads are network).

The load-bearing calc is the C2 L2 ST digital-number -> Celsius conversion and the
QA_PIXEL water-bit test that keeps the sampler on water, not the 33-36 C land pixels.
"""
from tbay_fishcast.ingest.landsat_st import is_water, st_dn_to_celsius


def test_st_dn_to_celsius_matches_usgs_scaling():
    # 0 K offset -> -273.15; a mid-scale DN gives a lake-plausible temperature
    assert abs(st_dn_to_celsius(0) - (149.0 - 273.15)) < 1e-6
    # DN ~ 41600 -> ~18 C (Marina-ish); monotone increasing
    t = st_dn_to_celsius(41600)
    assert 15.0 < t < 21.0
    assert st_dn_to_celsius(42000) > st_dn_to_celsius(41000)


def test_qa_water_bit():
    assert is_water(1 << 7)              # water bit set
    assert is_water((1 << 7) | 1)        # water + other flags
    assert not is_water(0)               # clear-land
    assert not is_water(1 << 6)          # a different bit, not water
