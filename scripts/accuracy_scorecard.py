"""Honest accuracy scorecard from the standing observation logs — deterministic, no LLM, no
network (reads data/gate_log.csv + data/nearshore_anchor.csv, writes docs/ACCURACY_SCORECARD.md).

This is the back-test the audit points to ("re-score when n is respectable"). It reports what
can HONESTLY be claimed today and grows as the daily gate accumulates — it never tunes anything.

Isotherm-depth gate (the product's core number: depth of the 12 °C crossing):
  * corrected MAE (the shipped live correction) and raw-LSOFS MAE, per chain and pooled;
  * skill vs raw = 1 - MAE_corrected / MAE_raw  (does the correction earn its place? rule/ADR-006);
  * persistence baseline (yesterday's obs → today) where consecutive obs exist;
  * loud caveats: both chains are 170–270 km away, obs are a coarse/surface proxy for a 6 m
    target, and a chain whose obs is identical every day (sensor-pinned) is flagged, not hidden.

Nearshore anchor: Landsat 30 m shore skin-temp minus offshore GLSEA, same-day pairs.

    python scripts/accuracy_scorecard.py            # writes the scorecard, prints a summary
    python scripts/accuracy_scorecard.py --check     # exit 1 if the correction fails to beat raw
"""
from __future__ import annotations

import csv
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "data" / "gate_log.csv"
ANCHOR = ROOT / "data" / "nearshore_anchor.csv"
OUT = ROOT / "docs" / "ACCURACY_SCORECARD.md"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _mae(errs):
    errs = [e for e in errs if e is not None]
    return (sum(errs) / len(errs)) if errs else None


def load_gate():
    if not GATE.exists():
        return []
    with GATE.open() as f:
        return list(csv.DictReader(f))


def per_chain_stats(rows):
    """{chain: {n, raw_mae, corr_mae, frozen_mae, skill, persistence_mae, obs_constant}}."""
    by_chain: dict[str, list[dict]] = {}
    for r in rows:
        by_chain.setdefault(r["chain"], []).append(r)
    out = {}
    for chain, rs in by_chain.items():
        rs = sorted(rs, key=lambda r: r["date"])
        scored = [r for r in rs if _f(r["corr_iso_live_m"]) is not None
                  and _f(r["obs_iso_m"]) is not None]
        raw_errs, corr_errs, froz_errs = [], [], []
        for r in scored:
            obs = _f(r["obs_iso_m"])
            raw_errs.append(abs(_f(r["raw_iso_m"]) - obs) if _f(r["raw_iso_m"]) is not None else None)
            corr_errs.append(_f(r["abs_err_live_m"]))
            froz_errs.append(_f(r["abs_err_frozen_m"]))
        # persistence: |obs_today - obs_yesterday| over consecutive-day obs
        obs_by_day = {r["date"]: _f(r["obs_iso_m"]) for r in rs if _f(r["obs_iso_m"]) is not None}
        pers = []
        days = sorted(obs_by_day)
        for i in range(1, len(days)):
            d0, d1 = date.fromisoformat(days[i - 1]), date.fromisoformat(days[i])
            if (d1 - d0).days == 1:
                pers.append(abs(obs_by_day[days[i]] - obs_by_day[days[i - 1]]))
        obs_vals = [obs_by_day[d] for d in days]
        raw_mae, corr_mae = _mae(raw_errs), _mae(corr_errs)
        skill = (1 - corr_mae / raw_mae) if (raw_mae and corr_mae is not None and raw_mae > 0) else None
        out[chain] = {
            "n": len(scored), "raw_mae": raw_mae, "corr_mae": corr_mae,
            "frozen_mae": _mae(froz_errs), "skill": skill,
            "persistence_mae": _mae(pers),
            "obs_constant": len(set(obs_vals)) == 1 and len(obs_vals) > 1,
        }
    return out


def pooled(stats, key):
    # weight by n so pooled reflects the evidence, not the chain count
    num = sum(s[key] * s["n"] for s in stats.values() if s.get(key) is not None)
    den = sum(s["n"] for s in stats.values() if s.get(key) is not None)
    return (num / den) if den else None


def anchor_summary():
    if not ANCHOR.exists():
        return None
    with ANCHOR.open() as f:
        rows = list(csv.DictReader(f))
    deltas = [_f(r["delta_c"]) for r in rows if _f(r["delta_c"]) is not None]
    if not deltas:
        return None
    return {"n": len(deltas), "mean": statistics.mean(deltas),
            "lo": min(deltas), "hi": max(deltas)}


def _fmt(x, u="", nd=2):
    return f"{x:.{nd}f}{u}" if x is not None else "—"


def render(stats, anchor) -> str:
    L = ["# Accuracy scorecard (auto-generated — do not hand-edit)",
         "",
         "Honest, un-tuned back-test of the standing observation logs. Grows as the daily gate",
         "accumulates. **Caveats that bound every number below:** both isotherm chains are",
         "170–270 km from Thunder Bay (45216 Ontonagon, llo1 Duluth), and their obs are a",
         "coarse/surface proxy for the product's ~6 m target — so this tracks *trend and sign of",
         "skill*, not a Thunder Bay 6 m verification. Local truth (Bare Point intake) would replace",
         "these proxies.", ""]
    p_raw, p_corr = pooled(stats, "raw_mae"), pooled(stats, "corr_mae")
    p_skill = (1 - p_corr / p_raw) if (p_raw and p_corr is not None and p_raw > 0) else None
    n_tot = sum(s["n"] for s in stats.values())
    L += ["## Isotherm-depth (12 °C) gate", "",
          f"Scored day×chain rows: **{n_tot}**", "",
          "| chain | n | raw MAE | corrected MAE | skill vs raw | persistence MAE | note |",
          "|---|---|---|---|---|---|---|"]
    for chain, s in sorted(stats.items()):
        note = "obs sensor-pinned (constant) — persistence is trivially 0, discount it" if s["obs_constant"] else ""
        skill_s = f"{s['skill']*100:+.0f}%" if s["skill"] is not None else "—"
        L.append(f"| {chain} | {s['n']} | {_fmt(s['raw_mae'],' m')} | {_fmt(s['corr_mae'],' m')} "
                 f"| {skill_s} | {_fmt(s['persistence_mae'],' m')} | {note} |")
    skill_p = f"{p_skill*100:+.0f}%" if p_skill is not None else "—"
    L.append(f"| **pooled** | {n_tot} | {_fmt(p_raw,' m')} | {_fmt(p_corr,' m')} | {skill_p} | | n-weighted |")
    L += ["",
          f"**Read:** the correction {'beats' if (p_skill or 0) > 0 else 'does NOT beat'} raw LSOFS "
          f"pooled ({_fmt(p_corr,' m')} vs {_fmt(p_raw,' m')}). "
          "Skill is the audit's demotion test (ADR-006): a correction that can't beat raw gets benched.",
          ""]
    if anchor:
        L += ["## Nearshore anchor (Landsat 30 m shore − GLSEA offshore, same-day)", "",
              f"n={anchor['n']} clear scenes · shore-warm **{anchor['mean']:+.2f} °C** "
              f"(range {anchor['lo']:+.2f}…{anchor['hi']:+.2f}). "
              "GLSEA (1 km) cannot resolve this nearshore warming; too few scenes to fold into the "
              "anchor yet (see OVERNIGHT_ITERATION ADR-020).", ""]
    return "\n".join(L)


def main(argv) -> int:
    rows = load_gate()
    stats = per_chain_stats(rows)
    anchor = anchor_summary()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(stats, anchor))
    p_raw, p_corr = pooled(stats, "raw_mae"), pooled(stats, "corr_mae")
    n_tot = sum(s["n"] for s in stats.values())
    print(f"scorecard: {n_tot} scored rows · pooled corrected MAE {_fmt(p_corr,' m')} "
          f"vs raw {_fmt(p_raw,' m')} -> {OUT.relative_to(ROOT)}")
    if "--check" in argv:
        if p_raw is None or p_corr is None:
            print("scorecard --check: not enough data to judge (skipped)")
            return 0
        if p_corr > p_raw:
            print("scorecard --check: FAIL — correction does not beat raw LSOFS")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
