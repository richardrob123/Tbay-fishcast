"""Daily nearshore-anchor accumulation — Landsat shore skin-temp vs GLSEA offshore anchor.

GLSEA (and MUR/ERA5) land-mask the immediate shore, so a station's surface anchor is pulled
from a pixel ~0.6-1.3 km offshore — a measured ~1.5-2 C error at the shallow-isotherm spots
(docs/NEARSHORE_SST.md). Landsat C2 L2 ST (~100 m) keeps water pixels to the shoreline. This
step logs, once per clear Landsat pass, the nearshore skin temp, the GLSEA offshore anchor it
should replace, and their delta -> data/nearshore_anchor.csv. Accumulated over the season this
is the calibration set that turns "GLSEA offshore" into a corrected nearshore anchor, and the
independent truth to validate the product's shore surface against.

Deterministic, no LLM (ADR-001): a plain step in the daily coast-site Action. Idempotent on
(scene_date, station) — Landsat has ~10 d latency and clear-sky gaps, so most days re-find the
same recent scene and append nothing; a new row lands only when a new clear pass appears.

    python scripts/accumulate_anchor.py [end YYYY-MM-DD]   # default: today (UTC)
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from tbay_fishcast.ingest import glsea, landsat_st  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "nearshore_anchor.csv"
BBOX = [-89.40, 48.33, -88.88, 48.58]
WINDOW_DAYS = 20   # look back for the most recent clear water scene
FIELDS = ["scene_date", "station", "landsat_st_c", "dist_m", "scene", "cloud_pct",
          "glsea_sst_c", "glsea_dist_km", "delta_c", "retrieved_utc"]


def _shore_stations() -> list[tuple[str, float, float]]:
    d = yaml.safe_load((ROOT / "stations.yaml").read_text())
    return [(s["id"], s["lat"], s["lon"]) for s in d["stations"] if s.get("lsofs_shore")]


def _existing_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    with path.open() as f:
        return {(r["scene_date"], r["station"]) for r in csv.DictReader(f)}


def main(argv) -> int:
    end = date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.now(timezone.utc).date()
    start = end - timedelta(days=WINDOW_DAYS)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    have = _existing_keys(LOG)
    new_rows: list[dict] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for sid, la, lo in _shore_stations():
        try:
            px = landsat_st.fetch_recent_st(la, lo, BBOX, start, end, max_cloud=25)
        except Exception as e:  # noqa: BLE001
            print(f"{sid}: landsat fetch failed ({str(e)[:50]})"); continue
        if px is None:
            print(f"{sid}: no clear water scene in last {WINDOW_DAYS} d"); continue
        if (px.day, sid) in have:
            print(f"{sid}: latest scene {px.day} already logged"); continue
        g = None
        try:
            g = glsea.fetch_recent_sst(la, lo, end)
        except Exception:  # noqa: BLE001
            pass
        row = {
            "scene_date": px.day, "station": sid, "landsat_st_c": px.sst_c,
            "dist_m": px.dist_m, "scene": px.scene, "cloud_pct": round(px.cloud_pct, 1),
            "glsea_sst_c": round(g.sst_c, 2) if g else "",
            "glsea_dist_km": round(g.dist_km, 2) if g else "",
            "delta_c": round(px.sst_c - g.sst_c, 2) if g else "", "retrieved_utc": now,
        }
        new_rows.append(row)
        print(f"{sid} {px.day}: Landsat {px.sst_c:.1f}C @{px.dist_m:.0f}m  "
              f"GLSEA {row['glsea_sst_c']}C  Δ {row['delta_c']}")

    if not new_rows:
        print("anchor: nothing new to append")
        return 0
    write_header = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(new_rows)
    print(f"anchor: appended {len(new_rows)} row(s) -> {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
