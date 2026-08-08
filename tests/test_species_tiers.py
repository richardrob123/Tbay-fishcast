"""Unit tests for the per-species tier core (_species_tiers) — the depth gate and the continuous
structure-strength banding, without the LSOFS/geometry pipeline (validation test-debt #12).
"""
import importlib.util
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_spec = importlib.util.spec_from_file_location(
    "bcs_tiers", Path(__file__).resolve().parents[1] / "scripts" / "build_coast_site.py")
_bcs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bcs)


@dataclass
class _Sp:
    id: str
    range_c: tuple
    optimal: tuple
    min_depth_m: float | None = None
    max_depth_m: float | None = None
    temp_cue: str = "strong"


@dataclass
class _Prod:
    max_reach_depth_m: float = 22.0


def _row(vals):
    return np.array([vals], dtype=float)


def test_depth_gate_excludes_shallow_for_min_depth_species():
    # 4 pixels, all optimal temperature (8 C in a 6-10 optimal), all reachable, NO structure.
    bottom_c = _row([8, 8, 8, 8])
    strength = _row([0, 0, 0, 0])
    within = _row([1, 1, 1, 1]).astype(bool)
    depth = _row([1.0, 3.0, 6.0, 12.0])           # first two are < 4 m
    lt = _Sp("lake_trout", (6, 12), (6, 10), min_depth_m=4.0)
    tiers, fair = _species_and_check(bottom_c, strength, within, depth, lt)
    # adult lakers are gated OUT of < 4 m even though the temperature is optimal there
    assert not fair[0, 0] and not fair[0, 1]
    assert fair[0, 2] and fair[0, 3]
    # good (optimal plateau) only where in range AND deep enough
    assert tiers["s2"][0, 2] and not tiers["s2"][0, 0]


def test_structure_marks_are_nested_cumulative_and_temperature_free():
    """ADR-038/039: structure marks are a SEPARATE static channel — pure bathymetry, no temperature
    input at all. Levels are the measured percentile RAMP (g75..g99), NESTED CUMULATIVE (filled
    contours: g99 ⊆ g95 ⊆ … ⊆ g75) — disjoint rings would shred into sliver speckle."""
    ramp_tags = [f"g{q}" for q in _bcs._RAMP_QS]
    # one pixel below the ramp floor, then one pixel exactly at each measured edge
    strength = _row([0.5 * _bcs.STRENGTH_RAMP[0]] + list(_bcs.STRENGTH_RAMP))
    n = strength.shape[1]
    within = _row([1] * n).astype(bool)
    depth = _row([8] * n)
    lt = _Sp("lake_trout", (6, 12), (6, 10))
    g = _bcs._species_structure(strength, within, depth, lt, _Prod())
    assert set(g) == set(ramp_tags)
    assert not any(g[t][0, 0] for t in ramp_tags)          # below the ramp floor: no mark at all
    for i, t in enumerate(ramp_tags):
        assert g[t][0, i + 1], f"{t} must fire at its own measured edge"
    for hi, lo in zip(ramp_tags[1:], ramp_tags[:-1]):      # nesting: stronger ⊆ weaker
        assert (~g[hi] | g[lo]).all(), f"{hi} must be a subset of {lo}"
    # temperature-free by construction: the function takes no bottom_c at all — and the marks are
    # identical whatever the thermal field does (static across forecast days by construction)
    g2 = _bcs._species_structure(strength, within, depth, lt, _Prod())
    for t in g:
        assert (g[t] == g2[t]).all()


def test_temperature_wash_is_pure_cited_bands():
    """ADR-038: the temperature tiers are the cited-band wash ONLY (s1 in-range ring, s2 optimal
    core) — no structure mixed in. A strong break under the pixel changes nothing in the wash."""
    lt = _Sp("lake_trout", (4, 12), (6, 10))
    within = _row([1, 1]).astype(bool)
    depth = _row([8, 8])
    strong = _row([_bcs.STRENGTH_STRONG, 0.0])
    t_opt, _ = _species_and_check(_row([8, 8]), strong, within, depth, lt)
    assert t_opt["s2"][0, 0] and t_opt["s2"][0, 1]   # both optimal — structure irrelevant to the wash
    t_marg, fair = _species_and_check(_row([11, 11]), strong, within, depth, lt)
    assert t_marg["s1"][0, 0] and t_marg["s1"][0, 1] # both in-range ring
    assert fair.all()


def test_out_of_range_temperature_is_floor_blank():
    bottom_c = _row([20, 20])                       # way above the laker range -> suit 0
    strength = _row([_bcs.STRENGTH_TOP, _bcs.STRENGTH_TOP])   # strong structure, but wrong temp
    within = _row([1, 1]).astype(bool)
    depth = _row([8, 8])
    lt = _Sp("lake_trout", (6, 12), (6, 10))
    tiers, fair = _species_and_check(bottom_c, strength, within, depth, lt)
    # the floor: no temperature match => no WASH (the static structure marks are a separate
    # channel and legitimately still show — the bottom exists; the absent wash says "wrong temp")
    assert not fair.any()
    assert not any(tiers[t].any() for t in ("s1", "s2"))


def _species_and_check(bottom_c, strength, within, depth, sp):
    tiers, fair = _bcs._species_tiers(bottom_c, strength, within, depth, sp, _Prod())
    return tiers, fair


def test_fall_laker_gate_relaxes_shallow():
    """T1d: in FALL, lake trout stage on shallow shoals, so the summer >=4 m gate must relax —
    otherwise the map excludes the very water the fall season badge points anglers to."""
    bottom_c = _row([8, 8]); strength = _row([0, 0]); within = _row([1, 1]).astype(bool)
    depth = _row([2.0, 8.0])                       # 2 m is below the summer 4 m laker floor
    lt = _Sp("lake_trout", (4, 12), (6, 10), min_depth_m=4.0)
    _, fair_summer = _bcs._species_tiers(bottom_c, strength, within, depth, lt, _Prod(), season="summer")
    _, fair_fall = _bcs._species_tiers(bottom_c, strength, within, depth, lt, _Prod(), season="fall")
    assert not fair_summer[0, 0] and fair_summer[0, 1]      # summer: 2 m excluded
    assert fair_fall[0, 0] and fair_fall[0, 1]              # fall: 2 m shoal now included


def test_struct_calib_fallback_matches_committed_json():
    """The hardcoded fallback in _load_struct_calib must equal the committed calibration —
    otherwise an unreadable json silently applies STALE bars (caught in the 2026-08 stress test,
    where the fallback still carried the pre-smoothing values)."""
    import json
    from pathlib import Path
    d = json.loads((Path(_bcs.__file__).resolve().parents[1] / "data" / "calib"
                    / "bathy_slope.json").read_text())
    ramp = tuple(d["strength_pcts"][str(q)] for q in _bcs._RAMP_QS)
    assert (_bcs.STRUCT_SLOPE_ABS, _bcs.STRUCT_RELIEF_ABS, _bcs.STRENGTH_RAMP) == (
        d["struct_slope_abs"], d["struct_relief_abs_m"], ramp)
    # the ranking bars stay pinned to the ADR-029 p90/p95/p99 entries of the same ladder
    sb = d["strength_bands"]
    assert (_bcs.STRENGTH_BREAK, _bcs.STRENGTH_STRONG, _bcs.STRENGTH_TOP) == (
        sb["break"], sb["strong"], sb["exceptional"])
    # and the fallback tuple itself (returned when the json is unreadable) matches too
    import unittest.mock as _m
    with _m.patch.object(type(_bcs._BATHY_CALIB), "read_text", side_effect=OSError("gone")):
        assert _bcs._load_struct_calib() == (
            d["struct_slope_abs"], d["struct_relief_abs_m"], ramp)
