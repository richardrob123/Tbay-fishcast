"""Golden-file test: extract station temps from the real LSOFS subset fixture.

Values recorded from lsofs.t00z.20260804.fields.n000.nc (valid 2026-08-03T18:00Z),
subset to the Thunder Bay bbox. If ingest logic changes these must be re-blessed
deliberately — that is the point of a golden test.
"""
from datetime import datetime, timezone

import netCDF4 as nc
import pytest

from tbay_fishcast.ingest.lsofs_extract import extract_nodes, valid_time_from_dataset

GOLDEN = {
    "silver_harbour_outer": {2.0: 14.558, 6.0: 11.141, 10.0: 10.171},
    "mackenzie_point": {2.0: 12.509, 6.0: 11.087, 10.0: 9.452},
    "marina_east_mcvicar": {2.0: 18.120, 6.0: 14.033, 10.0: 12.760},
}
VALID_TIME = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)


def _station_nodes(grid_meta):
    return {sid: grid_meta[sid]["fixture_node"] for sid in GOLDEN}


def test_valid_time_is_utc(lsofs_fixture):
    ds = nc.Dataset(lsofs_fixture)
    try:
        vt = valid_time_from_dataset(ds)
    finally:
        ds.close()
    assert vt == VALID_TIME
    assert vt.tzinfo is not None and vt.utcoffset().total_seconds() == 0


def test_golden_temperatures(lsofs_fixture, grid_meta, config):
    ds = nc.Dataset(lsofs_fixture)
    try:
        rows = extract_nodes(ds, _station_nodes(grid_meta), config.lsofs.target_depths_m)
    finally:
        ds.close()
    got = {(r.station_id, r.depth_m): r.temp_c for r in rows}
    for sid, depths in GOLDEN.items():
        for d, exp in depths.items():
            assert got[(sid, d)] == pytest.approx(exp, abs=0.01), f"{sid}@{d}m"


def test_extract_flags_no_spurious_clamp(lsofs_fixture, grid_meta, config):
    """All pinned nodes are >=10 m, so the 2/6/10 m band must NOT clamp."""
    ds = nc.Dataset(lsofs_fixture)
    try:
        rows = extract_nodes(ds, _station_nodes(grid_meta), config.lsofs.target_depths_m)
    finally:
        ds.close()
    assert not any(r.clamped for r in rows)


def test_thermocline_present(lsofs_fixture, grid_meta, config):
    """Physical sanity: 2 m is warmer than 10 m at every station (August strat.)."""
    ds = nc.Dataset(lsofs_fixture)
    try:
        rows = extract_nodes(ds, _station_nodes(grid_meta), config.lsofs.target_depths_m)
    finally:
        ds.close()
    by = {(r.station_id, r.depth_m): r.temp_c for r in rows}
    for sid in GOLDEN:
        assert by[(sid, 2.0)] > by[(sid, 10.0)]
