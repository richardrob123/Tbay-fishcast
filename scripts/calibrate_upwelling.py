"""Calibrate the upwelling-favorability response from OBSERVED wind↔cooling — not a guess.

The map's upwelling signal must not lean on an arbitrary "≥13 kt or nothing" cutoff. Here we
FIT the continuous response (features/suitability.upwelling_favorability) to real in-situ data:
NDBC western-Lake-Superior buoys report hourly wind AND surface water temperature going back
years. A sustained west-quadrant blow drives Ekman upwelling → surface WTMP drops within ~a
day (Li et al. 2021, r=-0.87). So we:

  1. pull buoy stdmet history for stratified-season months (upwelling cooling is detectable
     against a warm surface layer),
  2. for each sampled window, measure the sustained favorable-wind speed and whether a
     significant surface cooling (>= COOL_C over the next COOL_LAG_H) followed,
  3. fit a logistic P(cooling | wind speed) by maximum likelihood, and pin s50 (speed at 50 %
     cooling probability) and width to data/calib/upwelling_favorability.json with provenance.

The build reads that file; when it is absent (or the fit is too thin) the physics PRIOR in
suitability.py is used and the degraded state is stated loudly (CLAUDE rule 5). This is the
buoy-water response (offshore western Superior) used as the physical calibrator; the north-
shore nearshore magnitude will differ, which the field logs will later refine. No LLM (ADR-001).

    python scripts/calibrate_upwelling.py [--years 2024 2025] [--buoys 45027 45028]
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.features.wind import in_sector  # noqa: E402

STDMET = "https://www.ndbc.noaa.gov/data/historical/stdmet/{station}h{year}.txt.gz"
MS_TO_KN = 1.943844
OUT = Path(__file__).resolve().parents[1] / "data" / "calib" / "upwelling_favorability.json"

# event definition (physical, fixed a-priori so the FIT is of the response curve, not of these):
SETUP_H = 12.0        # trailing window over which a sustained blow sets up upwelling
COOL_LAG_H = 24.0     # cooling shows up within ~a day of the blow
COOL_C = 2.0          # a "significant" surface cooling event (°C drop)
FAV_FRAC = 0.6        # >= this fraction of the setup window must be in the west quadrant
SAMPLE_H = 6          # sample every 6 h to limit autocorrelation between overlapping windows
MONTHS = (6, 7, 8, 9)  # stratified season


def _fetch(station: str, year: int) -> list[tuple[datetime, float, float, float]]:
    """(time, wdir_deg, wspd_kn, wtmp_c) hourly-ish rows for one buoy-year, missing dropped."""
    url = STDMET.format(station=station, year=year)
    r = requests.get(url, timeout=90)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    rows = []
    for line in gzip.decompress(r.content).decode(errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split()
        if len(p) < 15:
            continue
        try:
            t = datetime(int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]), tzinfo=timezone.utc)
            wdir, wspd, wtmp = float(p[5]), float(p[6]), float(p[14])
        except (ValueError, IndexError):
            continue
        if wdir > 360 or wspd >= 90 or wtmp >= 90 or wtmp <= -5:
            continue
        rows.append((t, wdir, wspd * MS_TO_KN, wtmp))
    return rows


def _hourly(rows):
    """Collapse to one sample per hour (first obs in the hour), sorted."""
    by_h = {}
    for t, wd, ws, wt in sorted(rows):
        key = t.replace(minute=0, second=0, microsecond=0)
        by_h.setdefault(key, (key, wd, ws, wt))
    return [by_h[k] for k in sorted(by_h)]


def build_samples(series) -> list[tuple[float, int]]:
    """(sustained_favorable_speed_kn, cooled?) for each sampled window with a favorable blow."""
    times = [s[0] for s in series]
    wd = np.array([s[1] for s in series])
    ws = np.array([s[2] for s in series])
    wt = np.array([s[3] for s in series])
    idx = {t: i for i, t in enumerate(times)}
    out = []
    for i in range(0, len(series), SAMPLE_H):
        t = times[i]
        if t.month not in MONTHS:
            continue
        # trailing setup window
        w0 = t - _hours(SETUP_H)
        win = [j for j in range(max(0, i - int(SETUP_H) - 4), i + 1) if times[j] >= w0]
        if len(win) < SETUP_H * 0.6:                       # need most of the window present
            continue
        favmask = in_sector(wd[win])
        if favmask.mean() < FAV_FRAC:                      # not a favorable-wind window
            continue
        v = float(ws[win][favmask].mean())                 # sustained favorable speed (kn)
        # cooling over the next COOL_LAG_H
        tgt = t + _hours(COOL_LAG_H)
        js = [idx[k] for k in times if t < k <= tgt]
        if not js:
            continue
        drop = wt[i] - float(np.min(wt[js]))               # max cooling reached in the lag window
        out.append((v, 1 if drop >= COOL_C else 0))
    return out


def _hours(h):
    from datetime import timedelta
    return timedelta(hours=h)


def fit_logistic(samples):
    """MLE fit of P(cool)=sigmoid((v-s50)/width). Returns (s50, width, n, pos, auc)."""
    from scipy.optimize import minimize
    v = np.array([s[0] for s in samples], dtype=float)
    y = np.array([s[1] for s in samples], dtype=float)
    n, pos = len(y), int(y.sum())

    def nll(theta):
        a, b = theta                       # logit = a + b v
        z = a + b * v
        # stable log-likelihood
        ll = y * z - np.logaddexp(0.0, z)
        return -float(ll.sum())

    # init from a rough guess: b>0 (more wind -> more cooling)
    res = minimize(nll, x0=[-3.0, 0.25], method="Nelder-Mead",
                   options={"xatol": 1e-4, "fatol": 1e-4, "maxiter": 5000})
    a, b = res.x
    if b <= 1e-6:                          # degenerate (no positive wind response) — reject
        return None
    s50 = -a / b
    width = 1.0 / b
    auc = _auc(v, y)
    return s50, width, n, pos, auc


def _auc(scores, y):
    order = np.argsort(scores)
    y = y[order]
    pos, neg = y.sum(), (1 - y).sum()
    if pos == 0 or neg == 0:
        return float("nan")
    ranks = np.arange(1, len(y) + 1)
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="+", type=int, default=[2023, 2024, 2025])
    ap.add_argument("--buoys", nargs="+", default=["45027", "45028"])
    a = ap.parse_args(argv)

    allrows = []
    for st in a.buoys:
        for yr in a.years:
            try:
                rows = _fetch(st, yr)
            except requests.RequestException as e:
                print(f"  {st} {yr}: unreachable ({str(e)[:40]})")
                continue
            if rows:
                print(f"  {st} {yr}: {len(rows)} obs")
                allrows.append((st, yr, rows))

    samples = []
    for st, yr, rows in allrows:
        samples += build_samples(_hourly(rows))
    print(f"favorable-wind windows: {len(samples)} "
          f"(cooling events: {sum(s[1] for s in samples)})")

    def _write_prior(reason):
        """Record an auditable null result so 'we use the prior' is a logged finding, not silent."""
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "calibrated": False, "reason": reason,
            "n_windows": len(samples), "n_cooling_events": sum(s[1] for s in samples),
            "buoys": a.buoys, "years": a.years,
            "source": "NDBC stdmet historical (wind + surface WTMP)",
            "retrieved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "prior_s50_kn": None, "prior_width_kn": None,
            "note": "Offshore western-Superior buoys do NOT show the coastal-upwelling wind→"
                    "cooling response (surface WTMP corr with favorable wind speed ~0 / slightly "
                    "negative) — coastal upwelling is a NEARSHORE phenomenon these deep-water "
                    "buoys miss. The favorability curve therefore stays the physics prior "
                    "(Wedderburn ~12-17 kt; Li et al. 2021 r=-0.87). A nearshore logger or a "
                    "GLSEA north-shore-pixel analysis is the calibration path; documented gap "
                    "(docs/SUBSURFACE_SOURCES.md).",
        }, indent=1))
        print(f"↳ recorded null-calibration provenance to {OUT}")

    # THE EVENT COUNT IS THE SAMPLE SIZE, and this floor was 8 — low enough that an AUC of 0.62
    # is comfortably inside noise, in the one script whose output the PRODUCT reads. The repo
    # already owns the right number (favorability_fit.MIN_EVENTS, set at 25 after a held-out AUC
    # computed on seven events was nearly published); it was simply never applied here. Imported
    # rather than copied so the two cannot drift apart.
    from tbay_fishcast.features.favorability_fit import MIN_EVENTS
    n_events = sum(s[1] for s in samples)
    if len(samples) < 40 or n_events < MIN_EVENTS:
        print(f"⚠ too few observed events to fit ({n_events} events / {len(samples)} windows; "
              f"need {MIN_EVENTS} events) — keeping the physics prior (suitability.py)")
        _write_prior(f"insufficient_events({n_events}<{MIN_EVENTS})")
        return 1

    fit = fit_logistic(samples)
    if fit is None:
        print("⚠ degenerate fit (no positive wind→cooling response) — keeping the prior")
        _write_prior("no_positive_response_offshore_buoys")
        return 1
    s50, width, n, pos, auc = fit
    # ACCEPTANCE GUARD: only adopt a fit that actually discriminates (AUC well above chance)
    # AND is physically plausible (s50 in the Wedderburn-ish band, finite width). A numerically
    # valid but non-discriminating fit (AUC~0.5, absurd s50) is worse than the honest prior.
    if not (auc >= 0.62 and 6.0 <= s50 <= 25.0 and 0.5 <= width <= 12.0):
        print(f"⚠ fit does not discriminate / implausible (s50={s50:.1f}, width={width:.1f}, "
              f"AUC={auc:.3f}) — keeping the physics prior")
        _write_prior(f"fit_rejected(auc={auc:.3f},s50={s50:.1f},width={width:.1f})")
        return 1
    s50 = round(float(s50), 2)
    width = round(float(max(width, 0.5)), 2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "s50_kn": s50, "width_kn": width,
        "n_windows": n, "n_cooling_events": pos, "auc": round(float(auc), 3),
        "buoys": a.buoys, "years": a.years,
        "event_def": {"setup_h": SETUP_H, "cool_lag_h": COOL_LAG_H, "cool_c": COOL_C,
                      "fav_frac": FAV_FRAC, "months": list(MONTHS)},
        "source": "NDBC stdmet historical (wind + surface WTMP)",
        "retrieved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Fitted P(surface cooling >= cool_c within cool_lag_h | sustained favorable "
                "wind speed). Offshore-buoy response; nearshore magnitude refined by field logs.",
    }
    OUT.write_text(json.dumps(payload, indent=1))
    print(f"FIT s50={s50} kn width={width} kn  (n={n}, events={pos}, AUC={auc:.3f}) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
