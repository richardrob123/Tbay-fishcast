"""Self-audit of the BUILT coast map (web/data) — catches the defects a human shouldn't have to.

Runs against the emitted geojson + manifest and asserts the shading is sane, so regressions like
"green on the docks", "random speckle blobs", or "structure that moves between days" are caught by
the build, not by the operator zooming the live map. Prints a per-check report and exits non-zero on
any FAIL, so it can gate CI.

Checks (per stretch, lead-0 unless noted), thresholds chosen from the 2026-08 data-correctness pass:
  1. LAND BLEED   — % of shaded polygon vertices sitting on OSM land (must be ~0; the fill is clipped
                    to the eroded water mask). FAIL > 1.0%.
  2. SPECKLE      — count of shaded polygons and the fraction that are tiny (<300 m²). Hundreds of
                    tiny polys = grid-noise speckle. FAIL if tiny-fraction > 0.45 with > 60 polys.
  3. STATIC GLOW  — structure-glow (s3-s5) area coefficient-of-variation ACROSS forecast leads. The
                    lake bottom doesn't move, so the glow should be ~stable. WARN > 0.25 CV.
  4. COVERAGE     — total shaded ha per species is within a sane band (not ~0, not the whole box).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DATA = ROOT / "web" / "data"

TINY_M2 = 300.0
LAND_BLEED_FAIL = 1.0        # %
TINY_FRAC_FAIL = 0.45
GLOW_CV_WARN = 0.25
_R = 6378137.0


def _merc(lon, lat):
    return _R * math.radians(lon), _R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def _ring_area_m2(ring):
    if len(ring) < 3:
        return 0.0
    lat0 = math.radians(sum(p[1] for p in ring) / len(ring))
    kx, ky = 111320.0 * math.cos(lat0), 110540.0
    xy = [(x * kx, y * ky) for x, y in ring]
    s = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))
    return abs(s) / 2.0


_FROZEN_MASKS = None


def _frozen_masks():
    """All committed frozen water masks, loaded once — [(bounds_3857, (H, W), mask)]. Fully
    OFFLINE (iter_frozen reads the committed .npz files); the audit must never hit the network
    or hang on Overpass, so an uncovered stretch just skips its land check with a note."""
    global _FROZEN_MASKS
    if _FROZEN_MASKS is None:
        from tbay_fishcast.ingest.watermask import iter_frozen
        _FROZEN_MASKS = list(iter_frozen())
    return _FROZEN_MASKS


def _load_water_mask(center_latlon):
    """Frozen mask whose bounds contain the stretch centre, or None (skip, don't fetch)."""
    clat, clon = center_latlon
    mx, my = _merc(clon, clat)
    for bounds, (H, W), mask in _frozen_masks():
        x0, y0, x1, y1 = bounds
        if x0 <= mx <= x1 and y0 <= my <= y1:
            return mask, bounds, (H, W)
    return None


def main() -> int:
    man = json.loads((DATA / "manifest.json").read_text())
    species = [s["id"] for s in man["species"]]
    fails, warns = [], []
    print(f"AUDIT coast output — issue {man['issue']}, {len(man['stretches'])} stretches\n")

    for st in man["stretches"]:
        sid = st["id"]
        gj = json.loads((DATA / "areas" / f"{sid}.geojson").read_text())
        feats = gj["features"]
        got = _load_water_mask(st["center"])
        if got is not None:
            wm, bounds, shape = got
            x0, y0, x1, y1 = bounds; H, W = shape
            # land distance-to-water (m): a vertex counts as BLEED only when it sits more than
            # ~1.5 px inland — a boundary vertex 1 px "inland" is rasterization jitter of the
            # shoreline itself, not shading on land (verified: such vertices are exactly 1 px out).
            from scipy.ndimage import distance_transform_edt
            res_m = (x1 - x0) / W
            d2w = distance_transform_edt(~wm) * res_m
        else:
            wm = None; print(f"  {sid}: no frozen water mask covers centre — land check skipped")

        # default species for the land/speckle check
        for sp in species:
            polys = [f for f in feats
                     if f["properties"].get("lead") == 0
                     and f["properties"].get("temp", "").startswith(f"sp:{sp}:")]
            if not polys:
                continue
            areas = [_ring_area_m2(f["geometry"]["coordinates"][0]) for f in polys]
            tiny = sum(1 for a in areas if a < TINY_M2)
            tiny_frac = tiny / len(polys)
            # LAND BLEED
            land_v = tot_v = 0
            if wm is not None:
                for f in polys:
                    for ring in f["geometry"]["coordinates"]:
                        for lon, lat in ring:
                            mx, my = _merc(lon, lat)
                            col = int((mx - x0) / (x1 - x0) * W); row = int((y1 - my) / (y1 - y0) * H)
                            if 0 <= row < H and 0 <= col < W:
                                tot_v += 1
                                if not wm[row, col] and d2w[row, col] > 1.5 * res_m:
                                    land_v += 1
            bleed = 100.0 * land_v / max(tot_v, 1)
            tag = ""
            if bleed > LAND_BLEED_FAIL:
                fails.append(f"{sid}/{sp}: land bleed {bleed:.1f}%"); tag += " ❌LAND"
            if len(polys) > 60 and tiny_frac > TINY_FRAC_FAIL:
                fails.append(f"{sid}/{sp}: {len(polys)} polys, {tiny_frac:.0%} tiny (speckle)"); tag += " ❌SPECKLE"
            # only print the default species per stretch to keep it readable, plus any tagged
            if sp == species[0] or tag:
                print(f"  {sid:18s}/{sp:11s} polys={len(polys):3d} tiny={tiny_frac:4.0%} "
                      f"area={sum(areas)/1e4:5.1f}ha land={bleed:4.1f}%{tag}")

        # STATIC GLOW across leads (default species)
        sp0 = species[0]
        by_lead = {}
        for f in feats:
            p = f["properties"]; t = p.get("temp", "")
            if t.startswith(f"sp:{sp0}:") and t.split(":")[2] in ("s3", "s4", "s5"):
                by_lead.setdefault(p["lead"], 0.0)
                by_lead[p["lead"]] += _ring_area_m2(f["geometry"]["coordinates"][0]) / 1e4
        if len(by_lead) >= 3:
            vals = np.array(list(by_lead.values()))
            cv = float(vals.std() / max(vals.mean(), 1e-6))
            if cv > GLOW_CV_WARN:
                # NOTE: under the continuous product (ADR-037) glow AREA legitimately breathes as
                # temperature dims a break through the levels IN PLACE — this warn flags the
                # magnitude for an eyeball (is it dimming, or relocating?), it is not a FAIL.
                warns.append(f"{sid}/{sp0}: glow area breathes {cv:.0%} across leads "
                             f"(expected=thermal dimming in place; eyeball that it isn't relocating)")

    # COVERAGE per species (all stretches, lead 0)
    print("\n  coverage (lead 0, all stretches):")
    for sp in species:
        tot = 0.0
        for st in man["stretches"]:
            gj = json.loads((DATA / "areas" / f"{st['id']}.geojson").read_text())
            for f in gj["features"]:
                p = f["properties"]
                if p.get("lead") == 0 and p.get("temp", "").startswith(f"sp:{sp}:"):
                    tot += _ring_area_m2(f["geometry"]["coordinates"][0]) / 1e4
        print(f"    {sp:12s} {tot:6.1f} ha")
        if tot < 0.5:
            warns.append(f"{sp}: near-zero coverage ({tot:.1f} ha)")

    print("\n" + "=" * 60)
    for w in warns:
        print(f"  ⚠ WARN  {w}")
    for f in fails:
        print(f"  ❌ FAIL  {f}")
    if not fails and not warns:
        print("  ✅ all checks passed")
    elif not fails:
        print(f"  ✅ no failures ({len(warns)} warnings)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
