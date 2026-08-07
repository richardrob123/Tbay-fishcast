"""NDBC buoy ocean-data client — REAL in-situ subsurface water temperature.

The Phase-0 breakthrough truth source. Western Lake Superior buoys 45027 and 45028
carry a thermistor reporting water temperature at depth (`.ocean` files: DEPTH, OTMP).
LSOFS is a whole-lake model, so these in-situ series are independent truth for LSOFS's
subsurface temperature and upwelling-event skill — the validation GLSEA (surface,
smoothed) and the distant Slate buoy could not provide (see docs/DATA_SOURCES.md).

Two feeds, same `.ocean` column layout (YY MM DD hh mm DEPTH OTMP ...):
  * realtime2/<id>.ocean         — last ~45 days
  * historical/ocean/<id>o<YR>.txt.gz — full past years

Sensor deploy depth varies by year (1.0 m in 2024-25, 6.0 m in 2026 for 45027); each
record carries its own DEPTH so the model is compared at the matching depth. Pure
parse + HTTP; no LLM.
"""
from __future__ import annotations

import gzip
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from . import SourceUnavailable

REALTIME = "https://www.ndbc.noaa.gov/data/realtime2/{station}.ocean"
HISTORICAL = "https://www.ndbc.noaa.gov/data/historical/ocean/{station}o{year}.txt.gz"
STDMET = "https://www.ndbc.noaa.gov/data/realtime2/{station}.txt"  # standard-met: WDIR/WSPD (wind)
MISSING = {"MM", "999.0", "99.0", "9999.0", "999"}
_KN_PER_MS = 1.943844   # NDBC stdmet WSPD is m/s; the product speaks knots


@dataclass(frozen=True)
class Buoy:
    station: str
    lat: float
    lon: float
    name: str


# Lake Superior buoys with subsurface `.ocean` thermistors (verified 2026-08-05).
# Spread across the lake so replication tests whether LSOFS bias is model-wide or local.
BUOYS = {
    "45027": Buoy("45027", 46.860, -91.930, "Western Superior (McQuade)"),
    "45028": Buoy("45028", 46.814, -91.829, "Western Superior"),
    "45023": Buoy("45023", 47.270, -88.610, "Central Superior"),
    "45216": Buoy("45216", 46.907, -89.354, "South-central Superior (Ontonagon)"),  # NDBC station_table
}


@dataclass(frozen=True)
class OceanRecord:
    time: datetime  # UTC
    depth_m: float
    temp_c: float


def parse_ocean(text: str) -> list[OceanRecord]:
    """Parse an NDBC .ocean payload (realtime or decompressed historical)."""
    out: list[OceanRecord] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = line.split()
        if len(p) < 7 or p[6] in MISSING or p[5] in MISSING:
            continue
        try:
            t = datetime(int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]),
                         tzinfo=timezone.utc)
            depth, temp = float(p[5]), float(p[6])
        except ValueError:
            continue
        if temp >= 50 or temp <= -5:  # physical sanity for liquid lake water
            continue
        out.append(OceanRecord(time=t, depth_m=depth, temp_c=temp))
    return out


def fetch_ocean_history(station: str, year: int, timeout: float = 90.0) -> list[OceanRecord]:
    url = HISTORICAL.format(station=station, year=year)
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 404:
            return []
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"NDBC history {station} {year} unreachable: {e}") from e
    return parse_ocean(gzip.decompress(r.content).decode(errors="replace"))


def fetch_ocean_realtime(station: str, timeout: float = 60.0) -> list[OceanRecord]:
    url = REALTIME.format(station=station)
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 404:
            return []
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"NDBC realtime {station} unreachable: {e}") from e
    return parse_ocean(r.text)


@dataclass(frozen=True)
class WindRecord:
    time: datetime      # UTC
    dir_deg: float      # direction wind is coming FROM (degrees true)
    speed_kn: float     # knots (converted from NDBC m/s)


def parse_stdmet_wind(text: str) -> list[WindRecord]:
    """Parse WDIR/WSPD from an NDBC standard-met (.txt) payload. Columns:
    #YY MM DD hh mm WDIR WSPD GST ... — WDIR degT, WSPD m/s. MM = missing (skipped)."""
    out: list[WindRecord] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = line.split()
        if len(p) < 7 or p[5] in MISSING or p[6] in MISSING:
            continue
        try:
            t = datetime(int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]), tzinfo=timezone.utc)
            d = float(p[5]); spd_ms = float(p[6])
        except ValueError:
            continue
        if not (0.0 <= d <= 360.0) or spd_ms < 0 or spd_ms > 60:   # physical sanity
            continue
        out.append(WindRecord(time=t, dir_deg=d, speed_kn=round(spd_ms * _KN_PER_MS, 2)))
    return out


def fetch_stdmet_wind(station: str, timeout: float = 60.0) -> list[WindRecord]:
    """Observed over-lake wind (knots) at an NDBC buoy — the real over-water wind the upwelling
    signal depends on, for cross-checking the GFS forecast. Newest-first as NDBC serves it."""
    url = STDMET.format(station=station)
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 404:
            return []
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"NDBC stdmet {station} unreachable: {e}") from e
    return parse_stdmet_wind(r.text)
