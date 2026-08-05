"""Cut a small Thunder Bay regulargrid golden fixture from a real LSOFS file,
and print golden 6 m temps at the three shore stations for the test suite.

    python scripts/cut_regulargrid_fixture.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import fsspec
import netCDF4 as nc
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.ingest.lsofs_regulargrid import (  # noqa: E402
    bootstrap_pixels, extract_regulargrid, read_regulargrid_grid,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "lsofs_regulargrid_tbay.nc"
URL = ("https://noaa-nos-ofs-pds.s3.amazonaws.com/lsofs/netcdf/202408/"
       "lsofs.t12z.20240815.regulargrid.n006.nc")
BBOX = dict(lat_min=48.30, lat_max=48.62, lon_min=-89.45, lon_max=-88.85)


def main() -> None:
    data = fsspec.filesystem("https", block_size=16_000_000).cat_file(URL)
    ds = nc.Dataset("m", "r", memory=data)
    lat = np.asarray(ds.variables["Latitude"][:]); lon = np.asarray(ds.variables["Longitude"][:])
    lat1d = lat[:, 0]; lon1d = lon[0, :]
    iy = np.flatnonzero((lat1d >= BBOX["lat_min"]) & (lat1d <= BBOX["lat_max"]))
    ix = np.flatnonzero((lon1d >= BBOX["lon_min"]) & (lon1d <= BBOX["lon_max"]))
    y0, y1, x0, x1 = iy[0], iy[-1] + 1, ix[0], ix[-1] + 1
    print(f"subset ny={y1-y0} nx={x1-x0} depths={ds.variables['Depth'].shape[0]}")

    temp = np.asarray(ds.variables["temp"][:, :, y0:y1, x0:x1])
    Lat = lat[y0:y1, x0:x1]; Lon = lon[y0:y1, x0:x1]
    mask = np.asarray(ds.variables["mask"][:])[y0:y1, x0:x1] if ds.variables["mask"].ndim == 2 \
        else np.asarray(ds.variables["mask"][0, y0:y1, x0:x1])
    h = np.asarray(ds.variables["h"][:])[y0:y1, x0:x1] if ds.variables["h"].ndim == 2 \
        else np.asarray(ds.variables["h"][0, y0:y1, x0:x1])
    Depth = np.asarray(ds.variables["Depth"][:])
    time_v = np.asarray(ds.variables["time"][:]); time_u = ds.variables["time"].units
    times_v = np.asarray(ds.variables["Times"][:])

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    out = nc.Dataset(FIXTURE, "w", format="NETCDF4")
    out.title = "LSOFS regulargrid Thunder Bay subset — TEST FIXTURE (real data)"
    out.createDimension("time", None); out.createDimension("Depth", Depth.size)
    out.createDimension("ny", y1 - y0); out.createDimension("nx", x1 - x0)
    out.createDimension("DateStrLen", times_v.shape[1])
    def mk(n, dims, arr, **a):
        v = out.createVariable(n, "f4", dims, zlib=True, complevel=4, fill_value=-99999.0)
        v[:] = arr.astype("f4")
        for k, val in a.items():
            setattr(v, k, val)
    mk("temp", ("time", "Depth", "ny", "nx"), temp, units="Celsius")
    mk("Latitude", ("ny", "nx"), Lat); mk("Longitude", ("ny", "nx"), Lon)
    mk("mask", ("ny", "nx"), mask); mk("h", ("ny", "nx"), h)
    out.createVariable("Depth", "f4", ("Depth",))[:] = Depth
    tv = out.createVariable("time", "f8", ("time",)); tv[:] = time_v; tv.units = time_u
    out.createVariable("Times", "S1", ("time", "DateStrLen"))[:] = times_v
    out.close()

    ds2 = nc.Dataset(FIXTURE)
    cfg = load_config()
    grid = read_regulargrid_grid(ds2)
    px = bootstrap_pixels(grid, cfg.stations)
    print("\n=== golden 6 m temps (regulargrid) ===")
    rows = extract_regulargrid(ds2, px, [6.0])
    for r in rows:
        print(f"  {r.station_id:22s} 6m={r.temp_c:.3f}C  valid={r.valid_time.isoformat()}")
    for sid, m in px.items():
        print(f"  pixel {sid:22s} iy={m.iy} ix={m.ix} dist={m.dist_km:.2f}km h={m.depth_m:.1f}m")
    ds2.close(); ds.close()
    print(f"\nwrote {FIXTURE} ({FIXTURE.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
