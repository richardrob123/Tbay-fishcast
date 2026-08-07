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
    cold_reachable, merc, reachable_area_features)
from tbay_fishcast.features.reachability import corrected_fields  # noqa: E402
from tbay_fishcast.ingest import glsea, lsofs_grid, nonna  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_native_columns, valid_time_from_dataset  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402

HALF_M = 4200.0
# ~4 m/px ground. ROOT CAUSE of "no cold water off the point" (user-caught): at the old
# ~7 m/px the coarse grid smeared a small point's shallow rocky shelf into the pixels just
# offshore, so that water read ~0-1 m deep (below the ~3 m isotherm) and failed the cold
# test — erasing the within-cast cold band right off the tip. At ~4 m/px the true ~3-4 m
# depth resolves and the point's cold water shows. Costs a slower build; correctness wins.
PX = 1400
LEADS = [("n", 6, 0)] + [("f", h, h) for h in (24, 48, 72, 96, 120)]
_R = 6378137.0

# Shore stretches along the NW Lake Superior shore. Overlapping ~8.4 km boxes; centers and
# exposure chosen from a live NONNA-coverage probe (a box with <3 % surveyed water or no LSOFS
# nodes is skipped gracefully at build time). Three groups:
#   * SW extension (Cloud Bay -> Chippewa): open cold shore south of the city toward, but not
#     reaching, Little Trout Bay — LTB itself is UNCHARTED in NONNA (0-1 % soundings; see
#     docs/COVERAGE.md) so it can't be depth-mapped yet.
#   * the developed city arc (Kam mouth -> MacKenzie/Silver): continuous, 3 infill boxes.
#   * NE detached cluster (Silver Islet / Sibley): the Sleeping Giant peninsula's open E shore,
#     ~40 km E across Black Bay (unsurveyed) so not continuous with the arc. Access/regs for
#     Sleeping Giant PP are the operator's to verify (ADR-007 handled off-system per the owner).
# exposure: qualitative fetch to W-quadrant upwelling wind — 'high' open shore (most reliable),
# 'med', 'low' sheltered harbour (the physics is least calibrated there).
STRETCHES = [
    ("little_trout_bay", "Little Trout Bay / Cloud Bay", 48.073, -89.451, "low"),
    ("chippewa", "Chippewa / Sturgeon River shore", 48.334, -89.212, "med"),
    ("kam_mission", "Kam mouth / Mission Island", 48.395, -89.240, "low"),
    ("mckellar_harbour", "McKellar / north harbour", 48.420, -89.212, "low"),
    ("marina_mcvicar", "Marina Park / McVicar", 48.442, -89.190, "med"),
    ("boulevard_current", "Boulevard / Current mouth", 48.468, -89.148, "med"),
    ("current_barepoint", "Current River / Trowbridge shore", 48.487, -89.095, "high"),
    ("shipyard_mackenzie", "Shipyard / MacKenzie approach", 48.505, -89.025, "med"),
    ("mackenzie_silver", "MacKenzie Point / Silver Harbour", 48.516, -88.962, "high"),
    ("silver_islet", "Silver Islet / Sleeping Giant tip", 48.322, -88.812, "high"),
]

# Stretches whose reachable-cold area is INDICATIVE ONLY — sparse survey + few LSOFS nodes +
# far from validation. Their overlay still draws (flagged) but they are excluded from the
# headline "ha reachable" total so a large, poorly-constrained number can't mislead. Curated,
# not a coverage threshold (the verified city arc sits at low whole-box coverage but maps well).
LOW_CONFIDENCE = {"little_trout_bay"}

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


# Thermal-habitat targets (°C). Optimal-thermal-habitat mapping for lake trout: the
# preferendum is ~10 °C (USGS), peak growth ~12.5 °C, comfortable band ~4–12 °C, chronic
# ceiling ~16 °C. So 12 = outer edge of comfort (the ceiling), 10 = the optimum/sweet
# spot, 8 = deep cold (marks the strongest upwelling). NOT "colder is better" — colder
# than the preferendum is deep-cold structure, not more optimal. TARGETS[0] is primary.
TARGETS = (12.0, 10.0, 8.0)


def _overlay(depth, dist, gx, gy, inbox, node_xy, cols, g_sst, bias, bounds_3857, res, prod,
             targets=TARGETS, species=()):
    """Return (area_features, reach_px, line_features) for one lead, or (None, 0, []).

    Two honest signals, no false precision:
    * AREA fills — the reachable region for EACH thermal target (12/10/8 °C), central bias,
      tagged temp=t12|t10|t8. Nested (colder needs deeper bottom ⇒ t8 ⊆ t10 ⊆ t12), shaded
      light→deep so the angler reads how cold the reachable water is, with 10–12 °C the
      laker optimum. Contouring the same corrected LSOFS field at three thresholds — no new
      data (this is standard optimal-thermal-habitat mapping).
    * The 12 °C FRONT — central + shallow/deep bias bookends (tagged band=central|possible|
      certain), so the position UNCERTAINTY (~100–300 m, > the cast band; AUDIT_ROUND3)
      reads as a ribbon, not a crisp line.
    """
    central, lo, hi, n = bias
    iso_pts = []
    tvals = {t: [] for t in targets}          # central isotherm depth per target
    f_sh, f_ce, f_de = [], [], []             # 12 °C shallow/central/deep for the front ribbon
    for i, nd in enumerate(inbox):
        col = cols.get(str(int(nd)))
        if col is None or len(col.depths_m) < 4:
            continue
        depths, raw = col.depths_m, col.temps_c
        surf_bias = (raw[0] - g_sst) if g_sst is not None else 0.0
        bm = thermocline.BiasModel(surf_bias, central, lo, hi, n_buoys=n)
        prim = thermocline.isotherm_band(depths, raw, bm, prod.target_c)
        if prim["central"] is None:
            continue
        iso_pts.append(node_xy[i])
        # 999 sentinel = no crossing (column never that cold) => below any bottom => unreachable
        for t in targets:
            zc = thermocline.isotherm_band(depths, raw, bm, t)["central"]
            tvals[t].append(zc if zc is not None else 999.0)
        f_ce.append(prim["central"])
        f_sh.append(prim["shallow"] if prim["shallow"] is not None else 999.0)
        f_de.append(prim["deep"] if prim["deep"] is not None else 999.0)
    if len(iso_pts) < 3:
        return None, 0.0, []
    iso_pts = np.array(iso_pts)
    # min_reach_px small + min_area small: the old speckle those filters guarded against
    # came from the no-data-apron heuristic, which the authoritative water mask removed —
    # so a genuine within-cast cold wedge off a NARROW point is no longer wrongly clipped
    # (user-caught: solid green missing right off Silver tip).
    kw = {"cast_m": prod.cast_m, "max_reach_depth_m": prod.max_reach_depth_m, "min_reach_px": 4}
    MIN_AREA = 120.0
    from shapely.geometry import Polygon as _ShPoly
    from shapely.ops import unary_union as _uunion

    def _shape(mask):
        """reachable_area_features -> one clean shapely (Multi)Polygon (lon/lat)."""
        polys = []
        for rings in reachable_area_features(mask, bounds_3857, res, min_area_m2=MIN_AREA):
            try:
                p = _ShPoly([(a, b) for a, b in rings[0]],
                            [[(a, b) for a, b in h] for h in rings[1:]])
                if not p.is_valid:
                    p = p.buffer(0)
                if not p.is_empty:
                    polys.append(p)
            except Exception:  # noqa: BLE001
                pass
        return _uunion(polys) if polys else None

    def _polys(geom):
        if geom is None or geom.is_empty:
            return []
        if not geom.is_valid:               # marching-squares can self-touch; buffer(0) repairs
            geom = geom.buffer(0)
        if geom.is_empty:
            return []
        if geom.geom_type == "Polygon":
            return [geom]
        return [g for g in geom.geoms if g.geom_type == "Polygon" and not g.is_empty]

    area_feats, lines = [], []
    reach_px_primary = 0.0

    def _emit(geom, tag):
        for gg in _polys(geom):
            rings = [[list(c) for c in gg.exterior.coords]]
            rings += [[list(c) for c in r.coords] for r in gg.interiors]
            area_feats.append((tag, rings))

    # PREFERRED-RANGE model (docs/FISH_BEHAVIOR_REVIEW.md): for each species, shade water whose
    # BOTTOM temperature is within [cold, warm] and reachable within a cast — a depth annulus
    # between two isotherms, not "colder = better". The isotherm-DEPTH field for each temperature
    # is computed once and reused across species. The UI outlines each range band; that outline is
    # the thermal FRONT / feeding edge (the prime mark), so no separate line geometry is emitted.
    iso_fields = {t: _iso_field(gx, gy, iso_pts, tvals[t]) for t in targets}
    water = np.isfinite(depth)
    within = water & (dist <= prod.cast_m) & (depth <= prod.max_reach_depth_m)
    default_id = None
    for sp in species:
        cold_c, warm_c = sp.range_c
        iso_warm = iso_fields.get(warm_c)      # shallow isotherm = warm edge of the range
        iso_cold = iso_fields.get(cold_c)      # deeper isotherm = cold edge
        if iso_warm is None or iso_cold is None:
            continue
        # bottom in [cold, warm]  <=>  iso_depth(warm) <= depth <= iso_depth(cold)
        rng = within & (depth >= iso_warm) & (depth <= iso_cold)
        _emit(_shape(rng), "sp:" + sp.id)
        if sp.default or (default_id is None and sp is species[0]):
            reach_px_primary = float(rng.sum())
            default_id = sp.id
    # keep a (possibly empty) lines file so the manifest reference stays valid
    return area_feats, reach_px_primary, lines


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
    for sid, name, clat, clon, exposure in STRETCHES:
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
                                             cfg.product, targets=cfg.band_temps,
                                             species=cfg.species)
            if area is None:
                continue
            days.append({"label": f"{vt:%a %b %-d}", "lead": lead,
                         "valid_utc": vt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "reach_ha": round(reach_px * res * res / 1e4, 1)})
            for temp, rings in area:
                coords = [[[round(lon, 6), round(lat, 6)] for lon, lat in ring] for ring in rings]
                area_feats.append({"type": "Feature",
                                   "properties": {"lead": lead, "temp": temp},
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
        # survey_cov = fraction of the box with real CHS soundings (informational). NOTE: a low
        # whole-box fraction does NOT by itself mean a bad map — the verified city stretches sit
        # at 0.16–0.19 yet map correctly, because the NEARSHORE (where casting happens) is
        # surveyed even when the offshore half of the box isn't. So low_confidence is set
        # EXPLICITLY (LOW_CONFIDENCE below) for stretches that are genuinely unreliable —
        # currently only Little Trout Bay: sparse soundings + few LSOFS nodes + a sheltered bay
        # far from any validation, which together produce a large but poorly-constrained cold
        # area (~138 ha vs 7–80 ha for verified stretches). Flagged, not hidden.
        stretches_out.append({"id": sid, "name": name, "corners": corners,
                              "center": [clat, clon], "res_m": round(res, 1),
                              "exposure": exposure, "anchor_day": anchor_day,
                              "survey_cov": round(float(patch.coverage_frac), 2),
                              "low_confidence": sid in LOW_CONFIDENCE, "days": days,
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
        "targets_c": list(cfg.band_temps),  # every isotherm shaded (union across species)
        "species": [{"id": sp.id, "name": sp.name, "range_c": list(sp.range_c),
                     "front_c": sp.front_c, "temp_cue": sp.temp_cue,
                     "default": sp.default, "note": sp.note} for sp in cfg.species],
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
