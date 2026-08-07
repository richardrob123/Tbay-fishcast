"""`_iso_field` must not interpolate a real isotherm depth against the 999 'no-crossing'
sentinel — that manufactured a spurious ~mid-range isotherm depth at the crossing/no-crossing
frontier that survived into the bottom-temperature inversion exactly where the cold edge lives
(validation R5). It must instead grid depth from real crossings only and emit the sentinel where
the local node majority never reaches the target.
"""
import importlib.util
from pathlib import Path

import numpy as np

_spec = importlib.util.spec_from_file_location(
    "bcs_isofield", Path(__file__).resolve().parents[1] / "scripts" / "build_coast_site.py")
_bcs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bcs)


def _grid():
    return np.mgrid[0:5:25j, 0:10:40j][::-1]   # gx, gy


def test_no_spurious_isotherm_at_sentinel_frontier():
    gx, gy = _grid()
    # left cluster reaches the target at ~4-6 m; right cluster never reaches it (999 sentinel)
    pts = np.array([[1, 1], [2, 3], [3, 1.5], [2.5, 4],
                    [7, 1], [8, 3], [9, 1.5], [7.5, 4]], dtype=float)
    vals = [4.0, 5.0, 6.0, 5.5, 999.0, 999.0, 999.0, 999.0]
    iso = _bcs._iso_field(gx, gy, pts, vals)
    finite = iso[np.isfinite(iso)]
    real = finite[finite < 900]
    # THE bug: an interior pixel receiving a 22–899 m "isotherm" ramped between 6 m and 999.
    assert not ((real > 22.0)).any(), "spurious deep isotherm survived the frontier"
    assert (iso[:, :8] < 22).mean() > 0.5, "crossing side should be real shallow depths"
    assert (iso[:, -8:] >= 900).mean() > 0.5, "no-crossing side should be the sentinel"


def test_all_sentinel_returns_sentinel_field():
    gx, gy = _grid()
    pts = np.array([[1, 1], [5, 3], [9, 2]], dtype=float)
    iso = _bcs._iso_field(gx, gy, pts, [999.0, 999.0, 999.0])
    assert np.all(iso >= 900.0)


def test_no_sentinel_path_is_plain_interpolation():
    gx, gy = _grid()
    pts = np.array([[1, 1], [5, 3], [9, 2], [4, 4]], dtype=float)
    iso = _bcs._iso_field(gx, gy, pts, [4.0, 5.0, 6.0, 5.5])
    assert np.isfinite(iso).all() and iso.max() < 22 and iso.min() >= 0


def test_collinear_real_nodes_do_not_crash():
    # degenerate (collinear) real nodes must fall back to nearest, not raise QhullError
    gx, gy = _grid()
    pts = np.array([[1, 2], [3, 2], [5, 2], [8, 2]], dtype=float)
    iso = _bcs._iso_field(gx, gy, pts, [4.0, 5.0, 6.0, 999.0])
    assert np.isfinite(iso).all()
