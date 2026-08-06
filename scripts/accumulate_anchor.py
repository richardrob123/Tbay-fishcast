"""Nearshore-anchor accumulation — Landsat shore skin-temp vs SAME-DAY GLSEA.

Logs, for every clear Landsat pass over each shore station, the nearshore water-pixel
skin temperature next to the actual same-day GLSEA offshore anchor it would replace,
appending to data/nearshore_anchor.csv. Over a season this builds the dataset for a
nearshore anchor correction and an independent nearshore validation truth.

AUDIT_ROUND3 redesign (the first version had three defects):
  * GLSEA is fetched AT THE SCENE DAY (`fetch_sst(..., px.day)`), not at run time — the
    old delta compared temperatures up to 20 days apart, which booked pure temporal
    drift into a "spatial anchor error" (the first committed rows did exactly that);
  * EVERY unlogged clear scene in the window is logged, not just the newest — two
    passes landing between runs no longer lose one forever;
  * a scene whose same-day GLSEA is unavailable is SKIPPED (key not consumed), so the
    pair can be completed on a later run instead of being permanently half-logged.

Honest caveats that any fitted correction must carry (also in docs/NEARSHORE_SST.md):
clear-sky selection (Landsat exists only on sunny days, when nearshore skin runs warm —
part of the delta is diurnal-skin, not spatial), and skin temp vs GLSEA's daily bulk
composite. The delta is a calibration *candidate*, not a bias to ship as-is.

Deterministic, idempotent, no LLM (ADR-001).

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
WINDOW_DAYS = 20   # Landsat C2 L2 latency ~10 d + clear-sky gaps
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
    stations = _shore_stations()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_rows: list[dict] = []

    try:
        scenes = landsat_st.search_scenes(BBOX, start, end, max_cloud=25)
    except Exception as e:  # noqa: BLE001
        print(f"anchor: STAC search failed ({str(e)[:60]})"); return 0

    glsea_cache: dict[tuple[str, float, float], object] = {}
    for item in scenes:                       # every clear-ish scene, newest first
        scene_day = item["properties"]["datetime"][:10]
        for sid, la, lo in stations:
            if (scene_day, sid) in have or any(r["scene_date"] == scene_day
                                               and r["station"] == sid for r in new_rows):
                continue
            try:
                px = landsat_st.sample_scene(item, la, lo)
            except Exception as e:  # noqa: BLE001
                print(f"{sid} {scene_day}: sample failed ({str(e)[:40]})"); continue
            if px is None:                    # clouded/off-scene/no nearby water pixel
                continue
            # SAME-DAY GLSEA only — a cross-day pair is not an anchor error. If the
            # day's GLSEA is unavailable, skip (don't consume the key): a later run
            # can complete the pair once the GLSEA archive fills in.
            ck = (px.day, la, lo)
            if ck not in glsea_cache:
                try:
                    glsea_cache[ck] = glsea.fetch_sst(la, lo, date.fromisoformat(px.day))
                except Exception:  # noqa: BLE001
                    glsea_cache[ck] = None
            g = glsea_cache[ck]
            if g is None:
                print(f"{sid} {px.day}: same-day GLSEA unavailable — skipped (retry later)")
                continue
            row = {
                "scene_date": px.day, "station": sid, "landsat_st_c": px.sst_c,
                "dist_m": px.dist_m, "scene": px.scene, "cloud_pct": round(px.cloud_pct, 1),
                "glsea_sst_c": round(g.sst_c, 2), "glsea_dist_km": round(g.dist_km, 2),
                "delta_c": round(px.sst_c - g.sst_c, 2), "retrieved_utc": now,
            }
            new_rows.append(row)
            print(f"{sid} {px.day}: Landsat {px.sst_c:.1f}C @{px.dist_m:.0f}m  "
                  f"GLSEA(same-day) {g.sst_c:.1f}C  Δ {row['delta_c']:+.2f}")

    if not new_rows:
        print("anchor: nothing new to append")
        return 0
    write_header = not LOG.exists()
    new_rows.sort(key=lambda r: (r["scene_date"], r["station"]))
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(new_rows)
    print(f"anchor: appended {len(new_rows)} row(s) -> {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
