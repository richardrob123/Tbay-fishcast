"""NDBC .ocean + stdmet-wind parser tests (hermetic — parse a sample payload, no network)."""
from datetime import timezone

from tbay_fishcast.ingest.ndbc import BUOYS, parse_ocean, parse_stdmet_wind

STDMET = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC
2026 08 07 20 40 270  5.0  6.0   0.3     3    MM 144 1012.1  14.4
2026 08 07 20 30  90  3.0  4.0   0.2     3    MM 100 1012.0  14.3
2026 08 07 20 20  MM   MM   MM   0.2    MM    MM  MM 1011.9  14.2
2026 08 07 20 10 260 999   MM   0.1    MM    MM  MM 1011.8  14.1
"""


def test_parse_stdmet_wind():
    recs = parse_stdmet_wind(STDMET)
    # two valid rows (the MM-speed and 999 rows are dropped)
    assert len(recs) == 2
    assert recs[0].dir_deg == 270.0
    assert recs[0].speed_kn == round(5.0 * 1.943844, 2)   # m/s -> kn
    assert recs[0].time.tzinfo == timezone.utc
    assert recs[1].dir_deg == 90.0


def test_parse_stdmet_wind_empty_and_header():
    assert parse_stdmet_wind("") == []
    assert parse_stdmet_wind("#h\n#u\n") == []

SAMPLE = """#YY  MM DD hh mm   DEPTH  OTMP   COND   SAL
#yr  mo dy hr mn       m  degC  mS/cm   psu
2025 08 01 12 00     1.0 18.30     MM    MM
2025 08 01 11 50     1.0 18.10     MM    MM
2025 08 01 11 40     1.0    MM     MM    MM
2025 08 01 11 30     1.0 999.0     MM    MM
2025 08 01 11 20     6.0  9.90     MM    MM
"""


def test_parses_valid_records():
    recs = parse_ocean(SAMPLE)
    assert len(recs) == 3  # two 1 m + one 6 m; MM and 999.0 dropped
    assert recs[0].temp_c == 18.30 and recs[0].depth_m == 1.0
    assert recs[0].time.tzinfo == timezone.utc
    assert recs[-1].depth_m == 6.0 and recs[-1].temp_c == 9.90


def test_drops_missing_and_fill():
    recs = parse_ocean(SAMPLE)
    assert all(-5 < r.temp_c < 50 for r in recs)


def test_empty_and_header_only():
    assert parse_ocean("") == []
    assert parse_ocean("#header\n#units\n") == []


def test_buoy_registry():
    assert BUOYS["45027"].lat == 46.860 and BUOYS["45027"].lon == -91.930
    assert "45028" in BUOYS
