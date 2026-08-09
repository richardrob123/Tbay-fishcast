"""Build the river seam + hydraulics overlay the map consumes (ADR-045).

Combines every measured layer into one artifact:
  * 1 m lidar water surface     -> slope, width (T1 measured)
  * live ECCC discharge         -> depth, velocity, Froude (T3 derived, banded)
  * planform geometry           -> bend seams on the correct bank (T1 measured)
  * width gradient              -> constriction / expansion seams (T1 measured)
  * velocity gradient           -> longitudinal seams (T3, only where discharge is usable)
  * step geometry vs leap ability -> species-specific barriers and upstream limits

Writes web/data/rivers.geojson and data/calib/river_seam_calib.json.

TWO THINGS THIS DELIBERATELY DOES NOT DO. It does not draw a diffuse heat cloud — the features
are discrete and reach-scale (~100 m), and a soft glow would imply a continuous well-resolved
field we do not have. And it does not rank rivers for lake trout, which barely enters them.

    python scripts/build_river_seams.py
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from tbay_fishcast.features import hydraulics as hy      # noqa: E402
from tbay_fishcast.features import river_profile as rp   # noqa: E402
from tbay_fishcast.ingest import hrdem, hydat            # noqa: E402
import build_river_reaches as B                          # noqa: E402

GEOJSON = ROOT / "web" / "data" / "rivers.geojson"
CALIB = ROOT / "data" / "calib" / "river_seam_calib.json"
STEP_M = 20.0
SPECIES = ("steelhead", "salmon", "brook_trout", "lake_trout")


def _normal(pts, i):
    a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
    mlat = 111320.0
    mlon = 111320.0 * math.cos(math.radians(a[0]))
    dx, dy = (b[1] - a[1]) * mlon, (b[0] - a[0]) * mlat
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return 0.0, 0.0
    return (dx / n) / mlat, (-dy / n) / mlon


def _live_q():
    q = {}
    for rid, meta in hydat.GAUGE_META.items():
        try:
            rows = hydat.fetch_recent_discharge(meta["station"])
            v = [x for _t, x in rows if x is not None]
            q[rid] = sorted(v)[len(v) // 2] if v else None
        except Exception as e:  # noqa: BLE001
            print(f"  gauge {rid} ({meta['station']}) unavailable: {str(e)[:50]}")
            q[rid] = None
    return q


def profile(r):
    line = rp.chain_ways(B.fetch_centreline(r), mouth=r.get("mouth"))
    pts, dist = rp.densify(line, STEP_M)
    pts = [p for p in pts if hrdem.in_footprint(*p)]
    dist = dist[:len(pts)]
    if len(pts) < 20:
        return None
    z, _ = hrdem.sample_water_surface(pts)
    zf = list(z)
    # Fill gaps BOTH ways. Looking only backwards leaves leading Nones unfilled, which dropped
    # McIntyre entirely — it has 625 nodata samples where the channel runs under cover, several of
    # them at the head of the profile.
    for i in range(len(zf)):
        if zf[i] is None:
            zf[i] = next((zf[j] for j in range(i - 1, -1, -1) if zf[j] is not None), None)
    for i in range(len(zf) - 1, -1, -1):
        if zf[i] is None:
            zf[i] = next((zf[j] for j in range(i + 1, len(zf)) if zf[j] is not None), None)
    if any(v is None for v in zf):
        return None
    if zf[0] < zf[-1]:
        pts, zf = pts[::-1], zf[::-1]
        dist = [dist[-1] - d for d in dist[::-1]]
    ws = hrdem.measure_widths(pts, stride=5)
    w_ref = hrdem.reach_width(ws, dist, smooth_m=1000.0)
    return {"pts": pts, "dist": dist, "z": zf, "w_ref": w_ref}


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(GEOJSON))
    a = ap.parse_args(argv)

    Q = _live_q()
    print("live discharge:", {k: (round(v, 3) if v else None) for k, v in Q.items()})

    P, all_dv, all_rw, all_dw = {}, [], [], []
    for r in B.RIVERS:
        rid = r["id"]
        pr = profile(r)
        if pr is None:
            print(f"  {r['name']:22s} profile unusable — skipped")
            continue
        meta = hydat.GAUGE_META.get(rid, {})
        q, qref = Q.get(rid), meta.get("q_ref_cms")
        wm = [x for x in pr["w_ref"] if x]
        med_ref = sorted(wm)[len(wm) // 2] if wm else None
        # WIDTH AT TODAY'S FLOW, not at the lidar date — see hydraulics.width_at_flow
        w_now = [hy.width_at_flow(x, q, qref) if (x and q and qref) else None
                 for x in pr["w_ref"]]
        med_now = None
        wn = [x for x in w_now if x]
        if wn:
            med_now = sorted(wn)[len(wn) // 2]
        # THE WINDOW MUST SATISFY TWO CONSTRAINTS AT ONCE. A few channel widths is the right
        # PHYSICAL scale, but on a narrow river that lands below the sampling: Neebing at 10 m
        # wide gives a 30 m window spanning 2 stations at 20 m spacing, and a least-squares slope
        # needs 3. Every slope came back NaN and the river silently produced no hydraulics at all.
        win = max(3.0 * (med_ref or 25.0), 5.0 * STEP_M)
        slopes = rp.reach_slope(pr["dist"], pr["z"], win)
        fin = [s for s in slopes if s is not None and math.isfinite(s) and s > 0]
        mean_slope = (sum(fin) / len(fin)) if fin else None
        back = [hy.is_backwater(zz, hrdem.LAKE_SUPERIOR_DATUM_M) for zz in pr["z"]]
        states = [hy.solve(q, wi, s, z_m=zz, lake_datum_m=hrdem.LAKE_SUPERIOR_DATUM_M,
                           mean_slope_m_km=mean_slope)
                  for s, wi, zz in zip(slopes, w_now, pr["z"])]
        vel = [st.velocity_ms for st in states]
        dv = hy.velocity_gradient(vel, pr["dist"])
        dw = rp.width_gradient(pr["w_ref"], pr["dist"])
        bends = rp.bend_seams(pr["pts"], pr["w_ref"], suppress=back, dists=pr["dist"])
        P[rid] = {**pr, "r": r, "q": q, "q_ref": qref, "w_now": w_now, "slopes": slopes,
                  "states": states, "dv": dv, "dw": dw, "bends": bends, "back": back,
                  "med_ref": med_ref, "med_now": med_now, "mean_slope": mean_slope}
        all_dv += [x for x in dv if x is not None and math.isfinite(x)]
        all_rw += [b["rw"] for b in bends]
        all_dw += [x for x in dw if x is not None and math.isfinite(x)]
        nfr = sum(1 for st in states if st.froude is not None)
        print(f"  {r['name']:22s} Q={str(q):>7s}  w {med_ref:.0f}->{('%.0f' % med_now) if med_now else 'n/a':>4s} m"
              f"  {len(bends):3d} bends  hydraulics on {nfr}/{len(states)} stations")

    if not P:
        print("nothing built")
        return 1

    # MEASURED RAMPS — ADR-039 discipline, pooled regionally so a band means "strong for here".
    ramps = {}
    # BEND STRENGTH IS 1/(R/w), NOT R/w. A TIGHTER bend scours harder, so strength rises as the
    # ratio falls — and seam_band takes abs(), so simply negating rw was silently undone and the
    # ramp came out inverted: wide lazy bends outranked tight ones, with 93 of 124 bends landing
    # in band 0. Inverting to a dimensionless bend intensity fixes the ordering at the source.
    for name, vals in (("bend", [1.0 / v for v in all_rw if v > 0]),
                       ("constriction", all_dw), ("velocity_gradient", all_dv)):
        try:
            ramps[name] = hy.seam_ramp_bands(vals)
            print(f"  ramp {name:18s} n={ramps[name]['n']:6d}  "
                  f"p75={ramps[name]['edges'][0]:.4g} .. p99={ramps[name]['edges'][-1]:.4g}")
        except ValueError as e:
            print(f"  ramp {name:18s} NOT BUILT ({e})")

    feats = []
    for rid, p in P.items():
        pts, w_ref, dist = p["pts"], p["w_ref"], p["dist"]
        # ---- bend seams, offset to the outer bank, graded by the measured ramp
        for b in p["bends"]:
            side = 1 if b["side"] == "right" else -1
            coords = []
            for k in range(b["i0"], b["i1"] + 1):
                wi = w_ref[k] or 0.0
                dlat, dlon = _normal(pts, k)
                coords.append([round(pts[k][1] + dlon * side * wi / 2, 6),
                               round(pts[k][0] + dlat * side * wi / 2, 6)])
            if len(coords) < 2:
                continue
            band = (hy.seam_band(1.0 / b["rw"], ramps["bend"])
                    if "bend" in ramps and b["rw"] > 0 else 0)
            feats.append({"type": "Feature",
                          "geometry": {"type": "LineString", "coordinates": coords},
                          "properties": {"river": rid, "kind": "bend", "band": band,
                                         "side": b["side"], "rw": round(b["rw"], 2),
                                         "radius_m": round(b["radius_m"]),
                                         "len_m": b.get("extent_m"),
                                         "tier": "T1 measured (lidar planform + width)"}})
        # ---- longitudinal + constriction seams, on the centreline
        i = 0
        while i < len(pts):
            dvb = (hy.seam_band(p["dv"][i], ramps["velocity_gradient"])
                   if "velocity_gradient" in ramps and i < len(p["dv"]) else 0)
            dwb = (hy.seam_band(p["dw"][i], ramps["constriction"])
                   if "constriction" in ramps and i < len(p["dw"]) else 0)
            kind = None
            band = 0
            if dvb >= 9:                       # top of the measured ramp only
                kind = "decel" if (p["dv"][i] or 0) < 0 else "accel"
                band = dvb
            elif dwb >= 9:
                kind = "constriction" if (p["dw"][i] or 0) < 0 else "expansion"
                band = dwb
            if kind is None or p["back"][i]:
                i += 1
                continue
            j = i
            while j + 1 < len(pts) and not p["back"][j + 1]:
                nb = (hy.seam_band(p["dv"][j + 1], ramps["velocity_gradient"])
                      if "velocity_gradient" in ramps and j + 1 < len(p["dv"]) else 0)
                nw = (hy.seam_band(p["dw"][j + 1], ramps["constriction"])
                      if "constriction" in ramps and j + 1 < len(p["dw"]) else 0)
                if max(nb, nw) < 9:
                    break
                j += 1
            coords = [[round(q[1], 6), round(q[0], 6)] for q in pts[i:j + 1]]
            if len(coords) >= 2:
                st = p["states"][i]
                feats.append({"type": "Feature",
                              "geometry": {"type": "LineString", "coordinates": coords},
                              "properties": {
                                  "river": rid, "kind": kind, "band": band,
                                  "v_ms": round(st.velocity_ms, 2) if st.velocity_ms else None,
                                  "depth_m": round(st.depth_m, 2) if st.depth_m else None,
                                  "froude": round(st.froude, 2) if st.froude else None,
                                  "regime": st.state,
                                  "tier": ("T1 measured (lidar width)" if kind in
                                           ("constriction", "expansion")
                                           else "T3 derived (Manning velocity)")}})
            i = j + 1
        # ---- barriers + species upstream limits, from absolute step geometry
        reaches = rp.to_reaches(dist, p["z"], pts,
                                ["riffle"] * len(pts), 0.0)  # placeholder classes; geometry only
        del reaches

    summary = {}
    for rid, p in P.items():
        summary[rid] = {"name": p["r"]["name"], "q_cms": p["q"], "q_ref_cms": p["q_ref"],
                        "width_ref_m": p["med_ref"], "width_now_m": p["med_now"],
                        "mean_slope_m_km": round(p["mean_slope"], 3) if p["mean_slope"] else None,
                        "bends": len(p["bends"]),
                        "backwater_frac": round(sum(p["back"]) / len(p["back"]), 3),
                        "hydraulics_stations": sum(1 for s in p["states"] if s.froude is not None)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    CALIB.parent.mkdir(parents=True, exist_ok=True)
    CALIB.write_text(json.dumps({
        "ramps": ramps, "rivers": summary,
        "species_seam": hy.SPECIES_SEAM,
        "source": {"lidar": hrdem.STAC_ITEM, "acquired": hrdem.ACQUIRED,
                   "gauges": {k: v["station"] for k, v in hydat.GAUGE_META.items()},
                   "width_q_exponent": hy.WIDTH_Q_EXPONENT},
        "method": ("bend seams are T1 lidar planform offset to the outer bank by half the measured "
                   "width; velocity seams are T3 Manning derived and only exist where discharge is "
                   "usable; ramps are measured regional percentiles (ADR-039 discipline)"),
    }, indent=1) + "\n")
    print(f"\nwrote {len(feats)} seam features -> {a.out}")
    print(f"wrote {CALIB.relative_to(ROOT)}")
    from collections import Counter
    print("  by kind:", dict(Counter(f['properties']['kind'] for f in feats)))
    print("  by band:", dict(sorted(Counter(f['properties']['band'] for f in feats).items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
