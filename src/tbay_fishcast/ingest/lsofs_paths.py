"""LSOFS NODD S3 key/URL construction — verified against live buckets 2026-08-04.

Two layouts exist, confirmed by listing the real buckets:

  RECENT  (bucket noaa-ofs-pds, <=~30 d rolling):
      lsofs.YYYYMMDD/lsofs.tHHz.YYYYMMDD.fields.{n,f}NNN.nc

  ARCHIVE (bucket noaa-nos-ofs-pds, historical):
      native fields:  lsofs/netcdf/YYYY/MM/DD/lsofs.tHHz.YYYYMMDD.fields.{n,f}NNN.nc
      regulargrid:    lsofs/netcdf/YYYYMM/lsofs.tHHz.YYYYMMDD.regulargrid.{n,f}NNN.nc  (2024-03..2024-12)

Phase 0 uses native `fields` files (node-indexed → HDF5 byte-range subsetting).
`regulargrid` is a separate (lat/lon) product and is out of Phase 0 scope.
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
        d = self.day
        return f"lsofs/netcdf/{d.year:04d}/{d.month:02d}/{d.day:02d}/{self.filename}"


def _https(bucket: str, key: str, byterange: bool) -> str:
    url = f"https://{bucket}.{S3_HOST}/{key}"
    return url + BYTERANGE_SUFFIX if byterange else url


def recent_url(f: LsofsFile, recent_bucket: str, byterange: bool = True) -> str:
    return _https(recent_bucket, f.recent_key(), byterange)


def archive_url(f: LsofsFile, archive_bucket: str, byterange: bool = True) -> str:
    return _https(archive_bucket, f.archive_key(), byterange)


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
