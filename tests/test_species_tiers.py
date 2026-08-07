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


def test_structure_glow_bands_are_ordered_and_disjoint():
    # all optimal temp + reachable + deep enough; increasing structure strength across pixels
    bottom_c = _row([8, 8, 8, 8])
    strength = _row([0.5, _bcs.STRENGTH_BREAK, _bcs.STRENGTH_STRONG, _bcs.STRENGTH_TOP])
    within = _row([1, 1, 1, 1]).astype(bool)
    depth = _row([8, 8, 8, 8])
    lt = _Sp("lake_trout", (6, 12), (6, 10))
    tiers, _ = _species_and_check(bottom_c, strength, within, depth, lt)
    assert tiers["s2"][0, 0]   # below the break bar -> optimal-temp base only
    assert tiers["s3"][0, 1]   # break
    assert tiers["s4"][0, 2]   # strong
    assert tiers["s5"][0, 3]   # top break
    # disjoint: exactly one tier true per pixel
    for j in range(4):
        assert sum(int(tiers[t][0, j]) for t in ("s1", "s2", "s3", "s4", "s5")) == 1


def test_out_of_range_temperature_is_floor_blank():
    bottom_c = _row([20, 20])                       # way above the laker range -> suit 0
    strength = _row([_bcs.STRENGTH_TOP, _bcs.STRENGTH_TOP])   # strong structure, but wrong temp
    within = _row([1, 1]).astype(bool)
    depth = _row([8, 8])
    lt = _Sp("lake_trout", (6, 12), (6, 10))
    tiers, fair = _species_and_check(bottom_c, strength, within, depth, lt)
    # the floor: no temperature match => nothing shades, structure alone never glows
    assert not fair.any()
    assert not any(tiers[t].any() for t in ("s1", "s2", "s3", "s4", "s5"))


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
