"""Configuration loading — stations.yaml and knowledge-pack versioning.

Deterministic, no network. Every backtest/brief pins the knowledge-pack version
that produced it (ADR-013); `knowledge_pack_version()` is the hook for that.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
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
    node_lat: float | None = None
    node_lon: float | None = None
    coord_tier: str = "T4"
    field_verify: bool = True
    # Additive LSOFS bias correction (°C), fit against co-located in-situ truth
    # (buoy/logger). None until such data exists for this station — never a guess.
    # Buoy validation shows LSOFS runs +2.4…3.6 °C warm at mid-depth (docs/BUOY_VALIDATION.md).
    calibration_offset_c: float | None = None

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
class DateRange:
    start: date
    end: date

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


@dataclass(frozen=True)
class TemporalSplit:
    """Tune vs held-out validation windows (ADR-004, revised by ADR-018).

    `assign` returns 'tune' | 'validate' | None so a caller can never accidentally
    tune a threshold on a date it will later report skill for (CLAUDE rule 6)."""
    tune: DateRange
    validate: DateRange

    def assign(self, d: date) -> str | None:
        if self.tune.contains(d):
            return "tune"
        if self.validate.contains(d):
            return "validate"
        return None


@dataclass(frozen=True)
class ProductConfig:
    """The three product-defining constants — single source of truth (stations.yaml
    `product:`; provenance documented there). Map, pins, and briefs must all read
    these so the surfaces cannot silently diverge (AUDIT_ROUND3 finding)."""
    target_c: float = 12.0
    cast_m: float = 75.0
    max_reach_depth_m: float = 22.0


@dataclass(frozen=True)
class Species:
    """A target species and its PREFERRED-RANGE thermal model (stations.yaml `species:`).
    range_c=(cold, warm) °C: the map shades where the bottom is within this range and reachable
    within a cast. front_c is the warm-edge isotherm (the thermal break where fish feed) drawn as
    the prime mark. temp_cue 'strong'|'weak' flags how predictive temperature is for this species.
    See docs/FISH_BEHAVIOR_REVIEW.md."""
    id: str
    name: str
    range_c: tuple[float, float]
    front_c: float
    temp_cue: str = "strong"
    default: bool = False
    tier: str = "T3"
    note: str = ""


def _default_species() -> tuple[Species, ...]:
    return (Species("lake_trout", "Lake trout", (6.0, 12.0), 12.0, "strong", default=True),)


@dataclass(frozen=True)
class Config:
    lsofs: LsofsConfig
    temporal_split: TemporalSplit
    stations: tuple[Station, ...] = field(default_factory=tuple)
    product: ProductConfig = field(default_factory=ProductConfig)
    species: tuple[Species, ...] = field(default_factory=_default_species)

    @property
    def default_species(self) -> Species:
        for s in self.species:
            if s.default:
                return s
        return self.species[0]

    @property
    def band_temps(self) -> tuple[float, ...]:
        """All distinct isotherm temperatures needed across species (range endpoints + fronts),
        warmest first — the map computes each isotherm-depth field once and reuses it."""
        temps = set()
        for sp in self.species:
            temps.update(sp.range_c)
            temps.add(sp.front_c)
        return tuple(sorted(temps, reverse=True))

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

    def _d(v) -> date:
        return v if isinstance(v, date) else datetime.fromisoformat(str(v)).date()

    ts = raw["temporal_split"]
    temporal_split = TemporalSplit(
        tune=DateRange(_d(ts["tune"]["start"]), _d(ts["tune"]["end"])),
        validate=DateRange(_d(ts["validate"]["start"]), _d(ts["validate"]["end"])),
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
            node_lat=s.get("node_lat"),
            node_lon=s.get("node_lon"),
            calibration_offset_c=s.get("calibration_offset_c"),
            coord_tier=s.get("coord_tier", "T4"),
            field_verify=bool(s.get("field_verify", True)),
        )
        for s in raw["stations"]
    )
    p = raw.get("product", {})
    product = ProductConfig(
        target_c=float(p.get("target_c", 12.0)),
        cast_m=float(p.get("cast_m", 75.0)),
        max_reach_depth_m=float(p.get("max_reach_depth_m", 22.0)),
    )
    sp_raw = raw.get("species") or []
    species = tuple(
        Species(id=s["id"], name=s["name"],
                range_c=(float(s["range_c"][0]), float(s["range_c"][1])),
                front_c=float(s.get("front_c", s["range_c"][1])),
                temp_cue=s.get("temp_cue", "strong"),
                default=bool(s.get("default", False)),
                tier=s.get("tier", "T3"), note=s.get("note", ""))
        for s in sp_raw
    ) or _default_species()
    return Config(lsofs=lsofs, temporal_split=temporal_split, stations=stations,
                  product=product, species=species)


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
