"""LSOFS hindcast backfill orchestration (PLAN task 2).

Ties the verified pieces together: for each cycle/nowcast-hour, build candidate S3
URLs, open the first that succeeds via HDF5 byte-range, extract station-node temps
at target depths, and append bronze parquet. Deterministic, no LLM.

This is the skeleton of the backfill loop. It runs against live S3 (the only
allowlisted source). The historical extent it can cover is bounded by NODD:
native `fields` files begin ~2024 (verified), NOT Oct 2022 — see the first-hour
report. Callers pass an explicit date range; there is no silent clamping.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import fsspec
import netCDF4 as nc

from ..config import Config
from .lsofs_extract import SurfaceRow, TempRow, extract_nodes, extract_surface
from .lsofs_paths import LsofsFile, candidate_urls

# Remote read strategy (benchmarked live 2026-08-04):
#   netCDF `#mode=bytes` open  ... ~37 s/file  (many latency-bound range GETs)
#   fsspec cat + nc in-memory  ... ~5  s/file  (one bulk GET, ~4 s @ ~40 MB/s)
# We fetch the file bytes with fsspec block-caching, then open in-memory with
# netCDF4 so the SAME extract_nodes path serves fixtures and live reads.
# Further optimization if bandwidth (not latency) becomes binding: fsspec+h5py
# 16 MB-block partial reads (~3 s, ~40 MB/file) or a kerchunk reference over the
# identical FVCOM structure (near-instant open). Documented in ADR-017.
_FSSPEC_BLOCK = 16_000_000


@dataclass(frozen=True)
class BackfillItem:
    day: date
    cycle: str
    hour: int


def station_node_map(cfg: Config) -> dict[str, int]:
    """{station_id: lsofs_node} for shore stations that have been bootstrapped."""
    return {s.id: int(s.lsofs_node) for s in cfg.stations if s.has_lsofs}


def _open_first(urls: list[str]):
    """Fetch the first reachable URL's bytes and open it in-memory (netCDF4).

    Recent bucket first, then archive. FileNotFoundError only if none open."""
    fs = fsspec.filesystem("https", block_size=_FSSPEC_BLOCK)
    last: Exception | None = None
    for u in urls:
        try:
            data = fs.cat_file(u)
        except Exception as e:  # noqa: BLE001 - fsspec raises varied transport errors
            last = e
            continue
        return nc.Dataset("inmem", mode="r", memory=data)
    raise FileNotFoundError(f"no candidate URL opened; last error: {last}")


def extract_item(cfg: Config, item: BackfillItem,
                 station_nodes: dict[str, int]) -> list[TempRow]:
    """Extract one nowcast hour's station temps from live S3 (~5 s/file)."""
    f = LsofsFile(day=item.day, cycle=item.cycle, kind="n", hour=item.hour)
    # Plain https URLs (no #mode=bytes) — fsspec does the range/bulk fetch.
    urls = candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket,
                          byterange=False)
    ds = _open_first(urls)
    try:
        return extract_nodes(ds, station_nodes, cfg.lsofs.target_depths_m)
    finally:
        ds.close()


def extract_surface_item(cfg: Config, item: BackfillItem,
                         station_nodes: dict[str, int]) -> list[SurfaceRow]:
    """Model SST (topmost sigma layer) for one nowcast hour, from live S3."""
    f = LsofsFile(day=item.day, cycle=item.cycle, kind="n", hour=item.hour)
    urls = candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket,
                          byterange=False)
    ds = _open_first(urls)
    try:
        return extract_surface(ds, station_nodes)
    finally:
        ds.close()


def iter_nowcast_items(days: list[date], cfg: Config):
    """One item per (day, cycle, nowcast-hour). Nowcast hours give the best-analyzed
    state; forecast hours are for the live product, not the hindcast truth series."""
    for d in days:
        for cyc in cfg.lsofs.cycles:
            for h in range(cfg.lsofs.nowcast_hours):
                yield BackfillItem(day=d, cycle=cyc, hour=h)
