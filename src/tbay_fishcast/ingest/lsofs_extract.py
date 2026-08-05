"""LSOFS temperature extraction at station nodes × target depths.

The Phase-0 backfill workhorse. Verified live 2026-08-04: opening an LSOFS file
via netCDF-c HDF5 byte-range mode (`url#mode=bytes`) and reading
`temp[0, :, station_nodes]` costs ~0.5 s/file versus a ~189 MB full download.
That is what makes an Oct-scale backfill feasible without OPeNDAP/THREDDS (which
is blocked from this environment anyway).

Returns tidy bronze rows: one (station, depth, valid_time) temperature per read.
All times UTC (ADR-014). No LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from ..features.sigma import interp_column

# LSOFS `time` variable epoch, verified from the file: "seconds since 2018-01-01 00:00:00"
LSOFS_EPOCH = datetime(2018, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class TempRow:
    station_id: str
    node: int
    depth_m: float
    temp_c: float
    valid_time: datetime  # UTC
    water_column_m: float
    clamped: bool  # target depth exceeded the local water column (shallow node)


def valid_time_from_dataset(dataset) -> datetime:
    """Read the file's valid time. Prefer the numeric `time` (epoch seconds);
    fall back to the `Times` character string."""
    if "time" in dataset.variables:
        secs = float(np.asarray(dataset.variables["time"][:]).ravel()[0])
        return LSOFS_EPOCH + timedelta(seconds=secs)
    raw = dataset.variables["Times"][0]
    s = b"".join(np.asarray(raw).astype("S1").tolist()).decode(errors="replace").strip()
    return datetime.fromisoformat(s.split(".")[0]).replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class NativeColumn:
    """The full native FVCOM sigma-layer profile at one node — no depth binning.

    Used for the isotherm computation, which is sensitive to vertical resolution
    near a sharp thermocline: interpolating the 20 sigma layers down to a handful of
    fixed target depths and *then* finding the isotherm can misplace it by up to
    ~0.7 m where the gradient is steep and falls in a sampling gap (measured at
    MacKenzie Point). Finding the isotherm on the native layers removes that
    discretization error for free (same file read). Bronze extraction still uses
    fixed target depths (schema + buoy-depth matching); this is a parallel path.
    """
    station_id: str
    node: int
    depths_m: list[float]   # positive-down, sorted shallow -> deep
    temps_c: list[float]
    water_column_m: float
    valid_time: datetime    # UTC


def extract_native_columns(dataset, station_nodes: dict[str, int]) -> dict[str, NativeColumn]:
    """Native sigma-layer temperature profiles (depth positive-down, temp) per node.

    FVCOM depth of layer = -siglay * (h + zeta) (siglay runs 0 at surface to -1 at
    bottom). Non-finite layers (fill values below the bed) are dropped.
    """
    nodes = list(station_nodes.values())
    vt = valid_time_from_dataset(dataset)
    temp = np.asarray(dataset.variables["temp"][0, :, nodes], dtype=float)   # (siglay, k)
    siglay = np.asarray(dataset.variables["siglay"][:, nodes], dtype=float)  # (siglay, k)
    h = np.asarray(dataset.variables["h"][nodes], dtype=float)
    if "zeta" in dataset.variables:
        zeta = np.asarray(dataset.variables["zeta"][0, nodes], dtype=float)
    else:
        zeta = np.zeros(len(nodes))

    out: dict[str, NativeColumn] = {}
    for col, (sid, node) in enumerate(station_nodes.items()):
        H = float(h[col] + zeta[col])
        z = -siglay[:, col] * H
        t = temp[:, col]
        ok = np.isfinite(z) & np.isfinite(t)
        z, t = z[ok], t[ok]
        order = np.argsort(z)
        out[sid] = NativeColumn(
            station_id=sid, node=int(node),
            depths_m=[float(v) for v in z[order]],
            temps_c=[float(v) for v in t[order]],
            water_column_m=H, valid_time=vt,
        )
    return out


@dataclass(frozen=True)
class SurfaceRow:
    station_id: str
    node: int
    temp_c: float          # topmost sigma layer = model SST (skin-comparable)
    valid_time: datetime   # UTC


def extract_surface(dataset, station_nodes: dict[str, int]) -> list[SurfaceRow]:
    """Model SST = temperature at the shallowest sigma layer (siglay index 0, ~0.3 m).

    This is the depth-appropriate model quantity to compare against GLSEA skin SST
    (G1) — no interpolation, no 2 m/skin mismatch. FVCOM orders siglay surface-first
    (verified: -0.025 at index 0), so index 0 is the surface-most layer.
    """
    nodes = list(station_nodes.values())
    vt = valid_time_from_dataset(dataset)
    surf = np.asarray(dataset.variables["temp"][0, 0, nodes], dtype=float)  # layer 0
    return [
        SurfaceRow(station_id=sid, node=int(node), temp_c=float(surf[col]), valid_time=vt)
        for col, (sid, node) in enumerate(station_nodes.items())
    ]


def extract_nodes(
    dataset,
    station_nodes: dict[str, int],
    target_depths_m,
    *,
    read_zeta: bool = True,
) -> list[TempRow]:
    """Extract temperature at each station node interpolated to target depths.

    dataset: an OPEN netCDF4 Dataset (caller owns open/close so byte-range URLs,
             local fixtures, and DAP handles all work through the same code).
    station_nodes: {station_id: node_index}.
    """
    nodes = list(station_nodes.values())
    vt = valid_time_from_dataset(dataset)

    # temp: (time, siglay, node) -> subset the node axis only.
    temp = np.asarray(dataset.variables["temp"][0, :, nodes], dtype=float)  # (siglay, k)
    siglay = np.asarray(dataset.variables["siglay"][:, nodes], dtype=float)  # (siglay, k)
    h = np.asarray(dataset.variables["h"][nodes], dtype=float)  # (k,)
    if read_zeta and "zeta" in dataset.variables:
        zeta = np.asarray(dataset.variables["zeta"][0, nodes], dtype=float)
    else:
        zeta = np.zeros(len(nodes))

    rows: list[TempRow] = []
    for col, (sid, node) in enumerate(station_nodes.items()):
        res = interp_column(
            temp[:, col], siglay[:, col], float(h[col]), target_depths_m, float(zeta[col])
        )
        for d, t, cl in zip(res.depths_m, res.temp_c, res.clamped):
            rows.append(
                TempRow(
                    station_id=sid,
                    node=int(node),
                    depth_m=float(d),
                    temp_c=float(t),
                    valid_time=vt,
                    water_column_m=res.water_column_m,
                    clamped=bool(cl),
                )
            )
    return rows
