"""One-time backfill of the nearshore surface warm-delta from the HISTORICAL Landsat archive.

The live anchor (`data/nearshore_anchor.csv`) had only n=3 rows — three shore stations on ONE 2026
Landsat pass — yet the mean delta warms the whole map every day (audit T1e: the shakiest input).
This backfill converts it from a single-scene prior into a MULTI-YEAR distribution by replaying the
same shore-minus-same-day-GLSEA computation over every clear Landsat 8/9 summer scene since 2019.

Same methodology as `accumulate_anchor.py` (so the rows are comparable): nearest WATER-pixel Landsat
skin temp at each `lsofs_shore` station, minus the SAME-DAY GLSEA value it would replace. Landsat-7
is skipped (SLC-off striping + a different TIR asset name). Clear-sky selection bias (Landsat only
exists on sunny days, when nearshore skin runs warm) is inherent and already documented — this
backfill widens the sample, it does not remove that caveat. Idempotent: dedupes on (scene_date,
station), so re-running only adds new scenes.
"""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tbay_fishcast.ingest import glsea, landsat_st  # noqa: E402

LOG = ROOT / "data" / "nearshore_anchor.csv"
FIELDS = ["scene_date", "station", "landsat_st_c", "dist_m", "scene", "cloud_pct",
          "glsea_sst_c", "glsea_dist_km", "delta_c", "retrieved_utc"]
# City-arc bbox that contains all three shore stations (STAC scene search).
BBOX = (-89.32, 48.40, -88.90, 48.56)
YEARS = range(2019, 2026)
MONTHS = [(6, 1), (9, 30)]          # Jun 1 → Sep 30 (open-water, stratified)
MAX_CLOUD = 20.0
MAX_SCENES_PER_YEAR = 10            # bound runtime; plenty for a distribution


def _shore_stations():
    d = yaml.safe_load((ROOT / "stations.yaml").read_text())
    return [(s["id"], s["node_lat"], s["node_lon"]) for s in d["stations"]
            if s.get("lsofs_shore") and s.get("node_lat")]


def _existing_keys():
    if not LOG.exists():
        return set()
    return {(r["scene_date"], r["station"]) for r in csv.DictReader(open(LOG))}


def main() -> int:
    stations = _shore_stations()
    seen = _existing_keys()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_rows = []
    for yr in YEARS:
        try:
            scenes = landsat_st.search_scenes(BBOX, date(yr, *MONTHS[0]), date(yr, *MONTHS[1]),
                                              max_cloud=MAX_CLOUD)
        except Exception as e:  # noqa: BLE001
            print(f"{yr}: scene search failed ({str(e)[:50]})"); continue
        # Landsat 8/9 only (lwir11); skip L7 (lwir, SLC-off)
        scenes = [s for s in scenes if s["properties"].get("platform") in ("landsat-8", "landsat-9")]
        used = 0
        for item in scenes:
            if used >= MAX_SCENES_PER_YEAR:
                break
            sday = item["properties"]["datetime"][:10]
            cloud = float(item["properties"].get("eo:cloud_cover", -1))
            got_any = False
            for sid, la, lo in stations:
                if (sday, sid) in seen:
                    got_any = True
                    continue
                try:
                    px = landsat_st.sample_scene(item, la, lo)
                except Exception:  # noqa: BLE001 - bad asset / off-scene
                    px = None
                if not px:
                    continue
                try:
                    g = glsea.fetch_sst(la, lo, date.fromisoformat(px.day))
                except Exception:  # noqa: BLE001
                    g = None
                if not g:
                    continue
                new_rows.append({
                    "scene_date": px.day, "station": sid, "landsat_st_c": round(px.sst_c, 2),
                    "dist_m": round(px.dist_m, 1), "scene": px.scene, "cloud_pct": round(cloud, 1),
                    "glsea_sst_c": round(g.sst_c, 2), "glsea_dist_km": round(g.dist_km, 2),
                    "delta_c": round(px.sst_c - g.sst_c, 2), "retrieved_utc": now,
                })
                seen.add((px.day, sid))
                got_any = True
            if got_any:
                used += 1
        print(f"{yr}: {used} scenes used, {len([r for r in new_rows])} rows so far")

    if not new_rows:
        print("no new rows"); return 0
    header = not LOG.exists()
    with open(LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if header:
            w.writeheader()
        w.writerows(new_rows)
    deltas = [r["delta_c"] for r in new_rows]
    allrows = list(csv.DictReader(open(LOG)))
    alld = [float(r["delta_c"]) for r in allrows if r.get("delta_c")]
    alld.sort()
    med = alld[len(alld) // 2]
    print(f"\nappended {len(new_rows)} rows (Δ {min(deltas):+.2f}..{max(deltas):+.2f})")
    print(f"CSV now n={len(alld)}: mean {sum(alld)/len(alld):+.2f} °C, median {med:+.2f} °C, "
          f"range {min(alld):+.2f}..{max(alld):+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
