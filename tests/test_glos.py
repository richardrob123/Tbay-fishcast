"""GLOS ERDDAP thermistor-chain parser tests (hermetic — parse a sample payload, no network)."""
from datetime import datetime, timezone

from tbay_fishcast.ingest.glos import parse_chain_csv, profile_at

SAMPLE = """time,depth,sea_water_temperature
UTC,m,K
2026-08-04T12:00:00Z,2.0,291.15
2026-08-04T12:00:00Z,6.0,287.59
2026-08-04T12:00:00Z,10.0,NaN
"""


def test_skips_header_and_units_rows():
    samples = parse_chain_csv(SAMPLE)
    assert len(samples) == 2  # NaN row dropped


def test_converts_kelvin_to_celsius():
    samples = parse_chain_csv(SAMPLE)
    by_depth = {s.depth_m: s.temp_c for s in samples}
    assert by_depth[2.0] == 18.0  # 291.15 K
    assert round(by_depth[6.0], 2) == 14.44  # 287.59 K


def test_drops_nan_rows():
    samples = parse_chain_csv(SAMPLE)
    assert all(s.depth_m != 10.0 for s in samples)


def test_parses_utc_time():
    samples = parse_chain_csv(SAMPLE)
    assert samples[0].time == datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    assert samples[0].time.tzinfo == timezone.utc


def test_empty_and_header_only():
    assert parse_chain_csv("") == []
    assert parse_chain_csv("time,depth,sea_water_temperature\nUTC,m,K\n") == []


def test_profile_at_returns_depth_temp_map():
    samples = parse_chain_csv(SAMPLE)
    target = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    profile = profile_at(samples, target, tol_h=1.5)
    assert profile[2.0] == 18.0
    assert round(profile[6.0], 2) == 14.44


def test_profile_at_averages_duplicate_depths():
    dup_csv = SAMPLE + "2026-08-04T12:10:00Z,2.0,293.15\n"
    samples = parse_chain_csv(dup_csv)
    target = datetime(2026, 8, 4, 12, 5, tzinfo=timezone.utc)
    profile = profile_at(samples, target, tol_h=1.5)
    # (291.15 + 293.15)/2 - 273.15 = 19.0
    assert profile[2.0] == 19.0


def test_profile_at_empty_when_out_of_tolerance():
    samples = parse_chain_csv(SAMPLE)
    target = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)  # a day away
    assert profile_at(samples, target, tol_h=1.5) == {}
