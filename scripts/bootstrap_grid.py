"""One-shot: resolve station LSOFS nodes from a real file, pin them into
stations.yaml, and cut a small Thunder Bay golden fixture for the test suite.

Run against a locally downloaded LSOFS file (the 189 MB full grid). Not part of
the heartbeat; a maintenance/commissioning script.

    python scripts/bootstrap_grid.py <path-to-lsofs.nc>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import netCDF4 as nc
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.ingest.lsofs_grid import read_grid, bootstrap_stations  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "lsofs_tbay_subset.nc"
GRID_META = REPO / "tests" / "fixtures" / "grid_meta.json"
BBOX = dict(lat_min=48.30, lat_max=48.62, lon_min=-89.45, lon_max=-88.85)
# Shore stations pin to the nearest node deep enough to resolve the full target
# band (>=10 m), so 2/6/10 m temps are interpolated rather than clamped at a
# 1-2 m shoreline node. Provisional; revisit if a station's representative node
# ends up implausibly far offshore.
SHORE_MIN_DEPTH_M = 10.0


def main(src: str) -> None:
    cfg = load_config()
    ds = nc.Dataset(src)
    grid = read_grid(ds)
    matches = bootstrap_stations(grid, cfg.stations, min_depth_m=SHORE_MIN_DEPTH_M)

    print("=== station node matches (full grid) ===")
    meta = {}
    for sid, m in matches.items():
        print(f"  {sid:22s} node={m.node:6d} depth={m.node_depth_m:6.1f}m "
              f"dist={m.dist_m:7.0f}m  ({m.node_lat:.4f},{m.node_lon:.4f})")
        meta[sid] = dict(node=m.node, node_depth_m=round(m.node_depth_m, 2),
                         dist_m=round(m.dist_m, 1),
                         node_lat=round(m.node_lat, 5), node_lon=round(m.node_lon, 5))

    # --- cut the fixture: nodes inside the Thunder Bay bbox, re-indexed 0..N-1 ---
    # grid.lon is 0-360; wrap to -180..180 for the bbox test. Fixture KEEPS the
    # native 0-360 values so the test suite exercises the longitude-wrap path.
    lat = grid.lat
    lon_wrapped = (grid.lon + 180.0) % 360.0 - 180.0
    sel = np.flatnonzero(
        (lat >= BBOX["lat_min"]) & (lat <= BBOX["lat_max"]) &
        (lon_wrapped >= BBOX["lon_min"]) & (lon_wrapped <= BBOX["lon_max"])
    )
    print(f"\nfixture: {sel.size} nodes in bbox")

    temp = np.asarray(ds.variables["temp"][:, :, sel])  # (1, siglay, nsel)
    siglay = np.asarray(ds.variables["siglay"][:, sel])
    siglev = np.asarray(ds.variables["siglev"][:, sel])
    zeta = np.asarray(ds.variables["zeta"][:, sel])
    h = np.asarray(ds.variables["h"][sel])
    slon = np.asarray(ds.variables["lon"][sel])
    slat = np.asarray(ds.variables["lat"][sel])
    time_v = np.asarray(ds.variables["time"][:])
    times_v = np.asarray(ds.variables["Times"][:])
    time_units = ds.variables["time"].units

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    out = nc.Dataset(FIXTURE, "w", format="NETCDF4")
    out.title = "LSOFS Thunder Bay subset — TEST FIXTURE (real data, bbox-clipped)"
    out.source_file = Path(src).name
    out.createDimension("time", None)
    out.createDimension("siglay", temp.shape[1])
    out.createDimension("siglev", siglev.shape[0])
    out.createDimension("node", sel.size)
    out.createDimension("DateStrLen", times_v.shape[1])

    def mk(name, dims, data, **attrs):
        v = out.createVariable(name, "f4" if data.dtype.kind == "f" else data.dtype, dims,
                               zlib=True, complevel=4)
        v[:] = data
        for k, val in attrs.items():
            setattr(v, k, val)

    mk("temp", ("time", "siglay", "node"), temp.astype("f4"), units="degrees_C")
    mk("siglay", ("siglay", "node"), siglay.astype("f4"))
    mk("siglev", ("siglev", "node"), siglev.astype("f4"))
    mk("zeta", ("time", "node"), zeta.astype("f4"), units="meters")
    mk("h", ("node",), h.astype("f4"), units="m")
    mk("lon", ("node",), slon.astype("f4"), units="degrees_east")
    mk("lat", ("node",), slat.astype("f4"), units="degrees_north")
    tv = out.createVariable("time", "f8", ("time",))
    tv[:] = time_v
    tv.units = time_units
    tsv = out.createVariable("Times", "S1", ("time", "DateStrLen"))
    tsv[:] = times_v
    out.close()
    ds.close()

    # remap station nodes into fixture-local indices for the golden meta
    for sid, m in matches.items():
        local = np.flatnonzero(sel == m.node)
        meta[sid]["fixture_node"] = int(local[0]) if local.size else None
    GRID_META.write_text(json.dumps(meta, indent=2))
    print(f"\nwrote fixture {FIXTURE} ({FIXTURE.stat().st_size/1024:.0f} KB)")
    print(f"wrote {GRID_META}")


if __name__ == "__main__":
    main(sys.argv[1])
