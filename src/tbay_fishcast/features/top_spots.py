"""'Best spots today' — turn the explore-map into an answer, from the model's OWN measured output.

The map shows WHERE each species' conditions are met; an angler still has to zoom ten stretches × four
species by hand. This module ranks the stretches per species so the tool hands over the shortlist —
but every number comes from the deterministic layer already on the map, not a new judgment:

  habitat index = Σ over the stretch's lead-0 polygons of  tier_weight × polygon_area
                  (tier_weight escalates s1→s5, i.e. in-range < optimal < break < strong < top)

So a stretch scores high by having a LOT of the RIGHT water (optimal temperature) with STRONG
structure (the glow tiers) reachable from shore — exactly the conjunction the map paints. The score
is reported as a RELATIVE index (0–100 vs the day's best stretch), never an absolute catch rate.

Species honesty is built in: the strong-temperature-cue species (lake trout, coaster) are ranked on
this thermal-×-structure index because that IS their signal. For the weak-cue migratory species
(salmon, steelhead) the module says so — their real driver is run timing and tributary plumes
(run_calendar / the river-mouth markers), so their thermal ranking carries an explicit caveat and the
active run windows are surfaced alongside. Pure ranking over pre-computed areas; no I/O, no LLM.
"""
from __future__ import annotations

# Tier → habitat weight. Mirrors the UI's SUIT ramp intent: optimal temp and stronger structure are
# worth more per unit area than merely being in range. Not a fitted weight — an ordering of the map's
# own disjoint tiers (in-range < optimal < break < strong break < top break).
TIER_WEIGHT = {"s1": 0.15, "s2": 0.50, "s3": 0.70, "s4": 0.85, "s5": 1.00}
_GLOW = ("s3", "s4", "s5")   # structure tiers (only emitted for strong-cue species)


def _index(tier_area: dict) -> float:
    """Weighted habitat index (m²-weighted) for one stretch/species from {tier: area_m2}."""
    return sum(TIER_WEIGHT.get(t, 0.0) * a for t, a in tier_area.items())


def _reason(tier_area: dict, weak_cue: bool) -> str:
    """One honest phrase describing WHY this stretch ranks, from its tier composition (ha)."""
    ha = {t: a / 1e4 for t, a in tier_area.items()}
    opt = ha.get("s2", 0.0)
    glow = sum(ha.get(t, 0.0) for t in _GLOW)
    inrange = ha.get("s1", 0.0)
    if weak_cue:
        total = opt + inrange
        return (f"{total:.0f} ha in preferred temperature"
                if total >= 0.5 else "marginal preferred-temperature water")
    if glow >= 0.3 and opt >= 0.3:
        return f"{opt:.0f} ha optimal temp over {glow:.0f} ha of structure (breaks/points)"
    if glow >= 0.3:
        return f"{glow:.0f} ha of reachable structure in range"
    if opt >= 0.3:
        return f"{opt:.0f} ha of optimal-temperature water"
    if inrange >= 0.3:
        return f"{inrange:.0f} ha in range, little strong structure"
    return "little reachable habitat today"


def rank_species(sp_id: str, weak_cue: bool, per_stretch: dict, names: dict,
                 *, min_ha: float = 0.25, top_n: int = 5) -> list[dict]:
    """Rank stretches for one species.

    per_stretch: {stretch_id: {tier: area_m2}} for THIS species at lead 0.
    names:       {stretch_id: display name}.
    Returns up to top_n entries [{id, name, score, index, opt_ha, glow_ha, reason}], score = the
    index scaled 0–100 vs the day's top stretch. Stretches with < min_ha total reachable habitat are
    dropped (nothing to recommend there). Empty list when the species has no meaningful water today.
    """
    import math as _math
    scored = []
    for sid, ta in per_stretch.items():
        # areas are physical m² — drop non-finite/negative entries so a NaN can't crash round()
        # and a negative can't invert the 0-100 score contract (stress-test 2026-08)
        ta = {t: a for t, a in ta.items() if _math.isfinite(a) and a > 0.0}
        total_ha = sum(ta.values()) / 1e4
        if total_ha < min_ha:
            continue
        idx = _index(ta)
        scored.append((sid, idx, ta, total_ha))
    if not scored:
        return []
    top = max(idx for _, idx, _, _ in scored) or 1.0
    scored.sort(key=lambda r: r[1], reverse=True)
    out = []
    for sid, idx, ta, _ in scored[:top_n]:
        out.append({
            "id": sid, "name": names.get(sid, sid),
            "score": round(100.0 * idx / top),
            "opt_ha": round(ta.get("s2", 0.0) / 1e4, 1),
            "glow_ha": round(sum(ta.get(t, 0.0) for t in _GLOW) / 1e4, 1),
            "reason": _reason(ta, weak_cue),
        })
    return out


def build(species, tier_area_by_stretch: dict, names: dict, active_runs=None) -> dict:
    """Assemble the full top-spots block for the manifest.

    species: iterable of objects with .id, .name, .temp_cue.
    tier_area_by_stretch: {stretch_id: {species_id: {tier: area_m2}}} at lead 0.
    active_runs: optional list of active run dicts (from run_calendar) to surface for weak-cue fish.
    Returns {species_id: {ranked: [...], weak_cue: bool, caveat: str|None}}.
    """
    out = {}
    for sp in species:
        weak = getattr(sp, "temp_cue", "strong") == "weak"
        per = {sid: d.get(sp.id, {}) for sid, d in tier_area_by_stretch.items()}
        ranked = rank_species(sp.id, weak, per, names)
        caveat = None
        if weak:
            caveat = ("Temperature is a minor cue for this species — the real driver is run timing "
                      "and tributary plumes. Treat the map ranking as secondary to the river-mouth "
                      "markers and the run windows.")
        out[sp.id] = {"ranked": ranked, "weak_cue": weak, "caveat": caveat}
    if active_runs is not None:
        out["_active_runs"] = active_runs
    return out
