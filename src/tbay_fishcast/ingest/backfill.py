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

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date

import fsspec
import netCDF4 as nc

from ..config import Config
from .lsofs_extract import SurfaceRow, TempRow, extract_nodes, extract_surface
from .lsofs_paths import LsofsFile, archive_flat_url, candidate_urls
from .lsofs_regulargrid import PixelMatch, extract_regulargrid

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


# Bounded in-process cache of fetched file bytes, keyed by URL. LSOFS files are immutable
# once posted (a given day/cycle/hour never changes), so caching by URL is always correct.
# The coast build opens the SAME ~6 lead files once per stretch — with 7 stretches that was
# 42 network fetches of 6 files; as we add stretches it grew linearly. Caching the bytes cuts
# it to one fetch per distinct file regardless of stretch count (the key efficiency lever for
# expanding coverage). Bounded (LRU) so long backfill loops over many distinct days can't grow
# the cache without limit — those touch each file once anyway, so eviction costs nothing.
_BYTES_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_BYTES_CACHE_MAX = 8   # ~6 coast lead files + a couple of archive fallbacks; ~40 MB each


def _cat_bytes(url: str) -> bytes:
    hit = _BYTES_CACHE.get(url)
    if hit is not None:
        _BYTES_CACHE.move_to_end(url)
        return hit
    # Bound the fetch: this is the heaviest call in the 4x/day heartbeat hot path and was the ONE
    # HTTP source with no timeout (aiohttp defaults to 300 s/request), so a stalled NODD host could
    # block ~5 min/file invisibly (validation finding #10). Every other ingest sets a tight timeout.
    try:
        import aiohttp
        _ckw = {"timeout": aiohttp.ClientTimeout(total=60)}
    except Exception:  # noqa: BLE001  (aiohttp always present with fsspec[http], but fail safe)
        _ckw = {}
    fs = fsspec.filesystem("https", block_size=_FSSPEC_BLOCK, client_kwargs=_ckw)
    data = fs.cat_file(url)
    _BYTES_CACHE[url] = data
    _BYTES_CACHE.move_to_end(url)
    while len(_BYTES_CACHE) > _BYTES_CACHE_MAX:
        _BYTES_CACHE.popitem(last=False)
    return data


def _open_bytes(url: str):
    """Open one URL in-memory (netCDF4), reusing cached bytes if already fetched."""
    return nc.Dataset("inmem", mode="r", memory=_cat_bytes(url))


def _open_first(urls: list[str]):
    """Fetch the first reachable URL's bytes and open it in-memory (netCDF4).

    Recent bucket first, then archive. FileNotFoundError only if none open."""
    last: Exception | None = None
    for u in urls:
        try:
            return _open_bytes(u)
        except Exception as e:  # noqa: BLE001 - fsspec raises varied transport errors
            last = e
            continue
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


def extract_regulargrid_item(cfg: Config, item: BackfillItem,
                             station_pixels: dict[str, PixelMatch],
                             target_depths_m) -> list[TempRow]:
    """Extract station temps from a flat-archive `regulargrid` nowcast file (the 2024
    ice-free tune-season source). 6 m is an exact z-level here — no sigma interp."""
    f = LsofsFile(day=item.day, cycle=item.cycle, kind="n", hour=item.hour)
    url = archive_flat_url(f, cfg.lsofs.archive_bucket, product="regulargrid",
                           byterange=False)
    ds = _open_bytes(url)
    try:
        return extract_regulargrid(ds, station_pixels, target_depths_m)
    finally:
        ds.close()


def iter_nowcast_items(days: list[date], cfg: Config):
    """One item per (day, cycle, nowcast-hour). Nowcast hours give the best-analyzed
    state; forecast hours are for the live product, not the hindcast truth series."""
    for d in days:
        for cyc in cfg.lsofs.cycles:
            for h in range(cfg.lsofs.nowcast_hours):
                yield BackfillItem(day=d, cycle=cyc, hour=h)
