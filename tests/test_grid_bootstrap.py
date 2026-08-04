"""Grid bootstrap — nearest-node resolution on the real (subset) grid."""
import netCDF4 as nc
import numpy as np
import pytest

from tbay_fishcast.ingest.lsofs_grid import (
    Grid, nearest_node, read_grid, _local_xy,
)


def test_read_grid_shapes(lsofs_fixture):
    ds = nc.Dataset(lsofs_fixture)
    try:
        g = read_grid(ds)
    finally:
        ds.close()
    assert g.n_nodes > 0
    assert g.lon.shape == g.lat.shape == g.h.shape
    assert g.siglay.shape[1] == g.n_nodes


def test_longitude_wrap_handles_0_360():
    """A 0-360 grid longitude and a -180..180 station must project to a small
    delta, not a ~357-degree one."""
    # grid node at 270.8 degE == -89.2; station at -89.2 -> ~0 delta
    x, y = _local_xy(np.array([270.8]), np.array([48.43]), -89.2, 48.43)
    assert abs(x[0]) < 200.0 and abs(y[0]) < 200.0


def test_nearest_node_matches_pinned(lsofs_fixture, grid_meta, config):
    """Nearest node (>=10 m) for each shore station reproduces the pinned depth."""
    ds = nc.Dataset(lsofs_fixture)
    try:
        g = read_grid(ds)
        for s in config.shore_stations:
            m = nearest_node(g, s.lat, s.lon, min_depth_m=10.0)
            # fixture is re-indexed, so compare node DEPTH to the pinned value
            assert m.node_depth_m == pytest.approx(grid_meta[s.id]["node_depth_m"], abs=0.05)
            assert m.node_depth_m >= 10.0
    finally:
        ds.close()


def test_min_depth_screens_shallow_nodes():
    # two nodes: one 1 m (land-ish), one 15 m; nearest overall is the shallow one,
    # but min_depth=10 must select the deep one.
    g = Grid(
        lon=np.array([270.80, 270.81]),
        lat=np.array([48.430, 48.431]),
        h=np.array([1.0, 15.0]),
        siglay=np.tile((-(np.arange(20) + 0.5) / 20.0)[:, None], (1, 2)),
    )
    shallow = nearest_node(g, 48.4300, 270.800, min_depth_m=0.5)
    deep = nearest_node(g, 48.4300, 270.800, min_depth_m=10.0)
    assert shallow.node_depth_m == pytest.approx(1.0)
    assert deep.node_depth_m == pytest.approx(15.0)
