"""Cached bathymetry profiles — the heartbeat's read side.

build_bathymetry.py fetches CHS NONNA-10 (or NCEI fallback) offline and writes one
JSON per station under knowledge/bathymetry/. This loader reads them: pure, no
network, no geo dependency — safe in the 4x/day loop (ADR-001). Each profile is a
shore-anchored (dist_m, depth_m) transect plus provenance (source, tier, retrieved,
attribution). Returns None when a station has no cached profile so callers can fall
back to a coarse guess and flag it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BATHY_DIR = REPO_ROOT / "knowledge" / "bathymetry"


@dataclass(frozen=True)
class BathyProfile:
    station_id: str
    dist_m: list[float]       # distance from shore, ascending
    depth_m: list[float]      # positive-down depth at each distance
    bearing_deg: float | None
    resolution_ground_m: float | None
    source: str
    tier: str
    retrieved: str | None
    attribution: str | None
    coverage_frac: float | None
    note: str

    @property
    def is_coarse(self) -> bool:
        """True for the NCEI ~92 m fallback (vs 10 m NONNA)."""
        return (self.resolution_ground_m or 0) > 30 or not self.source.startswith("CHS NONNA")

    @property
    def max_depth_m(self) -> float | None:
        return max(self.depth_m) if self.depth_m else None


def load(station_id: str, bathy_dir: Path | None = None) -> BathyProfile | None:
    path = (bathy_dir or BATHY_DIR) / f"{station_id}.json"
    if not path.exists():
        return None
    d = json.loads(path.read_text())
    if not d.get("dist_m") or len(d["dist_m"]) < 2:
        return None
    return BathyProfile(
        station_id=d.get("station_id", station_id),
        dist_m=[float(x) for x in d["dist_m"]],
        depth_m=[float(x) for x in d["depth_m"]],
        bearing_deg=d.get("bearing_deg"),
        resolution_ground_m=d.get("resolution_ground_m"),
        source=d.get("source", "unknown"),
        tier=d.get("tier", "T4"),
        retrieved=d.get("retrieved"),
        attribution=d.get("attribution"),
        coverage_frac=d.get("coverage_frac"),
        note=d.get("note", ""),
    )
