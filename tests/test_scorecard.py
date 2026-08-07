"""Accuracy-scorecard math — pure, hermetic (no files, no network)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "accuracy_scorecard", Path(__file__).resolve().parents[1] / "scripts" / "accuracy_scorecard.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def _row(day, chain, obs, raw, corr_err, froz_err=None):
    return {"date": day, "chain": chain, "obs_iso_m": str(obs), "raw_iso_m": str(raw),
            "corr_iso_frozen_m": "", "abs_err_frozen_m": "" if froz_err is None else str(froz_err),
            "corr_iso_live_m": str(obs + corr_err), "abs_err_live_m": str(abs(corr_err)),
            "bias_source": "live", "glsea_anchor": "ok", "retrieved_utc": ""}


def test_skill_and_mae():
    rows = [
        _row("2026-08-04", "A", obs=8.0, raw=15.0, corr_err=3.0),   # raw err 7, corr 3
        _row("2026-08-05", "A", obs=4.0, raw=6.0, corr_err=1.0),    # raw err 2, corr 1
    ]
    s = sc.per_chain_stats(rows)["A"]
    assert s["n"] == 2
    assert abs(s["raw_mae"] - 4.5) < 1e-9         # (7+2)/2
    assert abs(s["corr_mae"] - 2.0) < 1e-9        # (3+1)/2
    assert abs(s["skill"] - (1 - 2.0 / 4.5)) < 1e-9
    # consecutive days -> persistence = |4-8| = 4
    assert abs(s["persistence_mae"] - 4.0) < 1e-9
    assert s["obs_constant"] is False


def test_constant_obs_flagged():
    rows = [_row("2026-08-04", "B", 3.0, 6.0, 1.0), _row("2026-08-05", "B", 3.0, 2.0, 0.5)]
    s = sc.per_chain_stats(rows)["B"]
    assert s["obs_constant"] is True
    assert abs(s["persistence_mae"] - 0.0) < 1e-9   # obs never moves


def test_absent_rows_excluded():
    rows = [_row("2026-08-04", "C", 5.0, 9.0, 2.0),
            {"date": "2026-08-06", "chain": "C", "obs_iso_m": "5.0", "raw_iso_m": "8.0",
             "corr_iso_frozen_m": "", "abs_err_frozen_m": "", "corr_iso_live_m": "",
             "abs_err_live_m": "", "bias_source": "live", "glsea_anchor": "absent", "retrieved_utc": ""}]
    s = sc.per_chain_stats(rows)["C"]
    assert s["n"] == 1                              # the absent-anchor row is not scored


def test_pooled_is_n_weighted():
    stats = {"A": {"n": 3, "corr_mae": 2.0}, "B": {"n": 1, "corr_mae": 6.0}}
    assert abs(sc.pooled(stats, "corr_mae") - (2.0 * 3 + 6.0 * 1) / 4) < 1e-9


def _frow(lead, obs, raw, err):
    return {"valid_date": "2026-08-05", "chain": "X", "lead_h": str(lead), "issue_date": "",
            "obs_iso_m": str(obs), "fcst_raw_iso_m": str(raw), "fcst_corr_iso_m": "",
            "abs_err_m": str(err), "bias_source": "live", "glsea_anchor": "ok", "retrieved_utc": ""}


def test_per_lead_stats_groups_by_lead():
    rows = [_frow(24, 8.0, 12.0, 1.0), _frow(24, 4.0, 6.0, 3.0), _frow(120, 5.0, 11.0, 5.0)]
    s = sc.per_lead_stats(rows)
    assert s[24]["n"] == 2 and abs(s[24]["corr_mae"] - 2.0) < 1e-9      # (1+3)/2
    assert abs(s[24]["raw_mae"] - 3.0) < 1e-9                            # (|12-8|+|6-4|)/2 = (4+2)/2
    assert s[120]["n"] == 1 and abs(s[120]["corr_mae"] - 5.0) < 1e-9
