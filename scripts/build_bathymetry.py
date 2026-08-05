"""Build cached cross-shore bathymetry profiles from CHS NONNA-10.

Run OFFLINE (once, or when a spot moves): pulls a NONNA-10 GeoTIFF per Superior-
shore station, reduces it to a shore-anchored depth-vs-distance transect, and
writes knowledge/bathymetry/<station>.json. The heartbeat/brief then reads those
static JSON files — no geo dependency and no CHS call in the 4x/day loop (ADR-001).

    python scripts/build_bathymetry.py            # all shore stations
    python scripts/build_bathymetry.py silver_harbour_outer

Provenance (CHS NONNA licence): each profile records source + retrieval date; any
derivative product (map, brief) must carry the CHS attribution notice.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.ingest import ncei_bathy, nonna  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1] / "knowledge" / "bathymetry"
RETRIEVED = date(2026, 8, 5).isoformat()  # deterministic stamp (context date)


def _usable(prof: dict) -> bool:
    return bool(prof.get("snapped")) and len(prof.get("dist_m", [])) >= 3


def build_one(station) -> dict:
    # PRIMARY: CHS NONNA-10 (10 m hydrographic survey)
    prof = nonna.build_profile(
        station.lat, station.lon,
        bearing_deg=station.exposure_bearing_deg,
        half_m=1500.0, scale_px=300,  # 300px/3km => ~10 m sampling of the ~10 m grid
    )
    # FALLBACK: NCEI 92 m grid where NONNA has a nearshore hole (e.g. MacKenzie Point)
    if not _usable(prof):
        grd = ncei_bathy.default_grd_path()
        if grd is not None:
            alt = ncei_bathy.build_profile(
                station.lat, station.lon, grd, bearing_deg=station.exposure_bearing_deg)
            if _usable(alt):
                print(f"   NONNA sparse -> NCEI fallback ({grd.name})")
                prof = alt
    is_nonna = prof.get("source", "").startswith("CHS NONNA")
    prof.update({
        "station_id": station.id,
        "station_name": station.name,
        "retrieved": RETRIEVED,
        # NONNA = official CHS survey (T1); NCEI 92 m fallback is coarse, T2.
        "tier": "T1" if is_nonna else "T2",
        "licence": ("CHS NONNA — non-navigational; attribution required in derivatives"
                    if is_nonna else "NCEI Great Lakes bathymetry — US Government, public domain"),
        "attribution": (
            "Contains intellectual property of the Canadian Hydrographic Service (CHS), "
            "Fisheries and Oceans Canada. Not for navigation." if is_nonna
            else "Bathymetry: NOAA National Centers for Environmental Information (NCEI)."),
    })
    return prof


def main(argv) -> int:
    cfg = load_config()
    wanted = set(argv[1:])
    stations = [s for s in cfg.shore_stations if not wanted or s.id in wanted]
    if not stations:
        print(f"no matching shore stations for {wanted}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for s in stations:
        print(f"-> {s.id} ({s.lat:.4f},{s.lon:.4f}) exposure={s.exposure_bearing_deg}")
        try:
            prof = build_one(s)
        except Exception as e:
            print(f"   FAILED: {e}")
            continue
        out = OUT_DIR / f"{s.id}.json"
        out.write_text(json.dumps(prof, indent=2))
        n = len(prof["dist_m"])
        cov = prof["coverage_frac"]
        if prof["snapped"] and n > 1:
            print(f"   ok  bearing {prof['bearing_deg']}deg  {n} pts to "
                  f"{prof['dist_m'][-1]:.0f} m  max depth {max(prof['depth_m']):.1f} m  "
                  f"res {prof['resolution_ground_m']} m  water-cov {cov:.0%}")
        else:
            print(f"   sparse: snapped={prof['snapped']} pts={n} cov={cov:.0%} — {prof['note']}")
        print(f"   wrote {out.relative_to(OUT_DIR.parents[1])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
