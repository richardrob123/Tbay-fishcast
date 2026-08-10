"""Freeze the coloured shoreline: Ontario land tenure -> web/data/shore_access.geojson (ADR-046).

WHY THIS IS FROZEN RATHER THAN BUILT DAILY. Both inputs are static. The water masks are already
committed (``data/watermask_frozen/``) so the coast does not move between builds, and the parcel
fabric changes on the timescale of land transactions, not weather. Regenerating it four times a
day would burn ~3,000 ArcGIS requests to reproduce the same bytes — the same reasoning that made
``data/river_geometry.json`` a committed artifact. Re-run this script when the masks change or
when the province republishes the parcel layers.

WHAT THE COLOURS MEAN, precisely, because the gap between these two sentences is the whole risk:
  green  — the land behind this shore is PUBLIC (Crown, government-held title, or a regulated
           park/reserve). It does NOT mean you can get down to the water; a public bank can be a
           cliff or a rail corridor.
  yellow — we have conflicting or absent tenure evidence. Most of this is private title sitting
           beside an official provincial fishing access point, which is exactly the case where a
           confident answer would be a lie.
  red    — patented land in private title, with nothing contradicting it.

ACCURACY IS UNVALIDATED against surveyed points; see the note printed at the end and the
provenance block written into the geojson. The layer ships with that stated, not hidden.

    python scripts/build_shore_access.py [--step-m 60] [--out web/data/shore_access.geojson]
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FROZEN = ROOT / "data" / "watermask_frozen"
DEFAULT_OUT = ROOT / "web" / "data" / "shore_access.geojson"

# Classification points sit at the centre of the land pixel touching water — one half-pixel
# inland of the traced line, which is the closest point that is unambiguously on the bank.
#
# NO INLAND PROBE, and that is a measured decision rather than a stylistic one. The first version
# walked up to four mask pixels inland wherever the bank pixel found no tenure record, justified
# as correcting registration error between a raster waterline and a surveyed parcel fabric. If
# that were the cause the fix would resolve at ONE pixel; the measured histogram was flat
# (22 / 20 / 20 / 20 samples resolving at 1 / 2 / 3 / 4 pixels), which is the signature of walking
# inland until something is hit, not of closing a registration gap. It bought 2.2% of samples by
# attributing parcels that do not front the shore, so it is gone: those samples are `unknown`,
# which is what they are.

# Douglas-Peucker tolerance for the drawn line. 6 m is ~1.5 mask pixels: it removes the raster
# staircase without moving the line further than the mask itself is certain of.
SIMPLIFY_M = 6.0
COORD_DP = 5          # ~1.1 m, an order below the 4 m mask resolution


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step-m", type=float, default=60.0,
                    help="ground spacing of classification samples along the shore")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)

    try:
        from shapely.geometry import LineString
    except ImportError:
        print("needs shapely (pip install -e '.[geo]')")
        return 2

    from tbay_fishcast.features import shore_access as sacc
    from tbay_fishcast.features import shoreline as sl
    from tbay_fishcast.ingest import lio_parcels as lio

    print("fetching Ontario tenure layers for the domain ...")
    patent, n_pat = lio.fetch_layer(lio.PATENT)
    crown, n_cr = lio.fetch_layer(lio.CROWN)
    fap, n_fap = lio.fetch_layer(lio.FAP)
    print(f"  patented parcels  : {len(patent):>5} / {n_pat} ids")
    print(f"  crown unpatented  : {len(crown):>5} / {n_cr} ids")
    print(f"  fishing access pts: {len(fap):>5} / {n_fap} ids")
    # COMPLETENESS IS A PRECONDITION. A partial parcel set does not degrade gracefully: missing
    # Crown reads as yellow and missing public parcels read as RED, i.e. a fetch bug ships as a
    # confident instruction to stay off legal water.
    if len(patent) != n_pat or len(crown) != n_cr:
        print("\n  ABORT: tenure fetch incomplete — the layer would be wrong, not merely thin.")
        return 2
    park_rows = []
    for spec in lio.PARKS:
        rows, _n = lio.fetch_layer(spec)
        park_rows += rows
    # THE SAFETY LAYER. 2.6 km of our traced shoreline lies inside Fort William 52. Reserve land
    # carries federal/Crown attributes — exactly the ones this classifier reads as PUBLIC — so
    # without this the map would eventually paint reserve frontage green and invite people to walk
    # onto it. It overrides every other line of evidence (see shore_access.decide).
    reserve_rows, n_res = lio.fetch_layer(lio.RESERVE)
    if len(reserve_rows) != n_res:
        print("\n  ABORT: First Nation reserve layer fetch incomplete — refusing to build a map "
              "that could paint reserve frontage as public.")
        return 2
    osm_res = []
    try:
        from tbay_fishcast.ingest import osm_protected
        osm_res = osm_protected.fetch_reserves()
    except Exception as e:  # noqa: BLE001
        # T3, demotion-only: losing it costs precision on a handful of Conservation Areas, never
        # correctness in the dangerous direction. Loud, but not fatal.
        print(f"    WARN: OSM reserve layer unavailable ({str(e)[:60]}) — Conservation Areas held "
              f"as private title will read RED rather than being demoted to unknown")
    holders = Counter(str(at.get("TITLE_HOLDER_TYPE")) for at, _ in patent)
    print("  title holders     :", dict(holders.most_common()))

    sa = sacc.from_layers(patent, crown, park_rows, fap, reserve_rows, osm_res)
    print(f"  indexed {len(sa.parcels)} parcels, {len(sa.parks)} regulated park/reserve polygons, "
          f"{len(sa.restricted)} reserve polygons, {len(sa.reserves)} community-mapped areas")

    print("\ntracing the frozen shoreline ...")
    chains = sl.trace_all(sorted(FROZEN.glob("wm_*.npz")))
    total_km = sum(sum(c["seg_m"]) for c in chains) / 1000.0
    print(f"  {len(chains)} polylines, {total_km:.1f} km of shoreline")
    if not chains:
        print("  ABORT: no shoreline traced — the frozen masks are missing or unreadable.")
        return 2

    feats = []
    cover = Counter()          # ground metres per class
    why_km = Counter()         # ground metres per stated reason — the receipt for the colours
    no_tenure = n_samples = 0
    for ch in chains:
        pts, seg = ch["pts"], ch["seg_m"]
        idx = sl.sample_positions(seg, a.step_m)
        labels_at = []
        for e in idx:
            lon, lat = ch["bank"][e]
            ev = sa.evidence(lat, lon)
            if not (ev.park or ev.crown or ev.holders):
                no_tenure += 1
            labels_at.append(sacc.decide(ev))
        # every edge takes the class of the nearest classification sample along the chain
        # (bisect, not a scan: some chains carry 10k edges and the naive form is quadratic)
        labels = []
        for e in range(len(seg)):
            k = bisect.bisect_left(idx, e)
            if k == 0:
                j = 0
            elif k >= len(idx):
                j = len(idx) - 1
            else:
                j = k if (idx[k] - e) < (e - idx[k - 1]) else k - 1
            labels.append(labels_at[j])
        n_samples += len(idx)
        for s, t, lab in sl.runs_from_labels(labels):
            cls, why = lab
            cover[cls] += sum(seg[s:t])
            why_km[f"{cls}: {why}"] += sum(seg[s:t])
            line = LineString(pts[s:t + 1])
            simp = line.simplify(SIMPLIFY_M / 111000.0, preserve_topology=False)
            coords = [[round(x, COORD_DP), round(y, COORD_DP)] for x, y in simp.coords]
            if len(coords) < 2:
                continue
            feats.append({"type": "Feature",
                          "properties": {"cls": cls, "why": why},
                          "geometry": {"type": "LineString", "coordinates": coords}})

    tot_m = max(1.0, sum(cover.values()))
    print("\n  === COVERAGE (km of shoreline) ===")
    for k in (sacc.PUBLIC, sacc.UNKNOWN, sacc.PRIVATE):
        print(f"    {k:8s} {cover[k]/1000:7.1f} km  {100*cover[k]/tot_m:5.1f}%")
    print(f"  samples with NO tenure record on the bank pixel: "
          f"{no_tenure}/{n_samples} ({100*no_tenure/max(1,n_samples):.1f}%)")
    print("\n  === WHY (km per stated reason) ===")
    for k, v in why_km.most_common():
        print(f"    {v/1000:7.1f} km  {k}")

    # THE CROSS-CHECK THAT GATES THE BUILD. The province publishes places to fish; if our layer
    # paints one red we are telling someone to leave a legal access point. That is the failure
    # this whole design exists to prevent, so it fails the build rather than printing a warning.
    bad = [at.get("SITE_NAME") for at, xy in fap
           if isinstance(xy, tuple) and xy[0] is not None
           and sa.classify(xy[1], xy[0])[0] == sacc.PRIVATE]
    print(f"\n  official fishing access points reading PRIVATE: {len(bad)}/{len(fap)}")
    if bad:
        for n in bad[:10]:
            print(f"    {n}")
        print("\n  ABORT: refusing to ship a layer that marks official fishing access as private.")
        return 1

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "type": "FeatureCollection",
        "meta": {
            "adr": "ADR-046",
            "tier": "T1",
            "source": ("Ontario LIO: patented parcels (TITLE_HOLDER_TYPE), Crown unpatented, "
                       "regulated provincial park / conservation reserve / municipal park, "
                       "Fishing Access Points; shoreline from data/watermask_frozen/"),
            "sample_step_m": a.step_m,
            "simplify_m": SIMPLIFY_M,
            "shoreline_km": round(total_km, 1),
            "coverage_km": {k: round(v / 1000.0, 1) for k, v in cover.items()},
            "means": ("cls=public: the land behind the shore is publicly owned. It does NOT mean "
                      "the bank is walkable or reachable — accessibility is not published in any "
                      "of these layers. cls=unknown: conflicting or absent tenure evidence. "
                      "cls=private: patented land in private title with nothing contradicting it."),
            "validation": ("ownership is measured (T1); ACCURACY IS UNVALIDATED against surveyed "
                           "points. Cross-check: 0 of the province's own fishing access points "
                           "classify as private. Not a legal determination — verify on the "
                           "ground before crossing anything."),
        },
        "features": feats,
    }, separators=(",", ":")))
    kb = out.stat().st_size / 1024
    print(f"\nwrote {out.relative_to(ROOT)}  ({len(feats)} segments, {kb:.0f} KB)")
    print("\n  NOTE: green means PUBLIC LAND, not 'you can get to the water here'. Accuracy is\n"
          "  unvalidated against surveyed points — the operator's GPS pins are the real test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
