"""G2 scorecard — upwelling-event POD (primary) / FAR (characterization).

Executes the pre-registered protocol (docs/G2_PREREGISTRATION.md):
  TUNE on 2024 (regulargrid 6 m): set PERSIST_H and calibrate the GLSEA differential
    truth threshold DG_C from the 6 m->surface coupling; DROP_C=4 C is the PLAN event
    definition (not free-tuned). Lock.
  VALIDATE on 2025 (+partial 2026) held out (fields 6 m): detect with locked thresholds,
    build the INDEPENDENT GLSEA differential truth, match EVENT-based per station, pool.
  POD is the gate; FAR is reported as caveated characterization (GLSEA blindness).
  Plus: no-skill random-alarm baseline; ERA5 wind corroboration subset.

    python scripts/g2_scorecard.py

Reads bronze 6 m parquet produced by scripts/backfill_6m_season.py for 2024/2025/2026.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.ingest import glsea  # noqa: E402
from tbay_fishcast.ingest.era5_wind import fetch_wind  # noqa: E402
from tbay_fishcast.features.wind import wind_consistent  # noqa: E402
from tbay_fishcast.verification.g2 import (  # noqa: E402
    cluster_episodes, detect_onsets, match_episodes,
)
from tbay_fishcast.verification.scorecard import Contingency  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DROP_C = 4.0          # PLAN event magnitude (6 m cooling)
# Detection window CALIBRATED on 2024 tune data: the 6 m upwelling signal develops on a
# ~48 h timescale (matching the seed's ~40 h internal seiche), NOT the 24 h that PLAN
# assumed for the fast surface signal. At 24 h the exposed nodes fire ~0 events (max
# 24 h-drop 3.7-4.5 C); at 48 h the drops are 5-6 C. This window is a tuned parameter.
WINDOW_H = 48.0
TRUTH_WINDOW_H = 48.0
TAU_DAYS = 1
MERGE_GAP = 1
# Offshore-basin reference pixel for the differential truth (open Superior SE of TBay).
REF_LAT, REF_LON = 48.05, -87.70
EXPOSED = ["silver_harbour_outer", "mackenzie_point", "marina_east_mcvicar"]


def load_6m(year: int) -> dict[str, tuple[list, np.ndarray]]:
    p = REPO / "data" / "bronze" / "g2_6m" / f"year={year}" / "temps.parquet"
    if not p.exists():
        return {}
    df = pd.read_parquet(p)
    df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True)
    out = {}
    for sid, g in df.sort_values("valid_time").groupby("station_id"):
        out[sid] = (list(g["valid_time"].dt.to_pydatetime()), g["temp_c"].to_numpy())
    return out


def detected(series, drop_c, persist_h):
    out = {}
    for sid, (t, y) in series.items():
        onsets = detect_onsets(t, y, drop_c=drop_c, persist_h=persist_h, window_h=WINDOW_H)
        out[sid] = cluster_episodes(onsets, MERGE_GAP)
    return out


def _glsea_pixels(cfg):
    """Resolve one valid station pixel + the offshore reference pixel (clear summer day)."""
    day = "2025-08-01"
    pix = {}
    for s in cfg.shore_stations:
        px = glsea.fetch_sst(s.node_lat, s.node_lon, day)
        pix[s.id] = (px.pixel_lat, px.pixel_lon)
    ref = glsea.fetch_sst(REF_LAT, REF_LON, day)
    return pix, (ref.pixel_lat, ref.pixel_lon)


def truth_episodes(start, end, pix, ref_pixel, dg_c):
    """GLSEA differential truth episodes over [start,end] (ISO). Also returns the max
    48 h differential drop per station (the 'did GLSEA even move?' blindness metric)."""
    ref = glsea.fetch_series(ref_pixel[0], ref_pixel[1], start, end)
    out, maxdrop = {}, {}
    for sid, (plat, plon) in pix.items():
        s = glsea.fetch_series(plat, plon, start, end)
        days = sorted(set(s) & set(ref))
        if not days:
            out[sid] = []; maxdrop[sid] = float("nan"); continue
        times = [pd.Timestamp(d).to_pydatetime() for d in days]
        diff = np.array([s[d] - ref[d] for d in days])
        # blindness metric: biggest 48h (2-day) differential cooling all season
        md = max((diff[max(0, i - 2):i + 1].max() - diff[i]) for i in range(len(diff)))
        maxdrop[sid] = float(md)
        onsets = detect_onsets(times, diff, drop_c=dg_c, persist_h=0, window_h=TRUTH_WINDOW_H)
        out[sid] = cluster_episodes(onsets, MERGE_GAP)
    return out, maxdrop


def wind_corroboration(series, start, end):
    """Physical-consistency check: does 6 m temperature respond to favorable
    (west-quadrant) wind-run as upwelling requires? Reports the season correlation
    corr(trailing-48h favorable wind-run, 6 m temp) per station — expected NEGATIVE
    (more upwelling-favorable wind -> colder 6 m). This survives GLSEA's blindness.

    Caveat: LSOFS is wind-FORCED, so this confirms the model produces physically
    sensible wind-driven upwelling, NOT that the events match the real lake (only the
    in-situ logger can, ADR-019)."""
    from tbay_fishcast.features.wind import favorable_wind_run
    try:
        h = fetch_wind(start, end)
    except Exception:  # noqa: BLE001
        return None
    wt = pd.DatetimeIndex([pd.Timestamp(t, tz="UTC") for t in h["time"]])
    spd = np.asarray(h["wind_speed_10m"], float); dr = np.asarray(h["wind_direction_10m"], float)
    run = pd.Series(favorable_wind_run([t.to_pydatetime() for t in wt], spd, dr,
                                       window_h=48.0), index=wt)
    corrs = {}
    for sid, (t, y) in series.items():
        ti = pd.DatetimeIndex([pd.Timestamp(x) for x in t])
        wr = np.array([run.asof(x) for x in ti])
        m = ~np.isnan(wr)
        corrs[sid] = float(np.corrcoef(wr[m], np.asarray(y)[m])[0, 1]) if m.sum() > 2 else float("nan")
    return corrs


def calibrate_dg(cfg, pix, ref_pixel) -> float:
    """Coupling calibration on 2024: regress GLSEA differential 48 h drop against the
    LSOFS 6 m 24 h drop on matched days, set DG_C = slope * DROP_C (bounded [1.0, 3.0]).
    Falls back to 2.5 C if the fit is degenerate. Physics, not agreement-maximizing."""
    s24 = load_6m(2024)
    if not s24:
        return 2.5
    start, end = "2024-06-15", "2024-09-30"
    ref = glsea.fetch_series(ref_pixel[0], ref_pixel[1], start, end)
    drops_6m, drops_surf = [], []
    for sid, (plat, plon) in pix.items():
        if sid not in s24:
            continue
        gl = glsea.fetch_series(plat, plon, start, end)
        t, y = s24[sid]
        # daily min 6m and its trailing 24h drop
        df = pd.DataFrame({"vt": pd.to_datetime(t, utc=True), "temp": y})
        df["day"] = df["vt"].dt.date
        daily6 = df.groupby("day")["temp"].mean()
        for d in daily6.index:
            iso = d.isoformat()
            if iso not in gl or iso not in ref:
                continue
            surf_diff = gl[iso] - ref[iso]
            # crude paired signal: use anomaly vs each series' own median
            drops_6m.append(daily6[d] - daily6.median())
            drops_surf.append(surf_diff - np.median([gl[k] - ref[k]
                              for k in sorted(set(gl) & set(ref))]))
    if len(drops_6m) < 10 or np.std(drops_6m) == 0:
        return 2.5
    slope = float(np.polyfit(drops_6m, drops_surf, 1)[0])
    dg = float(np.clip(abs(slope) * DROP_C, 1.0, 3.0))
    return round(dg, 2)


def score(detected_eps, truth_eps):
    hits = misses = fa = 0
    per = {}
    for sid in EXPOSED:
        m = match_episodes(truth_eps.get(sid, []), detected_eps.get(sid, []), TAU_DAYS)
        per[sid] = m
        hits += m.hits; misses += m.misses; fa += m.false_alarms
    return hits, misses, fa, per


def no_skill_pod(detected_eps, truth_eps, season_days=107):
    """Analytic no-skill POD: if a station's `ndet` alarms were placed uniformly at
    random over the season, the chance a given truth event is hit within +-tau days is
    p = 1 - (1 - (2*tau+1)/season_days)**ndet. Expected pooled POD = sum(n_truth*p) /
    sum(n_truth). This is what the detector's POD must beat to be more than luck."""
    win = (2 * TAU_DAYS + 1) / season_days
    exp_hits = n_truth = 0.0
    for sid in EXPOSED:
        ndet = len(detected_eps.get(sid, []))
        nt = len(truth_eps.get(sid, []))
        if nt == 0:
            continue
        p = 1.0 - (1.0 - win) ** ndet
        exp_hits += nt * p
        n_truth += nt
    return float(exp_hits / n_truth) if n_truth else float("nan")


def main() -> int:
    cfg = load_config()
    print("Resolving GLSEA pixels (station + offshore reference)...")
    pix, ref_pixel = _glsea_pixels(cfg)
    glsea_end = glsea.coverage_end()
    print(f"  reference pixel: {ref_pixel} | GLSEA coverage end: {glsea_end}")

    # --- TUNE on 2024 ---
    s24 = load_6m(2024)
    if not s24:
        print("no 2024 bronze — run scripts/backfill_6m_season.py 2024", file=sys.stderr)
        return 2
    print("\n== TUNE 2024 ==")
    for persist_h in (6, 12, 18):
        d = detected(s24, DROP_C, persist_h)
        counts = {sid: len(d[sid]) for sid in EXPOSED}
        print(f"  PERSIST_H={persist_h:2d}h -> detected episodes/station {counts} "
              f"(target 2-5/season)")
    PERSIST_H = 12  # locked: removes sub-12h spikes, keeps sustained upwelling
    DG_C = calibrate_dg(cfg, pix, ref_pixel)
    print(f"  LOCKED: DROP_C={DROP_C} PERSIST_H={PERSIST_H} DG_C={DG_C} "
          f"(coupling-calibrated)")

    # --- VALIDATE on 2025 (+2026 partial) ---
    for year in (2025, 2026):
        s = load_6m(year)
        if not s:
            print(f"\n(no {year} bronze — skipping)")
            continue
        # align the truth window to the actual 6 m data coverage (clamps 2026 to today)
        start = min(min(t) for t, _ in s.values()).date().isoformat()
        end = min(max(max(t) for t, _ in s.values()).date().isoformat(), glsea_end)
        det = detected(s, DROP_C, PERSIST_H)
        tru, blind = truth_episodes(start, end, pix, ref_pixel, DG_C)
        hits, misses, fa, per = score(det, tru)
        c = Contingency(hits, misses, fa, correct_neg=0)
        wind = wind_corroboration(s, start, end)
        n_truth = sum(len(tru[sid]) for sid in EXPOSED)
        n_det = sum(len(det[sid]) for sid in EXPOSED)
        print(f"\n== VALIDATE {year} ({start}..{end}, held out) ==")
        for sid in EXPOSED:
            print(f"  {sid:22s} detected={len(det[sid])} GLSEA-truth={len(tru[sid])} "
                  f"(max 48h GLSEA-diff drop {blind[sid]:.1f}C)")
        if n_truth == 0:
            print(f"  GLSEA witnessed 0 events (max diff-drop < {DG_C}C all season) — "
                  f"POD UNDEFINED. G2 vs satellite = INCONCLUSIVE (satellite blind to 6m "
                  f"upwelling; ADR-019 logger required).")
        else:
            ns = no_skill_pod(det, tru)
            print(f"  POOLED hits={hits} miss={misses} FA={fa} | POD={c.pod:.2f} "
                  f"(no-skill {ns:.2f}) | FAR={c.far:.2f} [characterization]")
            print(f"  G2 POD gate (>=0.70): "
                  f"{'PASS' if c.pod >= 0.70 else 'FAIL'}")
        if wind:
            cs = ", ".join(f"{sid.split('_')[0]}={wind[sid]:+.2f}" for sid in EXPOSED)
            mean_r = np.nanmean([wind[sid] for sid in EXPOSED])
            print(f"  WIND physical-consistency corr(favWindRun, 6mTemp): {cs} "
                  f"(mean {mean_r:+.2f}; negative = upwelling physics present in LSOFS)")
    print("\nNote: FAR is characterization not gate (ADR-019/critique). POD (when defined) "
          "conditional on LSOFS not assimilating GLSEA-SST (T4).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
