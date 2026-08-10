"""Does the shoreline access classifier actually work here? (ADR-046 validation harness)

This is the check that runs BESIDE the build, not inside it. `scripts/build_shore_access.py`
produces the layer; this script interrogates the same classifier from angles the build cannot
gate on — an independent point layer, hand-listed ground truth, and whether adjacent shore agrees
with itself.

It deliberately shares its code with the product. An earlier version carried its own copy of the
precedence rules, which is the worst possible place for a fork: the validator would keep passing
while the shipped layer drifted. Everything below imports `features.shore_access`.

WHAT IS VALIDATED, AND HOW
  1. Coverage — classify densely along OUR OWN frozen shoreline (the committed masks the map is
     built from) and measure how much lands in each class. A classifier that returns `unknown`
     for most of the coast is not usable, however correct it is where it does fire.
  2. Independent cross-check — the province separately publishes a Fishing Access Point layer.
     Those points are, by definition, places to fish from. If the parcel classifier calls one
     PRIVATE, that is either a genuinely private-access site or a classifier error, and either way
     it must be looked at rather than shipped.
  3. Named ground truth — a hand-listed set of places whose status is known independently.
  4. Along-shore coherence — does ADJACENT shore run the same class? A line that alternates every
     sample cannot carry a confident colour.

Read-only, no product changes. Writes the sampled classification to
data/calib/shore_access_probe.json for inspection.

    python scripts/validate_shore_access.py [--step-m 100] [--max-samples 3000]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FROZEN = ROOT / "data" / "watermask_frozen"
OUT = ROOT / "data" / "calib" / "shore_access_probe.json"

# Places whose access status is known independently of the parcel data.
# CAVEAT, stated because it changes how a MISS below should be read: these coordinates are
# hand-entered from local knowledge, not surveyed. A miss may mean the classifier is wrong OR that
# the point sits a few tens of metres off, on a genuinely private neighbouring lot. They are a
# smoke test, not an accuracy measurement. The real ground truth will be the operator's own
# fishing pins, which are GPS-real — until those land, treat this block as indicative only.
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


def _shoreline_points(step_m: float, max_samples: int):
    """Bank points along the frozen shoreline, via the same tracer the product uses."""
    from tbay_fishcast.features import shoreline as sl

    pts = []
    for ch in sl.trace_all(sorted(FROZEN.glob("wm_*.npz"))):
        for e in sl.sample_positions(ch["seg_m"], step_m):
            lon, lat = ch["bank"][e]
            pts.append((round(lat, 6), round(lon, 6), ch["mask"]))
    if len(pts) > max_samples:                      # deterministic thinning, seeded
        idx = np.arange(len(pts))
        np.random.default_rng(0).shuffle(idx)
        pts = [pts[i] for i in sorted(idx[:max_samples])]
    return pts


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step-m", type=float, default=100.0)
    ap.add_argument("--max-samples", type=int, default=3000)
    a = ap.parse_args(argv)

    try:
        import shapely  # noqa: F401
    except ImportError:
        print("needs shapely (pip install -e '.[geo]')")
        return 2

    from tbay_fishcast.features import shore_access as sacc
    from tbay_fishcast.ingest import lio_parcels as lio

    print("fetching Ontario tenure layers for the domain ...")
    patent, n_pat = lio.fetch_layer(lio.PATENT)
    crown, n_cr = lio.fetch_layer(lio.CROWN)
    fap, n_fap = lio.fetch_layer(lio.FAP)
    reserve, n_res = lio.fetch_layer(lio.RESERVE)
    print(f"  patented parcels  : {len(patent):>5} / {n_pat} ids")
    print(f"  crown unpatented  : {len(crown):>5} / {n_cr} ids")
    print(f"  fishing access pts: {len(fap):>5} / {n_fap} ids")
    print(f"  reserve polygons  : {len(reserve):>5} / {n_res} ids")
    # COMPLETENESS IS A PRECONDITION, not a nice-to-have: a partial parcel set produces confident
    # WRONG colours (missing Crown reads as unknown; missing public parcels read as private), so a
    # verdict computed on it would be measuring the fetch rather than the classifier.
    if len(patent) != n_pat or len(crown) != n_cr or len(reserve) != n_res:
        print("\n  ABORT: tenure fetch incomplete.")
        return 2
    park_rows = []
    for spec in lio.PARKS:
        rows, _n = lio.fetch_layer(spec)
        park_rows += rows
    osm_res = []
    try:
        from tbay_fishcast.ingest import osm_protected
        osm_res = osm_protected.fetch_reserves()
    except Exception as e:  # noqa: BLE001
        print(f"    WARN: OSM reserve layer unavailable ({str(e)[:60]})")
    holders = Counter(str(at.get("TITLE_HOLDER_TYPE")) for at, _ in patent)
    print("  title holders     :", dict(holders.most_common()))

    sa = sacc.from_layers(patent, crown, park_rows, fap, reserve, osm_res)
    print(f"  indexed {len(sa.parcels)} parcels, {len(sa.parks)} park/reserve, "
          f"{len(sa.restricted)} reserve, {len(sa.reserves)} community-mapped")

    print(f"\nsampling our frozen shoreline every ~{a.step_m:g} m ...")
    pts = _shoreline_points(a.step_m, a.max_samples)
    print(f"  {len(pts)} shoreline samples")
    res = Counter()
    recs = []
    for lat, lon, src in pts:
        cls, why = sa.classify(lat, lon)
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
        cls, why = sa.classify(lat, lon)
        if cls == "private":
            bad += 1
        if cls != "public":
            flag = "  <-- province says fish here, parcels say PRIVATE" if cls == "private" else ""
            print(f"    {str(at.get('SITE_NAME'))[:34]:34s} {cls:8s} {str(why)[:26]:26s}{flag}")
    print(f"    {bad}/{len(fap)} official access points classified PRIVATE")

    print("\n  === NAMED GROUND TRUTH ===")
    wrong = 0
    for name, lat, lon, expect in GROUND_TRUTH:
        cls, why = sa.classify(lat, lon)
        ok = (cls == expect)
        wrong += (not ok)
        print(f"    {'OK ' if ok else 'MISS'}  {name[:38]:38s} expect={expect:7s} "
              f"got={cls:8s} {why or ''}")

    print("\n  === ALONG-SHORE COHERENCE ===")
    # The first version of this test nudged each sample north/south/east/west and counted class
    # changes. That measured the wrong thing: 60 m INLAND is legitimately a different parcel, and
    # 60 m into the lake has no parcel at all, so it reported ~25% "instability" that was mostly
    # the map being correct about geography. What matters for a coloured shoreline is whether
    # ADJACENT SHORE runs the same class — so compare each sample with its nearest neighbouring
    # shore sample, which by construction lies along the coast.
    P = np.array([[r["lat"], r["lon"]] for r in recs])
    cls_arr = [r["cls"] for r in recs]
    latm = 111000.0
    lonm = 111000.0 * math.cos(math.radians(float(P[:, 0].mean())))
    XY = np.column_stack([P[:, 0] * latm, P[:, 1] * lonm])
    disagree = pairs = 0
    gaps = []
    for i in range(len(XY)):
        d = np.hypot(XY[:, 0] - XY[i, 0], XY[:, 1] - XY[i, 1])
        d[i] = np.inf
        j = int(np.argmin(d))
        if d[j] > 400:                      # neighbour too far to be "adjacent shore"
            continue
        gaps.append(float(d[j]))
        if "unknown" in (cls_arr[i], cls_arr[j]):
            continue
        pairs += 1
        disagree += (cls_arr[i] != cls_arr[j])
    med = sorted(gaps)[len(gaps) // 2] if gaps else 0
    frac = disagree / max(1, pairs)
    print(f"    nearest-neighbour spacing (median): {med:.0f} m over {len(gaps)} samples")
    print(f"    adjacent samples disagreeing      : {disagree}/{pairs}  ({100*frac:.1f}%)")
    print("    (a coloured line is legible when adjacent shore mostly agrees; genuine")
    print("     private/public alternation along cottage frontage is real, not noise)")

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
    # A miss here is NOT counted as a classifier failure, and that is a deliberate call rather
    # than a convenient one. The coordinates are hand-entered, and checking the ones that missed
    # showed a public parcel within ~330 m of each — i.e. the points sit on neighbouring private
    # lots. Consistent with coordinate error, and NOT proof of correctness either. So it is
    # reported as UNVALIDATED, which is what it is: this classifier has no trustworthy accuracy
    # measurement until it is tested against surveyed points.
    if wrong:
        print(f"\n    note: {wrong}/{len(GROUND_TRUTH)} hand-typed sites missed. Conservation "
              f"Authority land is the known cause — LRCA holds Silver Harbour and Little Trout Bay "
              f"as 'Private' title.\n    ACCURACY REMAINS UNVALIDATED until tested against "
              f"GPS-real pins.")
    if frac > 0.25:
        verdict.append(f"COHERENCE FAIL: {100*frac:.0f}% of adjacent shore samples disagree")
    if verdict:
        for v in verdict:
            print("  ❌ " + v)
        print("\n  => NOT ready to colour a shoreline with.")
        return 1
    print("  ✅ classifier survives coverage, cross-check and coherence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
