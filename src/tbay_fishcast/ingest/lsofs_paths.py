"""LSOFS NODD S3 key/URL construction — verified against live buckets 2026-08-04/05.

THREE layouts exist, confirmed by listing the real buckets:

  RECENT  (bucket noaa-ofs-pds, <=~30 d rolling):
      lsofs.YYYYMMDD/lsofs.tHHz.YYYYMMDD.fields.{n,f}NNN.nc          [nowcast + forecast]

  ARCHIVE-NESTED (bucket noaa-nos-ofs-pds), native `fields`, nowcast + forecast:
      lsofs/netcdf/YYYY/MM/DD/lsofs.tHHz.YYYYMMDD.fields.{n,f}NNN.nc
      Verified present: 2024/11, 2024/12, and all of 2025-2026.

  ARCHIVE-FLAT (bucket noaa-nos-ofs-pds), months 2024-03 .. 2024-12:
      lsofs/netcdf/YYYYMM/lsofs.tHHz.YYYYMMDD.{regulargrid|fields}.{n,f}NNN.nc
      DOMINATED BY `regulargrid` (lat/lon gridded). Native `fields` and nowcast (`n`)
      coverage here is sparse/inconsistent across days.

CONSEQUENCE (see ADR-018): the node-indexed `fields` nowcast pipeline reliably covers
2024-11 -> present (nested). The ICE-FREE 2024 tune season (Apr-Oct) lives in the flat
months as `regulargrid`, which this node pipeline does NOT read — tuning on that season
needs a separate lat/lon regulargrid reader (deferred). `candidate_urls` builds the
recent + nested-fields paths; `archive_flat_url` is provided for completeness but is
not wired into the nowcast backfill.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

S3_HOST = "s3.amazonaws.com"
BYTERANGE_SUFFIX = "#mode=bytes"  # netCDF-c HDF5-over-HTTP byte-range mode


@dataclass(frozen=True)
class LsofsFile:
    """Identity of one LSOFS output file (one valid hour of a cycle)."""

    day: date
    cycle: str  # 't00z' | 't06z' | 't12z' | 't18z'
    kind: str  # 'n' (nowcast) | 'f' (forecast)
    hour: int  # 0..6 nowcast, 0..120 forecast

    def __post_init__(self) -> None:
        if self.cycle not in ("t00z", "t06z", "t12z", "t18z"):
            raise ValueError(f"bad cycle: {self.cycle}")
        if self.kind not in ("n", "f"):
            raise ValueError(f"bad kind: {self.kind}")
        if self.hour < 0 or self.hour > 999:
            raise ValueError(f"bad hour: {self.hour}")

    @property
    def filename(self) -> str:
        ymd = self.day.strftime("%Y%m%d")
        return f"lsofs.{self.cycle}.{ymd}.fields.{self.kind}{self.hour:03d}.nc"

    def recent_key(self) -> str:
        ymd = self.day.strftime("%Y%m%d")
        return f"lsofs.{ymd}/{self.filename}"

    def archive_key(self) -> str:
        """Nested archive layout (native fields, 2024-11 -> present)."""
        d = self.day
        return f"lsofs/netcdf/{d.year:04d}/{d.month:02d}/{d.day:02d}/{self.filename}"

    def archive_flat_key(self, product: str = "regulargrid") -> str:
        """Flat archive layout (months 2024-03..2024-12). `product` is 'regulargrid'
        (the reliably-present one) or 'fields'. Not wired into the nowcast backfill —
        provided so a future regulargrid reader can address these months."""
        d = self.day
        ymd = d.strftime("%Y%m%d")
        fn = f"lsofs.{self.cycle}.{ymd}.{product}.{self.kind}{self.hour:03d}.nc"
        return f"lsofs/netcdf/{d.year:04d}{d.month:02d}/{fn}"


def _https(bucket: str, key: str, byterange: bool) -> str:
    url = f"https://{bucket}.{S3_HOST}/{key}"
    return url + BYTERANGE_SUFFIX if byterange else url


def recent_url(f: LsofsFile, recent_bucket: str, byterange: bool = True) -> str:
    return _https(recent_bucket, f.recent_key(), byterange)


def archive_url(f: LsofsFile, archive_bucket: str, byterange: bool = True) -> str:
    return _https(archive_bucket, f.archive_key(), byterange)


def archive_flat_url(f: LsofsFile, archive_bucket: str, product: str = "regulargrid",
                     byterange: bool = True) -> str:
    return _https(archive_bucket, f.archive_flat_key(product), byterange)


def candidate_urls(f: LsofsFile, recent_bucket: str, archive_bucket: str,
                   byterange: bool = True) -> list[str]:
    """URLs to try in order: recent bucket first (fast, <=30 d), then archive.

    The caller opens the first that succeeds. Ordering, not existence-checking —
    a HEAD probe belongs in the extractor, not in path construction.
    """
    return [
        recent_url(f, recent_bucket, byterange),
        archive_url(f, archive_bucket, byterange),
    ]
