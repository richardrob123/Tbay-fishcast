"""Build the hosted whole-coast forecast site (GitHub Pages, MapLibre).

Precomputes, for a set of overlapping stretches spanning the Thunder Bay shore:
  * a land-aware shore-distance field over the CHS NONNA-10 bathymetry (with mid-water
    survey gaps filled so they can't fake a coastline), and
  * for the nowcast + each forecast day, a reachable-cold-water overlay (green) with
    the 12 C front (red), from the LSOFS isotherm field corrected the same way the
    per-spot product is.
Both overlays are VECTOR GeoJSON (green polygons + red polylines, tagged by lead), so
they stay crisp at any zoom; the page renders them over a live Esri World Imagery
basemap (fetched by the browser, so no imagery is embedded). It also emits a manifest
with per-station verdicts, the ensemble upwelling-wind probabilities, and the data age.
The page (web/index.html) is static; this script only (re)writes web/data/. A daily
Action runs it and deploys.

    python scripts/build_coast_site.py [issue YYYY-MM-DD]

No LLM (ADR-001).
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.interpolate import griddata

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import forecast_window as fw  # noqa: E402
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import bias_live, thermocline  # noqa: E402
from tbay_fishcast.features.forecast import summarize  # noqa: E402
from tbay_fishcast.features.overlay import (  # noqa: E402
    cold_front_features, cold_reachable, merc, reachable_area_features)
from tbay_fishcast.features.reachability import corrected_fields  # noqa: E402
from tbay_fishcast.ingest import glsea, lsofs_grid, nonna  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_native_columns, valid_time_from_dataset  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402

HALF_M = 4200.0
PX = 760
LEADS = [("n", 6, 0)] + [("f", h, h) for h in (24, 48, 72, 96, 120)]
_R = 6378137.0

# Shore stretches spanning the developed Thunder Bay arc (SW Kam mouth -> NE
# MacKenzie/Silver). Overlapping ~8.4 km boxes; a stretch with no NONNA water or
# no LSOFS nodes is skipped gracefully.
STRETCHES = [
    ("kam_mission", "Kam mouth / Mission Island", 48.395, -89.240),
    ("marina_mcvicar", "Marina Park / McVicar / Boulevard", 48.442, -89.190),
    ("current_barepoint", "Current River / Trowbridge shore", 48.487, -89.095),
    ("mackenzie_silver", "MacKenzie Point / Silver Harbour", 48.516, -88.962),
]

OUT = Path(__file__).resolve().parents[1] / "web" / "data"


def _merc_to_ll(x, y):
    lon = x / _R * 180.0 / math.pi
    lat = (2.0 * math.atan(math.exp(y / _R)) - math.pi / 2.0) * 180.0 / math.pi
    return lon, lat


def _resolve_issue(cfg, issue, max_back=3):
    """Most recent issue day whose t12z nowcast is actually posted.

    The daily Action can fire before today's t12z cycle reaches NODD (or on a slow
    posting day); rather than crash, walk back to the latest available cycle. The
    forecast hours of a slightly older cycle still cover the days ahead.
    """
    for k in range(max_back + 1):
        d = issue - timedelta(days=k)
        try:
            ds = _open_first(candidate_urls(LsofsFile(d, "t12z", "n", 6),
                             cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket, byterange=False))
            ds.close()
            if k:
                print(f"issue {issue} t12z not posted yet; using {d}")
            return d
        except Exception:  # noqa: BLE001
            continue
    return issue  # nothing found — let the downstream open fail loudly


def _node_columns_in_box(cfg, issue, clat, clon):
    """LSOFS nodes inside the box + a reader that returns native columns per lead."""
    f0 = LsofsFile(issue, "t12z", "n", 6)
    ds0 = _open_first(candidate_urls(f0, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket, byterange=False))
    grid = lsofs_grid.read_grid(ds0)
    ds0.close()
    lon = np.where(grid.lon > 180, grid.lon - 360, grid.lon)
    dlat = HALF_M / 111000.0
    dlon = HALF_M / (111000.0 * math.cos(math.radians(clat)))
    inbox = np.where((lon >= clon - dlon) & (lon <= clon + dlon)
                     & (grid.lat >= clat - dlat) & (grid.lat <= clat + dlat) & (grid.h >= 3.0))[0]
    node_xy = np.array([merc(grid.lat[n], lon[n]) for n in inbox]) if len(inbox) else np.empty((0, 2))
    return inbox, node_xy


def _iso_field(gx, gy, iso_pts, vals):
    iso = griddata(iso_pts, vals, (gx, gy), method="linear")
    nan = ~np.isfinite(iso)
    if nan.any():
        iso[nan] = griddata(iso_pts, vals, (gx[nan], gy[nan]), method="nearest")
    return iso


def _overlay(depth, dist, gx, gy, inbox, node_xy, cols, g_sst, bias, bounds_3857, res, prod):
    """Return (area_features, reach_px, line_features) for one lead, or (None, 0, []).

    AUDIT_ROUND3: the honest band (isotherm_band's shallow/central/deep) used to be
    computed and then discarded — a crisp central-only polygon overclaimed a boundary
    whose measured position error exceeds the 75 m cast band. Now every lead ships
    THREE reachability members tagged band=certain|central|possible:
      certain  = reachable even under the conservative (deep) end of the bias band
      central  = the best estimate (what was previously the only polygon)
      possible = reachable under the optimistic (shallow) end
    and the 12 C front for the central member plus faint bookend fronts.
    """
    central, lo, hi, n = bias
    iso_pts, v_sh, v_ce, v_de = [], [], [], []
    for i, nd in enumerate(inbox):
        col = cols.get(str(int(nd)))
        if col is None or len(col.depths_m) < 4:
            continue
        depths, raw = col.depths_m, col.temps_c
        surf_bias = (raw[0] - g_sst) if g_sst is not None else 0.0
        bm = thermocline.BiasModel(surf_bias, central, lo, hi, n_buoys=n)
        b = thermocline.isotherm_band(depths, raw, bm, prod.target_c)
        if b["central"] is not None:
            iso_pts.append(node_xy[i])
            v_ce.append(b["central"])
            # a member with no crossing (whole column warm) = unreachable there: carry
            # a below-bottom sentinel so the interpolated field stays defined
            v_sh.append(b["shallow"] if b["shallow"] is not None else 999.0)
            v_de.append(b["deep"] if b["deep"] is not None else 999.0)
    if len(iso_pts) < 3:
        return None, 0.0, []
    iso_pts = np.array(iso_pts)
    kw = {"cast_m": prod.cast_m, "max_reach_depth_m": prod.max_reach_depth_m}
    area_feats, lines = [], []
    reach_px_central = 0.0
    members = [("possible", v_sh), ("central", v_ce), ("certain", v_de)]
    for tag, vals in members:
        iso = _iso_field(gx, gy, iso_pts, vals)
        _cold, reachable = cold_reachable(depth, iso, dist, **kw)
        if tag == "central":
            reach_px_central = float(reachable.sum())
        for rings in reachable_area_features(reachable, bounds_3857, res):
            area_feats.append((tag, rings))
        # the 12 C front is the isotherm outcrop (depth == iso), drawn to TRACE THE
        # SHORE where cold water reaches the edge; central solid, bookends faint so the
        # user sees the measured position uncertainty as a ribbon, not a false line.
        for path in cold_front_features(depth, iso, dist, bounds_3857):
            lines.append((tag, path))
    return area_feats, reach_px_central, lines


def main(argv) -> int:
    # default to today (UTC): the daily Action runs after the t12z cycle posts. A
    # local run can pass an explicit issue (GLSEA lags ~1 day, so backfilling uses
    # yesterday). HEARTBEAT_ISSUE is honored too, to share the heartbeat's override.
    import os
    if len(argv) > 1:
        issue = date.fromisoformat(argv[1])
    elif os.environ.get("HEARTBEAT_ISSUE"):
        issue = date.fromisoformat(os.environ["HEARTBEAT_ISSUE"])
    else:
        issue = datetime.now(timezone.utc).date()
    cfg = load_config()
    issue = _resolve_issue(cfg, issue)     # fall back if today's t12z isn't posted yet
    OUT.mkdir(parents=True, exist_ok=True)

    central, lo, hi, _, n, bias_src = bias_live.pooled_or_prior(cfg, issue)
    bias = (central, lo, hi, n)
    if bias_src != "live":
        print("⚠ live buoy bias unavailable — frozen prior in use (degraded mode)")

    stretches_out = []
    for sid, name, clat, clon in STRETCHES:
        try:
            patch = nonna.fetch_patch(clat, clon, half_m=HALF_M, scale_px=PX)
        except Exception as e:  # noqa: BLE001
            print(f"skip {sid}: NONNA fetch failed ({str(e)[:50]})"); continue
        if patch.coverage_frac < 0.03:
            print(f"skip {sid}: water {patch.coverage_frac:.0%}"); continue
        res = nonna.ground_res_m(patch.res_mercator_m, clat)
        # the ONE corrected land/water pipeline (shared with the pin verdicts via
        # reachability.corrected_fields so map and pins cannot re-diverge): fill
        # mid-water survey seams, imagery shore mask, bridge narrow unsurveyed gaps,
        # mainland-only shore distance. Degrades to bathymetry-only shore on imagery
        # failure — loudly.
        depth, dist, degraded = corrected_fields(patch.depth, patch.bounds_3857, res)
        if degraded:
            print(f"    {sid}: basemap unavailable — bathymetry-only shore (degraded)")
        ny, nx = depth.shape
        x0, y0, x1, y1 = patch.bounds_3857
        # pixel CENTERS (not edge-inclusive linspace): keeps the interpolated iso field
        # on the same georeference as the depth raster and the contour mapping
        cx = x0 + (np.arange(nx) + 0.5) / nx * (x1 - x0)
        cy = y1 - (np.arange(ny) + 0.5) / ny * (y1 - y0)
        gx, gy = np.meshgrid(cx, cy)
        inbox, node_xy = _node_columns_in_box(cfg, issue, clat, clon)
        if len(inbox) == 0:
            print(f"skip {sid}: no LSOFS nodes"); continue
        # surface anchor: GLSEA lags ~1 day, so fall back to the most recent available
        # day rather than dropping it — without it the corrected isotherm collapses to
        # the surface (whole shelf reads "cold", no 12 C line).
        px = glsea.fetch_recent_sst(clat, clon, issue)
        g_sst = px.sst_c if px else None
        anchor_day = px.day if px else None
        if px and px.day != issue.isoformat():
            print(f"    {sid}: GLSEA anchor {px.sst_c:.1f}C from {px.day} (issue not yet posted)")

        # corner lon/lats for the MapLibre image source (TL, TR, BR, BL)
        tl = _merc_to_ll(x0, y1); tr = _merc_to_ll(x1, y1)
        br = _merc_to_ll(x1, y0); bl = _merc_to_ll(x0, y0)
        corners = [list(tl), list(tr), list(br), list(bl)]

        days = []
        line_feats = []                       # vector 12 C front, tagged by lead
        area_feats = []                       # vector reachable-cold polygons, tagged by lead
        for kind, fh, lead in LEADS:
            f = LsofsFile(issue, "t12z", kind, fh)
            try:
                ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket, byterange=False))
            except Exception:  # noqa: BLE001
                continue
            vt = valid_time_from_dataset(ds)
            cols = extract_native_columns(ds, {str(int(nd)): int(nd) for nd in inbox})
            ds.close()
            area, reach_px, lines = _overlay(depth, dist, gx, gy, inbox, node_xy,
                                             cols, g_sst, bias, patch.bounds_3857, res,
                                             cfg.product)
            if area is None:
                continue
            days.append({"label": f"{vt:%a %b %-d}", "lead": lead,
                         "valid_utc": vt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "reach_ha": round(reach_px * res * res / 1e4, 1)})
            for band, rings in area:
                coords = [[[round(lon, 6), round(lat, 6)] for lon, lat in ring] for ring in rings]
                area_feats.append({"type": "Feature",
                                   "properties": {"lead": lead, "band": band},
                                   "geometry": {"type": "Polygon", "coordinates": coords}})
            for band, path in lines:
                coords = [[round(lon, 6), round(lat, 6)] for lon, lat in path]
                line_feats.append({"type": "Feature",
                                   "properties": {"lead": lead, "band": band},
                                   "geometry": {"type": "LineString", "coordinates": coords}})
        if not days:
            print(f"skip {sid}: no frames"); continue
        (OUT / "lines").mkdir(exist_ok=True)
        (OUT / "areas").mkdir(exist_ok=True)
        (OUT / "lines" / f"{sid}.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": line_feats}))
        (OUT / "areas" / f"{sid}.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": area_feats}))
        stretches_out.append({"id": sid, "name": name, "corners": corners,
                              "center": [clat, clon], "res_m": round(res, 1),
                              "anchor_day": anchor_day, "days": days,
                              "area": f"data/areas/{sid}.geojson",
                              "lines": f"data/lines/{sid}.geojson"})
        print(f"  {sid}: {len(days)} days, {len(inbox)} nodes, res {res:.0f} m, "
              f"ha {days[0]['reach_ha']}..{max(d['reach_ha'] for d in days)}")

    if not stretches_out:
        print("no stretches produced"); return 1

    # per-station verdicts (reuse the per-spot forecast)
    stations = []
    for s in cfg.shore_stations:
        pts, wins, meta = fw.forecast_spot(cfg, s.lat, s.lon, s.name, s.lsofs_node, issue, (central, lo, hi, n))
        if not pts:
            continue
        traj = [{"label": f"{p.valid_time:%a}", "lead": p.lead_h,
                 "iso": round(p.isotherm_depth_m, 1) if p.isotherm_depth_m is not None else None,
                 "reach": bool(p.reachable), "certain": bool(p.reachable_certain),
                 "possible": bool(p.reachable_possible)} for p in pts]
        verdict = summarize(pts, wins)
        if pts[0].reachable and not pts[0].reachable_certain:
            verdict += " (edge of band — uncertain)"
        stations.append({"id": s.id, "name": s.name, "lat": s.lat, "lon": s.lon,
                         "now": bool(pts[0].reachable),
                         "now_certain": bool(pts[0].reachable_certain),
                         "verdict": verdict,
                         "surf_sst": (round(meta["surf_sst"], 1)
                                      if meta.get("surf_sst") is not None else None),
                         "traj": traj})

    # ensemble upwelling-wind probability per day
    wind = []
    try:
        from tbay_fishcast.features import upwelling
        from tbay_fishcast.ingest import wind_forecast
        w = wind_forecast.fetch_ensemble_wind(forecast_days=5)
        prob = upwelling.upwelling_probability(w["time"], w["members"])
        wind = [{"day": f"{d:%a}", "p": round(prob[d], 2)} for d in sorted(prob)]
    except Exception as e:  # noqa: BLE001
        print(f"wind ensemble unavailable: {str(e)[:50]}")

    now = datetime.now(timezone.utc)
    age_h = (now - datetime(issue.year, issue.month, issue.day, 12, tzinfo=timezone.utc)).total_seconds() / 3600
    manifest = {
        "issue": issue.isoformat(), "issued_utc": f"{issue}T12:00:00Z",
        "built_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        # age_h/stale are the BUILD-TIME values; the page recomputes age client-side
        # from issued_utc so a dead build can't display a frozen age (AUDIT_ROUND3)
        "age_h": round(age_h, 1), "stale": age_h > 18,
        "target_c": cfg.product.target_c, "cast_m": cfg.product.cast_m,
        "max_reach_depth_m": cfg.product.max_reach_depth_m,
        "n_leads": len(LEADS),
        "bias": {"central": round(central, 1), "lo": round(lo, 1), "hi": round(hi, 1),
                 "n": n, "source": bias_src},
        "stretches": stretches_out, "stations": stations, "wind": wind,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {OUT/'manifest.json'} — {len(stretches_out)} stretches, {len(stations)} stations, "
          f"{len(wind)} wind-days, age {age_h:.0f} h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
