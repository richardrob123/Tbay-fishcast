"""ERA5 FLake parse — pure netCDF -> FlakeCell, against a recorded Thunder Bay granule.

Hermetic (golden fixture, no network/CDS). The fixture is a real ERA5 single-levels
pull over the bay (2026-08-01 12Z, lake_mix_layer_temperature/depth + bottom_temperature),
so this pins the K->C conversion, nearest-cell pick, and plausibility of the values.
"""
from pathlib import Path

import pytest

from tbay_fishcast.ingest.era5_flake import parse_flake

FIX = Path(__file__).parent / "fixtures" / "era5_flake_tbay.nc"

netCDF4 = pytest.importorskip("netCDF4")


def test_parse_flake_thunder_bay_cell_is_physical():
    cell = parse_flake(str(FIX), 48.4, -89.2)
    assert cell is not None
    # snapped to the nearest 0.25 deg cell, near the request point
    assert abs(cell.lat - 48.4) <= 0.2 and abs(cell.lon - (-89.2)) <= 0.2
    assert cell.dist_km < 25.0
    # values converted K->C and physically sane for a stratified Aug lake
    assert 2.0 < cell.mixed_layer_temp_c < 26.0
    assert 0.5 < cell.mixed_layer_depth_m < 60.0
    assert 1.0 < cell.bottom_temp_c < cell.mixed_layer_temp_c + 0.5   # bottom no warmer than surface
    # a land/out-of-grid request still snaps to the grid extent, not a crash
    edge = parse_flake(str(FIX), 60.0, -80.0)
    assert edge is not None
