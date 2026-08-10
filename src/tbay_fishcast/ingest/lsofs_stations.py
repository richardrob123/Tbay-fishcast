"""LSOFS station-output files — the whole forecast at a point, in one small file (ADR-049).

NOAA publishes, alongside the large `fields` files this project already reads, a per-cycle
STATION file: `lsofs.tHHz.YYYYMMDD.stations.{forecast,nowcast}.nc`. Measured on the live bucket:

    10 MB, ~2 s to fetch, 19 stations x 20 sigma layers x 1201 time steps
    = the ENTIRE f000-f120 window at 6-minute resolution, all depths, in ONE file.

That is the difference between a hindcast that is affordable and one that is not. The `fields`
route needs one large file per (day, lead); this needs one small file per day and yields every
lead from it. It also removes a confound: two of the published stations sit essentially ON the
things we care about —

    station 45027 @ 46.861,-91.931 -> 0.1 km from the Duluth LLO1 thermistor chain (h 53.0 m)
    station 10050 @ 48.410,-89.215 -> 2.3 km from the Thunder Bay waterfront   (h 14.1 m)

so the model can be scored where the observation actually is, instead of at whatever unstructured
node `nearest_node` happens to pick.

ARCHIVE EXTENT, verified by probe: station files exist from 2024-12 onward (2024-11 returns 404).
That covers the whole 2025 open-water season, which is on the VALIDATION side of the project's
temporal split (rule 6) — so a hindcast built from it cannot contaminate anything tuned earlier.

CACHING POLICY: cache the EXTRACT, not the file. 138 days of station files is 1.4 GB and we want
two columns out of each. The .nc is fetched, read and discarded; a small JSON of the extracted
profiles is what persists.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_CACHE = _ROOT / "data" / "lsofs_station_cache"

RECENT = "https://noaa-ofs-pds.s3.amazonaws.com/lsofs.{ymd}/lsofs.t{hh}z.{ymd}.stations.{kind}.nc"
ARCHIVE = ("https://noaa-nos-ofs-pds.s3.amazonaws.com/lsofs/netcdf/{y}/{m}/{d}/"
           "lsofs.t{hh}z.{ymd}.stations.{kind}.nc")
EPOCH = datetime(2018, 1, 1, tzinfo=timezone.utc)     # `time:units` on these files
ARCHIVE_FIRST = date(2024, 12, 1)                     # probed: 2024-11 is 404


def urls(day: date, cycle: str = "t12z", kind: str = "forecast") -> list[str]:
    hh = cycle.strip("tz")
    ymd = day.strftime("%Y%m%d")
    return [RECENT.format(ymd=ymd, hh=hh, kind=kind),
            ARCHIVE.format(y=day.year, m=f"{day.month:02d}", d=f"{day.day:02d}",
                           ymd=ymd, hh=hh, kind=kind)]


def _cache_path(key: str) -> Path:
    return _CACHE / f"{hashlib.sha256(key.encode()).hexdigest()[:20]}.json"


def _download(url: str, dest: Path, timeout: int) -> bool:
    r = subprocess.run(["curl", "-sS", "-f", "-m", str(timeout), "-o", str(dest), url],
                       capture_output=True)
    return r.returncode == 0 and dest.exists() and dest.stat().st_size > 100_000


def station_table(day: date, cycle: str = "t12z", kind: str = "forecast",
                  timeout: int = 180):
    """[(name, lat, lon, depth_m)] for one cycle — cheap metadata, cached."""
    prof = extract(day, [], cycle=cycle, kind=kind, timeout=timeout, want_table=True)
    return prof["stations"]


def nearest(stations, lat: float, lon: float):
    """(index, name, distance_km) of the published station closest to a point."""
    best = None
    for i, (name, slat, slon, _h) in enumerate(stations):
        d = math.hypot((slat - lat) * 111.32,
                       (slon - lon) * 111.32 * math.cos(math.radians(lat)))
        if best is None or d < best[2]:
            best = (i, name, d)
    return best


def extract(day: date, want_times, *, station_lat: float | None = None,
            station_lon: float | None = None, cycle: str = "t12z", kind: str = "forecast",
            timeout: int = 180, want_table: bool = False):
    """Profiles at one station for a set of valid times, from one cycle's station file.

    ``want_times`` are timezone-aware UTC datetimes. Returns
    ``{"stations": [...], "station": {...}, "profiles": {iso_time: {"depth_m": [...],
    "temp_c": [...]}}}``. Times outside the file's span are simply absent from `profiles` —
    the caller must check rather than be handed an extrapolation.
    """
    key = json.dumps(["lsofs-stn", day.isoformat(), cycle, kind, station_lat, station_lon,
                      sorted(t.isoformat() for t in want_times), want_table])
    cp = _cache_path(key)
    if cp.exists():
        try:
            return json.loads(cp.read_text())
        except ValueError:
            pass

    import netCDF4 as nc
    import numpy as np

    os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
    tmp = Path(tempfile.mkdtemp()) / "stations.nc"
    got = None
    for u in urls(day, cycle, kind):
        if _download(u, tmp, timeout):
            got = u
            break
    if got is None:
        raise FileNotFoundError(f"no station file for {day} {cycle} {kind}")
    try:
        ds = nc.Dataset(str(tmp))
        try:
            names = [b"".join(r).decode(errors="ignore").strip()
                     for r in ds.variables["name_station"][:]]
            lat = np.asarray(ds.variables["lat"][:], dtype=float)
            # published in 0-360; -360 puts it back on the same convention as everything else
            lon = np.asarray(ds.variables["lon"][:], dtype=float)
            lon = np.where(lon > 180.0, lon - 360.0, lon)
            h = np.asarray(ds.variables["h"][:], dtype=float)
            stations = [[names[i], float(lat[i]), float(lon[i]), float(h[i])]
                        for i in range(len(names))]
            out = {"stations": stations, "source": got, "profiles": {}}
            if want_table or station_lat is None:
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_text(json.dumps(out))
                return out

            si, sname, dist_km = nearest(stations, station_lat, station_lon)
            tv = np.asarray(ds.variables["time"][:], dtype=float)
            times = [EPOCH + timedelta(seconds=float(s)) for s in tv]
            siglay = np.asarray(ds.variables["siglay"][:], dtype=float)[:, si]
            temp = ds.variables["temp"]
            zeta = ds.variables["zeta"]
            out["station"] = {"index": si, "name": sname, "lat": stations[si][1],
                              "lon": stations[si][2], "depth_m": stations[si][3],
                              "dist_km": round(dist_km, 2)}
            for wt in want_times:
                # nearest published step; the file is 6-minutely so this is <=3 min of slack.
                # A tolerance is still enforced because a truncated cycle silently shortens the
                # time axis, and "nearest" to a 60 h gap is not a forecast.
                k = min(range(len(times)), key=lambda j: abs((times[j] - wt).total_seconds()))
                if abs((times[k] - wt).total_seconds()) > 900:
                    continue
                z0 = float(zeta[k, si]) if zeta.ndim == 2 else 0.0
                if not math.isfinite(z0):
                    z0 = 0.0
                # FVCOM sigma: z = zeta + siglay*(h+zeta), siglay in [-1, 0].
                # Depth BELOW SURFACE (what a thermistor reports) is therefore -siglay*(h+zeta).
                depths = [-float(s) * (stations[si][3] + z0) for s in siglay]
                temps = [float(v) for v in np.asarray(temp[k, :, si], dtype=float)]
                keep = [(d, t) for d, t in zip(depths, temps)
                        if math.isfinite(d) and math.isfinite(t) and -5.0 < t < 40.0]
                keep.sort()
                if len(keep) >= 3:
                    out["profiles"][wt.isoformat()] = {
                        "depth_m": [round(d, 3) for d, _ in keep],
                        "temp_c": [round(t, 3) for _, t in keep],
                        "valid_utc": times[k].isoformat()}
        finally:
            ds.close()
    finally:
        try:
            tmp.unlink()
            tmp.parent.rmdir()
        except OSError:
            pass
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(out))
    return out


def interp_to(depth_m: float, prof: dict) -> float | None:
    """Model temperature at an observed sensor depth, linearly between bracketing sigma layers.

    Returns None ABOVE the shallowest layer and BELOW the deepest rather than clamping. Clamping
    is what turns a geometry mismatch into a fake agreement: the top sigma layer at LLO1 sits at
    1.3 m and the shallowest thermistor at 1.0 m, so a clamp would silently score the 1.3 m model
    value against a 1.0 m observation and call it a match every time.
    """
    z = prof.get("depth_m") or []
    t = prof.get("temp_c") or []
    if len(z) < 2 or depth_m < z[0] or depth_m > z[-1]:
        return None
    for i in range(len(z) - 1):
        if z[i] <= depth_m <= z[i + 1]:
            if z[i + 1] == z[i]:
                return t[i]
            f = (depth_m - z[i]) / (z[i + 1] - z[i])
            return t[i] + f * (t[i + 1] - t[i])
    return None
