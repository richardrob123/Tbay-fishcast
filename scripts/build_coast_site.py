"""Build the hosted whole-coast forecast site (GitHub Pages, MapLibre).

Precomputes, for a set of overlapping stretches spanning the Thunder Bay shore:
  * a land-aware shore-distance field over the CHS NONNA-10 bathymetry (with mid-water
    survey gaps filled so they can't fake a coastline), and
  * for the nowcast + each forecast day, graded per-species suitability AREA fills
    (fair/good/prime) from the LSOFS isotherm field corrected the same way the per-spot
    product is.
These overlays are VECTOR GeoJSON polygons, tagged by lead and species tier, so they stay
crisp at any zoom; the page renders them over a live Esri World Imagery basemap (fetched by
the browser, so no imagery is embedded). It also emits a manifest
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
from tbay_fishcast.features.overlay import merc, reachable_area_features  # noqa: E402
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

# River-mouth structure markers (docs/FISH_BEHAVIOR_REVIEW.md: river mouths, plumes and
# forage rival or exceed TEMPERATURE for actual shore catch — decisive for the weak-cue
# species, salmon/steelhead, which stage on tributary plumes). Points, not scored layers:
# the map draws them so the angler weighs structure alongside the thermal bands. Positions
# are map-derived mouth locations (tier T4, field_verify: replace with field GPS before any
# access claim). `species` = the species each mouth most helps (drives the popup copy).
RIVER_MOUTHS = [
    # Coords are the LAKE-ENTRY points: where the OSM waterway CENTERLINE crosses the Lake
    # Superior water-polygon BOUNDARY (computed 2026-08-08). NOT the downstream way end — OSM
    # continues rivers as flowlines INSIDE the lake polygon, so "last vertex" sat km out in the
    # harbour (the Kam pin was 3.4 km up its flowline by the Mission Is. lagoons; the floodway
    # pin 1.9 km off — both user-caught). Still coord_tier T4 (field-verify the plume edge).
    {"id": "kam", "name": "Kaministiquia R. mouth", "lat": 48.3661, "lon": -89.2525,
     "note": "Delta channels + warm plume — prime chinook/coho staging (late summer–fall). "
             "Temperature is a minor cue here; fish the plume edge and current seams.",
     "species": ["salmon", "steelhead"]},
    {"id": "current", "name": "Current R. mouth", "lat": 48.4510, "lon": -89.1842,
     "note": "North-shore plume below Boulevard Lake — steelhead/salmon staging; lake trout "
             "hold where the plume meets the cold shelf.",
     "species": ["steelhead", "salmon", "lake_trout"]},
    {"id": "neebing", "name": "Neebing–McIntyre Floodway mouth", "lat": 48.3995, "lon": -89.2191,
     "note": "Floodway outlet into the harbour — plume + forage; secondary structure to the Kam "
             "delta.",
     "species": ["salmon", "steelhead"]},
]

OUT = Path(__file__).resolve().parents[1] / "web" / "data"
FAVOR_CALIB = Path(__file__).resolve().parents[1] / "data" / "calib" / "upwelling_favorability.json"

# Observation-ledger species (as researched sources name them) -> the map's modelled species.
# The three Pacific salmon collapse to the one 'salmon' layer the map draws (ADR-042).
LEDGER_SPECIES_TO_APP = {
    "chinook": "salmon", "coho": "salmon", "pink": "salmon",
    "steelhead": "steelhead", "lake_trout": "lake_trout", "brook_trout": "brook_trout",
}

# Depth-contour CHART VIEW (ADR-041): static isobath polylines per stretch, drawn from the SAME
# native-smoothed CHS NONNA field the structure marks are computed on — one bathymetry, two views.
# Nearshore-weighted ladder: fine where casting happens, coarser past reach (max reach is 22 m).
CONTOUR_DEPTHS_M = (2, 4, 6, 8, 10, 12, 15, 20, 25)


def _depth_contours(depth, res, bounds_3857):
    """Isobath LineString features (lon/lat) on the native-smoothed depth, PLUS the water polygon
    of the box (property w=1). The chart view paints ONLY those water polygons dark and leaves the
    satellite LAND visible (operator request 2026-08-08) — the frozen OSM water mask is the same
    authority the land-clip uses, so chart water and painted water can never disagree. contourpy
    handles the NaN mask, so lines END at the survey/data edge — no strokes along the box border."""
    from contourpy import contour_generator
    from shapely.geometry import LineString
    from tbay_fishcast.features import suitability as _su
    ds = _su.native_smoothed(depth, res)
    x0, y0, x1, y1 = bounds_3857
    ny, nx = ds.shape
    cx = x0 + (np.arange(nx) + 0.5) / nx * (x1 - x0)
    cy = y0 + (np.arange(ny) + 0.5) / ny * (y1 - y0)          # ascending for contourpy
    gen = contour_generator(x=cx, y=cy, z=np.ma.masked_invalid(ds[::-1]))
    feats = []
    for d in CONTOUR_DEPTHS_M:
        for line in gen.lines(float(d)):
            if len(line) < 8:
                continue
            ls = LineString(line).simplify(res * 1.5)          # ~6 m: below the 10 m native cell
            if ls.length < 60:                                 # sub-cast-length squiggles are noise
                continue
            coords = [[round(v, 6) for v in _merc_to_ll(x, y)] for x, y in ls.coords]
            feats.append({"type": "Feature", "properties": {"d": d},
                          "geometry": {"type": "LineString", "coordinates": coords}})
    # WATER POLYGONS for the chart ground (frozen OSM mask; graceful skip if absent)
    try:
        from tbay_fishcast.features.overlay import reachable_area_features
        from tbay_fishcast.ingest.watermask import water_mask
        wm = water_mask(bounds_3857, depth.shape)
        for rings in reachable_area_features(wm, bounds_3857, res, min_area_m2=5000.0,
                                             smooth_iters=1):
            coords = [[[round(v, 6) for v in ring_pt] for ring_pt in ring] for ring in rings]
            feats.append({"type": "Feature", "properties": {"w": 1},
                          "geometry": {"type": "Polygon", "coordinates": coords}})
    except Exception as e:  # noqa: BLE001 - chart ground degrades to lines-on-satellite
        print(f"    chart water polygons unavailable ({str(e)[:50]})")
    return feats


def _nearshore_delta_by_class():
    """Measured shore-minus-GLSEA surface delta PER EXPOSURE CLASS (°C, n). Delegates to the
    SHARED features.nearshore helper so the map and the station-pin path apply identical
    corrections (validation finding #1 — they had diverged). Exposed open coast ≈ -0.4 (reads
    like GLSEA); sheltered ≈ +1.6 (trapped warm lens). See that module for provenance."""
    from tbay_fishcast.features.nearshore import nearshore_delta_by_class
    return nearshore_delta_by_class()


FORECAST_ERR = Path(__file__).resolve().parents[1] / "data" / "calib" / "forecast_lead_error.json"


def _load_forecast_error():
    """MEASURED thermal-forecast error (isotherm-depth MAE) from the accumulated gate, or None.

    scripts/analyze_forecast_error.py writes the pooled + per-lead MAE and a lead-trend verdict
    from data/forecast_gate_log.csv. The map uses this to state the thermal-field position
    uncertainty HONESTLY — a measured ±metres, not a fabricated growing-with-lead fade. Returns
    the dict or None if the analysis hasn't been run (then the UI shows no numeric band)."""
    try:
        d = json.loads(FORECAST_ERR.read_text())
    except (OSError, ValueError):
        return None
    # validate shape: this file is machine-rewritten daily; a partial/interrupted write must
    # degrade to "no numeric band", not KeyError later in main() (stress-test 2026-08 M2)
    if not isinstance(d, dict) or not d.get("n"):
        return None
    if not all(k in d for k in ("pooled_mae_m", "lead_trend_detected", "n_effective_chains")):
        print("⚠ forecast_lead_error.json incomplete — ignoring (no numeric band)")
        return None
    return d


def _load_favor_calib():
    """Fitted upwelling-favorability params if a real calibration exists, else None (prior).

    scripts/calibrate_upwelling.py writes {calibrated: bool, s50_kn, width_kn, ...}. When it
    could not fit a discriminating response from observed data it writes calibrated:false — we
    then fall back to the physics prior in suitability.py, and say so (CLAUDE rule 5)."""
    try:
        d = json.loads(FAVOR_CALIB.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    if not d.get("calibrated"):
        print(f"upwelling-favorability: prior in use (calib {d.get('reason','absent')})")
        return None
    if not all(k in d for k in ("s50_kn", "width_kn")):
        # a calibrated:true file missing its params would KeyError deep in the wind block and
        # silently drop the WHOLE wind/favorability section, blaming the ensemble (stress-test L1)
        print("⚠ upwelling_favorability.json calibrated but missing s50_kn/width_kn — prior in use")
        return None
    return d


# Wedderburn upwelling bar (low end of the ~12-17 kt range) for the ensemble forecast tail. Named
# so it isn't a bare literal that can drift from the value the manifest reports (validation #9). Now
# EQUAL to the observed bar (up.OBSERVED_THRESHOLD_KN): the old lower airport bar assumed CYQT reads
# ~3 kn low, which the buoy measurement refuted (see upwelling_phase.py). Both segments use 13 kn.
FORECAST_THRESHOLD_KN = 13.0


def _build_phase(lead_valid, ens):
    """Regional upwelling-PHASE timeline for the manifest, or None if no wind is reachable.

    The nowcast (lead 0) is classified from OBSERVED wind (CYQT METAR) — the operator's
    requirement that the "past couple days" conditioning day 0 be actual data, not our own
    forecast fed back in. Forecast leads are classified from the observed tail STITCHED to
    the ensemble control member, so a blow that begins in the observed past and runs into
    the forecast is seen whole. Relaxation after a west-quadrant blow is the prime window
    (docs/FISH_BEHAVIOR_REVIEW.md). Pure classification over already-fetched series (ADR-001).
    """
    from tbay_fishcast.features import upwelling_phase as up
    from tbay_fishcast.ingest import metar

    obs = None
    try:
        obs = metar.fetch_recent_wind(hours=72)
    except Exception as e:  # noqa: BLE001
        print(f"observed wind (METAR) unavailable: {str(e)[:50]}")

    o_t = [o.time for o in obs] if obs else []
    o_d = [o.dir_deg for o in obs] if obs else []
    o_s = [o.speed_kn for o in obs] if obs else []
    cutoff = o_t[-1] if o_t else None

    st_t, st_d, st_s = list(o_t), list(o_d), list(o_s)
    if ens and ens.get("members"):
        ctrl = ens["members"][0]
        for i, ts in enumerate(ens.get("time", [])):
            t = up._utc(ts)
            if cutoff is None or t > cutoff:
                st_t.append(t)
                st_d.append(ctrl["dir_deg"][i])
                st_s.append(ctrl["speed_kn"][i])
    if not st_t:
        return None

    # Both segments use the same 13 kn Wedderburn bar: the buoy-vs-airport gate (wind_gate_log.csv)
    # showed CYQT reads within ~1 kn of over-lake wind in the W quadrant, refuting the old "airport
    # reads ~3 kn low" assumption that had justified a separate 10 kn day-0 bar (see upwelling_phase.py).
    obs_runs = up.extract_runs(o_t, o_d, o_s, threshold_kn=up.OBSERVED_THRESHOLD_KN) if o_t else []
    all_runs = up.extract_runs(st_t, st_d, st_s, threshold_kn=FORECAST_THRESHOLD_KN)

    def _wind_at(valid_iso):
        """Representative wind around a frame's valid instant from the stitched series:
        circular-mean direction + mean/max speed over ±6 h. Display data, no scoring."""
        import math as _m
        t0 = up._utc(valid_iso)
        sel = [(d, v) for t, d, v in zip(st_t, st_d, st_s)
               if abs((t - t0).total_seconds()) <= 6 * 3600 and v is not None and d is not None]
        if not sel:
            return None
        xs = sum(_m.sin(_m.radians(d)) for d, _ in sel) / len(sel)
        ys = sum(_m.cos(_m.radians(d)) for d, _ in sel) / len(sel)
        mean_dir = _m.degrees(_m.atan2(xs, ys)) % 360.0
        spd = [v for _, v in sel]
        return {"dir_deg": round(mean_dir), "mean_kn": round(sum(spd) / len(spd)),
                "max_kn": round(max(spd))}

    by_lead = {}
    daily_wind = {}
    for lead, valid in sorted(lead_valid.items()):
        w = _wind_at(valid)
        if w:
            daily_wind[str(lead)] = w
        if lead == 0 and obs:
            ph = up.classify_at(obs_runs, valid, source=f"observed:{metar.CYQT}",
                                confidence="high" if len(o_t) >= 24 else "med")
        else:
            src = "forecast:gfs025" if not (lead == 0 and obs) else f"observed:{metar.CYQT}"
            ph = up.classify_at(all_runs, valid, source=src,
                                confidence="med" if lead <= 48 else "low")
        by_lead[str(lead)] = ph.as_dict()

    return {"now": by_lead.get("0"), "by_lead": by_lead, "daily_wind": daily_wind,
            "obs_station": metar.CYQT if obs else None,
            "obs_available": bool(obs),
            # BOTH bars are reported honestly: day-0 observed uses the airport bar, the forecast
            # tail uses the higher over-lake bar. Reporting only the 10 kn understated the tail.
            "threshold_obs_kn": up.OBSERVED_THRESHOLD_KN,
            "threshold_forecast_kn": FORECAST_THRESHOLD_KN,
            "sector_deg": list(up.FAVORABLE_SECTOR)}


def _merc_to_ll(x, y):
    lon = x / _R * 180.0 / math.pi
    lat = (2.0 * math.atan(math.exp(y / _R)) - math.pi / 2.0) * 180.0 / math.pi
    return lon, lat



# LSOFS posts FOUR cycles a day (verified on NODD: t00z/t06z/t12z/t18z, ~258 objects each).
# The build used to read t12z only, so a map built at 02:40 UTC showed the PREVIOUS day's noon
# cycle even though that day's t00z was already on the bucket — a phone opened at breakfast read
# ~25 h stale every single morning (reported 2026-08-09). Newest-first ordering fixes that: take
# the freshest cycle actually posted, whatever hour it is.
_CYCLES_NEWEST_FIRST = ("t18z", "t12z", "t06z", "t00z")
_CYCLE_HOUR = {"t00z": 0, "t06z": 6, "t12z": 12, "t18z": 18}


def _resolve_issue(cfg, issue, max_back=3):
    """Newest (day, cycle) whose nowcast is actually posted, searching back from `issue`.

    Walks days newest-first and, within each day, cycles newest-first — but never picks a cycle
    dated in the FUTURE relative to now (a cycle hour that hasn't occurred yet cannot be posted,
    and asking for it just burns four failed opens every build).

    The Action can also fire before any of today's cycles reach NODD, so the walk-back across days
    is kept: the forecast hours of a slightly older cycle still cover the days ahead.
    """
    now = datetime.now(timezone.utc)
    for k in range(max_back + 1):
        d = issue - timedelta(days=k)
        for cyc in _CYCLES_NEWEST_FIRST:
            # skip cycles whose nominal hour is still in the future
            if datetime(d.year, d.month, d.day, _CYCLE_HOUR[cyc], tzinfo=timezone.utc) > now:
                continue
            try:
                ds = _open_first(candidate_urls(LsofsFile(d, cyc, "n", 6),
                                 cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket, byterange=False))
                ds.close()
                print(f"LSOFS cycle: {d} {cyc}"
                      + (f" (issue {issue} newer cycles not posted yet)" if (k or cyc != "t18z") else ""))
                return d, cyc
            except Exception:  # noqa: BLE001
                continue
    return issue, "t12z"  # nothing found — let the downstream open fail loudly


def _node_columns_in_box(cfg, issue, clat, clon, cycle="t12z"):
    """LSOFS nodes inside the box + a reader that returns native columns per lead."""
    f0 = LsofsFile(issue, cycle, "n", 6)
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
    """Grid the per-node isotherm DEPTHS onto (gx, gy). Nodes whose column never reaches the
    target carry the 999 sentinel (isotherm below the bottom). We must NOT linearly interpolate
    a real depth (say 5 m) against a 999 sentinel — that manufactures a spurious ~400 m ramp at
    the crossing/no-crossing frontier that survives into the bottom-temperature inversion
    exactly where the cold edge matters (validation R5, agents A#4/D#3). Instead: interpolate
    depth from the REAL crossings only, and separately interpolate the 0/1 sentinel indicator;
    where the local node majority is no-crossing, emit the sentinel (isotherm below bottom)."""
    vals = np.asarray(vals, dtype=float)
    pts = np.asarray(iso_pts, dtype=float)
    sentinel = vals >= 900.0
    if sentinel.all():
        return np.full(gx.shape, 999.0)          # nothing reaches this target anywhere
    real = ~sentinel
    rp, rv = pts[real], vals[real]

    def _grid(p, v):
        # linear where a triangulation exists (>=3 non-degenerate points), else nearest;
        # nearest-fill any remaining NaN. Robust to collinear/sparse node sets.
        out = np.full(gx.shape, np.nan)
        if len(p) >= 3:
            try:
                out = griddata(p, v, (gx, gy), method="linear")
            except Exception:  # noqa: BLE001  (QhullError on collinear/degenerate input)
                out = np.full(gx.shape, np.nan)
        nan = ~np.isfinite(out)
        if nan.any():
            out[nan] = griddata(p, v, (gx[nan], gy[nan]), method="nearest")
        return out

    iso = _grid(rp, rv)                            # depth from REAL crossings only (no 5->999 ramp)
    # RENDER AT THE FIELD'S OWN RESOLUTION (2026-08 visual review): linear griddata over sparse
    # LSOFS nodes is piecewise-PLANAR — temperature bands contoured from it inherit long straight
    # triangle-facet edges (isolated facets even appear as detached angular blobs). Below the node
    # spacing the model carries NO information, so those edges are interpolation artifacts, not
    # data. Low-pass the field at HALF THE MEASURED MEDIAN NODE SPACING (data-derived scale, not a
    # picked look): the bands become smooth curves at the model's honest effective resolution.
    # Consistent with the stated edge uncertainty (~100-300 m) in the UI.
    sig_px = _node_scale_px(rp, gx)
    if sig_px > 0:
        from scipy.ndimage import gaussian_filter
        iso = gaussian_filter(iso, sig_px)
    if sentinel.any():
        # fraction of surrounding nodes that never reach the target; >0.5 => below-bottom here
        frac = _grid(pts, sentinel.astype(float))
        if sig_px > 0:
            from scipy.ndimage import gaussian_filter
            frac = gaussian_filter(frac, sig_px)
        iso = np.where(frac > 0.5, 999.0, iso)
    return iso


def _node_scale_px(rp, gx):
    """Smoothing sigma (in grid px) = half the median nearest-neighbour LSOFS node spacing —
    the field's own sampling scale, measured per stretch, bounded to [0, 300 m] for safety."""
    if len(rp) < 2 or gx.shape[1] < 2:
        return 0.0
    from scipy.spatial import cKDTree
    d, _ = cKDTree(rp).query(rp, k=2)
    sig_m = min(0.5 * float(np.median(d[:, 1])), 300.0)
    res_m = float(abs(gx[0, 1] - gx[0, 0]))
    return sig_m / res_m if res_m > 0 else 0.0


def _bottom_temp_field(iso_fields, targets, depth):
    """Invert the stack of isotherm-DEPTH fields into a bottom-TEMPERATURE field.

    Each iso_fields[T] is the depth (m) of the T °C isotherm (999 sentinel = the column never
    reaches T °C, i.e. that isotherm is deeper than any bottom). Isotherm depth decreases as
    temperature rises, so for a pixel bottom depth d the bottom temperature is a piecewise-
    linear interpolation of T against those depths: warmer than the shallowest isotherm above
    it, colder than the deepest below it. Reuses the fields already computed for the bands, so
    the graded thermal suitability costs no extra model reads. Returns NaN off-water.
    """
    T = sorted(targets, reverse=True)                       # warm -> cold
    D = [np.where(iso_fields[t] >= 900, np.nan, iso_fields[t]) for t in T]  # sentinel -> NaN
    b = np.full(depth.shape, np.nan, dtype=float)
    for k in range(len(T) - 1):
        dh, dl = D[k], D[k + 1]                              # dh (warm, shallow) <= dl (cold, deep)
        valid = np.isfinite(dh) & np.isfinite(dl) & (dl > dh)
        m = valid & (depth >= dh) & (depth <= dl) & np.isnan(b)
        with np.errstate(invalid="ignore", divide="ignore"):
            frac = np.where(valid & (dl > dh), (depth - dh) / (dl - dh), 0.0)
        b = np.where(m, T[k] + (T[k + 1] - T[k]) * frac, b)
    # CLAMP to the WARMEST / COLDEST isotherm that ACTUALLY CROSSES here — not the fixed extreme
    # target. At a cold nearshore stretch the column never reaches 18 °C (nor, absent strong
    # upwelling, 4 °C), so those extreme isotherms are sentinel everywhere and the old clamp
    # (keyed to D[0]=18 / D[-1]=4) silently failed — leaving MOST of the reachable band NaN, and
    # the NaN coverage swung between forecast leads as different isotherms crossed, which flickered
    # the shaded area 3× day to day (operator-caught "blobs moving between days"; root cause traced
    # to here — the bottom_c VALUES were stable ~8 °C, only the valid COVERAGE swung). Clamping to
    # the shallowest/deepest FINITE isotherm gives the whole band a stable bottom temperature; when
    # genuinely cold water DOES reach 4 °C that isotherm becomes finite and is used (T1a preserved).
    Dstack = np.stack(D)                                     # (nT, H, W), warm->cold, NaN=sentinel
    finite = np.isfinite(Dstack)
    any_fin = finite.any(axis=0)
    warm_k = np.argmax(finite, axis=0)                      # first (warmest) finite isotherm index
    cold_k = (len(T) - 1) - np.argmax(finite[::-1], axis=0)  # last (coldest) finite isotherm index
    ii, jj = np.indices(depth.shape)
    warm_d = Dstack[warm_k, ii, jj]; warm_t = np.asarray(T)[warm_k]
    cold_d = Dstack[cold_k, ii, jj]; cold_t = np.asarray(T)[cold_k]
    wet = np.isfinite(depth)                                # never fill land (NaN depth) — stays NaN
    b = np.where(np.isnan(b) & any_fin & wet & (depth < warm_d), warm_t, b)   # above warmest crossing
    b = np.where(np.isnan(b) & any_fin & wet & (depth > cold_d), cold_t, b)   # below coldest crossing
    # a bracketed-but-unfilled WATER pixel (a gap between non-adjacent finite isotherms): pin to the
    # nearer of the warmest/coldest crossing by depth, so a deep gap-pixel doesn't read warm.
    gap = np.isnan(b) & any_fin & wet
    nearer_cold = np.abs(depth - cold_d) < np.abs(depth - warm_d)
    b = np.where(gap & nearer_cold, cold_t, b)
    b = np.where(gap & ~nearer_cold, warm_t, b)
    return b


# NOTE: the isotherm targets are NOT a constant here. The build always passes
# `targets=cfg.band_temps` — the union of every species' range/optimal/front endpoints, i.e.
# (16, 14, 12, 10, 8, 6) °C (config.band_temps). The bottom-temperature field therefore spans
# the full 6–16 °C across all species' niches, not a laker-only 8–12 °C stack. (A former
# module-level TARGETS=(12,10,8) constant was dead — never used, and it misdescribed the field's
# range — so it was removed in the 2026-08 consolidation.)

# Where-the-fish-ARE tiers. TEMPERATURE (published thermal-preference curves) is the primary
# driver — a spot must be in the right band to shade at all — refined by a real physical EDGE:
#   s1 fair  = in the preferred temperature range          (context — the water is the right temp)
#   s2 good  = in the OPTIMAL-temperature core              (genuinely good temperature)
#   s3 prime = optimal temperature AND on a real edge       (good temp + structure = truly prime)
# where EDGE = a genuine bottom drop-off / break (the DIRECTLY-MEASURED CHS NONNA slope). We do
# NOT use the modelled thermal-front gradient as the edge: the LSOFS field is too coarse to
# resolve real fronts (Landsat couldn't validate it) and it's largely redundant with depth
# structure, so it over-called prime. The thermal/relaxation aspect lives elsewhere — in the
# temperature grading (fair/good) and in the upwelling-phase banner. The edge bar is ABSOLUTE
# (below), NOT a per-scene percentile — so a flat, featureless stretch yields little or no prime,
# and "prime" means truly good conditions, not best-available-today. Disjoint tiers (no overlap).
# TWO honest axes, kept SEPARATE (conjunction, no fabricated blend — rule 7):
#   TEMPERATURE (discrete, CITED bands): fair = inside the preferred range (range_c); good = the
#     optimal_c core plateau. Boundaries are the literature bands from stations.yaml (T2/T3), not
#     picked suitability numbers (the old OPTIMAL_SUIT=0.7 / IN_RANGE_SUIT=0.15 were unsourced).
#   STRUCTURE (CONTINUOUS glow): within the optimal zone, the measured break STRENGTH grades the
#     fill so the strongest breaks glow brightest — no binary "prime" blob. This is the hybrid the
#     user chose: temperature discrete-and-cited, structure continuous, hard floor below.
# BIVARIATE levels (ADR-038): the two signals are SEPARATE layers in separate visual channels.
# s* = the TEMPERATURE wash (moves with the forecast); g* = the STATIC measured-structure marks
# (never move — pure bathymetry, emitted once per stretch, no lead). The conjunction is read by
# eye: a gold mark inside bright teal. No combine math anywhere in the display.
SUIT_LEVELS = [
    ("s1", "in range"),      # temperature wash: acceptable (range_c)
    ("s2", "optimal temp"),  # temperature wash: optimal core (optimal_c)
]
# STRUCTURE RAMP (ADR-039): three steps read as on/off ("is the gold just on or off?"). The marks
# are a FINE ramp of nested cumulative bands whose edges are the MEASURED percentiles of the pooled
# regional strength distribution (strength_pcts in bathy_slope.json) — faint amber at p75 rising to
# bright gold at p99, so intensity IS the regional rank of the break. 13 bands, ~2-percentile steps:
# coarse 6-band ladders still showed visible "stacked" contour edges; at 13 the per-band opacity
# increment (~0.05) sits below the visible-banding threshold and the ramp reads smooth. Data-derived
# edges, not a styling gradient. The "real break" bar for RANKING is unchanged: p90/p95/p99 (ADR-029).
_RAMP_QS = (75, 77, 79, 81, 83, 85, 87, 89, 90, 92, 95, 97, 99)   # ladder anchors; tags f"g{q}"
STRUCT_LEVELS = [(f"g{q}", f"structure ≥ regional p{q}") for q in _RAMP_QS]
IN_RANGE_SUIT = 0.0            # fair = strictly inside the preferred range (suit > this)
OPTIMAL_PLATEAU = 1.0 - 1e-6   # good = the optimal_c plateau (suit at/above ~1.0)
# Structure bars + glow bands are LOADED from the calibration record at runtime (single source of
# truth), NOT transcribed — so re-running scripts/analyze_bathy_slope.py actually propagates and the
# code can't silently drift from its own measurement (validation T1b). The values are the pooled
# regional percentiles over reachable water across the surveyed stretches (n~464k px):
#   struct_slope_abs / struct_relief_abs = p90 of |grad depth| / local relief (a "real break");
#   strength_bands = p90/p95/p99 of the pooled STRENGTH = max(slope/slope_bar, relief/relief_bar),
#   so a spot's glow reflects how its break ranks REGIONALLY. The percentile CHOICE is a bounded
#   judgment (see PROVENANCE_LEDGER); the values are measured. Fallbacks below are last-resort only.
_BATHY_CALIB = Path(__file__).resolve().parents[1] / "data" / "calib" / "bathy_slope.json"


def _load_struct_calib():
    try:
        d = json.loads(_BATHY_CALIB.read_text())
        if not isinstance(d, dict):
            raise ValueError("bathy_slope.json is not an object")
        ladder = d["strength_pcts"]
        ramp = tuple(float(ladder[str(q)]) for q in _RAMP_QS)
        return (float(d["struct_slope_abs"]), float(d["struct_relief_abs_m"]), ramp)
    except (OSError, ValueError, KeyError) as e:  # noqa: BLE001
        # Fallback = the CURRENT committed calibration (smoothed-field values, ADR-036/039). Must
        # be kept in lockstep with data/calib/bathy_slope.json — a stale fallback silently applies
        # old bars when the json is unreadable (caught in the 2026-08 stress-test pass).
        print(f"⚠ bathy_slope.json unreadable ({e}) — using fallback structure bars")
        return (0.123, 1.43, (0.716, 0.773, 0.836, 0.906, 0.987, 1.08, 1.185,
                              1.313, 1.384, 1.551, 1.907, 2.285, 3.258))


(STRUCT_SLOPE_ABS, STRUCT_RELIEF_ABS, STRENGTH_RAMP) = _load_struct_calib()
_RAMP_BY_Q = dict(zip(_RAMP_QS, STRENGTH_RAMP))
# The ADR-029 "real break" bars for RANKING (unchanged by the display ramp): p90 / p95 / p99.
STRENGTH_BREAK, STRENGTH_STRONG, STRENGTH_TOP = _RAMP_BY_Q[90], _RAMP_BY_Q[95], _RAMP_BY_Q[99]

# CLAMP-EXTENSION isotherms (°C). _bottom_temp_field clamps water beyond the coldest/warmest target
# to that target's temperature. If the coldest target equals a species' band edge (6 °C == laker
# cold floor), ALL colder water clamps up to that edge and reads in-band — so a strong upwelling
# paints the whole area optimal-green ("coldest = best", validation T1a). Adding a 4 °C and 18 °C
# target moves the clamp floor/ceiling OUTSIDE every species band, so genuinely cold (<4) / warm
# (>18) water reads out-of-range, and the 4-6 / 16-18 °C margins TAPER instead of pinning to optimal.
# On the 2 °C grid so each isotherm is still computed once. Union with the species band endpoints.
CLAMP_EXTEND = (4.0, 18.0)


def _clamp_targets(band_temps):
    return tuple(sorted(set(band_temps) | set(CLAMP_EXTEND), reverse=True))


def _species_tiers(bottom_c, strength, within, depth, sp, prod, season=None):
    """Pure, testable core of the per-species render: apply the DEPTH gate, grade TEMPERATURE into
    the cited bands (fair=range, good=optimal plateau), then split the optimal zone by CONTINUOUS
    structure STRENGTH into the data-derived glow bands. Returns (disjoint tiers dict, fair mask).

    Kept a standalone function so the depth gate (adult lakers out of <4 m — the marina over-cover
    fix) and the strength banding can be unit-tested without the full LSOFS/geometry pipeline."""
    import numpy as _np
    from tbay_fishcast.features import suitability as _su
    lo_d = sp.min_depth_m if sp.min_depth_m is not None else 0.0
    hi_d = sp.max_depth_m if sp.max_depth_m is not None else prod.max_reach_depth_m
    # FALL shoal-staging (audit T1d): in fall the season badge says lake trout move onto SHALLOW
    # rocky shoals to spawn — so the summer ≥4 m adult-hold gate would exclude the very water the
    # badge points to. Drop the laker min-depth floor during fall so the map stops contradicting it.
    if season == "fall" and sp.id == "lake_trout":
        lo_d = 0.0
    within_sp = within & (depth >= lo_d) & (depth <= hi_d)     # per-species depth band from shore
    suit = _su.thermal_suitability(bottom_c, sp.range_c, sp.optimal)   # ∈[0,1], CITED bands, smooth
    suit = _np.where(within_sp, suit, 0.0)
    fair = suit > IN_RANGE_SUIT                                # inside the preferred range (range_c)
    good = suit >= OPTIMAL_PLATEAU                             # the optimal-core plateau (optimal_c)
    # BIVARIATE display (ADR-038, supersedes the ADR-037 product): the TEMPERATURE tiers are the
    # cited-band wash only — s1 in-range ring, s2 optimal core — with NO structure mixed in. The
    # structure marks are a SEPARATE, lead-independent layer (_species_structure below). Operator
    # call: multiplying the uncertain thermal curve into the measured structure percentile was still
    # a combine-form choice; showing each signal in its own channel removes the last picked method —
    # the eye reads the conjunction (a gold break-mark inside bright teal) with zero fusion math.
    tiers = {"s1": fair & ~good, "s2": good}
    return tiers, fair


def _species_structure(strength, within, depth, sp, prod, season=None):
    """STATIC measured-structure marks for one species — pure bathymetry, NO temperature input.

    Gated only on reachability + the species' depth band, so the marks sit in the SAME place at the
    same level on every forecast day by construction (they are emitted once per stretch, not per
    lead). NOTE this depth gate is per-species ON PURPOSE: the bottom is the same everywhere, but a
    mark is only drawn where that species can actually use it (summer adult lakers ≥4 m, brookies
    shallow) — so the gold differs between species wherever their depth bands differ.

    Levels (ADR-039 continuous ramp): NESTED CUMULATIVE bands — g<q> = strength ≥ the MEASURED
    p<q> of the pooled regional strength distribution, for q in 75/80/85/90/95/99 (strength_pcts
    in bathy_slope.json). Cumulative (filled-contour stacking), NOT disjoint rings: a disjoint
    band is the thin annulus between two contours, which marching squares shreds into sliver
    fragments — that read as "detached blobs" on the map and tripped the speckle audit. Solid
    nested regions have no slivers; the renderer stacks their translucent fills so opacity
    accumulates into the gradient, brightest where the measured rank is highest. The temperature
    wash next to them says whether that water is right TODAY; the marks say where the bottom
    structure IS, period."""
    import numpy as _np
    lo_d = sp.min_depth_m if sp.min_depth_m is not None else 0.0
    hi_d = sp.max_depth_m if sp.max_depth_m is not None else prod.max_reach_depth_m
    if season == "fall" and sp.id == "lake_trout":
        lo_d = 0.0                                             # fall shoal-staging (T1d)
    within_sp = within & (depth >= lo_d) & (depth <= hi_d)
    s = _np.where(within_sp, strength, 0.0)
    return {f"g{q}": s >= edge for q, edge in zip(_RAMP_QS, STRENGTH_RAMP)}


def _overlay(depth, dist, gx, gy, inbox, node_xy, cols, g_sst, bias, bounds_3857, res, prod,
             targets=(), species=(), own_m=None, others_m=(), season=None,
             include_structure=False):
    """Return (area_features, reach_px) for one lead, or (None, 0.0).

    Signal: graded per-species suitability AREA fills — for each species, the reachable water
    graded through its thermal-preference curve (fair/good) intersected with a measured bottom
    edge (prime), emitted as nested disjoint contours of the SAME bias-corrected LSOFS field.
    No new data (standard optimal-thermal-habitat mapping).

    Position UNCERTAINTY is NOT drawn as a per-lead front ribbon. An earlier design computed
    shallow/central/deep isotherm bookends and never emitted them (dead false precision); the
    honest uncertainty is now a single MEASURED figure — the isotherm-depth MAE from the
    accumulated gate (manifest.forecast_error, ADR-031), shown in the legend. The whole empty
    line-feature pipeline was removed in the 2026-08 consolidation.
    """
    central, lo, hi, n = bias
    iso_pts = []
    tvals = {t: [] for t in targets}          # central isotherm depth per target
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
    if len(iso_pts) < 3:
        return None, 0.0, {}
    iso_pts = np.array(iso_pts)
    # min_reach_px small + min_area small: the old speckle those filters guarded against
    # came from the no-data-apron heuristic, which the authoritative water mask removed —
    # so a genuine within-cast cold wedge off a NARROW point is no longer wrongly clipped
    # (user-caught: solid green missing right off Silver tip).
    kw = {"cast_m": prod.cast_m, "max_reach_depth_m": prod.max_reach_depth_m, "min_reach_px": 4}
    # Drop sub-~garage fragments. Was 120 m² (kept to preserve a thin cold wedge off Silver tip),
    # but that also let grid-speckle through; now that structure is computed on the smoothed field
    # the speckle is gone at the source, so a modest floor cleans residual thermal fragments without
    # clipping a genuine 75 m-long nearshore wedge (~300 m²+).
    MIN_AREA = 300.0
    from shapely.geometry import Polygon as _ShPoly
    from shapely.ops import unary_union as _uunion

    def _shape(mask):
        """reachable_area_features -> one clean shapely (Multi)Polygon (lon/lat)."""
        polys = []
        for rings in reachable_area_features(mask, bounds_3857, res, min_area_m2=MIN_AREA,
                                             smooth_iters=1):
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

    area_feats = []
    reach_px_primary = 0.0

    def _emit(geom, tag, static=False):
        for gg in _polys(geom):
            rings = [[list(c) for c in gg.exterior.coords]]
            rings += [[list(c) for c in r.coords] for r in gg.interiors]
            area_feats.append((tag, rings, static))

    # GRADED SUITABILITY model (docs/FISH_BEHAVIOR_REVIEW.md): rather than a flat in/out band,
    # grade each reachable pixel by how close its BOTTOM temperature is to where the species
    # concentrates — 1.0 across the optimal core, tapering to 0 at the edges of the preferred
    # range (features/suitability.thermal_suitability, from published thermal preferences). We
    # invert the isotherm-depth stack into a bottom-temperature field ONCE, then emit three
    # nested contours per species (s1 total range -> s3 optimal core) so the angler sees the
    # sweet spot inside the habitable zone. This grades ONE measured variable through a
    # literature curve — it is NOT an arbitrary fusion of temperature with phase/front/structure
    # (that weighting needs catch data to fit; deferred, see suitability.py). The upwelling
    # PHASE is a separate, honestly-labelled temporal signal (manifest 'phase'), not baked in.
    from tbay_fishcast.features import suitability as _su
    iso_fields = {t: _iso_field(gx, gy, iso_pts, tvals[t]) for t in targets}
    water = np.isfinite(depth)
    # CLIP TO AUTHORITATIVE WATER: the fill previously used only isfinite(depth), so bridged
    # depth + small shore-distance leaked the shading onto the beach / docks / islands (verified:
    # ~7.6% of shaded pixels sat on OSM land at the marina). Intersect with the frozen OSM water
    # mask so nothing is ever painted on land.
    try:
        from scipy.ndimage import binary_erosion as _erode
        from tbay_fishcast.ingest.watermask import WaterMaskError, water_mask
        _wm = water_mask(bounds_3857, depth.shape)
        # Erode the water mask ~2 px (~8 m) so the SMOOTHED polygon edges (Chaikin rounds corners
        # outward by up to ~simplify_m) still land in water, not on the beach/dock. You cast FROM
        # shore into the water, so a few-metre setback off the exact waterline is correct, not lossy.
        _wm = _erode(_wm, iterations=2, border_value=1)
    except (WaterMaskError, Exception):  # noqa: BLE001 - degrade to prior behavior if mask absent
        _wm = water
    within = water & _wm & (dist <= prod.cast_m) & (depth <= prod.max_reach_depth_m)
    # TERRITORY CLIP: the coverage boxes overlap by design, so adjacent stretches would each
    # draw the same shore — doubling into lumpy "blobs". Keep only the pixels this stretch owns
    # (closer to its centre than to any other stretch's), so the stretches tile the shore instead
    # of overlapping. Voronoi in mercatormetres; cheap, vectorised.
    if own_m is not None and len(others_m):
        d_own = (gx - own_m[0]) ** 2 + (gy - own_m[1]) ** 2
        d_oth = np.full(gx.shape, np.inf)
        for ox, oy in others_m:
            d_oth = np.minimum(d_oth, (gx - ox) ** 2 + (gy - oy) ** 2)
        within = within & (d_own <= d_oth)
    bottom_c = _bottom_temp_field(iso_fields, targets, depth)

    # STRUCTURE STRENGTH — continuous, measured (NONNA soundings), normalized to the ABSOLUTE
    # data-derived p90 bars so ~1.0 is a regionally-typical break and the strongest breaks score
    # higher. max of the two measured kinds: slope catches drop-off flanks, relief catches the flat
    # shoal-tops / point-tips the slope misses. This continuous field drives the glow (no binary
    # prime blob); featureless water stays ~0 (the floor).
    # Compute structure on depth SMOOTHED to NONNA's native ~10 m resolution: the raw 4 m raster is
    # an upsampled step-field whose cell-boundary steps read as false breaks (the "random blobs").
    # Smoothing removes the grid steps while keeping real drop-offs (validated: strong-break speckle
    # 213→19 px at Silver Islet, real breaks retained). Bands in bathy_slope.json are recalibrated
    # on the same smoothed field so the p90/p95/p99 provenance stays consistent.
    depth_struct = _su.native_smoothed(depth, res)
    slope = _su.bathymetric_structure(depth_struct, res)     # rise/run
    relief = _su.bathymetric_relief(depth_struct, res)       # m, shallower-than-surroundings
    with np.errstate(invalid="ignore"):
        s_norm = np.where(np.isfinite(slope), slope / STRUCT_SLOPE_ABS, 0.0)
        r_norm = np.where(np.isfinite(relief), relief / STRUCT_RELIEF_ABS, 0.0)
    strength = np.maximum(s_norm, r_norm)                    # continuous structure strength

    default_id = None
    inter_area = {}   # {species: {tier: px}} — set-INTERSECTION tiers at this lead, for ranking only
    for sp in species:
        tiers, fair = _species_tiers(bottom_c, strength, within, depth, sp, prod, season=season)
        for tag in ("s1", "s2"):                             # temperature wash, per lead
            mask = tiers[tag]
            if mask.any():
                _emit(_shape(mask), f"sp:{sp.id}:{tag}")
        # STATIC structure marks: emitted ONCE per stretch (include_structure = lead-0 call only),
        # tagged WITHOUT a lead so they render identically on every forecast day by construction.
        # Shown for all species within their depth band — the marks are measured bottom, a fact;
        # the weak-cue caveat (temperature/structure are minor cues for salmon/steelhead) stays on
        # the wash + ranking, not on the existence of the bottom (ADR-038).
        gtier = _species_structure(strength, within, depth, sp, prod, season=season)
        if include_structure:
            for tag, mask in gtier.items():
                if mask.any():
                    _emit(_shape(mask), f"sp:{sp.id}:{tag}", static=True)
        # RANKING sample: plain BOOLEAN INTERSECTIONS (no product scalar) — how much in-range /
        # optimal water, and how much measured break sits INSIDE each (the conjunction as set
        # logic). Pixel counts here; converted to m² by the caller (res is uniform per stretch).
        # Ranking tiers keep the ADR-029 "real break" definition (p90/p95/p99) regardless of the
        # finer display ramp: the sub-p90 ramp bands are context shading, not "structure" for
        # scoring purposes, so the ranking is IDENTICAL to the pre-ramp product. Masks are nested
        # (g99 ⊆ g95 ⊆ g90), so the disjoint scoring tiers are set differences.
        good = tiers["s2"]
        anyg, strong, top = gtier["g90"], gtier["g95"], gtier["g99"]
        inter_area[sp.id] = {
            "s1": float(tiers["s1"].sum()), "s2": float((good & ~anyg).sum()),
            "s3": float((fair & anyg & ~strong).sum()),
            "s4": float((fair & strong & ~top).sum()),
            "s5": float((fair & top).sum()),
        }
        if sp.default or (default_id is None and sp is species[0]):
            reach_px_primary = float(fair.sum())             # total-zone (in-range) area
            default_id = sp.id
    return area_feats, reach_px_primary, inter_area


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
    issue, cycle = _resolve_issue(cfg, issue)   # freshest posted LSOFS cycle, not just t12z
    OUT.mkdir(parents=True, exist_ok=True)

    # SAFETY-CRITICAL (ADR-007 / CLAUDE rule 4): EVERY recommendation surface — stretches,
    # stations, river-mouth markers — passes through the regs gate before it is emitted, so the
    # product is structurally INCAPABLE of drawing closed/prohibited water, not merely by human
    # curation of the lists (the Kakabeka-pin failure mode). Closures are name-based hard/seasonal
    # today; a geometric clip is Phase 2. Every drop is LOUD.
    from tbay_fishcast.scoring.regs_gate import RegsGate
    _regs = RegsGate.load()
    _viol = _regs.validate()
    if _viol:
        # Phase 0: the seed regs aren't all T1 yet, so validate() has known violations. Surface
        # the go-live blocker LOUDLY but don't fail the dev build — flipping this to a hard block
        # (sys.exit) is the ADR-007 go-live gate, tracked separately.
        print(f"⚠ regs validate(): {len(_viol)} provenance violation(s) — GO-LIVE BLOCKER (not build): "
              + "; ".join(_viol))
    def _regs_ok(nm):
        if _regs.is_prohibited(nm, issue):
            print(f"⚠ REGS GATE: dropping prohibited water {nm!r} from recommendations (issue {issue})")
            return False
        return True
    stretches_in = [s for s in STRETCHES if _regs_ok(s[1])]   # s[1] = display name

    try:
        central, lo, hi, _, n, bias_src = bias_live.pooled_or_prior(cfg, issue)
    except Exception as e:  # noqa: BLE001 - a malformed LSOFS cycle must degrade, not kill (M4)
        print(f"⚠ bias pipeline failed ({str(e)[:60]}) — frozen prior in use (degraded mode)")
        central, lo, hi = bias_live.FROZEN_PRIOR
        n, bias_src = 0, "frozen-prior"
    bias = (central, lo, hi, n)
    if bias_src != "live":
        print("⚠ live buoy bias unavailable — frozen prior in use (degraded mode)")

    stretches_out = []
    tier_area_by_stretch: dict[str, dict] = {}   # {sid: {species: {tier: area_m2}}} at lead 0 (top-spots)
    lead_valid: dict[int, str] = {}   # region-wide lead -> valid_utc (all stretches share the cycle)
    centers_m = [merc(la, lo) for (_sid, _nm, la, lo, _ex) in stretches_in]  # for the territory clip
    from tbay_fishcast.features import season as _season
    _issue_season = _season.regime(issue).season   # summer|fall|... — drives the fall laker gate (T1d)
    ns_by = _nearshore_delta_by_class()   # measured shore-minus-GLSEA delta, per exposure class
    print("nearshore delta by class: "
          + ", ".join(f"{c} {d:+.2f} C (n={n})" for c, (d, n) in ns_by.items()))
    for si, (sid, name, clat, clon, exposure) in enumerate(stretches_in):
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
        try:
            inbox, node_xy = _node_columns_in_box(cfg, issue, clat, clon, cycle)
        except Exception as e:  # noqa: BLE001 - an S3 blip costs one stretch, not the build (M3)
            print(f"skip {sid}: LSOFS grid open failed ({str(e)[:50]})"); continue
        if len(inbox) == 0:
            print(f"skip {sid}: no LSOFS nodes"); continue
        # surface anchor: GLSEA lags ~1 day, so fall back to the most recent available
        # day rather than dropping it — without it the corrected isotherm collapses to
        # the surface (whole shelf reads "cold", no 12 C line).
        px = glsea.fetch_recent_sst(clat, clon, issue)
        g_sst = px.sst_c if px else None
        anchor_day = px.day if px else None
        # add the MEASURED nearshore surface warm-delta: GLSEA's ~1 km pixel reads the
        # nearshore too cold (Landsat 30 m over the shore is +1.7…2.8 °C warmer), which was
        # dragging the shallow reachable band into the cold band. Data-backed, not a guess.
        _cls = "exposed" if exposure == "high" else "sheltered"
        ns_delta, ns_n = ns_by[_cls]
        if g_sst is not None and ns_n > 0:
            g_sst = g_sst + ns_delta          # SIGNED: exposed is slightly negative, sheltered warm
        if px and px.day != issue.isoformat():
            print(f"    {sid}: GLSEA anchor {px.sst_c:.1f}C from {px.day} (issue not yet posted)")

        # corner lon/lats for the MapLibre image source (TL, TR, BR, BL)
        tl = _merc_to_ll(x0, y1); tr = _merc_to_ll(x1, y1)
        br = _merc_to_ll(x1, y0); bl = _merc_to_ll(x0, y0)
        corners = [list(tl), list(tr), list(br), list(bl)]

        days = []
        area_feats = []                       # vector reachable-cold polygons, tagged by lead
        tier_area = {}                        # {species_id: {tier: area_m2}} at lead 0 (for top-spots)
        for kind, fh, lead in LEADS:
            f = LsofsFile(issue, cycle, kind, fh)
            try:
                ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket, byterange=False))
            except Exception:  # noqa: BLE001
                continue
            vt = valid_time_from_dataset(ds)
            cols = extract_native_columns(ds, {str(int(nd)): int(nd) for nd in inbox})
            ds.close()
            area, reach_px, inter = _overlay(depth, dist, gx, gy, inbox, node_xy,
                                      cols, g_sst, bias, patch.bounds_3857, res,
                                      cfg.product, targets=_clamp_targets(cfg.band_temps),
                                      species=cfg.species,
                                      own_m=centers_m[si],
                                      others_m=[c for j, c in enumerate(centers_m) if j != si],
                                      season=_issue_season,
                                      include_structure=(lead == 0))
            if area is None:
                continue
            days.append({"label": f"{vt:%a %b %-d}", "lead": lead,
                         "valid_utc": vt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "reach_ha": round(reach_px * res * res / 1e4, 1)})
            for temp, rings, static in area:
                coords = [[[round(lon, 6), round(lat, 6)] for lon, lat in ring] for ring in rings]
                # STATIC structure marks carry NO lead — they render on every forecast day
                props = {"temp": temp} if static else {"lead": lead, "temp": temp}
                area_feats.append({"type": "Feature", "properties": props,
                                   "geometry": {"type": "Polygon", "coordinates": coords}})
            # RANKING areas at lead 0: boolean-INTERSECTION pixel counts from _overlay (set logic,
            # no product) converted to m² — replaces the old polygon-shoelace accumulation.
            if lead == 0 and inter:
                for _sp, tiers_px in inter.items():
                    tier_area[_sp] = {t: px * res * res for t, px in tiers_px.items()}
        if not days:
            print(f"skip {sid}: no frames"); continue
        if not lead_valid:
            lead_valid = {int(d["lead"]): d["valid_utc"] for d in days}
        (OUT / "areas").mkdir(exist_ok=True)
        (OUT / "areas" / f"{sid}.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": area_feats}))
        # CHART VIEW isobaths (ADR-041): static per-stretch, loaded lazily by the client only
        # when the user flips to depth-contour view. Failure costs the toggle, never the map.
        _ctr_path = None
        try:
            _cf = _depth_contours(depth, res, patch.bounds_3857)
            if _cf:
                (OUT / "contours").mkdir(exist_ok=True)
                (OUT / "contours" / f"{sid}.geojson").write_text(
                    json.dumps({"type": "FeatureCollection", "features": _cf}))
                _ctr_path = f"data/contours/{sid}.geojson"
        except Exception as e:  # noqa: BLE001
            print(f"    {sid}: contours failed ({str(e)[:60]})")
        tier_area_by_stretch[sid] = tier_area
        # survey_cov = fraction of the box with real CHS soundings (informational). NOTE: a low
        # whole-box fraction does NOT by itself mean a bad map — the verified city stretches sit
        # at 0.16–0.19 yet map correctly, because the NEARSHORE (where casting happens) is
        # surveyed even when the offshore half of the box isn't. So low_confidence is set
        # EXPLICITLY (LOW_CONFIDENCE below) for stretches that are genuinely unreliable —
        # currently only Little Trout Bay: sparse soundings + few LSOFS nodes + a sheltered bay
        # far from any validation, which together produce a large but poorly-constrained cold
        # area (~138 ha vs 7–80 ha for verified stretches). Flagged, not hidden.
        # per-stretch HEALTH so the client can warn on THIS stretch, not just a global banner:
        # degraded = bathymetry-only shore (imagery mask failed → artifact-prone reachable area);
        # anchor_stale = the GLSEA surface anchor is missing or > ~2 days old (correction weakens).
        anchor_stale = (anchor_day is None) or (
            (issue - date.fromisoformat(anchor_day)).days > 2 if anchor_day else True)
        stretches_out.append({"id": sid, "name": name, "corners": corners,
                              "center": [clat, clon], "res_m": round(res, 1),
                              "exposure": exposure, "anchor_day": anchor_day,
                              "survey_cov": round(float(patch.coverage_frac), 2),
                              "low_confidence": sid in LOW_CONFIDENCE, "days": days,
                              "degraded": bool(degraded), "anchor_stale": bool(anchor_stale),
                              "area": f"data/areas/{sid}.geojson", "contours": _ctr_path})
        print(f"  {sid}: {len(days)} days, {len(inbox)} nodes, res {res:.0f} m, "
              f"ha {days[0]['reach_ha']}..{max(d['reach_ha'] for d in days)}")

    if not stretches_out:
        print("no stretches produced"); return 1

    # per-station verdicts (reuse the per-spot forecast)
    stations = []
    for s in cfg.shore_stations:
        if not _regs_ok(s.name):        # regs gate on the station recommendation surface
            continue
        try:
            from tbay_fishcast.features.nearshore import STATION_EXPOSURE
            pts, wins, meta = fw.forecast_spot(cfg, s.lat, s.lon, s.name, s.lsofs_node, issue,
                                               (central, lo, hi, n),
                                               exposure=STATION_EXPOSURE.get(s.id),
                                               cycle=cycle)
        except Exception as e:  # noqa: BLE001 - a dead pin costs one pin, not the build (H3):
            # forecast_spot's internal NONNA fetch is unwrapped, and this loop runs AFTER all the
            # expensive stretch overlays — an escape here discarded the whole build (stress-test).
            print(f"skip station {s.id}: forecast failed ({str(e)[:50]})"); continue
        if not pts:
            print(f"skip station {s.id}: no forecast points (rule 5 — absence must be loud)")
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

    # ensemble upwelling-wind signal per day. The headline is the CONTINUOUS favorability
    # (features/upwelling.ensemble_favorability) — a graded response to wind strength, so a
    # persistent moderate west wind reads as moderate, not the flat 0 a hard ≥13 kt cutoff
    # gives. We keep the binary member-fraction too (how many members clear a sustained blow)
    # and the day's peak favorable wind, so a low number always carries its context.
    wind = []
    ens_w = None
    favor_calib = _load_favor_calib()
    fc_err = _load_forecast_error()   # measured thermal-field position uncertainty (± m), or None
    if fc_err:
        print(f"forecast error: {fc_err['pooled_mae_m']} m MAE, lead-trend="
              f"{fc_err['lead_trend_detected']} (n={fc_err['n']}, "
              f"{fc_err['n_effective_chains']} moving-obs chains)")
    try:
        from tbay_fishcast.features import upwelling
        from tbay_fishcast.features.wind import in_sector
        from tbay_fishcast.ingest import wind_forecast
        ens_w = wind_forecast.fetch_ensemble_wind(forecast_days=5)
        prob = upwelling.upwelling_probability(ens_w["time"], ens_w["members"])
        _fk = {"s50": favor_calib["s50_kn"], "width": favor_calib["width_kn"]} if favor_calib else {}
        favor = upwelling.ensemble_favorability(ens_w["time"], ens_w["members"], **_fk)
        # per-day peak favorable-sector wind from the control member (context for a 0 reading)
        from tbay_fishcast.features.upwelling import _naive
        ctrl = ens_w["members"][0]
        ct = [_naive(x) for x in ens_w["time"]]
        cs = np.array(ctrl["speed_kn"], dtype=float)
        cd = np.array(ctrl["dir_deg"], dtype=float)
        cfav = in_sector(cd)
        peak = {}
        for x, s, f in zip(ct, cs, cfav):
            if f:
                peak[x.date()] = max(peak.get(x.date(), 0.0), float(s))
        wind = [{"day": f"{d:%a}", "favor": round(favor[d], 2), "p": round(prob[d], 2),
                 "peak_kn": round(peak.get(d, 0.0))} for d in sorted(favor)]
    except Exception as e:  # noqa: BLE001
        print(f"wind ensemble unavailable: {str(e)[:50]}")

    # upwelling-PHASE (observed day 0 + forecast outlook) — reward the relaxation, not the trough
    phase = None
    try:
        phase = _build_phase(lead_valid, ens_w)
        if phase and phase.get("now"):
            print(f"phase now: {phase['now']['phase']} ({phase['now'].get('detail','')})")
        # OBSERVED corroboration (ADR-043): the phase above is inferred from WIND alone. The CHS
        # shore gauge measures the RESPONSE — an offshore blow draws the shoreline level down as
        # the thermocline tilts up. Agreement raises confidence; disagreement is stated, not
        # hidden. Never overrides the phase; a dead gauge just omits the line (rule 5).
        if phase is not None:
            from tbay_fishcast.ingest import water_level as _wl
            _lv = _wl.observed_state("thunder_bay").as_dict()
            phase["water_level"] = _lv
            if _lv.get("trend") != "unknown":
                _p = (phase.get("now") or {}).get("phase", "")
                _agrees = (_lv["upwelling_favorable"] == (_p in ("setup", "peak")))
                phase["water_level"]["agrees_with_wind_phase"] = _agrees
                print(f"shore gauge: {_lv['trend']} ({_lv['anomaly_m']:+.3f} m) — "
                      f"{'agrees with' if _agrees else 'DISAGREES with'} wind phase '{_p}'")
    except Exception as e:  # noqa: BLE001
        print(f"phase unavailable: {str(e)[:60]}")

    # seasonal regime context (research-backed direction; the zones are a SUMMER model)
    from tbay_fishcast.features import season as _season
    reg = _season.regime(issue)
    season_block = {"season": reg.season, "label": reg.label,
                    "upwelling": reg.upwelling, "note": reg.note}

    # WHEN-within-the-day: deterministic dawn/dusk low-light windows (pure astronomy, ADR-001).
    # Computed once at the basin centroid — the windows vary <2 min across the 5 stations, so a
    # single point is honest. Stored as UTC ISO (rule 9: UTC in storage, local only at display).
    from tbay_fishcast.features import daylight as _daylight
    _BASIN_LAT, _BASIN_LON = 48.43, -89.15   # Thunder Bay nearshore centroid
    _lw = _daylight.compute(issue, _BASIN_LAT, _BASIN_LON)

    def _iso(x):
        return x.strftime("%Y-%m-%dT%H:%M:%SZ") if x else None

    def _win(pair):
        return [_iso(pair[0]), _iso(pair[1])] if pair else None
    light_block = {
        "civil_dawn": _iso(_lw.civil_dawn), "sunrise": _iso(_lw.sunrise),
        "sunset": _iso(_lw.sunset), "civil_dusk": _iso(_lw.civil_dusk),
        "morning_prime": _win(_lw.morning_prime), "evening_prime": _win(_lw.evening_prime),
        # The crepuscular edge is the strongest INTRADAY cue for shore catch and is DECISIVE for the
        # weak-temperature-cue species (salmon/steelhead) the thermal map underserves. Effect size is
        # a cited behavioral prior (species_rules.yaml window_multipliers), not locally calibrated —
        # this supplies the deterministic clock, presented as timing, not a measured catch factor.
        "basis": "civil twilight (−6°) → sunrise+45m AM / sunset−45m → civil dusk PM; NOAA solar geometry",
    }

    # WHEN-across-the-season: spawning-run phenology from the committed calendar (T3b). Each
    # river-mouth marker carries its run-window status for the issue date, so a mouth reads bright
    # only inside its species' typical run — June's Kam mouth honestly shows "no run" (ADR-001,
    # deterministic date logic; the map never claims a specific fish is present).
    from tbay_fishcast.features import run_calendar as _runcal
    # Live river discharge → plume-strength trend (T3a). ECCC GeoMet realtime is reachable; a fetch
    # failure per gauge just leaves that marker with no flow line (rule 5, staleness loud).
    _flow_by_id = {}
    try:
        from tbay_fishcast.ingest import hydat as _hydat
        from tbay_fishcast.features import river_flow as _rflow
        for _mid, _gauge in _hydat.GAUGES.items():
            try:
                _fl = _rflow.classify(_hydat.fetch_recent_discharge(_gauge)).as_dict()
                # archive-climatology context: "20 m³/s ≈ <10th %ile for the date" — measured
                # against the gauge's full daily-mean record (Kam 1926-2025), see
                # build_flow_climatology.py. Omitted (None) when unavailable, never guessed.
                _ctx = _rflow.seasonal_context(_mid, _fl.get("q_cms"), issue)
                if _ctx:
                    _fl.update(_ctx)
                _flow_by_id[_mid] = _fl
            except Exception as e:  # noqa: BLE001 - per-gauge graceful
                print(f"river flow {_mid} unavailable: {str(e)[:50]}")
    except Exception as e:  # noqa: BLE001
        print(f"river discharge layer unavailable: {str(e)[:60]}")
    # LEDGER CONFIRMATIONS (ADR-042): the run calendar states the TYPICAL window; a researched,
    # dated, schema-validated report says the run is ACTUALLY on. This is a deterministic read of
    # the validated ledger inside the build — no LLM in the heartbeat (ADR-001). The claim is never
    # bare: the date + source domain travel with it, and it decays out of the window on its own.
    try:
        from tbay_fishcast.knowledge import observations as _obs
        _confirm = _obs.confirmations(issue)
        if _confirm:
            print(f"ledger confirmations active: {len(_confirm)} (place:species keys)")
    except Exception as e:  # noqa: BLE001 - a ledger problem must never break the map
        print(f"observation confirmations unavailable: {str(e)[:60]}")
        _confirm = {}
    markers_out = []
    for m in RIVER_MOUTHS:
        if not _regs_ok(m["name"]):
            continue
        mm = dict(m)
        mm["run"] = _runcal.marker_status(issue, m.get("species", []))
        mm["flow"] = _flow_by_id.get(m["id"])
        # Attach confirmations for THIS mouth, plus region-scope reports for a species this mouth
        # actually stages (a locationless "salmon are in" is honest evidence the run is on, and is
        # LABELLED regional — it never pretends the report named this river).
        _hits = []
        for _c in _confirm.values():
            _app_sp = LEDGER_SPECIES_TO_APP.get(_c["species"])
            if _app_sp is None or _app_sp not in m.get("species", []):
                continue
            if _c.get("place_id") == m["id"] or _c.get("place_id") is None:
                _hits.append({**_c, "app_species": _app_sp})
        if _hits:
            # placed reports first, then most recent — the strongest evidence leads
            _hits.sort(key=lambda c: (c.get("place_id") is None, c["date"]), reverse=False)
            _hits.sort(key=lambda c: (c.get("place_id") is not None, c["date"]), reverse=True)
            mm["confirmed"] = _hits[:3]
        markers_out.append(mm)
    _n_conf = sum(1 for m in markers_out if m.get("confirmed"))
    if _n_conf:
        print(f"run markers with LEDGER-CONFIRMED activity: {_n_conf}")
    _n_active = sum(1 for m in markers_out if m["run"]["active"])
    _n_fresh = sum(1 for m in markers_out if (m.get("flow") or {}).get("freshet"))
    print(f"run markers: {_n_active}/{len(markers_out)} in a run window; {_n_fresh} freshet on {issue}")

    # Barometric prior DEMOTED (2026-08): the direct pressure→feeding effect on salmonids is
    # debated in the literature and mostly an indirect proxy for the frontal weather already read
    # from wind; with no local catch logs it can never be measured here, so it failed the
    # "proven, measurable effect" bar and is no longer emitted. Modules (surface_meteo/barometric)
    # are retained but unused. See docs/PROVENANCE_LEDGER.md + ADR-035.
    baro_block = None

    # 'Best spots today' (T4a) — rank stretches per species from the model's OWN lead-0 habitat
    # areas (weighted by tier). Data-driven ordering, not a new judgment; weak-cue species carry the
    # run-timing caveat. Hands the angler the shortlist instead of a ten-stretch explore task.
    from tbay_fishcast.features import top_spots as _top
    _names = {sid: nm for (sid, nm, *_rest) in stretches_in}
    _active = [{"id": r.id, "species": r.species, "note": r.note}
               for r in _runcal.active_runs(issue)]
    _nxt = _runcal.next_run(issue)          # countdown to the next run window (walkthrough #5)
    top_block = _top.build(cfg.species, tier_area_by_stretch, _names, active_runs=_active,
                           low_confidence=frozenset(LOW_CONFIDENCE))
    if _nxt:
        top_block["_next_run"] = _nxt
    for _sp in cfg.species:
        _rk = top_block.get(_sp.id, {}).get("ranked", [])
        if _rk:
            print(f"top {_sp.id}: " + ", ".join(f"{r['name'].split('—')[0].strip()}={r['score']}" for r in _rk[:3]))

    now = datetime.now(timezone.utc)
    # Age is measured from the ACTUAL cycle hour we read, not a hardcoded noon: on a t00z or t06z
    # build a noon assumption would report the map as 12-18 h YOUNGER than it is, which is exactly
    # the direction rule 5 forbids (staleness must be loud, never flattering).
    _cyc_h = _CYCLE_HOUR[cycle]
    _issued = datetime(issue.year, issue.month, issue.day, _cyc_h, tzinfo=timezone.utc)
    age_h = (now - _issued).total_seconds() / 3600
    manifest = {
        "issue": issue.isoformat(), "issued_utc": _issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lsofs_cycle": cycle,
        "built_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        # age_h/stale are the BUILD-TIME values; the page recomputes age client-side
        # from issued_utc so a dead build can't display a frozen age (AUDIT_ROUND3)
        "age_h": round(age_h, 1), "stale": age_h > 30,
        "target_c": cfg.product.target_c, "cast_m": cfg.product.cast_m,
        "max_reach_depth_m": cfg.product.max_reach_depth_m,
        # ACTUAL isotherm targets driving the shading = species band endpoints +
        # CLAMP_EXTEND (4/18 °C taper rails), same tuple _overlay() interpolates on.
        # Report the real set so cold/warm tapering is auditable (was under-reporting
        # band_temps only, hiding the 18 °C warm rail — validation T1a).
        "targets_c": list(_clamp_targets(cfg.band_temps)),
        "species": [{"id": sp.id, "name": sp.name, "range_c": list(sp.range_c),
                     "optimal_c": list(sp.optimal), "front_c": sp.front_c,
                     "temp_cue": sp.temp_cue, "default": sp.default, "note": sp.note,
                     "min_depth_m": sp.min_depth_m, "max_depth_m": sp.max_depth_m}
                    for sp in cfg.species],
        "nearshore_delta_by_class": {c: {"delta_c": d, "n": n} for c, (d, n) in ns_by.items()},
        "suit_levels": [{"tag": t, "label": lb} for t, lb in SUIT_LEVELS],
        "struct_levels": [{"tag": t, "label": lb} for t, lb in STRUCT_LEVELS],
        "combine": "BIVARIATE display (ADR-038/039): cited temperature bands as a moving wash (s1 range / s2 optimal) + STATIC measured-structure ramp (g75..g99 = regional strength p75..p99, lead-independent) in separate visual channels — no fusion math; ranking uses boolean intersections at the p90/p95/p99 break bars only",
        "favor_calibrated": bool(favor_calib),  # False = physics prior (see suitability.py)
        "n_leads": len(LEADS),
        "bias": {"central": round(central, 1), "lo": round(lo, 1), "hi": round(hi, 1),
                 "n": n, "source": bias_src},
        "forecast_error": fc_err,   # MEASURED isotherm-depth MAE + lead-trend verdict (or None)
        "stretches": stretches_out, "stations": stations, "wind": wind,
        "phase": phase, "markers": markers_out,
        "season": season_block, "light": light_block, "barometric": baro_block,
        "top_spots": top_block,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {OUT/'manifest.json'} — {len(stretches_out)} stretches, {len(stations)} stations, "
          f"{len(wind)} wind-days, age {age_h:.0f} h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
