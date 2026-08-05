"""Regulargrid reader — golden + property tests against a real TBay subset fixture."""
import netCDF4 as nc
import numpy as np
import pytest

from tbay_fishcast.ingest.lsofs_regulargrid import (
    bootstrap_pixels, depth_index, extract_regulargrid, nearest_water_pixel,
    read_regulargrid_grid,
)

FIXTURE_NAME = "lsofs_regulargrid_tbay.nc"
GOLDEN_6M = {
    "silver_harbour_outer": 13.594,
    "mackenzie_point": 13.119,
    "marina_east_mcvicar": 13.872,
}


@pytest.fixture(scope="module")
def regrid_ds(repo_root):
    p = repo_root / "tests" / "fixtures" / FIXTURE_NAME
    assert p.exists(), "run scripts/cut_regulargrid_fixture.py"
    ds = nc.Dataset(p)
    yield ds
    ds.close()


def test_6m_is_exact_level(regrid_ds):
    grid = read_regulargrid_grid(regrid_ds)
    idx, exact = depth_index(grid, 6.0)
    assert exact and grid.depth[idx] == pytest.approx(6.0)
    assert idx == 4  # verified z-level ordering


def test_longitude_is_minus180_convention(regrid_ds):
    grid = read_regulargrid_grid(regrid_ds)
    assert grid.lon1d.min() < 0 and grid.lon1d.max() < 0  # all negative (TBay ~-89)
    assert -90 < grid.lon1d.mean() < -88


def test_nearest_pixel_is_water(regrid_ds):
    grid = read_regulargrid_grid(regrid_ds)
    m = nearest_water_pixel(grid, 48.4296, -89.2046)  # marina node
    assert grid.mask[m.iy, m.ix] == 1
    assert m.dist_km < 1.0


def test_golden_6m_temperatures(regrid_ds, config):
    grid = read_regulargrid_grid(regrid_ds)
    px = bootstrap_pixels(grid, config.stations)
    rows = extract_regulargrid(regrid_ds, px, [6.0])
    got = {r.station_id: r.temp_c for r in rows}
    for sid, exp in GOLDEN_6M.items():
        assert got[sid] == pytest.approx(exp, abs=0.01), sid


def test_only_shore_stations_get_pixels(regrid_ds, config):
    grid = read_regulargrid_grid(regrid_ds)
    px = bootstrap_pixels(grid, config.stations)
    assert set(px) == {"silver_harbour_outer", "mackenzie_point", "marina_east_mcvicar"}


def test_valid_time_utc(regrid_ds):
    from tbay_fishcast.ingest.lsofs_extract import valid_time_from_dataset
    vt = valid_time_from_dataset(regrid_ds)
    assert vt.isoformat() == "2024-08-15T12:00:00+00:00"


def test_fill_values_skipped(regrid_ds, config):
    """A deep target below a shallow pixel's column stays finite or is skipped, never
    emits the -99999 fill."""
    grid = read_regulargrid_grid(regrid_ds)
    px = bootstrap_pixels(grid, config.stations)
    rows = extract_regulargrid(regrid_ds, px, [6.0, 300.0])
    assert all(r.temp_c > -100 for r in rows)  # no fill leaked
