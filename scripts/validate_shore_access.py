"""Does a parcel-derived public/private shoreline classifier actually work here? (ADR-044 spike)

The idea: colour the shoreline by whether the land behind it is publicly owned, so the map can
show where you can stand without crossing someone's lot. Ontario publishes the parcels, so this
is answerable with T1 data rather than guesswork — but "answerable" and "correct" are different
claims, and this script exists to test the second one BEFORE any of it reaches the product.

THE TRAP THIS SCRIPT EXISTS TO CATCH. The obvious rule is "patented land = private". It is wrong,
and dangerously so: patented merely means granted out of the Crown, and a city park is patented
land. That rule marks Marina Park, Silver Harbour Conservation Area and the Mountdale boat launch
as PRIVATE — three of the most-used legal access points in Thunder Bay. Steering an angler away
from legal water is the same class of failure as steering them onto closed water (rule 4), just
inverted. The fix is TITLE_HOLDER_TYPE: of 1,538 patented parcels in the domain, 1,209 are
Private and 329 are Municipal / Provincial-agency / Federal.

WHAT IS VALIDATED, AND HOW
  1. Coverage — classify densely along OUR OWN frozen shoreline (the same committed masks the map
     is built from) and measure how much lands in each class. A classifier that returns "unknown"
     for most of the coast is not usable, however correct it is where it does fire.
  2. Independent cross-check — the province separately publishes a Fishing Access Point layer.
     Those points are, by definition, places to fish from. If the parcel classifier calls one
     PRIVATE, that is either a genuinely private-access site or a classifier error, and either way
     it must be looked at rather than shipped.
  3. Named ground truth — a hand-listed set of places whose status is known independently
     (conservation areas, municipal parks, boat launches). These are the assertions that would
     become regression tests.
  4. Stability — reclassify each sample after nudging it a few tens of metres along shore. A
     classification that flips under small perturbation cannot carry a confident colour.

Read-only, no product changes. Prints a verdict; writes the sampled classification to
data/calib/shore_access_probe.json for inspection.

    python scripts/validate_shore_access.py [--step-m 100] [--max-samples 4000]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FROZEN = ROOT / "data" / "watermask_frozen"
OUT = ROOT / "data" / "calib" / "shore_access_probe.json"

LIO = ("https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/LIO_OPEN_DATA/"
       "LIO_Open{svc:02d}/MapServer/{lid}/query")
BBOX = "-89.95,47.95,-87.95,48.90"
PATENT = dict(svc=8, lid=35, fields="TITLE_HOLDER_TYPE")     # granted out of Crown (any holder)
CROWN = dict(svc=8, lid=34, fields="OGF_ID")                 # Crown unpatented = public
FAP = dict(svc=7, lid=15, fields="SITE_NAME,FISHING_ACCESS_POINT_TYPE")

PUBLIC_HOLDERS = {"Municipal Government", "Other Provincial Government Agency",
                  "Federal Government", "Provincial Government"}

# LAYERS DELIBERATELY NOT USED, and why — both look like the fix and are traps:
#
#   Conservation Authority Admin Area (LIO_Open03/11) — JURISDICTION, NOT OWNERSHIP. Its polygons
#   are whole watersheds (the Hamilton Region CA feature is 450 km2). Treating it as "public land"
#   would paint every private cottage lot inside a CA's jurisdiction green — the opposite of the
#   error we are trying to fix, and far more dangerous, since a false GREEN sends someone onto
#   private property while a false RED only keeps them off legal water.
#
#   TITLE_HOLDER_TYPE alone — necessary but NOT sufficient. It records who holds title, and
#   Conservation Authorities hold theirs as "Private": Little Trout Bay, Hazelwood Lake and
#   Silver Harbour are conservation areas that exist FOR public recreation and every one of them
#   reads Private. Ownership is not accessibility, and no layer in this open-data set carries
#   accessibility directly.

# Places whose access status is known independently of the parcel data. These are the assertions
# that become regression tests if the classifier survives.
GROUND_TRUTH = [
    ("Marina Park / Prince Arthur's Landing", 48.4318, -89.2118, "public"),
    ("Chippewa Park",                          48.3556, -89.2372, "public"),
    ("Silver Harbour Conservation Area",       48.4880, -89.0640, "public"),
    ("MacKenzie Point",                        48.4360, -89.0230, "public"),
    ("Boulevard Lake park",                    48.4520, -89.2010, "public"),
    ("Sleeping Giant PP (Silver Islet rd)",    48.3700, -88.8600, "public"),
    ("Little Trout Bay Conservation Area",     48.0640, -89.5230, "public"),
    ("Hurkett Cove Conservation Area",         48.7250, -88.8450, "public"),
]


def _post(url: str, params: dict):
    """POST rather than GET: an objectIds list of a few hundred blows past URL length limits."""
    body = urllib.parse.urlencode(params).encode()
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            d = json.loads(urllib.request.urlopen(req, timeout=240).read())
            if d.get("error"):
                raise RuntimeError(str(d["error"])[:120])
            return d
        except Exception:  # noqa: BLE001
            if attempt == 3:
                raise
            time.sleep(2 ** attempt * 2)
    return {}


def _fetch_all(spec, want_geom=True, where="1=1", chunk=150):
    """Fetch a layer COMPLETELY, by object id.

    resultOffset paging cannot be trusted on these layers: the Crown-parcel layer silently
    returned a 496-row second page — no exceededTransferLimit flag, no error — so an offset loop
    that stops on a short page collected 996 of 1,380 parcels. The missing polygons then read as
    `unknown` shoreline, i.e. a fetch bug disguised as a data gap. Asking for the id list first
    and pulling geometries in id batches makes completeness CHECKABLE, which is the point:
    the caller asserts it below.
    """
    url = LIO.format(**spec)
    base = {"where": where, "geometry": BBOX, "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "f": "json"}
    ids = _post(url, {**base, "returnIdsOnly": "true"}).get("objectIds") or []
    out = []
    for i in range(0, len(ids), chunk):
        batch = ids[i:i + chunk]
        d = _post(url, {"objectIds": ",".join(str(x) for x in batch),
                        "outFields": spec["fields"], "returnGeometry": str(want_geom).lower(),
                        "outSR": "4326", "f": "json"})
        for f in d.get("features") or []:
            g = f.get("geometry") or {}
            out.append((f.get("attributes", {}), g.get("rings") or (g.get("x"), g.get("y"))))
    if len(out) != len(ids):
        print(f"    WARN: {spec} fetched {len(out)} of {len(ids)} ids")
    return out, len(ids)


def _shoreline_points(step_m: float, max_samples: int):
    """Land pixels adjacent to water, from the committed masks the product itself uses."""
    # inverse WebMercator, closed form — avoids a pyproj dependency in a probe script
    R = 6378137.0

    def to_ll(mx, my):
        lon = mx / R * 180.0 / math.pi
        lat = (2.0 * math.atan(math.exp(my / R)) - math.pi / 2.0) * 180.0 / math.pi
        return lon, lat
    pts = []
    for f in sorted(FROZEN.glob("wm_*.npz")):
        d = np.load(f)
        h, w = int(d["h"]), int(d["w"])
        m = np.unpackbits(d["packed"])[: h * w].reshape(h, w).astype(bool)  # True = water
        x0, y0, x1, y1 = d["bounds"]
        # land pixel touching water in 4-neighbourhood => shore
        land = ~m
        nb = np.zeros_like(m)
        nb[1:, :] |= m[:-1, :]; nb[:-1, :] |= m[1:, :]
        nb[:, 1:] |= m[:, :-1]; nb[:, :-1] |= m[:, 1:]
        shore = land & nb
        ys, xs = np.nonzero(shore)
        if not len(ys):
            continue
        res = (x1 - x0) / w
        keep = max(1, int(round(step_m / res)))
        idx = np.arange(len(ys))
        np.random.default_rng(0).shuffle(idx)          # deterministic spread, seeded
        idx = idx[:: keep][: max_samples // max(1, len(list(FROZEN.glob('wm_*.npz'))))]
        for i in idx:
            mx = x0 + (xs[i] + 0.5) * res
            my = y1 - (ys[i] + 0.5) * ((y1 - y0) / h)
            lon, lat = to_ll(mx, my)
            pts.append((round(lat, 6), round(lon, 6), f.stem))
    return pts


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step-m", type=float, default=100.0)
    ap.add_argument("--max-samples", type=int, default=3000)
    a = ap.parse_args(argv)

    try:
        from shapely.geometry import Point, Polygon
        from shapely.strtree import STRtree
    except ImportError:
        print("needs shapely (pip install -e '.[geo]')")
        return 2

    print("fetching Ontario parcel layers for the domain ...")
    patent, n_pat = _fetch_all(PATENT)
    crown, n_cr = _fetch_all(CROWN)
    fap, n_fap = _fetch_all(FAP, want_geom=True)
    print(f"  patented parcels : {len(patent):>5} / {n_pat} ids")
    print(f"  crown unpatented : {len(crown):>5} / {n_cr} ids")
    print(f"  fishing access pts:{len(fap):>5} / {n_fap} ids")
    # COMPLETENESS IS A PRECONDITION, not a nice-to-have: a partial parcel set produces confident
    # WRONG colours (missing Crown reads as unknown; missing public parcels read as private).
    if len(patent) != n_pat or len(crown) != n_cr:
        print("\n  ABORT: parcel fetch incomplete — any verdict below would be measuring the "
              "fetch, not the classifier.")
        return 2
    holders = Counter(str(at.get("TITLE_HOLDER_TYPE")) for at, _ in patent)
    print("  title holders    :", dict(holders.most_common()))

    def _signed_area(r):
        return sum((r[i][0]*r[i+1][1] - r[i+1][0]*r[i][1]) for i in range(len(r)-1)) / 2.0

    def polys(rows, tag):
        """ArcGIS packs a feature's exterior AND interior rings into one `rings` list, with
        exteriors clockwise (negative signed area in x/y order) and holes counter-clockwise.
        The first version treated every ring as a solid polygon, so each HOLE became fake
        coverage — which is how Silver Harbour CA came out `private`: it sits in the hole of a
        surrounding private parcel."""
        out = []
        for at, rings in rows:
            if not rings or isinstance(rings, tuple):
                continue
            shells = [r for r in rings if len(r) >= 4 and _signed_area(r) < 0]
            holes = [r for r in rings if len(r) >= 4 and _signed_area(r) >= 0]
            if not shells:                       # degenerate/unsigned: fall back to all rings
                shells = [r for r in rings if len(r) >= 4]
                holes = []
            for sh in shells:
                try:
                    g = Polygon(sh, holes if len(shells) == 1 else None)
                    if not g.is_valid:
                        g = g.buffer(0)
                    if not g.is_empty:
                        out.append((g, at, tag))
                except Exception:  # noqa: BLE001
                    pass
        return out

    allp = polys(patent, "patent") + polys(crown, "crown")
    geoms = [g for g, _, _ in allp]
    tree = STRtree(geoms)
    print(f"  indexed {len(geoms)} polygons")

    def classify(lat, lon):
        p = Point(lon, lat)
        hits = [i for i in tree.query(p) if geoms[i].covers(p)]
        if not hits:
            return "unknown", None
        # private wins ties: if any private parcel covers the point, treat as private (safe side)
        best = None
        for i in hits:
            _, at, tag = allp[i]
            if tag == "crown":
                best = best or ("public", "Crown unpatented")
                continue
            th = at.get("TITLE_HOLDER_TYPE")
            if th == "Private":
                return "private", th
            if th in PUBLIC_HOLDERS:
                best = ("public", th)
            else:
                best = best or ("unknown", th)
        return best or ("unknown", None)

    print(f"\nsampling our frozen shoreline every ~{a.step_m:g} m ...")
    pts = _shoreline_points(a.step_m, a.max_samples)
    print(f"  {len(pts)} shoreline samples")
    res = Counter()
    recs = []
    for lat, lon, src in pts:
        cls, why = classify(lat, lon)
        res[cls] += 1
        recs.append({"lat": lat, "lon": lon, "cls": cls, "holder": why, "mask": src})
    tot = max(1, sum(res.values()))
    print("\n  === COVERAGE along our shoreline ===")
    for k in ("public", "private", "unknown"):
        print(f"    {k:8s} {res[k]:>5}  {100*res[k]/tot:5.1f}%")

    print("\n  === CROSS-CHECK vs the province's own Fishing Access Points ===")
    bad = 0
    for at, xy in fap:
        if not isinstance(xy, tuple) or xy[0] is None:
            continue
        lon, lat = xy
        cls, why = classify(lat, lon)
        flag = "  <-- province says fish here, parcels say PRIVATE" if cls == "private" else ""
        if cls == "private":
            bad += 1
        if cls != "public":
            print(f"    {str(at.get('SITE_NAME'))[:34]:34s} {cls:8s} {str(why):26s}{flag}")
    print(f"    {bad}/{len(fap)} official access points classified PRIVATE")

    print("\n  === NAMED GROUND TRUTH ===")
    wrong = 0
    for name, lat, lon, expect in GROUND_TRUTH:
        cls, why = classify(lat, lon)
        ok = (cls == expect)
        wrong += (not ok)
        print(f"    {'OK ' if ok else 'MISS'}  {name[:38]:38s} expect={expect:7s} got={cls:8s} {why or ''}")

    print("\n  === STABILITY (nudge each sample ~60 m along shore) ===")
    flips = 0
    checked = 0
    for r in recs[:400]:
        dlat = 60.0 / 111000.0
        for dy, dx in ((dlat, 0), (-dlat, 0), (0, dlat / math.cos(math.radians(r["lat"]))),
                       (0, -dlat / math.cos(math.radians(r["lat"])))):
            c2, _ = classify(r["lat"] + dy, r["lon"] + dx)
            checked += 1
            if c2 != r["cls"] and "unknown" not in (c2, r["cls"]):
                flips += 1
    print(f"    {flips}/{checked} nudges flipped public<->private ({100*flips/max(1,checked):.1f}%)")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"samples": recs, "coverage": dict(res),
                               "holders": dict(holders)}, indent=1))
    print(f"\nwrote {OUT.relative_to(ROOT)}")

    print("\n" + "=" * 62)
    verdict = []
    if res["unknown"] / tot > 0.4:
        verdict.append(f"COVERAGE FAIL: {100*res['unknown']/tot:.0f}% of shoreline unclassified")
    if bad > 0:
        verdict.append(f"CROSS-CHECK FAIL: {bad} official fishing access points read PRIVATE")
    if wrong:
        verdict.append(f"GROUND TRUTH FAIL: {wrong}/{len(GROUND_TRUTH)} known sites misclassified")
    if flips / max(1, checked) > 0.10:
        verdict.append(f"STABILITY FAIL: {100*flips/max(1,checked):.0f}% flip under a 60 m nudge")
    if verdict:
        for v in verdict:
            print("  ❌ " + v)
        print("\n  => NOT ready to colour a shoreline with.")
    else:
        print("  ✅ classifier survives coverage, cross-check, ground truth and stability")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
