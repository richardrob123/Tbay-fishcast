"""Classify every Thunder Bay river into pools / riffles / rapids / barriers (ADR-045).

Reads the 1 m lidar water surface along each river's OSM centreline, measures reach slope, and
calibrates the class edges from the POOLED slope distribution across all rivers — so "rapid" means
"steep for Thunder Bay", not a number someone picked. Writes:

    data/calib/river_reach_calib.json   measured edges + provenance + validation results
    web/data/rivers.geojson             classified reaches for the map

Validation runs every time and is printed, because the whole claim of this layer is that it is
MEASURED rather than inferred:
  * datum      — each profile's lake end vs Lake Superior's 183.2 m (independent of our pipeline)
  * barriers   — do our steep reaches coincide with OSM's own waterfall/dam/rapids features?
  * monotonic  — a river must run downhill; a large uphill fraction means bad centreline ordering
  * geomorph   — riffle spacing should land near the classical 5-7 channel widths

    python scripts/build_river_reaches.py [--step-m 5] [--no-validate]
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tbay_fishcast.features import river_profile as rp   # noqa: E402
from tbay_fishcast.ingest import hrdem                    # noqa: E402

CALIB = ROOT / "data" / "calib" / "river_reach_calib.json"
GEOJSON = ROOT / "web" / "data" / "rivers.geojson"
CACHE = ROOT / "data" / "hrdem_cache"
OSM_CACHE = ROOT / "data" / "osm_cache"


def _cached(name: str, fetch):
    """Cache OSM geometry on disk.

    Overpass rate-limits, and this build asks it for five centrelines plus the barrier set every
    run. Mid-development that got the build stalled behind a 180 s curl timeout with nothing to
    show; in CI it would be a flaky external dependency on a step that has no business being
    flaky. River centrelines change on the order of never, so cache them and let a human delete
    the directory to refresh.
    """
    OSM_CACHE.mkdir(parents=True, exist_ok=True)
    f = OSM_CACHE / f"{name}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except ValueError:
            pass
    out = fetch()
    f.write_text(json.dumps(out))
    return out

# The rivers that carry runs to this shore. Each is matched by OSM name inside its own box, so a
# same-named watercourse elsewhere in the domain cannot bleed in.
RIVERS = [
    {"id": "current", "name": "Current River", "osm": "Current River",
     "bbox": (48.40, -89.30, 48.55, -89.05), "width_m": 25.0,
     "mouth": (48.4510, -89.1842)},
    {"id": "neebing", "name": "Neebing River", "osm": "Neebing River",
     "bbox": (48.28, -89.40, 48.45, -89.15), "width_m": 20.0,
     "mouth": (48.3995, -89.2191)},
    {"id": "mcintyre", "name": "McIntyre River", "osm": "McIntyre River",
     "bbox": (48.35, -89.35, 48.50, -89.15), "width_m": 15.0,
     "mouth": (48.3995, -89.2191)},
    {"id": "kam", "name": "Kaministiquia River", "osm": "Kaministiquia River",
     "bbox": (48.30, -89.70, 48.50, -89.15), "width_m": 80.0,
     "mouth": (48.3661, -89.2525)},
    {"id": "mcvicar", "name": "McVicar Creek", "osm": "McVicar Creek",
     "bbox": (48.40, -89.30, 48.50, -89.15), "width_m": 8.0,
     "mouth": (48.4318, -89.2085)},
]

OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
BARRIER_MATCH_M = 250.0        # how close a measured step must be to an OSM barrier to corroborate


def _overpass(query: str, timeout: int = 180):
    last = ""
    for i, ep in enumerate(OVERPASS * 2):
        try:
            p = subprocess.run(["curl", "-sS", "-m", str(timeout),
                                "--data-urlencode", f"data={query}", ep],
                               capture_output=True, timeout=timeout + 20)
            if p.stdout:
                return json.loads(p.stdout.decode("utf-8", "replace"))
            last = p.stderr[:120].decode("latin-1", "replace")
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(min(2 * 2 ** i, 12))
    raise RuntimeError(f"overpass failed: {last}")


def fetch_centreline(r):
    return _cached(f"centreline_{r['id']}", lambda: _fetch_centreline(r))


def _fetch_centreline(r):
    s, w, n, e = r["bbox"]
    q = (f'[out:json][timeout:120];'
         f'(way["waterway"~"river|stream"]["name"="{r["osm"]}"]({s},{w},{n},{e}););out geom;')
    d = _overpass(q)
    ways = [[(p["lat"], p["lon"]) for p in (el.get("geometry") or [])]
            for el in d.get("elements", [])]
    return [w_ for w_ in ways if len(w_) >= 2]


def fetch_barriers():
    return _cached("barriers", _fetch_barriers)


def _fetch_barriers():
    q = ('[out:json][timeout:180];('
         'node["waterway"="waterfall"](48.0,-89.95,48.9,-87.95);'
         'node["natural"="waterfall"](48.0,-89.95,48.9,-87.95);'
         'way["waterway"="rapids"](48.0,-89.95,48.9,-87.95);'
         'node["waterway"="dam"](48.0,-89.95,48.9,-87.95);'
         'way["waterway"="dam"](48.0,-89.95,48.9,-87.95);'
         'node["waterway"="weir"](48.0,-89.95,48.9,-87.95););out center;')
    out = []
    for el in _overpass(q).get("elements", []):
        t = el.get("tags") or {}
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is not None:
            out.append({"kind": t.get("waterway") or t.get("natural"),
                        "name": t.get("name"), "lat": lat, "lon": lon})
    return out


def _dist_m(a, b):
    mlat = 111320.0
    mlon = 111320.0 * math.cos(math.radians(a[0]))
    return math.hypot((b[0] - a[0]) * mlat, (b[1] - a[1]) * mlon)


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step-m", type=float, default=5.0)
    ap.add_argument("--no-validate", action="store_true")
    a = ap.parse_args(argv)

    profiles = {}
    print("=== profiling rivers from 1 m lidar "
          f"({hrdem.STAC_ITEM}, acquired {hrdem.ACQUIRED}, DTM) ===")
    for r in RIVERS:
        try:
            ways = fetch_centreline(r)
        except Exception as e:  # noqa: BLE001
            print(f"  {r['name']:22s} centreline fetch FAILED ({str(e)[:60]})")
            continue
        line = rp.chain_ways(ways, mouth=r.get("mouth"))
        if len(line) < 5:
            print(f"  {r['name']:22s} only {len(line)} nodes chained from {len(ways)} ways — skip")
            continue
        pts, dist = rp.densify(line, a.step_m)
        pts = [p for p in pts if hrdem.in_footprint(*p)]
        dist = dist[:len(pts)]
        z, stats = hrdem.sample_water_surface(pts)
        good = [i for i, v in enumerate(z) if v is not None]
        if len(good) < 20:
            print(f"  {r['name']:22s} only {len(good)} lidar samples — skip")
            continue
        # fill short nodata gaps linearly so the slope window stays continuous
        zf = list(z)
        for i in range(len(zf)):
            if zf[i] is None:
                prev = next((j for j in range(i - 1, -1, -1) if zf[j] is not None), None)
                nxt = next((j for j in range(i + 1, len(zf)) if zf[j] is not None), None)
                if prev is None or nxt is None:
                    zf[i] = zf[prev] if prev is not None else zf[nxt]
                else:
                    f = (dist[i] - dist[prev]) / max(1e-6, dist[nxt] - dist[prev])
                    zf[i] = zf[prev] + (zf[nxt] - zf[prev]) * f
        # orient downstream: the profile must end at the lower end
        if zf[0] < zf[-1]:
            pts, zf = pts[::-1], zf[::-1]
            dist = [dist[-1] - d for d in dist[::-1]]
        win = rp.SLOPE_WINDOW_WIDTHS * r["width_m"]
        slopes = rp.reach_slope(dist, zf, win)
        uphill = sum(1 for s in slopes if s is not None and math.isfinite(s) and s < -1.0)
        fin = sum(1 for s in slopes if s is not None and math.isfinite(s))
        profiles[r["id"]] = {"r": r, "pts": pts, "dist": dist, "z": zf, "slopes": slopes,
                            "stats": stats, "uphill_frac": uphill / max(1, fin)}
        print(f"  {r['name']:22s} {dist[-1]/1000:6.2f} km  {len(pts):5d} pts  "
              f"drop {zf[0]-zf[-1]:6.1f} m  mouth {zf[-1]:6.1f} m  "
              f"nodata {stats['nodata']:4d}  transect-lowered {stats['transect_lowered']:5d}  "
              f"uphill {100*uphill/max(1,fin):4.1f}%")

    if not profiles:
        print("no rivers profiled")
        return 1

    pooled = [s for p in profiles.values() for s in p["slopes"]
              if s is not None and math.isfinite(s)]
    edges = rp.calibrate(pooled)
    print(f"\n=== measured class edges (pooled over {edges['n_samples']} samples, "
          f"{len(profiles)} rivers) ===")
    print(f"  pool   <= p{rp.FLAT_PCT:g}  = {edges['flat']:7.2f} m/km")
    print(f"  rapid  >= p{rp.STEEP_PCT:g}  = {edges['steep']:7.2f} m/km")
    print(f"  barrier>= p{rp.BARRIER_PCT:g}  = {edges['barrier']:7.2f} m/km")

    feats, summary = [], {}
    for rid, p in profiles.items():
        cls = rp.classify_points(p["slopes"], edges)
        min_len = rp.MIN_REACH_WIDTHS * p["r"]["width_m"]
        reaches = rp.to_reaches(p["dist"], p["z"], p["pts"], cls, min_len)
        below = rp.holding_water_below(reaches)
        for bi, pi in below.items():
            reaches[pi].meta["below_barrier"] = True
        counts = {}
        for rr in reaches:
            counts[rr.cls] = counts.get(rr.cls, 0) + 1
        summary[rid] = {"reaches": len(reaches), "by_class": counts,
                        "length_km": round(p["dist"][-1] / 1000, 2),
                        "drop_m": round(p["z"][0] - p["z"][-1], 1),
                        "mouth_z_m": round(p["z"][-1], 2),
                        "uphill_frac": round(p["uphill_frac"], 4),
                        "holding_below_barrier": len(below)}
        # geometry: one LineString per reach
        for rr in reaches:
            i0 = min(range(len(p["dist"])), key=lambda i: abs(p["dist"][i] - rr.start_m))
            i1 = min(range(len(p["dist"])), key=lambda i: abs(p["dist"][i] - rr.end_m))
            coords = [[round(q[1], 6), round(q[0], 6)] for q in p["pts"][i0:i1 + 1]]
            if len(coords) < 2:
                continue
            feats.append({"type": "Feature",
                          "geometry": {"type": "LineString", "coordinates": coords},
                          "properties": {"river": rid, "cls": rr.cls,
                                         "slope_m_km": round(rr.slope_m_km, 1),
                                         "len_m": round(rr.length_m),
                                         "drop_m": round(rr.drop_m, 2),
                                         **rr.meta}})
        print(f"  {p['r']['name']:22s} {len(reaches):3d} reaches  {counts}  "
              f"holding-below-barrier {len(below)}")

    validation = {}
    if not a.no_validate:
        print("\n=== VALIDATION ===")
        # 1. datum
        dat = {}
        for rid, p in profiles.items():
            dat[rid] = hrdem.datum_check(p["z"][-1])
            ok = "OK  " if dat[rid]["ok"] else "MISS"
            print(f"  datum  {ok} {p['r']['name']:22s} mouth {dat[rid]['measured_m']:7.2f} m "
                  f"vs {hrdem.LAKE_SUPERIOR_DATUM_M} m  (err {dat[rid]['error_m']:.2f})")
        validation["datum"] = dat
        # 2. barriers vs OSM's own features
        try:
            barr = fetch_barriers()
        except Exception as e:  # noqa: BLE001
            print(f"  barriers: OSM fetch failed ({str(e)[:50]})")
            barr = []
        steep_feats = [f for f in feats if f["properties"]["cls"] in ("barrier", "rapid")]
        matched = 0
        for f in steep_feats:
            c = f["geometry"]["coordinates"][len(f["geometry"]["coordinates"]) // 2]
            mid = (c[1], c[0])
            if any(_dist_m(mid, (b["lat"], b["lon"])) <= BARRIER_MATCH_M for b in barr):
                matched += 1
        frac = matched / max(1, len(steep_feats))
        print(f"  barriers: {matched}/{len(steep_feats)} measured steep reaches sit within "
              f"{BARRIER_MATCH_M:.0f} m of an OSM waterfall/dam/rapids feature ({100*frac:.0f}%)")
        print(f"            ({len(barr)} OSM barrier features in the domain)")
        validation["barrier_corroboration"] = {"matched": matched, "steep": len(steep_feats),
                                               "frac": round(frac, 3), "osm_features": len(barr),
                                               "match_radius_m": BARRIER_MATCH_M}
        # 3. monotonicity
        worst = max(profiles.values(), key=lambda p: p["uphill_frac"])
        print(f"  monotonic: worst uphill fraction {100*worst['uphill_frac']:.1f}% "
              f"({worst['r']['name']})")
        validation["worst_uphill_frac"] = round(worst["uphill_frac"], 4)
        # 4. geomorphology — riffle spacing in channel widths
        spac = {}
        for rid, p in profiles.items():
            rs = [f for f in feats if f["properties"]["river"] == rid
                  and f["properties"]["cls"] in ("riffle", "rapid", "barrier")]
            if len(rs) >= 3:
                # crude: total length / number of fast units, in channel widths
                spac[rid] = round((p["dist"][-1] / len(rs)) / p["r"]["width_m"], 1)
        if spac:
            print(f"  geomorph: mean spacing between fast units, in channel widths: {spac}")
            print("            (classical pool-riffle spacing is 5-7 widths; far outside that "
                  "range means the window or the width is wrong)")
        validation["fast_unit_spacing_widths"] = spac

    CALIB.parent.mkdir(parents=True, exist_ok=True)
    CALIB.write_text(json.dumps({
        "edges": edges, "rivers": summary, "validation": validation,
        "source": {"lidar": hrdem.STAC_ITEM, "acquired": hrdem.ACQUIRED, "product": "dtm",
                   "resolution_m": 1.0, "centreline": "OpenStreetMap waterway",
                   "step_m": a.step_m},
        "method": ("water-surface elevation sampled as the MINIMUM over a perpendicular transect; "
                   "reach slope by least squares over a window of "
                   f"{rp.SLOPE_WINDOW_WIDTHS:g} channel widths; class edges are percentiles of the "
                   "pooled regional slope distribution, not fixed values"),
        "tier": "T1 (measured lidar water surface)",
    }, indent=1) + "\n")
    GEOJSON.parent.mkdir(parents=True, exist_ok=True)
    GEOJSON.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"\nwrote {CALIB.relative_to(ROOT)} and {GEOJSON.relative_to(ROOT)} "
          f"({len(feats)} reaches)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
