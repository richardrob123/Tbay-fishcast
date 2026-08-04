"""Configuration loading — stations.yaml and knowledge-pack versioning.

Deterministic, no network. Every backtest/brief pins the knowledge-pack version
that produced it (ADR-013); `knowledge_pack_version()` is the hook for that.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIONS_YAML = REPO_ROOT / "stations.yaml"
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    lat: float
    lon: float
    lsofs_shore: bool
    exposure_bearing_deg: float | None = None
    lsofs_node: int | None = None
    node_depth_m: float | None = None
    node_dist_m: float | None = None
    coord_tier: str = "T4"
    field_verify: bool = True

    @property
    def has_lsofs(self) -> bool:
        """A station participates in the LSOFS temperature layer only if it is a
        Superior-shore station AND its node has been bootstrapped."""
        return self.lsofs_shore and self.lsofs_node is not None


@dataclass(frozen=True)
class LsofsConfig:
    recent_bucket: str
    archive_bucket: str
    archive_starts: str
    target_depths_m: tuple[float, ...]
    cycles: tuple[str, ...]
    nowcast_hours: int


@dataclass(frozen=True)
class Config:
    lsofs: LsofsConfig
    stations: tuple[Station, ...] = field(default_factory=tuple)

    def station(self, station_id: str) -> Station:
        for s in self.stations:
            if s.id == station_id:
                return s
        raise KeyError(f"unknown station: {station_id}")

    @property
    def shore_stations(self) -> tuple[Station, ...]:
        return tuple(s for s in self.stations if s.lsofs_shore)


def load_config(path: Path | str = STATIONS_YAML) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    l = raw["lsofs"]
    lsofs = LsofsConfig(
        recent_bucket=l["recent_bucket"],
        archive_bucket=l["archive_bucket"],
        archive_starts=str(l["archive_starts"]),
        target_depths_m=tuple(float(d) for d in l["target_depths_m"]),
        cycles=tuple(l["cycles"]),
        nowcast_hours=int(l["nowcast_hours"]),
    )
    stations = tuple(
        Station(
            id=s["id"],
            name=s["name"],
            lat=float(s["lat"]),
            lon=float(s["lon"]),
            lsofs_shore=bool(s["lsofs_shore"]),
            exposure_bearing_deg=s.get("exposure_bearing_deg"),
            lsofs_node=s.get("lsofs_node"),
            node_depth_m=s.get("node_depth_m"),
            node_dist_m=s.get("node_dist_m"),
            coord_tier=s.get("coord_tier", "T4"),
            field_verify=bool(s.get("field_verify", True)),
        )
        for s in raw["stations"]
    )
    return Config(lsofs=lsofs, stations=stations)


def knowledge_pack_version(knowledge_dir: Path | str = KNOWLEDGE_DIR) -> str:
    """Content hash of the knowledge pack (schemas + seed) — pinned per forecast (ADR-013).

    Deterministic: sorted file list, sha256 over path + bytes. Truncated to 12 hex.
    """
    kd = Path(knowledge_dir)
    h = hashlib.sha256()
    for p in sorted(kd.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(kd).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]
