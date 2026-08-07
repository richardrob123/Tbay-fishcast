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
    {"id": "kam", "name": "Kaministiquia R. mouth", "lat": 48.383, "lon": -89.246,
     "note": "Delta channels + warm plume — prime chinook/coho staging (late summer–fall). "
             "Temperature is a minor cue here; fish the plume edge and current seams.",
     "species": ["salmon", "steelhead"]},
    {"id": "current", "name": "Current R. mouth", "lat": 48.455, "lon": -89.185,
     "note": "North-shore plume below Boulevard Lake — steelhead/salmon staging; lake trout "
             "hold where the plume meets the cold shelf.",
     "species": ["steelhead", "salmon", "lake_trout"]},
    {"id": "neebing", "name": "Neebing–McIntyre Floodway mouth", "lat": 48.373, "lon": -89.237,
     "note": "Mission Marsh outlet — plume + forage; secondary structure to the Kam delta.",
     "species": ["salmon", "steelhead"]},
]

OUT = Path(__file__).resolve().parents[1] / "web" / "data"
FAVOR_CALIB = Path(__file__).resolve().parents[1] / "data" / "calib" / "upwelling_favorability.json"


def _nearshore_delta():
    """Measured shore-minus-GLSEA surface warm-delta (°C) and its n. Delegates to the SHARED
    features.nearshore helper so the map and the station-pin path apply the identical correction
    (validation finding #1 — they had diverged). See that module for provenance."""
    from tbay_fishcast.features.nearshore import nearshore_surface_delta
    return nearshore_surface_delta()


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
    if not d.get("n"):
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
    if not d.get("calibrated"):
        print(f"upwelling-favorability: prior in use (calib {d.get('reason','absent')})")
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

    # Observed airport wind reads ~3 kn low vs over-lake, so day-0 uses the airport bar (10 kn);
    # the forecast tail is the ensemble control (already over-lake) so it uses the true over-lake
    # Wedderburn bar (~13 kn) — otherwise a modest 10-12 kn forecast wind over-detects a blow.
    obs_runs = up.extract_runs(o_t, o_d, o_s, threshold_kn=up.OBSERVED_THRESHOLD_KN) if o_t else []
    all_runs = up.extract_runs(st_t, st_d, st_s, threshold_kn=FORECAST_THRESHOLD_KN)

    by_lead = {}
    for lead, valid in sorted(lead_valid.items()):
        if lead == 0 and obs:
            ph = up.classify_at(obs_runs, valid, source=f"observed:{metar.CYQT}",
                                confidence="high" if len(o_t) >= 24 else "med")
        else:
            src = "forecast:gfs025" if not (lead == 0 and obs) else f"observed:{metar.CYQT}"
            ph = up.classify_at(all_runs, valid, source=src,
                                confidence="med" if lead <= 48 else "low")
        by_lead[str(lead)] = ph.as_dict()

    return {"now": by_lead.get("0"), "by_lead": by_lead,
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
    if sentinel.any():
        # fraction of surrounding nodes that never reach the target; >0.5 => below-bottom here
        frac = _grid(pts, sentinel.astype(float))
        iso = np.where(frac > 0.5, 999.0, iso)
    return iso


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
    # CLAMP: water shallower than the warmest isotherm pins to the warmest T; deeper than the
    # coldest pins to the coldest T. This is a floor/ceiling, NOT interpolation — genuinely colder
    # or warmer water reads as exactly the extreme target, so the extreme targets must sit OUTSIDE
    # every species band (CLAMP_EXTEND adds 4 °C / 18 °C) or cold water would pin to a species'
    # optimum and over-shade (validation T1a). Callers pass the extended target set.
    b = np.where(np.isnan(b) & np.isfinite(D[0]) & (depth < D[0]), T[0], b)
    b = np.where(np.isnan(b) & np.isfinite(D[-1]) & (depth > D[-1]), T[-1], b)
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
SUIT_LEVELS = [
    ("s1", "in range"),      # fair: acceptable temperature (range_c)
    ("s2", "optimal temp"),  # good: optimal-temperature core (optimal_c), little structure
    ("s3", "break"),         # optimal temp + a real measured break (regional structure p90)
    ("s4", "strong break"),  # optimal temp + strong structure (p95)
    ("s5", "top break"),     # optimal temp + exceptional structure (p99) — the best spots, rare
]
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
        sb = d.get("strength_bands", {})
        return (float(d["struct_slope_abs"]), float(d["struct_relief_abs_m"]),
                float(sb["break"]), float(sb["strong"]), float(sb["exceptional"]))
    except (OSError, ValueError, KeyError) as e:  # noqa: BLE001
        print(f"⚠ bathy_slope.json unreadable ({e}) — using fallback structure bars")
        return (0.162, 1.55, 1.45, 2.06, 3.68)


(STRUCT_SLOPE_ABS, STRUCT_RELIEF_ABS,
 STRENGTH_BREAK, STRENGTH_STRONG, STRENGTH_TOP) = _load_struct_calib()

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


def _species_tiers(bottom_c, strength, within, depth, sp, prod):
    """Pure, testable core of the per-species render: apply the DEPTH gate, grade TEMPERATURE into
    the cited bands (fair=range, good=optimal plateau), then split the optimal zone by CONTINUOUS
    structure STRENGTH into the data-derived glow bands. Returns (disjoint tiers dict, fair mask).

    Kept a standalone function so the depth gate (adult lakers out of <4 m — the marina over-cover
    fix) and the strength banding can be unit-tested without the full LSOFS/geometry pipeline."""
    import numpy as _np
    from tbay_fishcast.features import suitability as _su
    lo_d = sp.min_depth_m if sp.min_depth_m is not None else 0.0
    hi_d = sp.max_depth_m if sp.max_depth_m is not None else prod.max_reach_depth_m
    within_sp = within & (depth >= lo_d) & (depth <= hi_d)     # per-species depth band from shore
    suit = _su.thermal_suitability(bottom_c, sp.range_c, sp.optimal)
    suit = _np.where(within_sp, suit, 0.0)
    fair = suit > IN_RANGE_SUIT                                # inside the preferred range (range_c)
    good = suit >= OPTIMAL_PLATEAU                             # the optimal-core plateau (optimal_c)
    g_break = good & (strength >= STRENGTH_BREAK)             # a real break (regional p90)
    g_strong = good & (strength >= STRENGTH_STRONG)           # strong structure (p95)
    g_top = good & (strength >= STRENGTH_TOP)                 # exceptional — the best breaks (p99)
    # DISJOINT tiers (each pixel painted once): temperature base (s1/s2) + 3 structure-glow bands.
    tiers = {"s1": fair & ~good, "s2": good & ~g_break,
             "s3": g_break & ~g_strong, "s4": g_strong & ~g_top, "s5": g_top}
    return tiers, fair


def _overlay(depth, dist, gx, gy, inbox, node_xy, cols, g_sst, bias, bounds_3857, res, prod,
             targets=(), species=(), own_m=None, others_m=()):
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
        return None, 0.0
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

    area_feats = []
    reach_px_primary = 0.0

    def _emit(geom, tag):
        for gg in _polys(geom):
            rings = [[list(c) for c in gg.exterior.coords]]
            rings += [[list(c) for c in r.coords] for r in gg.interiors]
            area_feats.append((tag, rings))

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
    within = water & (dist <= prod.cast_m) & (depth <= prod.max_reach_depth_m)
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
    slope = _su.bathymetric_structure(depth, res)            # rise/run
    relief = _su.bathymetric_relief(depth, res)              # m, shallower-than-surroundings
    with np.errstate(invalid="ignore"):
        s_norm = np.where(np.isfinite(slope), slope / STRUCT_SLOPE_ABS, 0.0)
        r_norm = np.where(np.isfinite(relief), relief / STRUCT_RELIEF_ABS, 0.0)
    strength = np.maximum(s_norm, r_norm)                    # continuous structure strength

    default_id = None
    for sp in species:
        tiers, fair = _species_tiers(bottom_c, strength, within, depth, sp, prod)
        # WEAK-CUE species (salmon/steelhead) are plume/season driven — temperature AND structure
        # barely locate them — so the DATA does not emit the confident structure glow for them
        # (honesty in the artifact, not just the UI hiding it; a non-web consumer sees the truth).
        weak = getattr(sp, "temp_cue", "strong") == "weak"
        emit_tags = ["s1", "s2"] if weak else [t for t, _ in SUIT_LEVELS]
        emitted_any = False
        for tag in emit_tags:
            mask = tiers[tag]
            if mask.any():
                _emit(_shape(mask), f"sp:{sp.id}:{tag}")
                emitted_any = True
        if not emitted_any:
            continue
        if sp.default or (default_id is None and sp is species[0]):
            reach_px_primary = float(fair.sum())             # total-zone (in-range) area
            default_id = sp.id
    return area_feats, reach_px_primary


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

    central, lo, hi, _, n, bias_src = bias_live.pooled_or_prior(cfg, issue)
    bias = (central, lo, hi, n)
    if bias_src != "live":
        print("⚠ live buoy bias unavailable — frozen prior in use (degraded mode)")

    stretches_out = []
    lead_valid: dict[int, str] = {}   # region-wide lead -> valid_utc (all stretches share the cycle)
    centers_m = [merc(la, lo) for (_sid, _nm, la, lo, _ex) in stretches_in]  # for the territory clip
    ns_delta, ns_n = _nearshore_delta()   # measured shore-minus-GLSEA surface warm-delta
    if ns_delta > 0:
        print(f"nearshore surface delta +{ns_delta:.2f} C applied to anchor (Landsat n={ns_n})")
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
        inbox, node_xy = _node_columns_in_box(cfg, issue, clat, clon)
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
        if g_sst is not None and ns_delta > 0:
            g_sst = g_sst + ns_delta
        if px and px.day != issue.isoformat():
            print(f"    {sid}: GLSEA anchor {px.sst_c:.1f}C from {px.day} (issue not yet posted)")

        # corner lon/lats for the MapLibre image source (TL, TR, BR, BL)
        tl = _merc_to_ll(x0, y1); tr = _merc_to_ll(x1, y1)
        br = _merc_to_ll(x1, y0); bl = _merc_to_ll(x0, y0)
        corners = [list(tl), list(tr), list(br), list(bl)]

        days = []
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
            area, reach_px = _overlay(depth, dist, gx, gy, inbox, node_xy,
                                      cols, g_sst, bias, patch.bounds_3857, res,
                                      cfg.product, targets=_clamp_targets(cfg.band_temps),
                                      species=cfg.species,
                                      own_m=centers_m[si],
                                      others_m=[c for j, c in enumerate(centers_m) if j != si])
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
        if not days:
            print(f"skip {sid}: no frames"); continue
        if not lead_valid:
            lead_valid = {int(d["lead"]): d["valid_utc"] for d in days}
        (OUT / "areas").mkdir(exist_ok=True)
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
                              "area": f"data/areas/{sid}.geojson"})
        print(f"  {sid}: {len(days)} days, {len(inbox)} nodes, res {res:.0f} m, "
              f"ha {days[0]['reach_ha']}..{max(d['reach_ha'] for d in days)}")

    if not stretches_out:
        print("no stretches produced"); return 1

    # per-station verdicts (reuse the per-spot forecast)
    stations = []
    for s in cfg.shore_stations:
        if not _regs_ok(s.name):        # regs gate on the station recommendation surface
            continue
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
    except Exception as e:  # noqa: BLE001
        print(f"phase unavailable: {str(e)[:60]}")

    # seasonal regime context (research-backed direction; the zones are a SUMMER model)
    from tbay_fishcast.features import season as _season
    reg = _season.regime(issue)
    season_block = {"season": reg.season, "label": reg.label,
                    "upwelling": reg.upwelling, "note": reg.note}

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
                     "optimal_c": list(sp.optimal), "front_c": sp.front_c,
                     "temp_cue": sp.temp_cue, "default": sp.default, "note": sp.note,
                     "min_depth_m": sp.min_depth_m, "max_depth_m": sp.max_depth_m}
                    for sp in cfg.species],
        "nearshore_delta_c": round(ns_delta, 2), "nearshore_delta_n": ns_n,
        "suit_levels": [{"tag": t, "label": lb} for t, lb in SUIT_LEVELS],
        "combine": "cited temperature bands (fair=range, good=optimal) × CONTINUOUS measured structure glow (strength = max slope/relief, normalized to regional p90; glow bands at regional p90/p95/p99) — conjunction, no fitted weights",
        "favor_calibrated": bool(favor_calib),  # False = physics prior (see suitability.py)
        "n_leads": len(LEADS),
        "bias": {"central": round(central, 1), "lo": round(lo, 1), "hi": round(hi, 1),
                 "n": n, "source": bias_src},
        "forecast_error": fc_err,   # MEASURED isotherm-depth MAE + lead-trend verdict (or None)
        "stretches": stretches_out, "stations": stations, "wind": wind,
        "phase": phase, "markers": [m for m in RIVER_MOUTHS if _regs_ok(m["name"])],
        "season": season_block,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {OUT/'manifest.json'} — {len(stretches_out)} stretches, {len(stations)} stations, "
          f"{len(wind)} wind-days, age {age_h:.0f} h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
