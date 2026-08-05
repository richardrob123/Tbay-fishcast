"""Optimize the LSOFS correction — test techniques against the constant-offset baseline
with LEAVE-ONE-BUOY-OUT cross-validation (the Thunder Bay transfer test: fit on some
Superior buoys, predict a NEW buoy the model never saw).

Candidate corrections (all fit on training buoys, evaluated on the held-out buoy):
  raw         : no correction (LSOFS as-is)
  offset      : + mean(buoy - lsofs)                    [1 param]
  linear      : a*lsofs + b   (a>1 amplifies LSOFS's under-sized upwelling)  [2 params]
  linear_wind : a*lsofs + b + c*favwindrun               [3 params]
  linear_doy  : a*lsofs + b + seasonal(doy)              [smooth seasonal]

Decision: a technique wins only if it beats BOTH raw and the constant offset on
leave-one-buoy-out MAE — i.e. it generalizes to an unseen location. Anomaly (timing)
skill is reported too, to confirm corrections don't damage the actionable signal.

    python scripts/optimize_correction.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
MID_DEPTH_MIN = 2.0  # the product-relevant band; 1 m is separately known to be unusable


def load() -> pd.DataFrame:
    fs = sorted((REPO / "data" / "calib").glob("*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    return df


def _fit_predict(kind, tr, te):
    x, y = tr["lsofs"].to_numpy(), tr["buoy"].to_numpy()
    xt = te["lsofs"].to_numpy()
    if kind == "raw":
        return xt
    if kind == "offset":
        return xt + np.mean(y - x)
    if kind == "linear":
        a, b = np.polyfit(x, y, 1)
        return a * xt + b
    if kind == "linear_wind":
        A = np.column_stack([x, tr["favwindrun"].to_numpy(), np.ones(len(x))])
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        At = np.column_stack([xt, te["favwindrun"].to_numpy(), np.ones(len(xt))])
        return At @ coef
    if kind == "linear_doy":
        a, b = np.polyfit(x, y, 1)
        resid = y - (a * x + b)
        doy = tr["doy"].to_numpy()
        # smooth seasonal residual: bin by 15-day, interpolate
        s = pd.Series(resid, index=doy).groupby(level=0).mean()
        season = s.reindex(range(int(doy.min()), int(doy.max()) + 1)).interpolate().rolling(
            15, min_periods=1, center=True).mean()
        corr = np.array([season.get(int(d), 0.0) for d in te["doy"].to_numpy()])
        return a * xt + b + np.nan_to_num(corr)
    raise ValueError(kind)


def loo_buoy(df, kinds):
    buoys = sorted(df["station"].unique())
    rows = []
    for kind in kinds:
        maes, coefs = [], []
        for held in buoys:
            tr = df[df.station != held]
            te = df[df.station == held]
            if len(te) < 20 or len(tr) < 20:
                continue
            pred = _fit_predict(kind, tr, te)
            maes.append(float(np.mean(np.abs(pred - te["buoy"].to_numpy()))))
            if kind == "linear":
                a, b = np.polyfit(tr["lsofs"], tr["buoy"], 1)
                coefs.append(a)
        note = f" (mean slope a={np.mean(coefs):.2f})" if coefs else ""
        rows.append((kind, np.mean(maes), maes, note))
    return rows


def anomaly_skill(df, kind):
    """Timing skill preserved after correction (per buoy, then averaged)."""
    rs = []
    for st, g in df.groupby("station"):
        g = g.sort_values("t")
        pred = _fit_predict(kind, g, g)  # in-sample ok: anomaly is detrended
        win = 60
        pa = pd.Series(pred).rolling(win, center=True, min_periods=win // 2).mean()
        ba = g["buoy"].rolling(win, center=True, min_periods=win // 2).mean().to_numpy()
        pan = pred - pa.to_numpy(); ban = g["buoy"].to_numpy() - ba
        m = ~(np.isnan(pan) | np.isnan(ban))
        if m.sum() > 5:
            rs.append(np.corrcoef(pan[m], ban[m])[0, 1])
    return float(np.nanmean(rs))


def main() -> int:
    df = load()
    mid = df[df["depth"] >= MID_DEPTH_MIN].copy()
    print(f"loaded {len(df)} rows; mid-depth (>= {MID_DEPTH_MIN} m): {len(mid)} rows "
          f"from buoys {sorted(mid.station.unique())} depths {sorted(mid.depth.unique())}")
    kinds = ["raw", "offset", "linear", "linear_wind", "linear_doy"]
    print("\n=== LEAVE-ONE-BUOY-OUT CV (mid-depth) — the Thunder Bay transfer test ===")
    print(f"{'correction':14s} {'LOO MAE':>8s}  per-buoy")
    base = None
    for kind, mae, maes, note in loo_buoy(mid, kinds):
        if kind == "offset":
            base = mae
        ar = anomaly_skill(mid, kind)
        print(f"{kind:14s} {mae:8.2f}  {[round(m,2) for m in maes]}  anomR={ar:+.2f}{note}")
    print(f"\nBaseline to beat = constant offset ({base:.2f}). A technique is worth adopting")
    print("only if it beats BOTH raw and offset on LOO MAE (generalizes to a new shore).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
