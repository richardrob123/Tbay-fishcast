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
